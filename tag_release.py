#!/usr/bin/env python3
"""Resolve a release branch to the tag it must carry, and push it.

A release is a tag and the tag is the version (DESIGN §6): staging refuses a
release target whose tip is untagged, so the tag is the only thing that can name
what is being released. This is the step that creates it. It answers the three
questions a maintainer actually has — which tag, does it already exist, is this
the commit that was tagged — and pushes the tag when there is one to push.

    tag_release.py --branch bite_v1.21.0 --repo src              # report only
    tag_release.py --branch bite_v1.21.0 --repo src --push       # tag and publish

**A tag that is already at the tip is not a refusal.** That is the ordinary state
of a branch whose release did not finish: a crates.io *version* is immutable, but
the upload is not, so a run that died on a rate limit or a tooling bug is finished
by publishing the same version again, and `publish.py` skips what already landed.
Tagging has nothing left to do, so it says so and leaves the publishing to the
workflow that calls this.

**A tag naming a different commit is a refusal, always.** The content under a
published version is not allowed to change, so that is not something to override
with a flag — the answer is to bump the target's `amendment` (DESIGN §6), which
mints a new version for the new content. Moving the tag instead would be a false
statement about what was released.

The remote is asked, not the local clone: a tag that exists only locally has not
been released, and one that exists remotely at another commit is the case above.

Machine-readable `key=value` lines go to stdout, for `$GITHUB_OUTPUT`; everything
a human reads goes to stderr. Exit status follows the same split: a run that was
asked to push exits non-zero when it refuses to act, and a report-only run always
exits zero, letting the `state` field carry the verdict — checking is not a
failure, acting is.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import targets

# The tag keys the report emits, so a caller can rely on them.
REPORT_KEYS = ("target", "version", "tag", "state", "tip")


def note(message: str) -> None:
    print(message, file=sys.stderr)


def report(**values: str) -> None:
    for key in REPORT_KEYS:
        print(f"{key}={values.get(key, '')}")


def run(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"{' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def remote_tags(remote: str, repo: Path) -> dict[str, str]:
    """{ref: sha} for the remote's tags.

    Annotated tags appear twice: once as the tag object and once peeled with a
    `^{}` suffix, which is the commit. Both are kept, and callers read the peeled
    one, because "is this commit tagged" is a question about the commit.
    """
    refs: dict[str, str] = {}
    for line in run("git", "ls-remote", "--tags", remote, cwd=repo).splitlines():
        sha, _, ref = line.partition("\t")
        if ref:
            refs[ref.strip()] = sha.strip()
    return refs


def commit_for(refs: dict[str, str], tag: str) -> str | None:
    peeled = refs.get(f"refs/tags/{tag}^{{}}")
    if peeled:
        return peeled
    # A lightweight tag has no peeled entry; its ref is already the commit.
    return refs.get(f"refs/tags/{tag}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--branch", required=True, help="the bite_* branch to release")
    parser.add_argument("--repo", default=".", help="the checkout to tag (default: cwd)")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--push", action="store_true",
                        help="create the tag if it is missing and push it")
    args = parser.parse_args(argv)

    table = targets.load()
    problems = targets.validate(table)
    if problems:
        for problem in problems:
            note(f"FAIL {problem}")
        return 1

    target = targets.target_for_branch(table, args.branch)
    repo = Path(args.repo)
    tip = run("git", "rev-parse", "HEAD", cwd=repo).strip()

    if target.get("version_from", "release-tag") == "calver":
        version = targets.version_for(target, None)
        report(target=target["name"], version=version, state="rolling", tip=tip)
        note(
            f"{args.branch} is a rolling target. Its version is the date at staging "
            f"time — today that is {version} — so there is no tag to push and nothing "
            "to check. Dispatch the release workflow for it instead."
        )
        return 0

    version = targets.published_version(target["upstream"], target.get("amendment", 0))
    tag = targets.release_tag(target["upstream"], target.get("amendment", 0))
    tagged_at = commit_for(remote_tags(args.remote, repo), tag)

    if tagged_at is not None and tagged_at != tip:
        report(target=target["name"], version=version, tag=tag, state="conflict", tip=tip)
        amendment = target.get("amendment", 0)
        note(
            f"REFUSED: {tag} already names {tagged_at[:10]}, and {args.branch} is at "
            f"{tip[:10]}. A tag must not move, so the content under a published "
            f"version changed. Bump `amendment` for {target['name']} from {amendment} "
            f"to {amendment + 1} in targets.toml (DESIGN §6) and re-run."
        )
        # Only acting refuses loudly. A report is an answer, not a failure, so a
        # run that was not asked to push anything always succeeds and lets the
        # state field carry the verdict.
        return 2 if args.push else 0

    already = tagged_at == tip
    report(
        target=target["name"],
        version=version,
        tag=tag,
        state="already-tagged" if already else "untagged",
        tip=tip,
    )

    if already:
        # Not a refusal: a tip that carries its own tag is the ordinary state of a
        # release that did not finish, and finishing it means publishing, which is
        # the caller's next step. A version already on crates.io is skipped there,
        # so this is safe to do repeatedly.
        note(
            f"{args.branch} is already tagged {tag} at {tip[:10]}, so there is nothing "
            f"to tag. Publishing {version} again resumes rather than duplicates: "
            "crates.io skips versions that already exist."
        )
        return 0

    if not args.push:
        note(f"would push {tag} at {tip[:10]}")
        return 0

    local = run("git", "tag", "--list", tag, cwd=repo).strip()
    if local and tag not in targets.tags_at_head(repo):
        raise SystemExit(
            f"a local tag {tag} exists and does not point at HEAD. Delete it "
            "deliberately, or bump the amendment — nothing here will move it, because a "
            "moved tag is a false statement about what was released."
        )
    if not local:
        run("git", "tag", "-a", tag, "-m", tag, cwd=repo)
    run("git", "push", args.remote, f"refs/tags/{tag}", cwd=repo)
    note(f"pushed {tag} at {tip[:10]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
