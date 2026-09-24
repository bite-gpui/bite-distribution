#!/usr/bin/env python3
"""Publish a staged target's crates, in dependency order.

Two things make this more than a loop over `cargo publish`.

**Ordering and resume.** crates.io versions are immutable, so a run that is
interrupted halfway must continue rather than restart, and a version that
already exists is a skip, not an error. The order comes from stage.py's report,
which computes it from the manifests; the two forks and the eight release
targets have different leaf sets, so a hand-kept list would be wrong for most of
them.

**Verification of a first release.** `cargo publish` resolves a dependency with
a `version` from the registry, not from its `path`. A dependency-ordered first
release therefore fails at the second crate: its newly-versioned predecessor has
dependency-ordered first release therefore fails at the second crate: its newly-versioned predecessor has
not been uploaded yet. For dry runs every crate in the set is patched into the
temporary verification resolution, so `cargo publish` still packages and
compiles each tarball, but validates the local release graph instead of stale
registry versions. The patches are passed on the command line and never appear
in a published manifest.

**What cannot be verified.** Resolution also takes in the root package's own
dev-dependencies, and it is target-agnostic, so a crate that dev-depends on a
crate published after it (the facade is published last) or on a workspace crate
that is never published cannot be verified at all on a first release. stage.py
reports those as `no_verify`; they are packaged with `--no-verify`, which still
normalises the manifest, and the gate's workspace-wide check and clippy cover
the compilation that skips.

Usage:
    publish.py --stage wt/bite_v1.20.2 --list
    publish.py --stage wt/bite_v1.20.2 --dry-run
    publish.py --stage wt/bite_v1.20.2              # needs CARGO_REGISTRY_TOKEN
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

USER_AGENT = "bite-gpui distribution (https://github.com/bite-gpui)"


def load_report(stage: Path) -> dict:
    report = stage / "dist-stage.json"
    if not report.exists():
        raise SystemExit(f"no stage report at {report}; run stage.py first")
    data = json.loads(report.read_text())
    for crate in data["crates"]:
        manifest = stage / crate["dir"] / "Cargo.toml"
        if not manifest.exists():
            raise SystemExit(f"{crate['published']}: {manifest} is missing")
    return data


def registry_has(name: str, version: str) -> bool:
    url = f"https://crates.io/api/v1/crates/{name}/{version}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30):
            return True
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise SystemExit(f"could not check crates.io for {name} {version}: HTTP {error.code}")
    except urllib.error.URLError as error:
        raise SystemExit(f"could not reach crates.io for {name} {version}: {error.reason}")


def wait_for_visibility(name: str, version: str, attempts: int = 12, delay: float = 5.0) -> bool:
    """crates.io accepts an upload before its API reports the version.

    The next crate in the order depends on this one, so a run that continues
    too early fails on a version that does exist.
    """
    for _ in range(attempts):
        if registry_has(name, version):
            return True
        time.sleep(delay)
    return False


def patches(stage: Path, data: dict, current: str) -> list[str]:
    flags: list[str] = []
    for crate in data["crates"]:
        if crate["published"] == current:
            continue
        directory = (stage / crate["dir"]).resolve()
        flags += ["--config", f"patch.crates-io.{crate['published']}.path={json.dumps(str(directory))}"]
    return flags


def publish_one(
    stage: Path,
    crate: dict,
    data: dict,
    dry_run: bool,
    extra_no_verify: bool,
    no_verify: set[str],
) -> int:
    manifest = stage / crate["dir"] / "Cargo.toml"
    command = [
        "cargo", "publish",
        "--manifest-path", str(manifest),
        # Staging rewrites the tree in place, so it is dirty by construction.
        "--allow-dirty",
    ]
    if dry_run:
        command += ["--dry-run", *patches(stage, data, crate["published"])]
    if extra_no_verify or crate["published"] in no_verify:
        command.append("--no-verify")
    print(f"\n==> {crate['published']} {crate['version']}" + (" (dry run)" if dry_run else ""))
    return subprocess.run(command, cwd=stage).returncode


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list", metavar="KIND", nargs="?", const="publishable",
                        choices=["publishable", "native", "wasm", "all"],
                        help="print package names and exit")
    parser.add_argument("--dirs", action="store_true", help="print the crate directories and exit")
    parser.add_argument("--only", help="publish just this crate (comma-separated)")
    parser.add_argument("--no-verify", action="store_true")
    parser.add_argument("--allow-partial", action="store_true",
                        help="publish even though some crates are withheld")
    parser.add_argument("--force", action="store_true",
                        help="attempt versions that already exist instead of skipping them "
                             "(crates.io rejects them; only useful to surface a bump that did not happen)")
    args = parser.parse_args(argv)

    stage = Path(args.stage).expanduser().resolve()
    data = load_report(stage)
    order = data["order"]
    by_name = {crate["published"]: crate for crate in data["crates"]}
    withheld = {entry["published"]: entry["reason"] for entry in data.get("blocked", [])}
    no_verify = set(data.get("no_verify", []))

    if args.list:
        if args.list == "native":
            print(" ".join(data.get("native", [])))
        elif args.list == "wasm":
            print(" ".join(data.get("wasm_only", [])))
        elif args.list == "all":
            print(" ".join(crate["published"] for crate in data["crates"]))
        else:
            print(" ".join(order))
        return 0

    if args.dirs:
        for name in order:
            print(by_name[name]["dir"])
        return 0

    if withheld:
        for name, reason in sorted(withheld.items()):
            print(f"withheld: {name}: {reason}", file=sys.stderr)
        if not args.dry_run and not args.allow_partial:
            print(
                f"\nrefusing to publish: {len(withheld)} crates in this release cannot be "
                "published, and a release is all of its crates or none of them. "
                "Resolve the dependency above, or pass --allow-partial deliberately.",
                file=sys.stderr,
            )
            return 1

    selected = order
    if args.only:
        wanted = [name.strip() for name in args.only.split(",") if name.strip()]
        unknown = [name for name in wanted if name not in by_name]
        if unknown:
            raise SystemExit(f"not in this release: {', '.join(unknown)}")
        selected = [name for name in order if name in set(wanted)]

    if not args.dry_run and "CARGO_REGISTRY_TOKEN" not in os.environ:
        print("CARGO_REGISTRY_TOKEN is not set", file=sys.stderr)
        return 2

    if no_verify:
        print(
            "packaging without verification (their dev-dependencies cannot resolve "
            f"until other crates in this release are on the registry): "
            f"{', '.join(sorted(no_verify))}",
            file=sys.stderr,
        )

    skipped = []
    for name in selected:
        crate = by_name[name]
        if not args.dry_run and not args.force and registry_has(name, crate["version"]):
            print(f"--> {name} {crate['version']} is already published; skipping")
            skipped.append(name)
            continue
        code = publish_one(stage, crate, data, args.dry_run, args.no_verify, no_verify)
        if code != 0:
            print(f"\nFAILED at {name}; the crates before it are published and this run can be "
                  f"resumed with `--only {name}` once the cause is fixed", file=sys.stderr)
            return code
        if not args.dry_run:
            if not wait_for_visibility(name, crate["version"]):
                print(f"{name} {crate['version']} did not become visible through the crates.io API",
                      file=sys.stderr)
                return 1

    published = len(selected) - len(skipped)
    print(f"\n{'would publish' if args.dry_run else 'published'} {published} crates"
          f"{f', skipped {len(skipped)} already present' if skipped else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
