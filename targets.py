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
    targets.py --for-tag bite_1.21.0
    targets.py --for-branch bite_v1.21.0
    targets.py --env bite_ce_main
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
import tomllib
from pathlib import Path

DIST = Path(__file__).resolve().parent
TARGETS_FILE = DIST / "targets.toml"

REQUIRED = ("name", "url", "repo", "branch", "lineage", "roots")
LINEAGES = ("zed", "ce")
# `release-tag`: the tip carries a `bite_<version>` tag and publishes that
# version. `calver`: a rolling tip, dated at staging time.
SCHEMES = ("release-tag", "calver")

# One target per lineage: what a pull request verifies. The full twelve are a
# cold-build of gpui on three platforms' worth of code per target, which is not
# something to run on every push.
DEFAULT = ("bite_v1.20.2", "bite_ce_main")


def load() -> list[dict]:
    """The target table, with the repository default filled in.

    Named once at the top level because every target shares it; materialising it
    per target means every reader downstream still sees a concrete `url`.
    """
    with TARGETS_FILE.open("rb") as handle:
        table = tomllib.load(handle)
    default = table.get("repository")
    for target in table["target"]:
        if target.get("url"):
            continue
        if not default:
            raise SystemExit(f"{target.get('name')}: no url, and no top-level repository")
        target["url"] = default
    return table["target"]


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
        scheme = target.get("version_from", "release-tag")
        if scheme not in SCHEMES:
            problems.append(f"{where}: version_from must be one of {SCHEMES}")
        if scheme == "release-tag" and not target.get("upstream"):
            problems.append(f"{where}: version_from = \"release-tag\" needs a version")
        if scheme == "release-tag" and target.get("upstream"):
            try:
                published_version(target["upstream"], target.get("amendment", 0))
            except SchemeError as error:
                problems.append(f"{where}: {error}")
            disagreement = branch_agrees(target)
            if disagreement:
                problems.append(f"{where}: {disagreement}")
        if not isinstance(target.get("amendment", 0), int):
            problems.append(f"{where}: amendment must be an integer")
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


# Each upstream patch release gets this many slots: `1.20.2` publishes as
# `1.20.200`, amendments take 201..299, and `1.20.3` starts at 300. The gap is
# the point — an amendment can never collide with the next release, versions
# still sort in upstream order, and a retarget will not need anything like a
# hundred amendments.
SLOTS = 100


class SchemeError(ValueError):
    pass


def published_version(upstream: str, amendment: int) -> str:
    """Map an upstream release onto the version this project publishes.

    A preview keeps its prerelease tag — `1.21.0-pre` is a preview of what
    `1.21.0` will be, and `1.21.0` is where that line will start once the
    release exists — and an amendment appends a numeric identifier, which
    semver orders above the bare prerelease.
    """
    core, _, pre = upstream.partition("-")
    parts = core.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise SchemeError(f"{upstream} is not a three-part release version")
    major, minor, patch = (int(part) for part in parts)
    if not 0 <= amendment < SLOTS:
        raise SchemeError(f"amendment {amendment} is out of range (0..{SLOTS - 1})")
    if pre:
        suffix = f".{amendment}" if amendment else ""
        return f"{core}-{pre}{suffix}"
    if patch >= SLOTS:
        raise SchemeError(
            f"upstream patch {patch} does not fit the scheme; each upstream patch "
            f"gets {SLOTS} slots"
        )
    return f"{major}.{minor}.{patch * SLOTS + amendment}"


def release_tag(upstream: str, amendment: int) -> str:
    """The tag a release target must carry at HEAD: `bite_` + published version.

    The tag is the contract. Staging refuses to run for a release target whose
    tip is not tagged, so content cannot change without the version changing,
    and the version does not have to be inferred from a checkout's history —
    which is what lets CI use a depth-1 clone.
    """
    return "bite_" + published_version(upstream, amendment)


def target_for_tag(targets: list[dict], tag: str) -> dict:
    """The release target a pushed tag belongs to.

    The tag encodes the published version, and the table already computes the
    tag each release target must carry, so the release workflow resolves a tag
    push without a checkout and without parsing the tag.

    Rolling (`calver`) targets are excluded on purpose. Their version is a date,
    so both lineages could push a tag of the same name and the tag would not say
    which branch it meant; they are released by dispatch, which names the target.
    """
    frozen = [t for t in targets if t.get("version_from", "release-tag") == "release-tag"]
    matches = [t for t in frozen if release_tag(t["upstream"], t.get("amendment", 0)) == tag]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(sorted(t["name"] for t in matches))
        raise SystemExit(f"{tag} matches more than one target ({names}); a tag must name one")

    known = sorted(release_tag(t["upstream"], t.get("amendment", 0)) for t in frozen)
    rolling = sorted(t["name"] for t in targets if t not in frozen)
    raise SystemExit(
        f"no release target carries {tag}. A tag push must be one of:\n  "
        + "\n  ".join(known)
        + f"\nThe rolling targets ({', '.join(rolling)}) publish on the CalVer series "
        "and are released by dispatch, which names the target."
    )


def tags_at_head(repo: Path) -> list[str]:
    """Release tags pointing at HEAD, by OID comparison, not ancestry."""
    result = subprocess.run(
        ["git", "--no-pager", "tag", "--list", "--points-at", "HEAD", "bite_*"],
        cwd=repo, capture_output=True, text=True,
    )
    return result.stdout.split()


