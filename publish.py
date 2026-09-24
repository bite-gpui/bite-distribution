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

**Rate limits.** crates.io allows a burst of five *new* crate names per account
and then one every ten minutes; new *versions* of existing crates get a burst of
thirty and then one a minute. The first release of a target is therefore
thirty-one new names and cannot be published promptly, and the refusal names the
time the next attempt is allowed. The publisher waits for that time and carries
on, and after the first refusal it spaces the remaining publishes by the
interval the refusal implied, so the rest of the release is paced rather than
refused.

Usage:
    publish.py --stage wt/bite_v1.20.2 --list
    publish.py --stage wt/bite_v1.20.2 --commands
    publish.py --stage wt/bite_v1.20.2 --dry-run
    publish.py --stage wt/bite_v1.20.2              # needs a crates.io token
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
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


def registry_token_present() -> bool:
    """Whether cargo has a crates.io token to publish with.

    The environment variable is only one of the places cargo looks: a machine
    that ran `cargo login` has the token in `credentials.toml` and none in the
    environment, which is the ordinary case and was refused outright here.
    Nothing is read from the token itself, only that one is there.
    """
    if os.environ.get("CARGO_REGISTRY_TOKEN") or os.environ.get("CARGO_REGISTRIES_CRATES_IO_TOKEN"):
        return True

    cargo_home = Path(os.environ.get("CARGO_HOME") or Path.home() / ".cargo")
    for name in ("credentials.toml", "credentials"):
        path = cargo_home / name
        if not path.is_file():
            continue
        try:
            credentials = tomllib.loads(path.read_text())
        except (tomllib.TOMLDecodeError, OSError) as error:
            # Not being able to read it is not evidence of no token, and cargo is
            # the authority on whether the token works. Say so rather than
            # refusing a release over a file we could not parse.
            print(f"could not read {path}: {error}", file=sys.stderr)
            return True
        # `[registry]` is crates.io, and `[registries.crates-io]` is the same
        # registry named explicitly; either is what cargo would use.
        if (credentials.get("registry") or {}).get("token"):
            return True
        if ((credentials.get("registries") or {}).get("crates-io") or {}).get("token"):
            return True
    return False


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


# crates.io answers a publish over its leaky-bucket limit with 429 and the time
# the next attempt is allowed: "try again after Thu, 24 Sep 2026 15:27:06 GMT".
RATE_LIMIT = re.compile(
    r"try again after ([A-Z][a-z]{2}, \d{1,2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT)"
)

# Attempts per crate when the server keeps refusing. One refusal and one retry is
# the normal case; more than that means the pacing estimate was wrong twice.
RATE_LIMIT_ATTEMPTS = 4


def rate_limit_retry_at(output: str) -> datetime.datetime | None:
    """When crates.io says a publish may be attempted again, if it refused one."""
    match = RATE_LIMIT.search(output)
    if match is None:
        return None
    return parsedate_to_datetime(match.group(1))


