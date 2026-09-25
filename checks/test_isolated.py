#!/usr/bin/env python3
"""Compile the packaged archives the way a consumer does.

`cargo publish --dry-run` extracts a tarball and compiles it as if it were the
root of a build. That resolves the package's *dev*-dependencies, and it is why a
crate whose dev-dependencies cannot resolve is published with `--no-verify` — the
verification is skipped, so nothing ever looks inside its tarball. `gpui_apple`
was in that set when its build script turned out to read shader types from a
sibling crate, and it is the class of defect this check exists for.

A consumer never resolves a dependency's dev-dependencies. So this does what a
consumer does: it packages every publishable crate, unpacks the archives into a
directory with no workspace around them, and writes a small crate that depends on
all of them. Cargo then resolves and compiles exactly what a registry install
gets, `no_verify` included, and nothing is compiled that a consumer would not
compile.

The unpacked crates are named `<published>-<version>`, so a path that steps up
with `..` and then names a source crate — the `gpui_apple` shape — finds nothing:
the directory it would look for is not the directory it is standing in.

**`cargo check`, not `cargo build`.** The checks have already compiled and tested
the closure, and `publish.py --dry-run` has already built most of these archives
for real. What is left to prove is that each archive is *self-sufficient*:
resolution succeeds, build scripts run, and every path the crate names is in the
tarball. `check` proves all of that and emits no code, which is what keeps the
step's disk inside a runner. `--build` does the full build when it is wanted.

Default features, not `--all-features`: this is the build a consumer gets, and
the checks' workspace-wide `--all-features` clippy pass already covers the code behind
`bench-support`, `inspector` and `profiler`.

Usage:
    test_isolated.py --stage src                 # package, unpack, compile the consumer
    test_isolated.py --stage src --only bite-gp-apple
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tarfile

# Named so that it cannot be confused with a published crate.
CONSUMER = "bite-isolated-check"

# A package's own manifest as packaged, plus one line. The archive has no
# `[workspace]` table, and cargo resolves a manifest by walking up to the nearest
# workspace, so a crate unpacked inside the staging workspace is reported as
# believing it is a member when it is not — the member list cannot cover a
# directory that only exists at this point. Declaring it its own workspace is
# what makes it standalone, which is what a crate fetched from the registry is.
STANDALONE = "\n[workspace]\n"


def patch_flag(published: str, directory: pathlib.Path) -> list[str]:
    """Route a crates-io dependency at a local crate directory.

    Packaging needs this even though nothing is uploaded: cargo rewrites path
    dependencies into version requirements and then resolves them, and on a first
    release none of those versions is on the registry yet.
    """
    return ["--config", f"patch.crates-io.{published}.path={json.dumps(str(directory))}"]


def crate_dir(stage: pathlib.Path, crate: dict) -> pathlib.Path:
    return (stage / crate["dir"]).resolve()


def unpacked_dir(work: pathlib.Path, crate: dict) -> pathlib.Path:
    return (work / f"{crate['published']}-{crate['version']}").resolve()


def publishable(report: dict) -> list[dict]:
    """The crates that will be published, in publish order."""
    by_name = {crate["published"]: crate for crate in report["crates"]}
    return [by_name[name] for name in report["order"] if name in by_name]


def package(stage: pathlib.Path, crate: dict, report: dict) -> pathlib.Path:
    """Produce the `.crate` for one crate, without the verification build."""
    patches = [
        flag
        for other in report["crates"]
        if other["published"] != crate["published"]
        for flag in patch_flag(other["published"], crate_dir(stage, other))
    ]
    result = subprocess.run(
        [
            "cargo", "package",
            # Staging rewrites the tree in place, so it is dirty by construction.
            "--allow-dirty", "--no-verify",
            "--manifest-path", str(stage / crate["dir"] / "Cargo.toml"),
            *patches,
        ],
        cwd=stage,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"{crate['published']}: cargo package failed:\n{result.stderr.strip()}"
        )
    archive = stage / "target" / "package" / f"{crate['published']}-{crate['version']}.crate"
    if not archive.exists():
        raise SystemExit(f"{crate['published']}: expected {archive} to exist after packaging")
    return archive


def unpack(archive: pathlib.Path, work: pathlib.Path) -> None:
    """Extract a `.crate` into its own directory, as a standalone package."""
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(work, filter="data")
    crate = work / archive.name.removesuffix(".crate")
    manifest = crate / "Cargo.toml"
    if not manifest.is_file():
        raise SystemExit(f"{archive.name}: did not unpack into its own directory")
    text = manifest.read_text()
    if "[workspace]" not in text:
        manifest.write_text(text + STANDALONE)


def write_consumer(work: pathlib.Path, crates: list[dict]) -> pathlib.Path:
    """Write the crate that consumes every unpacked archive."""
    directory = work / "consumer"
    (directory / "src").mkdir(parents=True, exist_ok=True)
    lines = [
        "# Generated by test_isolated.py: a consumer of the unpacked archives.",
        "#",
        "# Depending on every crate is what makes one build cover the whole release.",
        "# A consumer resolves a dependency's normal dependencies and not its",
        "# dev-dependencies, which is the difference from `cargo publish --dry-run`",
        "# and the reason the crates in `no_verify` are checked here.",
        "",
        "[package]",
        f'name = "{CONSUMER}"',
        'version = "0.0.0"',
        'edition = "2021"',
        "",
        "# This lives inside the staging workspace's target directory, so it has to",
        "# say it is not a member of it.",
        "[workspace]",
        "",
        "[dependencies]",
    ]
    lines += [f'{crate["published"]} = "{crate["version"]}"' for crate in crates]
    (directory / "Cargo.toml").write_text("\n".join(lines) + "\n")
    (directory / "src" / "lib.rs").write_text("")
    return directory


def compile_consumer(
    work: pathlib.Path, consumer: pathlib.Path, crates: list[dict], mode: str
) -> int:
    patches = [
        flag
        for crate in crates
        for flag in patch_flag(crate["published"], unpacked_dir(work, crate))
    ]
    print(f"{mode} {CONSUMER} against {len(crates)} archive(s)")
    return subprocess.run(
        ["cargo", mode, "--manifest-path", str(consumer / "Cargo.toml"), *patches],
        cwd=consumer,
        # One target directory for the whole release, next to the unpacked
        # sources and outside the staging build's, so the two cannot collide and
        # this one can be discarded on its own.
        env={**os.environ, "CARGO_TARGET_DIR": str(work / "target")},
    ).returncode


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, type=pathlib.Path)
    parser.add_argument("--work", type=pathlib.Path,
                        help="where to unpack and compile; `isolated/` inside the "
                             "staged checkout by default")
    parser.add_argument("--unpack-only", action="store_true",
                        help="package and unpack, but do not compile")
    parser.add_argument("--build", action="store_true",
                        help="compile with `cargo build` instead of `cargo check`")
    parser.add_argument("--only", help="comma-separated crates for the consumer to depend on")
    args = parser.parse_args(argv)

    stage = args.stage.resolve()
    report_file = stage / "dist-stage.json"
    if not report_file.exists():
        raise SystemExit(f"no stage report at {report_file}; run stage.py first")
    report = json.loads(report_file.read_text())

    crates = publishable(report)
    if not crates:
        raise SystemExit("nothing is publishable; every crate in this release is withheld")

    if args.only:
        wanted = {name.strip() for name in args.only.split(",") if name.strip()}
        unknown = sorted(wanted - {crate["published"] for crate in crates})
        if unknown:
            raise SystemExit(f"not publishable in this release: {', '.join(unknown)}")
        crates = [crate for crate in crates if crate["published"] in wanted]

    # Inside the staged checkout but outside `target/`: the same volume the job
    # already writes to, so this is never a small tmpfs, while `target/` stays
    # what the build cache is keyed on and does not absorb a second closure's
    # worth of artifacts. The directory is disposable — re-running unpacks it
    # again — and is never committed.
    work = (args.work or stage / "isolated").resolve()
    work.mkdir(parents=True, exist_ok=True)
    print(f"unpacking {len(crates)} crates into {work}")

    for crate in crates:
        unpack(package(stage, crate, report), work)

    if args.unpack_only:
        return 0

    consumer = write_consumer(work, crates)
    return compile_consumer(work, consumer, crates, "build" if args.build else "check")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
