#!/usr/bin/env python3
"""Carve a target branch into a publishable state, in place.

Staging mutates a throwaway checkout of the target branch rather than copying
the closure into a synthetic workspace. That is not laziness: every crate in
the closure reaches its dependencies through `[workspace.dependencies]`
inheritance, so a copy would have to re-derive the whole workspace model
(inherited `version`/`edition`/`lints`, per-target tables, `[patch]`) and would
drift from it silently the first time a branch changes shape. Mutating a
worktree keeps cargo itself as the source of truth, and the worktree is cheap
(~100 MB, no target directory) and disposable.

What gets rewritten, and why each is needed for `cargo publish` to accept the
crate:

- `[package] name` -> the published name (naming.py), `[lib] name` untouched;
- `[package] version` -> the version the branch's release tag names (§6);
- crate-level `publish = false` removed, workspace default raised to true
  (zed's root sets `publish = false`, which 11 of the 1.14 closure inherit);
- `[workspace.dependencies]` entries for closure crates gain `package` and
  `version` — no zed entry has a version, and cargo refuses to publish a
  dependency without one;
- git dependencies with a registry equivalent are replaced by it, because a
  published manifest cannot carry a git source;
- a copy of the license text the manifest declares, plus a NOTICE recording the
  upstream origin and the modifications (Apache-2.0 §4(b));
- `[package.metadata.bite]`, so a consumer can tell what they got.

Usage:
    stage.py --target bite_v1.20.2 --source wt/bite_v1.20.2
    stage.py --target bite_v1.20.2 --source wt/x --report dist-stage.json
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

DIST = Path(__file__).resolve().parent
sys.path.insert(0, str(DIST))
import inventory  # noqa: E402
import naming  # noqa: E402
import targets as targets_mod  # noqa: E402

APACHE = "Apache-2.0"

# Git dependencies that have a crates.io equivalent, keyed by the package name
# the dependency resolves to. Measured against the registry; see DESIGN.md §8.
GIT_SUBSTITUTES = {
    "zed-font-kit": "0.14.1-zed",
    "zed-scap": "0.0.8-zed",
    "zed-xim": "0.4.0-zed",
    "calloop": "0.14.4",
    "wasm_thread": "0.3.3",
    "proptest": "1.11.0",
    "async-tar": "0.6.1",
}

HEADER_RE = re.compile(r"^\[\s*([^\]]+?)\s*\]\s*(?:#.*)?$")
KEY_RE = re.compile(r"^\s*([A-Za-z0-9_\-]+)\s*(?:\.\s*([A-Za-z0-9_\-]+)\s*)?=")

# Crates whose native build is not a configuration the project supports, so a
# native check of them proves nothing and their test targets do not build.
# `gpui_web` includes its modules under `cfg(any(target_family = "wasm", test))`
# while their dependencies (`gpui_engine`, `gpui_platform`) are declared only
# under `[target.'cfg(target_family = "wasm")'.dependencies]`, so on Linux the
# lib compiles to nothing and the lib *test* target cannot resolve those
# imports. zed's own CI does not check it natively either.
WASM_ONLY = {"gpui_web"}


DEP_TABLES = ("dependencies", "dev-dependencies", "build-dependencies")


class ManifestError(Exception):
    pass


# --- line-oriented TOML editing ---------------------------------------------
#
# Targeted edits, not re-serialization: these manifests carry comments and
# ordering that a TOML writer would discard, and the published crate's
# `[workspace.dependencies]`-style tables are read by cargo, not by us.


def read(path: Path) -> list[str]:
    return path.read_text().splitlines(keepends=True)


def write(path: Path, lines: list[str]) -> None:
    path.write_text("".join(lines))


def table_span(lines: list[str], header: str) -> tuple[int, int] | None:
    start = None
    for index, line in enumerate(lines):
        match = HEADER_RE.match(line)
        if not match:
            continue
        if start is not None:
            return (start, index - 1)
        if match.group(1) == header:
            start = index
    if start is None:
        return None
    return (start, len(lines) - 1)


def value_span(lines: list[str], span: tuple[int, int], key: str) -> tuple[int, int] | None:
    """Span of `key = …`, following an inline table across lines."""
    for index in range(span[0], span[1] + 1):
        match = KEY_RE.match(lines[index])
        if not match or match.group(1) != key:
            continue
        end = index
        depth = _depth(lines[index])
        while depth > 0 and end < span[1]:
            end += 1
            depth += _depth(lines[end])
        return (index, end)
    return None


def _depth(line: str) -> int:
    code = line.split("#", 1)[0]
    return code.count("{") + code.count("[") - code.count("}") - code.count("]")


def raw_value(lines: list[str], span: tuple[int, int]) -> str:
    joined = "".join(lines[span[0]:span[1] + 1])
    return joined.split("=", 1)[1].strip()


def force_keys(lines: list[str], span: tuple[int, int], overrides: dict[str, str]) -> bool:
    """Set keys in an inline table, replacing any existing value.

    Replacement, not addition: `collections` already carried
    `version = "0.1.0"`, and leaving it there produced a dependency on 0.1.0
    while the package itself became 1.21.0-pre. Cargo's resolver catches that
    as a version mismatch across the whole workspace, which is how this was
    found.

    Returns False when the value is not an inline table.
    """
    text = raw_value(lines, span)
    if not text.startswith("{"):
        return False
    spec = tomllib.loads(f"x = {text}")["x"]
    spec.update(overrides)
    replace_value(lines, span, inline_value(spec))
    return True


def replace_value(lines: list[str], span: tuple[int, int], value: str) -> None:
    prefix = lines[span[0]].split("=", 1)[0].rstrip()
    lines[span[0]:span[1] + 1] = [f"{prefix} = {value}\n"]


def set_key(lines: list[str], header: str, key: str, value: str) -> None:
    span = table_span(lines, header)
    if span is None:
        lines.append(f"\n[{header}]\n{key} = {value}\n")
        return
    found = value_span(lines, span, key)
    if found:
        replace_value(lines, found, value)
    else:
        lines.insert(span[0] + 1, f"{key} = {value}\n")


def drop_key(lines: list[str], header: str, key: str) -> bool:
    span = table_span(lines, header)
    if span is None:
        return False
    found = value_span(lines, span, key)
    if not found:
        return False
    del lines[found[0]:found[1] + 1]
    return True


def inline_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(inline_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k} = {inline_value(v)}" for k, v in value.items()) + " }"
    return json.dumps(value)


# --- staging ----------------------------------------------------------------


def dep_edits(source: Path, crates: dict, names, published: dict, version: str) -> dict:
    """Per-manifest dependency rewrites, for both lineages.

    The two lineages declare their dependencies differently, and assuming
    either one produces a target that resolves to the wrong version:

    - the zed lineage reaches everything through `[workspace.dependencies]`
      (measured: zero direct member paths), so its edits land in the root
      manifest;
    - ce declares a path and a version in each member, so its edits land in the
      member, and its workspace table has no entry to edit at all.

    Scoping matters as much as placement: zed's root manifest pins thirty-odd
    git dependencies for the editor (tree-sitter grammars, pet, livekit) and
    none of them is in gpui's way. Scanning the whole table produced thirty fake
    blockers.
    """
    root = source / "Cargo.toml"
    plans: dict[Path, dict[str, dict]] = {}
    blockers: list[dict] = []
    advisories: list[dict] = []
    patched: list[dict] = []

    # Every crate in the checkout, not just the closure. A crate outside the
    # closure that depends on one we renamed still has to resolve, and cargo
    # resolves the whole workspace, so leaving those alone breaks everything:
    # ce's `gpui_ce_elements` and `gpui_ce_tokio` are outside its closure and
    # both name `gpui-ce`.
    for name, crate in crates.items():
        in_closure = name in names
        manifest = source / crate["dir"] / "Cargo.toml"
        for key, records in crate["deps"].items():
            for record in records:
                at_root = record["base"] == "root"
                target = root if at_root else manifest

                if record["kind"] == "path":
                    dependency = record.get("path_package")
                    if dependency in published:
                        plans.setdefault(target, {})[key] = {
                            "kind": "path",
                            "drop": [],
                            "set": {"package": published[dependency], "version": version},
                        }
                    continue

                # Git substitutions and publishing blockers are about what we
                # publish, so they stay scoped to the closure.
                if not in_closure or record["kind"] != "git":
                    continue
                entry = {
                    "in": label_of(source, target, root),
                    "key": key,
                    "package": record.get("package") or key,
                    "needed_by": name,
                    "table": record["where"].split(".")[-1],
                }
                if record.get("via_patch"):
                    patched.append(entry)
                    continue

                substitute = GIT_SUBSTITUTES.get(entry["package"])
                if substitute:
                    plans.setdefault(target, {})[key] = {
                        "kind": "substitute",
                        "drop": ["git", "rev", "branch", "tag"],
                        "set": {"package": entry["package"], "version": substitute},
                    }
                elif entry["table"] in ("dependencies", "build-dependencies"):
                    blockers.append(entry)
                else:
                    advisories.append(entry)

    return {"plans": plans, "blockers": blockers, "advisories": advisories, "patched": patched}


def verify_staged(source: Path, before: dict, published: dict, version: str) -> list[str]:
    """Re-read the staged tree and confirm it says what was intended.

    Re-parsing rather than trusting the edits, because the edits are textual: a
    dependency declared as a `[workspace.dependencies.foo]` sub-table, a key the
    plan missed, or a crate outside the closure that still names a renamed
    package would otherwise pass unnoticed until cargo resolved a stale version
    — or until a consumer did.
    """
    after = inventory.collect(source)["crates"]
    problems: list[str] = []

    # No manifest anywhere may still name a package we renamed. This is the
    # check that would have caught `gpui_ce_elements` before the resolver did.
    renamed = {package: name for package, name in published.items() if name != package}
    for name, crate in after.items():
        for key, records in crate["deps"].items():
            for record in records:
                stale = renamed.get(record.get("package"))
                if stale:
                    problems.append(
                        f"{name}: dependency {key} still names {record['package']!r}, "
                        f"which was renamed to {stale!r}"
                    )

    for package, name in sorted(published.items()):
        staged = after.get(name)
        if staged is None:
            problems.append(f"{name} (from {package}) is missing from the staged workspace")
            continue
        if staged["version"] != version:
            problems.append(f"{name}: version is {staged['version']!r}, expected {version!r}")
        if staged["lib_name"] != before[package]["lib_name"]:
            problems.append(
                f"{name}: lib name is {staged['lib_name']!r}, "
                f"expected {before[package]['lib_name']!r}"
            )
        if not inventory.publishable(staged):
            problems.append(f"{name}: still marked unpublishable ({staged['publish']!r})")
        for key, records in staged["deps"].items():
            for record in records:
                dependency = record.get("path_package")
                if dependency not in published:
                    continue
                if record.get("package") != published[dependency]:
                    problems.append(
                        f"{name}: dependency {key} names package {record.get('package')!r}, "
                        f"expected {published[dependency]!r}"
                    )
                if record.get("version") != version:
                    problems.append(
                        f"{name}: dependency {key} is version {record.get('version')!r}, "
                        f"expected {version!r}"
                    )
    return problems


def dedupe(entries: list[dict]) -> list[dict]:
    """One row per dependency, with every crate that needs it."""
    merged: dict[tuple, dict] = {}
    for entry in entries:
        key = (entry["in"], entry["package"], entry["table"])
        if key in merged:
            merged[key]["needed_by"] += f", {entry['needed_by']}"
        else:
            merged[key] = dict(entry)
    return sorted(merged.values(), key=lambda e: (e["in"], e["package"]))


def label_of(source: Path, manifest: Path, root: Path) -> str:
    if manifest == root:
        return "Cargo.toml (workspace)"
    return str(manifest.relative_to(source))


def rewrite_dep_specs(lines: list[str], label: str, plan: dict[str, dict], report: dict) -> None:
    """Apply a per-key plan to every *dependency* table of one manifest.

    Every dependency table, because these sit under
    `[target.'cfg(…)'.dependencies]` as often as under `[dependencies]`. Not
    every table at all: `[profile.dev.package]` lists package names as profile
    keys, and a crate name appearing there is not a dependency. Rewriting it
    there produced `gpui_macros = { opt-level = 3, package = …, version = … }`,
    which cargo rejects.
    """
    for header in [m.group(1) for m in (HEADER_RE.match(l) for l in lines) if m]:
        if header.split(".")[-1] not in DEP_TABLES:
            continue
        span = table_span(lines, header)
        if span is None:
            continue
        cursor = span[0]
        while cursor <= span[1]:
            match = KEY_RE.match(lines[cursor])
            if not match or match.group(1) not in plan:
                cursor += 1
                continue
            found = value_span(lines, (cursor, span[1]), match.group(1))
            if found is None:
                cursor += 1
                continue
            edit = plan[match.group(1)]
            spec = tomllib.loads(f"x = {raw_value(lines, found)}")["x"]
            if not isinstance(spec, dict):
                # The same key can be declared twice for different targets —
                # `wasm_thread` is a git fork under one cfg and a registry crate
                # under another — and only the git form needs rewriting.
                cursor = found[1] + 1
                continue
            if edit["kind"] == "substitute" and not spec.get("git"):
                cursor = found[1] + 1
                continue
            for dropped in edit["drop"]:
                spec.pop(dropped, None)
            spec.update(edit["set"])
            replace_value(lines, found, inline_value(spec))
            report["rewrites"].append({
                "in": label,
                "key": match.group(1),
                "kind": edit["kind"],
                "package": spec.get("package"),
                "version": spec.get("version"),
            })
            cursor = found[1] + 1


def write_notice(crate_dir: Path, target: dict, version: str, license_id: str | None) -> None:
    """The prominent notice Apache-2.0 §4(b) requires of a modified work."""
    if not (license_id or "").startswith(APACHE):
        return
    notice = crate_dir / "NOTICE"
    if notice.exists():
        return
    notice.write_text(
        "bite-gpui\n"
        "\n"
        "This crate is derived from Zed's gpui and is distributed under the\n"
        "Apache License, Version 2.0. It has been modified: the crate has been\n"
        "restructured and renamed as part of the bite-gpui rearchitecture, and\n"
        "the manifest rewritten so it can be consumed as an independent package.\n"
        "\n"
        f"    upstream:  https://github.com/zed-industries/zed ({target.get('upstream', target['branch'])})\n"
        f"    built from: {target['url']} branch {target['branch']}\n"
        f"    version:   {version}\n"
        "\n"
        "See LICENSE-APACHE for the full licence text.\n"
    )


def stage(target: dict, source: Path, report_path: Path | None, strict: bool = False) -> int:
    source = source.expanduser().resolve()
    if not (source / "Cargo.toml").exists():
        print(f"FAIL no workspace manifest in {source}", file=sys.stderr)
        return 2

    commit = subprocess.run(
        ["git", "--no-pager", "rev-parse", "HEAD"], cwd=source, capture_output=True, text=True, check=True
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "--no-pager", "rev-parse", "--abbrev-ref", "HEAD"], cwd=source,
        capture_output=True, text=True,
    ).stdout.strip()
    version = targets_mod.version_for(target, source)
    amendment = target.get("amendment", 0)
    # Empty for a rolling target: it has no upstream release to name itself after.
    upstream = target.get("upstream", "")

    collected = inventory.collect(source)
    crates = collected["crates"]
    missing_roots = [root for root in target["roots"] if root not in crates]
    if missing_roots:
        # Staging rewrites the tree in place, so the usual cause is running it
        # twice against the same checkout.
        staged_names = sorted(name for name in crates if name.startswith("bite-"))
        hint = (
            f"; the checkout already looks staged ({', '.join(staged_names[:3])}, …), "
            "so reset it with `git checkout -- . && git clean -fd`"
            if staged_names
            else ""
        )
        print(f"FAIL {source} has no crate named {', '.join(missing_roots)}{hint}", file=sys.stderr)
        return 2
    reached = inventory.closure(crates, target["roots"])
    names = sorted(set(reached["normal"]) | set(reached["build"]))
    order = inventory.publish_order(crates, names)
    if not names:
        print("FAIL closure is empty", file=sys.stderr)
        return 2

    report: dict = {
        "target": target["name"],
        "branch": target["branch"],
        "commit": commit,
        "version": version,
        "crates": [],
        "rewrites": [],
        "unresolved_git": [],
        "dev_only_git": [],
        "patched_git": [],
        "problems": [],
        "licensed": [],
    }

    root_manifest = source / "Cargo.toml"
    root_lines = read(root_manifest)

    # The workspace default decides for every crate that does not override it.
    set_key(root_lines, "workspace.package", "publish", "true")
    set_key(root_lines, "workspace.package", "version", f'"{version}"')

    published: dict[str, str] = {}
    for package in order:
        published[package] = naming.published_name(package, target["lineage"])

    edits = dep_edits(source, crates, names, published, version)
    report["unresolved_git"] = dedupe(edits["blockers"])
    report["dev_only_git"] = dedupe(edits["advisories"])
    report["patched_git"] = dedupe(edits["patched"])
    plans = edits["plans"]

    # A closure crate that some member still reaches through the workspace table
    # needs its entry rewritten there, which `rewrite_dep_specs` does below via
    # the same plan. ce has no such entries — it declares a path in each member —
    # so for ce this is empty.
    rewrite_dep_specs(
        root_lines, "Cargo.toml (workspace)", plans.get(root_manifest, {}), report
    )

    for package in order:
        crate = crates[package]
        crate_dir = source / crate["dir"]
        manifest = crate_dir / "Cargo.toml"
        lines = read(manifest)
        license_id = crate["license"] if isinstance(crate["license"], str) else APACHE

        set_key(lines, "package", "name", f'"{published[package]}"')
        set_key(lines, "package", "version", f'"{version}"')
        drop_key(lines, "package", "publish")
        # Pin the lib name. These crates declare `[lib] path` without a name, so
        # their lib name is *derived from the package name*: renaming the package
        # would silently rename the crate that `use gpui::…` and every internal
        # `use gpui_types::…` refers to. Measured, not assumed.
        set_key(lines, "lib", "name", json.dumps(crate["lib_name"]))
        if crate["description"] in (None, ""):
            set_key(
                lines,
                "package",
                "description",
                json.dumps(f"{package} — part of the bite-gpui rearchitecture of zed's gpui"),
            )
        set_key(lines, "package", "repository", json.dumps(target["url"].removesuffix(".git")))

        rewrite_dep_specs(lines, f"{crate['dir']}/Cargo.toml", plans.get(manifest, {}), report)

        lines.append(
            "\n[package.metadata.bite]\n"
            f"source = {json.dumps(target['url'])}\n"
            f"branch = {json.dumps(target['branch'])}\n"
            f"commit = {json.dumps(commit)}\n"
            f"upstream = {json.dumps(upstream)}\n"
            f"amendment = {amendment}\n"
            f"original = {json.dumps(package)}\n"
        )
        write(manifest, lines)

        copied = copy_license(source, crate_dir, license_id)
        write_notice(crate_dir, {**target, "upstream": upstream}, version, license_id)
        if copied:
            report["licensed"].append({"crate": published[package], "file": copied})

        report["crates"].append(
            {
                "original": package,
                "published": published[package],
                "version": version,
                "lib": crate["lib_name"],
                "dir": crate["dir"],
                "license": license_id,
            }
        )

    # Plans can land outside the closure: a crate that depends on one we renamed
    # but is not itself published. Those manifests are not touched by the loop
    # above, and cargo still resolves them, so they get the dependency rewrite
    # and nothing else — no metadata, no version bump, no licence.
    processed = {source / crates[name]["dir"] / "Cargo.toml" for name in order}
    for manifest, plan in sorted(plans.items(), key=lambda item: str(item[0])):
        if manifest in processed or manifest == root_manifest:
            continue
        lines = read(manifest)
        rewrite_dep_specs(lines, str(manifest.relative_to(source)), plan, report)
        write(manifest, lines)

    write(root_manifest, root_lines)

    problems = verify_staged(source, crates, published, version)
    blocked: dict[str, str] = {}
    for entry in report["unresolved_git"]:
        reason = f"{entry['package']} is a git dependency with no registry version"
        for crate in entry["needed_by"].split(", "):
            blocked.setdefault(crate, reason)

    report["problems"] = problems
    report["blocked"] = [
        {"package": package, "published": published[package], "reason": reason}
        for package, reason in sorted(blocked.items())
    ]
    report["native"] = [
        crate["published"] for crate in report["crates"] if crate["original"] not in WASM_ONLY
    ]
    report["wasm_only"] = [
        crate["published"] for crate in report["crates"] if crate["original"] in WASM_ONLY
    ]
    report["order"] = [
        published[name] for name in order if name not in blocked
    ]
    out = report_path or (source / "dist-stage.json")
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    # A blocked crate is a publishing limitation, not a staging failure: the
    # tree still builds (a git dependency resolves locally), so the gate can
    # check everything and CI stays meaningful. `publish.py` is where the limit
    # is enforced, and it refuses a release rather than shipping part of one.
    for entry in report["unresolved_git"]:
        print(
            f"WARN {entry['package']} is a git dependency with no registry version "
            f"(in {entry['in']}, as {entry['key']}), so it cannot be published; "
            f"withheld: {entry['needed_by']}",
            file=sys.stderr,
        )
    for entry in report["blocked"]:
        print(f"WARN {entry['published']} is withheld: {entry['reason']}", file=sys.stderr)

    if problems or (strict and report["blocked"]):
        for problem in problems:
            print(f"FAIL {problem}", file=sys.stderr)
        print(f"stage report: {out}", file=sys.stderr)
        return 1

    print(f"target   {target['name']} ({target['branch']} @ {commit[:12]})")
    print(f"version  {version}  (upstream {upstream}, amendment {amendment})")
    rewrites = report["rewrites"]
    substituted = sum(1 for r in rewrites if r["kind"] == "substitute")
    print(f"crates   {len(report['crates'])}  dependency rewrites {len(rewrites)} "
          f"({substituted} git substitutions)  licences copied {len(report['licensed'])}")
    print(f"checks   {len(report['native'])} native, {len(report['wasm_only'])} wasm-only, "
          f"{len(report['blocked'])} withheld")
    if report["patched_git"]:
        print("patched deps (no manifest edit; the published manifest carries the registry version):")
        for entry in report["patched_git"]:
            print(f"  {entry['package']:<18} {entry['in']}")
    print(f"report   {out}")
    print("\npublish order (dependency first):")
    for name in report["order"]:
        print(f"  {name}")
    return 0


def copy_license(source: Path, crate_dir: Path, license_id: str) -> str | None:
    """Ensure the crate ships the licence text its manifest declares.

    Apache-2.0 §4(a) requires recipients to receive a copy of the licence, and
    7 of the 1.14 closure declare Apache without carrying the file.
    """
    if (license_id or "").startswith(APACHE):
        wanted, candidate = "LICENSE-APACHE", source / "LICENSE-APACHE"
    elif "GPL" in (license_id or ""):
        wanted, candidate = "LICENSE-GPL", source / "LICENSE-GPL"
    else:
        return None
    if (crate_dir / wanted).exists() or not candidate.exists():
        return None
    shutil.copyfile(candidate, crate_dir / wanted)
    return wanted


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--source", required=True, help="a checkout of the target branch")
    parser.add_argument("--report", help="write the stage report here (default: <source>/dist-stage.json)")
    parser.add_argument("--strict", action="store_true",
                        help="fail on a withheld crate instead of just reporting it")
    args = parser.parse_args(argv)

    declared = {t["name"]: t for t in targets_mod.load()}
    if args.target not in declared:
        raise SystemExit(f"unknown target: {args.target} (see targets.toml)")
    return stage(
        declared[args.target],
        Path(args.source),
        Path(args.report) if args.report else None,
        strict=args.strict,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