def run_and_show(command: list[str], cwd: Path) -> tuple[int, str]:
    """Run a publish, echo its output as it arrives, and return the text as well.

    The output has to be read to find the rate-limit message, and it has to be
    shown, because a release waiting out a limit is otherwise silent for ten
    minutes at a time.
    """
    process = subprocess.Popen(
        command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    lines: list[str] = []
    for line in process.stdout or ():
        sys.stdout.write(line)
        sys.stdout.flush()
        lines.append(line)
    return process.wait(), "".join(lines)


def wait_until(moment: datetime.datetime, label: str) -> None:
    """Sleep until `moment`, reporting what is left every five minutes."""
    reported = None
    while True:
        remaining = (moment - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
        if remaining <= 0:
            return
        bucket = int(remaining // 300)
        if bucket != reported:
            print(
                f"    {label}: {remaining / 60:.1f} minutes left, until {moment.isoformat()}",
                file=sys.stderr,
                flush=True,
            )
            reported = bucket
        time.sleep(min(remaining, 30))


def publish_command(
    stage: Path,
    crate: dict,
    data: dict,
    dry_run: bool,
    extra_no_verify: bool,
    no_verify: set[str],
) -> list[str]:
    command = [
        "cargo", "publish",
        "--manifest-path", str(stage / crate["dir"] / "Cargo.toml"),
        # Staging rewrites the tree in place, so it is dirty by construction.
        "--allow-dirty",
    ]
    if dry_run:
        command += ["--dry-run", *patches(stage, data, crate["published"])]
    if extra_no_verify or crate["published"] in no_verify:
        command.append("--no-verify")
    return command


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list", metavar="KIND", nargs="?", const="publishable",
                        choices=["publishable", "native", "wasm", "all"],
                        help="print package names and exit")
    parser.add_argument("--dirs", action="store_true", help="print the crate directories and exit")
    parser.add_argument("--commands", action="store_true",
                        help="print the cargo publish commands this would run, in order, and exit")
    parser.add_argument("--only", help="publish just this crate (comma-separated)")
    parser.add_argument("--gap", type=float, metavar="SECONDS",
                        help="minimum seconds between publishes; by default learned from "
                             "crates.io's first refusal (ten minutes for a new crate name)")
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

    if args.commands:
        # The same sequence as bare `cargo publish` lines, for running a release
        # by hand. It is derived here so it cannot drift from what this script
        # does: one order, one `--no-verify` set, both from the stage report.
        chosen = order
        if args.only:
            wanted = [name.strip() for name in args.only.split(",") if name.strip()]
            unknown = [name for name in wanted if name not in by_name]
            if unknown:
                raise SystemExit(f"not in this release: {', '.join(unknown)}")
            chosen = [name for name in order if name in set(wanted)]
        for name in chosen:
            crate = by_name[name]
            flags = ["--allow-dirty", "--manifest-path", f"{crate['dir']}/Cargo.toml"]
            if crate["published"] in no_verify:
                flags.append("--no-verify")
            print("cargo publish " + " ".join(flags))
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

    if not args.dry_run and not registry_token_present():
        print(
            "no crates.io token: run `cargo login`, or set CARGO_REGISTRY_TOKEN",
            file=sys.stderr,
        )
        return 2

    if no_verify:
        print(
            "packaging without verification (their dev-dependencies cannot resolve "
            f"until other crates in this release are on the registry): "
            f"{', '.join(sorted(no_verify))}",
            file=sys.stderr,
        )

    skipped = []
    # Pacing, learned from crates.io's own refusal: how long a refusal implied we
    # must leave between publishes, and the moment it named as the next attempt.
    # `--gap` seeds the interval, so a resumed run that already knows the limit
    # never has to be refused to find out.
    interval: float | None = args.gap
    earliest: datetime.datetime | None = None

    for name in selected:
        crate = by_name[name]
        if not args.dry_run and not args.force and registry_has(name, crate["version"]):
            print(f"--> {name} {crate['version']} is already published; skipping")
            skipped.append(name)
            continue

        command = publish_command(stage, crate, data, args.dry_run, args.no_verify, no_verify)
        print(f"\n==> {crate['published']} {crate['version']}" + (" (dry run)" if args.dry_run else ""))
        code = 1
        for attempt in range(1, RATE_LIMIT_ATTEMPTS + 1):
            if earliest is not None:
                wait_until(earliest, f"{name} is rate limited by crates.io")
            started = datetime.datetime.now(datetime.timezone.utc)
            code, output = run_and_show(command, stage)
            if code == 0:
                break
            retry_at = rate_limit_retry_at(output)
            if retry_at is None:
                break
            implied = (retry_at - started).total_seconds()
            interval = max(implied, interval or 0) + 20
            earliest = retry_at + datetime.timedelta(seconds=20)
            print(
                f"    crates.io refused this publish: the next attempt is allowed at "
                f"{retry_at.isoformat()}, which implies one new crate every "
                f"{implied / 60:.1f} minutes. Pacing the rest of the release at "
                f"{interval / 60:.1f} minutes instead of being refused again.",
                file=sys.stderr,
                flush=True,
            )
        if code != 0:
            print(f"\nFAILED at {name}; the crates before it are published and this run can be "
                  f"resumed with `--only {name}` once the cause is fixed", file=sys.stderr)
            return code

        # The server counts a publish when it accepts it, so the next one has to
        # wait from now.
        if interval is not None and not args.dry_run:
            earliest = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
                seconds=interval
            )
        else:
            earliest = None
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
