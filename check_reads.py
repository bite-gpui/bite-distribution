#!/usr/bin/env python3
"""Fail when a packaged crate reads a file its own archive does not carry.

Cargo packages only what is inside the crate directory and survives
`package.include`/`exclude` and `.gitignore`. A build script or an
`include_bytes!` that reaches past those bounds compiles in the monorepo and
fails for a consumer, because the file is simply not in the `.crate`.

`gpui_apple` was exactly this. Its build script fed cbindgen shader types read as
`CARGO_MANIFEST_DIR/../gpui_types/src/color.rs`, which the workspace layout made
work and which every published tarball failed on with `ParseCannotOpenFile`. The
crate is also in `no_verify`, so neither the resolver nor the workspace checks
could see it — only the archive can.

Two halves, one for each way a crate is wrong about what it carries:

* A path that resolves outside the crate directory is broken by construction —
  nothing outside the crate can be in the archive — so it is reported without
  consulting the archive. No `join` chain is followed: the upward step is its
  own literal, and `..` resolving outside the crate is enough.
* A path that resolves inside the crate is checked against `cargo package
  --list`: if the file exists in the checkout but is not packaged, a consumer
  will not have it.

Only paths that exist are reported. A build script may name a file it generates
or that exists on another platform, and the many non-path strings a build script
carries (`"-sdk"`, `"cargo:rerun-if-changed={}"`) name nothing, so all of them
cost nothing.

**Test code is reported but does not fail.** A reference inside `#[cfg(test)]`,
or under `tests/`, `examples/` or `benches/`, is not compiled when the crate is
built as a dependency, so it cannot break the published artifact — the font
`include_bytes!` in `gpui_wgpu`'s tests and `gpui_authoring`'s image fixture are
of this kind. They are still worth seeing, because they do break `cargo test`
for anyone building the crate from the registry.

Usage:
    check_reads.py --stage .                       # every crate in the stage report
    check_reads.py --crate crates/gpui_apple       # named crates, for a quick check
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

# Every double-quoted literal on a single line. Escapes are dropped rather than
# interpreted: a literal that needs them is not a path this check cares about.
STRING = re.compile(r'"([^"\n]*)"')

# The ways a source file names another file at compile time.
FILE_REFERENCE = re.compile(
    r'include_(?:bytes|str)!\s*\(\s*"([^"]*)"\s*\)'
    r'|#\s*\[\s*path\s*=\s*"([^"]*)"\s*\]'
)

CFG_TEST = re.compile(r'#\s*\[\s*cfg\s*\(\s*test\s*\)\s*\]')

# Directories whose contents are separate compilation units a consumer never
# builds, so a reference from one cannot break the published artifact.
TEST_DIRECTORIES = ("tests/", "benches/", "examples/")


class Finding:
    def __init__(self, crate: str, source: str, literal: str, detail: str, test_only: bool) -> None:
        self.crate = crate
        self.source = source
        self.literal = literal
        self.detail = detail
        self.test_only = test_only

    def __str__(self) -> str:
        kind = "warn" if self.test_only else "FAIL"
        scope = " (test-only, so not in the published artifact's build)" if self.test_only else ""
        return f'{kind} {self.crate}: {self.source}: "{self.literal}" {self.detail}{scope}'


def packaged_files(crate: pathlib.Path) -> list[str]:
    """The paths `cargo package` would put in the archive, relative to the crate."""
    result = subprocess.run(
        [
            "cargo", "package", "--list", "--allow-dirty",
            "--manifest-path", str(crate / "Cargo.toml"),
        ],
        cwd=str(crate),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"{crate.name}: cargo package --list failed:\n{result.stderr.strip()}"
        )
    # Warnings and notes go to stderr, and a `--list` line is a path that has no
    # spaces, so anything else on stdout is not one.
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _skip_literal(text: str, start: int) -> int:
    """Index just past the string or character literal opening at `start`."""
    quote = text[start]
    index = start + 1
    if quote == "'" and index < len(text) and (text[index].isalnum() or text[index] == "_"):
        # A lifetime such as `'static`, not a character literal.
        if index + 1 >= len(text) or text[index + 1] != "'":
            return index
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == quote:
            return index + 1
        if text[index] == "\n":
            return index  # an unterminated literal; do not run past the line
        index += 1
    return index


def _matching_brace(text: str, open_index: int) -> int:
    """Index of the `}` closing the `{` at `open_index`."""
    depth = 0
    index = open_index
    while index < len(text):
        char = text[index]
        if char == "/" and text.startswith("//", index):
            newline = text.find("\n", index)
            index = len(text) if newline == -1 else newline
            continue
        if char == "/" and text.startswith("/*", index):
            close = text.find("*/", index + 2)
            index = len(text) if close == -1 else close + 2
            continue
        if char in "\"'":
            index = _skip_literal(text, index)
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return len(text)


def _item_end(text: str, start: int) -> int:
    """End index of the item whose body starts at or after `start`."""
    index = start
    grouped = 0  # parentheses and brackets before the body, e.g. a fn signature
    while index < len(text):
        char = text[index]
        if char == "/" and text.startswith("//", index):
            newline = text.find("\n", index)
            index = len(text) if newline == -1 else newline
            continue
        if char == "/" and text.startswith("/*", index):
            close = text.find("*/", index + 2)
            index = len(text) if close == -1 else close + 2
            continue
        if char in "\"'":
            index = _skip_literal(text, index)
            continue
        if char in "([":
            grouped += 1
        elif char in ")]":
            grouped -= 1
        elif grouped == 0 and char == ";":
            return index + 1
        elif grouped == 0 and char == "{":
            return _matching_brace(text, index) + 1
        index += 1
    return len(text)


def test_ranges(text: str) -> list[tuple[int, int]]:
    """The spans of items gated by `#[cfg(test)]`."""
    ranges = []
    for match in CFG_TEST.finditer(text):
        ranges.append((match.start(), _item_end(text, match.end())))
    return ranges


def references(text: str, everything: bool) -> list[tuple[str, int]]:
    """Path literals with their offsets.

    A build script has no marker for the paths it reads, so every literal is a
    candidate and the existence test sorts them out. A source file does mark its
    file references, so only those are taken.
    """
    if not everything:
        return [
            (group[0] or group[1], match.start())
            for match in FILE_REFERENCE.finditer(text)
            for group in [match.groups()]
            if "\\" not in (group[0] or group[1])
        ]
    return [
        (match.group(1), match.start(1))
        for match in STRING.finditer(text)
        if "\\" not in match.group(1)
    ]


def check(
    crate: pathlib.Path,
    archive: set[str],
    source: pathlib.Path,
    text: str,
    everything: bool,
) -> list[Finding]:
    """Check one file's path literals against the crate's archive."""
    findings: list[Finding] = []
    relative = source.relative_to(crate).as_posix()
    # Build scripts run with the crate directory as the working directory; a
    # source file resolves a relative include against its own directory.
    base = crate if source.name == "build.rs" else source.parent
    gated = test_ranges(text)
    under_test_directory = relative.startswith(TEST_DIRECTORIES)

    for literal, offset in references(text, everything):
        if not literal or literal.startswith("/"):
            continue
        candidate = (base / literal).resolve()
        if not candidate.exists():
            continue  # generated into OUT_DIR, or another platform's file
        test_only = under_test_directory or any(
            start <= offset < end for start, end in gated
        )
        if not candidate.is_relative_to(crate):
            findings.append(Finding(
                crate.name, relative, literal,
                "resolves outside the crate, which no package can carry",
                test_only,
            ))
            continue
        packed = candidate.relative_to(crate).as_posix()
        if candidate.is_dir():
            if not any(entry.startswith(packed + "/") for entry in archive):
                findings.append(Finding(
                    crate.name, relative, literal,
                    f"is a directory with nothing packaged under {packed}/",
                    test_only,
                ))
        elif packed not in archive:
            findings.append(Finding(
                crate.name, relative, literal,
                f"is not in the package ({packed} exists in the checkout "
                "but nothing inserts or includes it)",
                test_only,
            ))
    return findings


def check_crate(crate: pathlib.Path) -> list[Finding]:
    crate = crate.resolve()
    if not (crate / "Cargo.toml").exists():
        raise SystemExit(f"{crate} is not a crate directory")

    archive = packaged_files(crate)
    packaged = set(archive)
    findings: list[Finding] = []

    build_script = crate / "build.rs"
    if build_script.exists():
        findings += check(
            crate, packaged, build_script, build_script.read_text(), everything=True
        )

    for entry in archive:
        if not entry.endswith(".rs"):
            continue
        source = crate / entry
        if not source.is_file():
            continue  # Cargo.toml.orig and friends are named but absent
        findings += check(crate, packaged, source, source.read_text(), everything=False)

    failures = sum(1 for finding in findings if not finding.test_only)
    print(f"--- {crate.name}: {len(archive)} files packaged, {failures} finding(s)")
    for finding in findings:
        print(finding)
    return findings


def stage_crates(stage: pathlib.Path) -> list[pathlib.Path]:
    report = stage / "dist-stage.json"
    if not report.exists():
        raise SystemExit(f"no stage report at {report}; run stage.py first")
    data = json.loads(report.read_text())
    return [stage / crate["dir"] for crate in data["crates"]]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=pathlib.Path)
    parser.add_argument("--crate", type=pathlib.Path, action="append", default=[],
                        help="a crate directory to check; repeatable")
    parser.add_argument("--strict", action="store_true",
                        help="treat test-only findings as failures too")
    args = parser.parse_args(argv)

    crates: list[pathlib.Path] = list(args.crate)
    if args.stage:
        crates = stage_crates(args.stage) + crates
    if not crates:
        raise SystemExit("nothing to check: pass --stage or --crate")

    findings: list[Finding] = []
    for crate in crates:
        findings += check_crate(crate)

    failures = [finding for finding in findings if not finding.test_only]
    if args.strict:
        failures = findings
    if failures:
        print(
            f"\n{len(failures)} path(s) a packaged crate reads but does not carry. "
            "Vendor the file into the crate or bundle it, as the vendored shader "
            "types in crates/gpui_apple/vendor and bundle_crate_assets.py do.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
