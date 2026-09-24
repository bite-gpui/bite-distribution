#!/usr/bin/env python3
"""Compile the packaged archives the way a consumer does.

`cargo publish --dry-run` extracts each tarball and compiles it, which is the
check that an artifact builds. It skips the crates in `no_verify`, though — and
those are the ones whose packaging is hardest to get right, because nothing else
in the pipeline ever looks inside their tarball. `gpui_apple` was in that set
when its build script turned out to read shader types from a sibling crate.

This closes that gap. Every publishable crate is packaged without verification,
the archives are unpacked into a directory that has no sibling of the workspace,
and each one is compiled there. A crate that built from the workspace but not
from its archive fails here, and that is the only property a registry install
depends on.

The unpacked crates are named `<published>-<version>`, so a path that steps up
with `..` and then names a source crate — the `gpui_apple` shape — finds nothing:
the directory it would look for is not the directory it is standing in.

Usage:
    test_isolated.py --stage src                 # package, unpack, build
    test_isolated.py --stage src --no-build      # package and unpack only
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile

# wasm-only crates are published but do not build for the host, so they are
# unpacked and left alone; the native check has nothing to say about them.
def publishable(report: dict) -> list[dict]:
    withheld = {entry["published"] for entry in report.get("blocked", [])}
    return [crate for crate in report["crates"] if crate["published"] not in withheld]


def package(stage: pathlib.Path, crate: dict, crates: list[dict]) -> pathlib.Path:
    """Produce the `.crate` for one crate, without the verification build.

    Verification is skipped because this run does its own: it compiles from the
    unpacked archive rather than from the tarball cargo would have built.

    Packaging still resolves a normalised manifest, whose path dependencies have
    become version requirements, so the rest of the release has to be patched in
    — the same flags a publish dry run passes. A first release has none of these
    versions on the registry yet, so without them the second crate already fails
    with `no matching package named …`.
    """
    patches = [
        flag
        for other in crates
        if other["published"] != crate["published"]
        for flag in (
            "--config",
            f"patch.crates-io.{other['published']}.path="
            f"{json.dumps(str((stage / other['dir']).resolve()))}",
        )
    ]
    command = [
        "cargo", "package",
        # Staging rewrites the tree in place, so it is dirty by construction.
        "--allow-dirty", "--no-verify",
        "--manifest-path", str(stage / crate["dir"] / "Cargo.toml"),
        *patches,
    ]
    result = subprocess.run(command, cwd=stage, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(
            f"{crate['published']}: cargo package failed:\n{result.stderr.strip()}"
        )
    archive = stage / "target" / "package" / f"{crate['published']}-{crate['version']}.crate"
    if not archive.exists():
        raise SystemExit(f"{crate['published']}: expected {archive} to exist after packaging")
    return archive


def unpack(archive: pathlib.Path, work: pathlib.Path) -> pathlib.Path:
    """Extract a `.crate` into its own directory and return that directory."""
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(work, filter="data")
    # A `.crate` contains exactly one top-level directory, named for the package.
    unpacked = work / archive.name.removesuffix(".crate")
    if not unpacked.is_dir():
        raise SystemExit(f"{archive.name}: does not unpack to {unpacked.name}/")
    return unpacked


def patches(work: pathlib.Path, crates: list[dict], root: str) -> list[str]:
    """Route every other unpacked crate into this one's resolution.

    The archives are unpacked, not published, so their dependencies have no
    registry version to resolve to yet. Patching by path is what a first release
    does; it is also what proves the archive alone is sufficient, because the
    patch points at the unpacked directory and never at the checkout.
    """
    flags: list[str] = []
    for crate in crates:
        if crate["published"] == root:
            continue
        directory = (work / f"{crate['published']}-{crate['version']}").resolve()
        flags += ["--config", f"patch.crates-io.{crate['published']}.path={json.dumps(str(directory))}"]
    return flags


def build(work: pathlib.Path, crates: list[dict], wasm_only: set[str], only: set[str]) -> int:
    target_dir = work / "target"
    failures: list[str] = []
    for crate in crates:
        if only and crate["published"] not in only:
            continue
        if crate["published"] in wasm_only:
            print(f"--> {crate['published']} is wasm-only; unpacked but not built natively")
            continue
        root = work / f"{crate['published']}-{crate['version']}"
        command = [
            "cargo", "build",
            "--manifest-path", str(root / "Cargo.toml"),
            *patches(work, crates, crate["published"]),
        ]
        environment = {**os.environ, "CARGO_TARGET_DIR": str(target_dir)}
        print(f"==> {crate['published']} {crate['version']}")
        result = subprocess.run(command, cwd=root, env=environment)
        if result.returncode != 0:
            failures.append(crate["published"])
    if failures:
        print(
            f"\nfailed to build from the archive: {', '.join(failures)}",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, type=pathlib.Path)
    parser.add_argument("--work", type=pathlib.Path,
                        help="where to unpack; a fresh temporary directory by default")
    parser.add_argument("--no-build", action="store_true",
                        help="package and unpack, but do not compile")
    parser.add_argument("--only", help="comma-separated crate names to build")
    args = parser.parse_args(argv)

    stage = args.stage.resolve()
    report_file = stage / "dist-stage.json"
    if not report_file.exists():
        raise SystemExit(f"no stage report at {report_file}; run stage.py first")
    report = json.loads(report_file.read_text())

    crates = publishable(report)
    if not crates:
        raise SystemExit("nothing is publishable; every crate in this release is withheld")

    work = args.work.resolve() if args.work else pathlib.Path(tempfile.mkdtemp(prefix="bite-isolated-"))
    work.mkdir(parents=True, exist_ok=True)
    print(f"unpacking {len(crates)} crates into {work}")

    for crate in crates:
        archive = package(stage, crate, crates)
        unpacked = unpack(archive, work)
        print(f"--- {crate['published']} {crate['version']} -> {unpacked.name}/")

    if args.no_build:
        return 0

    only = {name.strip() for name in args.only.split(",") if name.strip()} if args.only else set()
    known = {crate["published"] for crate in crates}
    unknown = sorted(only - known)
    if unknown:
        raise SystemExit(f"not in this release: {', '.join(unknown)}")

    try:
        return build(work, crates, set(report.get("wasm_only", [])), only)
    finally:
        if not args.work:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