def version_for(target: dict, source_repo: Path | None) -> str:
    """Resolve the version a target publishes at.

    `release-tag` targets publish the version their tag names: `bite_1.20.200`
    is `1.20.200`. `calver` targets get today's date, because a rolling branch
    has no release to name itself after and a date-shaped tag would have to be
    pushed daily.

    `source_repo` is optional so a caller without a checkout — the release
    workflow's first job, which only needs to know what it is about to do —
    gets the same number staging will produce.
    """
    amendment = target.get("amendment", 0)
    if target.get("version_from", "release-tag") == "calver":
        today = datetime.date.today().strftime("%Y%m%d")
        return f"0.{today}.{amendment}"

    try:
        version = published_version(target["upstream"], amendment)
    except SchemeError as error:
        raise SystemExit(f"{target['name']}: {error}") from error

    if source_repo is not None:
        expected = f"bite_{version}"
        found = tags_at_head(source_repo)
        if expected not in found:
            others = f"; found {', '.join(found)}" if found else ""
            raise SystemExit(
                f"{target['name']}: {source_repo} is not tagged {expected}{others}. "
                "A release target must be tagged at its tip, since the tag is what "
                f"says which version it publishes: git tag -a {expected} -m {expected}"
            )
    return version


def branch_version(branch: str) -> str | None:
    """The upstream release a branch name declares, if it declares one.

    `bite_v1.20.2` declares `1.20.2`; `bite_v1.14.x` declares `1.14.x`, whose
    patch is only known once published. Both are checkable without a checkout,
    which is why the table is validated against them.
    """
    if not branch.startswith("bite_v"):
        return None
    return branch[len("bite_v"):]


def branch_agrees(target: dict) -> str | None:
    """Why a target's declared version and its branch name disagree, if they do."""
    declared = target.get("upstream")
    named = branch_version(target.get("branch", ""))
    if not declared or not named:
        return None
    if named == declared:
        return None
    if named.endswith(".x") and named[:-2] == declared.rsplit(".", 1)[0]:
        return None
    return f"branch {target['branch']} declares {named}, table says {declared}"


def target_for_name(targets: list[dict], name: str) -> dict:
    for target in targets:
        if target.get("name") == name:
            return target
    raise SystemExit(f"unknown target: {name}")


def target_for_branch(targets: list[dict], branch: str) -> dict:
    """The target a branch belongs to.

    Resolved by `branch` rather than by `name`, though the two coincide for every
    target in the table today: a caller that has a branch should not have to know
    that it doubles as a name, and a target that ever names itself differently
    must not silently resolve to the wrong one.
    """
    for target in targets:
        if target.get("branch") == branch:
            return target
    known = ", ".join(sorted(t.get("branch", "?") for t in targets))
    raise SystemExit(f"no target has branch {branch}. Known branches: {known}")


def emit_env(target: dict) -> list[str]:
    """The `key=value` lines a workflow can append to `$GITHUB_OUTPUT`."""
    return [
        f"target={target['name']}",
        f"repository={repository_slug(target)}",
        f"branch={target['branch']}",
        f"lineage={target['lineage']}",
        f"upstream={target.get('upstream', '')}",
        f"amendment={target.get('amendment', 0)}",
        f"version={version_for(target, None)}",
    ]


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
    parser.add_argument("--platforms", default="ubuntu-latest",
                        help="comma-separated runner labels; one matrix leg per target "
                             "per label, so `--platforms ubuntu-latest,macos-14` adds a "
                             "macOS build of every selected target")
    parser.add_argument("--refs", action="store_true",
                        help="emit repository= and <lineage>=<branch> lines for $GITHUB_OUTPUT")
    parser.add_argument("--tags", action="store_true",
                        help="print the release tag each selected target must carry")
    parser.add_argument("--for-tag", metavar="TAG",
                        help="print the release target a pushed bite_* tag belongs to")
    parser.add_argument("--for-branch", metavar="BRANCH",
                        help="emit key=value lines for the target a branch belongs to")
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

    if args.for_tag:
        print(target_for_tag(targets, args.for_tag)["name"])
        return 0

    if args.for_branch:
        print("\n".join(emit_env(target_for_branch(targets, args.for_branch))))
        return 0

    if args.env:
        print("\n".join(emit_env(target_for_name(targets, args.env))))
        return 0

    if args.show:
        for target in targets:
            if target["name"] == args.show:
                print(json.dumps(target, indent=2, sort_keys=True))
                return 0
        raise SystemExit(f"unknown target: {args.show}")

    chosen = select(targets, args.select)
    if not chosen:
        raise SystemExit(f"selection {args.select!r} matched no targets")

    if args.tags:
        # What a maintainer pushes to make a branch releasable. Printed rather
        # than applied, because tagging is a release decision.
        for target in chosen:
            if target.get("version_from", "release-tag") == "calver":
                print(f"# {target['name']}: rolling, no tag")
                continue
            tag = release_tag(target["upstream"], target.get("amendment", 0))
            print(f"{target['name']:<18} {target['branch']:<18} {tag}")
        return 0

    if args.matrix:
        platforms = [platform.strip() for platform in args.platforms.split(",") if platform.strip()]
        if not platforms:
            raise SystemExit("--platforms selected no runners")
        print(json.dumps({
            "include": [
                {
                    "target": t["name"],
                    "repository": repository_slug(t),
                    "branch": t["branch"],
                    "os": platform,
                }
                for t in chosen
                for platform in platforms
            ]
        }))
    elif args.names:
        print(" ".join(t["name"] for t in chosen))
    else:
        for target in chosen:
            print(
                f"{target['name']:<18} {target['lineage']:<4} "
                f"{target['branch']:<18} {version_for(target, None)}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
