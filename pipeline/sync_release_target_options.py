#!/usr/bin/env python3
"""Rewrite the dispatch `options:` lists from the target table and a branch set.

GitHub fills a `choice` input's dropdown from the workflow file and from nothing
that can read `targets.toml`, so every workflow that dispatches a target carries
a second copy — the branches of the source repository for `tag.yml` and
`release-branch.yml`, the target names for `release.yml` and `verify.yml`.
`pipeline/targets.py --validate` fails when a copy drifts, but it cannot fix it;
this can.

    sync_release_target_options.py                  # branches from the table
    sync_release_target_options.py --branches FILE  # branches from a branch list
    sync_release_target_options.py --check

A branch list — one name per line, from `git ls-remote --heads` or the branches
API — is how the scheduled workflow drives it: the dropdowns then list the
release branches that actually exist. `targets.is_release_branch` filters the
list to the shapes the project releases from, so a working branch such as
`bite_v1.21.0-some-feature` is never offered; a branch that is release-shaped but
has no target is left to `--validate` to catch, since a dropdown entry nobody has
given a target to cannot be dispatched. Without `--branches` the table's own
branches are used, which is what a local run checks.

The rewrite is surgical rather than a YAML round trip. These workflows are built
around their comments, and a parser that re-emits the document drops them; only
the `options:` block of each named input is replaced and every other byte is left
alone. Order is not part of the contract — `--validate` compares the lists as
sets — so the order a file already has is kept, a stale option is dropped and a
missing one appended, which makes a run over an in-sync tree a no-op.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import targets


def option_lines(lines: list[str], input_name: str) -> tuple[int, range] | None:
    """The `options:` line of one workflow input, and the lines beneath it.

    The first `inputs:` block only, mirroring `targets.selector_options`: a
    workflow has a `workflow_dispatch` block and possibly a `workflow_call` one,
    and the dispatch selector is the former.
    """
    starts = [index for index, line in enumerate(lines) if line.strip() == "inputs:"]
    if not starts:
        return None
    for index in targets.indented(lines, starts[0]):
        if lines[index].strip() != f"{input_name}:":
            continue
        for inner in targets.indented(lines, index):
            if lines[inner].strip() == "options:":
                return inner, targets.indented(lines, inner)
        return None
    return None


def read_options(lines: list[str], block: range) -> list[tuple[str, str]]:
    """The block's options, each with any trailing comment kept verbatim."""
    options = []
    for index in block:
        text = lines[index].strip()
        if not text.startswith("- "):
            continue
        value, _, trailing = text[2:].partition(" ")
        options.append((value, trailing.strip()))
    return options


def merged(current: list[tuple[str, str]], wanted: list[str]) -> list[tuple[str, str]]:
    """The current order, minus what left the set, plus what joined it."""
    keep = set(wanted)
    present = {value for value, _ in current}
    ordered = [(value, comment) for value, comment in current if value in keep]
    ordered += [(value, "") for value in wanted if value not in present]
    return ordered


def rewrite(text: str, input_name: str, wanted: list[str]) -> str:
    lines = text.splitlines()
    located = option_lines(lines, input_name)
    if located is None:
        raise SystemExit(f"no `{input_name}:` input with an `options:` list to rewrite")
    index, block = located
    indent = " " * (len(lines[index]) - len(lines[index].lstrip()) + 2)
    replacement = [
        f"{indent}- {value}" + (f"  # {comment}" if comment else "")
        for value, comment in merged(read_options(lines, block), wanted)
    ]
    lines[index + 1 : block.stop] = replacement
    result = "\n".join(lines)
    return result + "\n" if text.endswith("\n") else result


def branch_options(path: Path | None, table: list[dict]) -> list[str]:
    """The branch values to offer, in the order the source gave them.

    `--branches` is a file of branch names; without it the table's own branches
    are used. Either way the list is filtered to the release-branch shapes and
    de-duplicated, and whatever the filter dropped is named so a run's log says
    why a branch it saw is not in a dropdown.
    """
    if path is None:
        return [target["branch"] for target in table]

    seen: list[str] = []
    dropped: list[str] = []
    for line in path.read_text().splitlines():
        branch = line.strip()
        if not branch or branch.startswith("#"):
            continue
        if not branch.startswith("bite_"):
            continue
        if not targets.is_release_branch(branch):
            if branch not in dropped:
                dropped.append(branch)
            continue
        if branch not in seen:
            seen.append(branch)
    if dropped:
        print(
            f"ignoring {len(dropped)} branch(es) that are not release-shaped: "
            + ", ".join(dropped),
            file=sys.stderr,
        )
    return seen


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--branches", metavar="FILE",
                        help="a branch list, one name per line, to take the branch "
                             "options from instead of the table")
    parser.add_argument("--check", action="store_true",
                        help="report drift and exit non-zero instead of rewriting")
    args = parser.parse_args(argv)

    table = targets.load()
    problems = targets.validate(table)
    if problems:
        for problem in problems:
            print(f"FAIL {problem}", file=sys.stderr)
        return 1

    branches = branch_options(Path(args.branches) if args.branches else None, table)
    names = [target["name"] for target in table]

    drifted = []
    for filename, (input_name, field) in sorted(targets.SELECTORS.items()):
        path = targets.WORKFLOWS / filename
        if not path.is_file():
            raise SystemExit(f"{filename}: not found, so its options cannot be synced")
        wanted = branches if field == "branch" else names
        text = path.read_text()
        updated = rewrite(text, input_name, wanted)
        changed = updated != text
        if changed:
            drifted.append(filename)
            if not args.check:
                path.write_text(updated)
        print(f"{filename}: {'updated' if changed else 'in step'}")

    if args.check and drifted:
        print(f"drift in {', '.join(drifted)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
