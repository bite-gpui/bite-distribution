#!/usr/bin/env python3
"""Lint the CI wiring: the workflow files, and the actions they are built from.

Two passes, because no single tool sees all of it.

`actionlint` reads `.github/workflows/` and, for a local action that a workflow
`uses:`, reads that action's metadata and checks the `with:` block against its
inputs. What it does not do is read the `steps:` inside a composite action — its
documentation says so: *"`steps` in Composite action's metadata is not checked at
this point"*. So it cannot see the four actions `verify` is built from, nor any
call site inside an action, and it never checks `steps.<id>.outputs`.

The second pass is that part: every `uses: ./.github/actions/<name>` is resolved,
every `with:` key is checked against the called action's `inputs:`, and every
`steps.<id>.outputs.<name>` is checked against the outputs the step's action
declares. A typo there is silent at run time — an output that does not exist
evaluates to the empty string, and an unrecognised *optional* input is dropped —
which is why it is worth a check rather than trusting review.

The actionlint binary is fetched from its release page and verified against a
checksum pinned here, so a run uses the tool this file names and no other.

Usage:
    workflows.py
    workflows.py --actionlint /path/to/actionlint   # use a binary you have
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import re
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ACTIONS = ROOT / ".github" / "actions"
CACHE = ROOT / ".cache" / "actionlint"

VERSION = "1.7.12"
# Copied from `actionlint_<version>_checksums.txt` on the release page, so a
# substituted or truncated archive fails here rather than running.
CHECKSUMS = {
    ("linux", "amd64"): "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8",
    ("linux", "arm64"): "325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6",
    ("darwin", "amd64"): "5b44c3bc2255115c9b69e30efc0fecdf498fdb63c5d58e17084fd5f16324c644",
    ("darwin", "arm64"): "aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f",
}
OSES = {"Linux": "linux", "Darwin": "darwin"}
ARCHES = {"x86_64": "amd64", "AMD64": "amd64", "arm64": "arm64", "aarch64": "arm64"}

STEP_OUTPUT = re.compile(r"steps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)")


def fetch_actionlint() -> Path:
    """The cached binary, downloaded and verified if it is not already there."""
    system = OSES.get(platform.system())
    machine = ARCHES.get(platform.machine())
    if system is None or machine is None:
        raise SystemExit(
            f"no actionlint release for {platform.system()}/{platform.machine()}; "
            "pass --actionlint with a binary you built"
        )

    binary = CACHE / f"actionlint-{VERSION}"
    if binary.exists():
        return binary

    asset = f"actionlint_{VERSION}_{system}_{machine}.tar.gz"
    url = f"https://github.com/rhysd/actionlint/releases/download/v{VERSION}/{asset}"
    print(f"fetching {url}")
    CACHE.mkdir(parents=True, exist_ok=True)
    archive = CACHE / asset
    with urllib.request.urlopen(url) as response:  # noqa: S310 - a pinned https URL
        archive.write_bytes(response.read())

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != CHECKSUMS[(system, machine)]:
        archive.unlink()
        raise SystemExit(f"{asset}: sha256 {digest} is not the recorded checksum")

    # Read the one member rather than extracting the archive: it also carries a
    # man page and its docs, and none of that belongs in the cache.
    with tarfile.open(archive) as tar:
        source = tar.extractfile("actionlint")
        if source is None:
            raise SystemExit(f"{asset}: no `actionlint` member")
        binary.write_bytes(source.read())
    binary.chmod(0o755)
    archive.unlink()
    return binary


def actionlint_problems(binary: Path) -> list[str]:
    """actionlint's findings, printed as it reports them."""
    # No paths: actionlint finds `.github/workflows` on its own, and resolves
    # `uses: ./.github/actions/...` relative to the repository root it runs in.
    result = subprocess.run([str(binary)], cwd=ROOT, text=True, capture_output=True)
    output = (result.stdout + result.stderr).strip()
    if output:
        print(output)
    if result.returncode == 0:
        return []
    return [f"actionlint exited {result.returncode}"]


def declarations(metadata: dict) -> tuple[set[str], set[str], set[str]]:
    """An action's inputs, the subset of them that is required, and its outputs."""
    inputs = metadata.get("inputs") or {}
    required = {name for name, spec in inputs.items() if (spec or {}).get("required")}
    return set(inputs), required, set((metadata.get("outputs") or {}))


def strings(node: object):
    """Every string in a parsed YAML fragment, so a step can be searched."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from strings(value)


def wiring_problems() -> list[str]:
    """The part actionlint cannot see: call sites inside composite actions."""
    try:
        import yaml
    except ImportError:
        raise SystemExit(
            "PyYAML is needed for the wiring check: python3 -m pip install 'pyyaml==6.*'"
        )

    problems: list[str] = []
    local: dict[str, dict] = {}
    for action in sorted(ACTIONS.glob("*/action.yml")):
        metadata = yaml.safe_load(action.read_text())
        local["./" + str(action.parent.relative_to(ROOT))] = metadata
        if (metadata.get("runs") or {}).get("using") != "composite":
            problems.append(f"{action}: runs.using is not composite")

    files = sorted(ACTIONS.glob("*/action.yml")) + sorted(
        (ROOT / ".github" / "workflows").glob("*.yml")
    )
    for path in files:
        document = yaml.safe_load(path.read_text())
        steps = list((document.get("runs") or {}).get("steps") or [])
        for name, job in (document.get("jobs") or {}).items():
            steps += list((job or {}).get("steps") or [])
            # A job that calls another workflow here, rather than an action.
            call = (job or {}).get("uses")
            if isinstance(call, str) and call.startswith("./"):
                if not (ROOT / call).is_file():
                    problems.append(f"{path}: job `{name}` uses {call}, which is not a file")

        by_id = {s["id"]: s for s in steps if isinstance(s, dict) and s.get("id")}
        for step in steps:
            if not isinstance(step, dict):
                continue
            call = step.get("uses")
            if isinstance(call, str) and call.startswith("./"):
                if call not in local:
                    problems.append(f"{path}: {call} is not an action in this repository")
                else:
                    declared, required, _ = declarations(local[call])
                    supplied = step.get("with") or {}
                    for key in supplied:
                        if key not in declared:
                            problems.append(f"{path}: {call} has no input `{key}`")
                    for key in sorted(required - set(supplied)):
                        problems.append(f"{path}: {call} requires input `{key}`")
            # An output reference, resolved against the step it names, because
            # only that step's action knows which outputs exist.
            for text in strings(step):
                for step_id, output in STEP_OUTPUT.findall(text):
                    target = by_id.get(step_id)
                    if target is None:
                        problems.append(
                            f"{path}: `steps.{step_id}.outputs.{output}` names no step"
                        )
                    elif isinstance(target.get("uses"), str) and target["uses"] in local:
                        _, _, outputs = declarations(local[target["uses"]])
                        if output not in outputs:
                            problems.append(
                                f"{path}: {target['uses']} declares no output `{output}`"
                            )
    return problems


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--actionlint", help="a binary to use instead of fetching one")
    args = parser.parse_args(argv)

    binary = Path(args.actionlint) if args.actionlint else fetch_actionlint()
    problems = actionlint_problems(binary) + wiring_problems()

    for problem in problems:
        print(f"FAIL {problem}")
    print(f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
