#!/usr/bin/env python3
"""Read, validate, and select from the target table.

Both workflows need the same two things from `targets.toml`: a validated table,
and a GitHub Actions matrix. Keeping that here — rather than in `jq` inside
YAML — means the table has one reader, and a malformed entry fails in a step
whose output says which field is wrong.

Usage:
    targets.py --validate
    targets.py --matrix --select all
    targets.py --refs --select default
    targets.py --env bite_ce_main
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

DIST = Path(__file__).resolve().parent
TARGETS_FILE = DIST / "targets.toml"

REQUIRED = ("name", "url", "repo", "branch", "lineage", "roots")
LINEAGES = ("zed", "ce")
SCHEMES = ("tag", "calver")

# One target per lineage: what a pull request verifies. The full ten are a
# cold-build of gpui on three platforms' worth of code per target, which is not
# something to run on every push.
DEFAULT = ("bite_v1.20.2", "bite_ce_main")


def load() -> list[dict]:
    with TARGETS_FILE.open("rb") as handle:
        return tomllib.load(handle)["target"]


def validate(targets: list[dict]) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for index, target in enumerate(targets):
        where = target.get("name") or f"target #{index + 1}"
        for field in REQUIRED:
            if not target.get(field):
                problems.append(f"{where}: missing {field}")
        if target.get("name") in seen:
            problems.append(f"{where}: duplicate target name")
        seen.add(target.get("name"))
        if target.get("lineage") not in LINEAGES:
            problems.append(f"{where}: lineage must be one of {LINEAGES}")
        scheme = target.get("version_from", "tag")
        if scheme not in SCHEMES:
            problems.append(f"{where}: version_from must be one of {SCHEMES}")
        if scheme == "tag" and not target.get("version"):
            problems.append(f"{where}: version_from = \"tag\" needs a version")
        if not target.get("url", "").startswith("https://"):
            problems.append(f"{where}: url must be https (CI clones it unauthenticated)")
        if not target.get("branch", "").startswith("bite_"):
            problems.append(f"{where}: branch should be a bite_* branch")
    return problems


def select(targets: list[dict], selector: str) -> list[dict]:
    if selector == "all":
        return targets
    if selector in LINEAGES:
        return [t for t in targets if t["lineage"] == selector]
    if selector == "default":
        wanted = set(DEFAULT)
        return [t for t in targets if t["name"] in wanted]
    wanted = {name.strip() for name in selector.split(",") if name.strip()}
    by_name = {t["name"]: t for t in targets}
    unknown = sorted(wanted - set(by_name))
    if unknown:
        raise SystemExit(f"unknown targets: {', '.join(unknown)}")
    return [by_name[name] for name in sorted(wanted)]


def version_for(target: dict, source_repo: Path | None) -> str:
    """Resolve the version a target publishes at.

    `tag` targets publish the upstream release they are built on, so the value
    is re-derived from the checkout rather than trusted from the table; a branch
    that has moved onto a different release must not publish under the old
    number. `calver` targets get today's date.
    """
    if target.get("version_from", "tag") == "calver":
        import datetime

        return f"0.{datetime.date.today().strftime('%Y%m%d')}.0"
    declared = target["version"]
    if source_repo is None:
        return declared
    measured = highest_release_tag(source_repo, target["version"])
    if measured != declared:
        raise SystemExit(
            f"{target['name']}: branch is on {measured}, table says {declared}; "
            "update targets.toml (or the branch) before publishing"
        )
    return declared


def highest_release_tag(repo: Path, series: str) -> str:
    """Highest semver-ordered v<series> tag reachable from HEAD.

    Sorted by semver, not `sort -V`: `sort -V` ranks `v1.14.2-pre` above
    `v1.14.2`, which would publish a preview branch's number as stable.
    """
    import subprocess

    major, minor = series.split("-")[0].split(".")[:2]
    pattern = f"v{major}.{minor}.*"
    out = subprocess.run(
        ["git", "--no-pager", "tag", "--list", "--merged", "HEAD", pattern],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.split()

    def key(tag: str):
        core, _, pre = tag.partition("-")
        parts = [int(part) for part in core.lstrip("v").split(".")]
        while len(parts) < 3:
            parts.append(0)
        return (parts, 0 if pre else 1, pre)

    if not out:
        raise SystemExit(f"no {pattern} tag reachable from HEAD in {repo}")
    return max(out, key=key).lstrip("v")


def repository_slug(target: dict) -> str:
    """`owner/repo`, which is what actions/checkout wants (not a URL)."""
    return target["url"].removeprefix("https://github.com/").removesuffix(".git")


def references(targets: list[dict], selector: str) -> list[str]:
    """The checkout coordinates a naming check needs: one repo, one ref per lineage.

    All targets share a repository, which is what lets the naming check read a
    real closure per lineage with two checkouts instead of ten. If a target ever
    moves elsewhere this fails rather than silently checking the wrong branch.
    """
    chosen = select(targets, selector)
    repositories = {repository_slug(target) for target in chosen}
    if len(repositories) != 1:
        raise SystemExit(
            "selection spans " + str(sorted(repositories))
            + "; the naming check assumes one repository per selection"
        )
    lines = [f"repository={repositories.pop()}"]
    for lineage in LINEAGES:
        for target in chosen:
            if target["lineage"] == lineage:
                lines.append(f"{lineage}={target['branch']}")
                break
    return lines


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--matrix", action="store_true")
    parser.add_argument("--refs", action="store_true",
                        help="emit repository= and <lineage>=<branch> lines for $GITHUB_OUTPUT")
    parser.add_argument("--names", action="store_true")
    parser.add_argument("--env", metavar="NAME", help="emit key=value lines for $GITHUB_OUTPUT")
    parser.add_argument("--show")
    parser.add_argument("--select", default="default")
    args = parser.parse_args(argv)

    targets = load()
    problems = validate(targets)
    if problems:
        for problem in problems:
            print(f"FAIL {problem}", file=sys.stderr)
        return 1
    if args.validate:
        print(f"{len(targets)} targets, no problems")
        return 0

    if args.refs:
        print("\n".join(references(targets, args.select)))
        return 0

    if args.env:
        for target in targets:
            if target["name"] == args.env:
                print(f"target={target['name']}")
                print(f"repository={repository_slug(target)}")
                print(f"branch={target['branch']}")
                print(f"lineage={target['lineage']}")
                return 0
        raise SystemExit(f"unknown target: {args.env}")

    if args.show:
        for target in targets:
            if target["name"] == args.show:
                print(json.dumps(target, indent=2, sort_keys=True))
                return 0
        raise SystemExit(f"unknown target: {args.show}")

    chosen = select(targets, args.select)
    if not chosen:
        raise SystemExit(f"selection {args.select!r} matched no targets")
    if args.matrix:
        print(json.dumps({
            "include": [
                {
                    "target": t["name"],
                    "repository": repository_slug(t),
                    "branch": t["branch"],
                }
                for t in chosen
            ]
        }))
    elif args.names:
        print(" ".join(t["name"] for t in chosen))
    else:
        for target in chosen:
            print(f"{target['name']:<18} {target['lineage']:<4} {target['branch']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
