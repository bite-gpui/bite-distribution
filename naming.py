#!/usr/bin/env python3
"""Map source package names to the crate names this project publishes.

The published name is the only thing a consumer writes in Cargo.toml; the
`[lib] name` is preserved so every `use gpui::…` / `use collections::…` in
downstream code keeps compiling. Two forks share one crates.io namespace, so
the lineage has to be visible in the name: the zed-lineage retarget owns the
short `bite-gp-*` names, and the community-edition transplant owns
`bite-gp-ce-*`, mirroring the fork's own `gpui_ce_*` spelling.

The map is deliberately derived by a small rule rather than hand-listed, but it
is *verified* against the measured closure of every target: a crate that enters
a closure without a name is an error, and two source packages that would land
on the same published name is an error. `--verify` enforces both.

Usage:
    naming.py --verify                      # check every declared target
    naming.py --verify --lineage-repo zed=.src/zed --lineage-repo ce=.src/ce
    naming.py --table                       # print the whole union
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inventory  # noqa: E402

DIST = Path(__file__).resolve().parent
TARGETS_FILE = DIST / "targets.toml"

# The facade of each lineage. Both keep the `gpui` lib name, so the package
# name is the only thing distinguishing them.
FACADES = {"zed": "gpui", "ce": "gpui-ce"}

# `gpui_util` would collide with zed's own `util` under the stripping rule.
# zed's `util` is the crate the short name belongs to; `gpui_util` keeps its
# full spelling. This is the only exception in the union, and `--verify`
# re-derives that claim on every run.
EXCEPTIONS = {("zed", "gpui_util"): "bite-gp-gpui-util"}


def published_name(package: str, lineage: str) -> str:
    if package == FACADES[lineage]:
        return "bite-gpui" if lineage == "zed" else "bite-gpui-ce"
    exception = EXCEPTIONS.get((lineage, package))
    if exception:
        return exception
    if lineage == "ce":
        # `gpui_ce_*` is ce's own spelling of a fork-marked crate and gets the
        # short `bite-gp-ce-*` name; the reference's `gpui_*` crates keep their
        # `gpui` token so the two can never land on the same name.
        body = package[len("gpui_ce_"):] if package.startswith("gpui_ce_") else None
        if body is not None:
            return "bite-gp-ce-" + body.replace("_", "-")
        return "bite-gp-ce-gpui-" + package[len("gpui_"):].replace("_", "-")
    body = package[len("gpui_"):] if package.startswith("gpui_") else package
    return "bite-gp-" + body.replace("_", "-")


def load_targets() -> list[dict]:
    with TARGETS_FILE.open("rb") as handle:
        return tomllib.load(handle)["target"]


def closure_of(target: dict, repo_override: str | None) -> tuple[set[str], dict]:
    repo = Path(repo_override or target["repo"]).expanduser().resolve()
    if not (repo / "Cargo.toml").exists():
        raise SystemExit(
            f"{target['name']}: no checkout at {repo}. Locally the release-line "
            "closures are branch-invariant, but that is measured per target in CI, "
            "where each target is staged from its own branch."
        )
    collected = inventory.collect(repo)
    reached = inventory.closure(collected["crates"], target["roots"])
    names = set(reached["normal"]) | set(reached["build"])
    return names, collected["crates"]


def verify(repo_overrides: dict[str, str], lineage_repos: dict[str, str]) -> int:
    targets = load_targets()
    if not targets:
        print("no targets declared", file=sys.stderr)
        return 2

    failures = 0
    claimed: dict[str, list[tuple[str, str, str]]] = {}
    union: dict[tuple[str, str], set[str]] = {}

    for target in targets:
        lineage = target["lineage"]
        repo = repo_overrides.get(target["name"]) or lineage_repos.get(lineage)
        names, crates = closure_of(target, repo)
        for package in sorted(names):
            published = published_name(package, lineage)
            claimed.setdefault(published, []).append((target["name"], lineage, package))
            union.setdefault((lineage, package), set()).add(published)
            crate = crates[package]
            if crate["lib_name"] is None:
                print(f"FAIL {target['name']}: {package} has no lib name")
                failures += 1

    for published, sources in sorted(claimed.items()):
        # One package may legitimately reach a name from several targets in the
        # same lineage; two packages, or two lineages, may not.
        packages = {package for _, _, package in sources}
        lineages = {lineage for _, lineage, _ in sources}
        if len(packages) > 1 or len(lineages) > 1:
            described = ", ".join(f"{p} ({t}/{l})" for t, l, p in sources)
            print(f"FAIL collision on {published}: {described}")
            failures += 1

    for (lineage, package), published in sorted(union.items()):
        if len(published) != 1:
            print(f"FAIL {lineage}: {package} maps to {sorted(published)}")
            failures += 1

    print(f"{len(targets)} targets, {len(union)} distinct (lineage, package) pairs, "
          f"{len(claimed)} published names, {failures} failures")
    return 1 if failures else 0


def table() -> int:
    targets = load_targets()
    rows: dict[tuple[str, str], set[str]] = {}
    for target in targets:
        names, crates = closure_of(target, None)
        for package in names:
            rows.setdefault((target["lineage"], package), set()).add(published_name(package, target["lineage"]))
    for (lineage, package), published in sorted(rows.items()):
        print(f"{lineage:<4} {package:<26} -> {sorted(published)[0]}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--table", action="store_true")
    parser.add_argument("--repo", action="append", default=[],
                        help="override one target's checkout as NAME=PATH")
    parser.add_argument("--lineage-repo", action="append", default=[],
                        help="use one checkout for every target of a lineage, as zed=PATH")
    args = parser.parse_args(argv)

    overrides = dict(entry.split("=", 1) for entry in args.repo)
    lineage_repos = dict(entry.split("=", 1) for entry in args.lineage_repo)
    if args.table:
        return table()
    return verify(overrides, lineage_repos)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
