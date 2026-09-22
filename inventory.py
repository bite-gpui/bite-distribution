#!/usr/bin/env python3
"""Inventory the in-checkout path-dependency closure of a Cargo workspace.

The distribution project publishes crates that live in a monorepo, so the first
question for every target is always the same: *which crates are actually in the
way of the facade?* This resolves that from the manifests themselves rather
than from a hand-kept list, because the answer changes with every target.

It reads a checkout's workspace root manifest, resolves every dependency that
points at another crate inside the same checkout (including `workspace = true`
inheritance and `[patch]` substitutions), then reports the closure reachable
from the requested root crates.

Usage:
    inventory.py --repo /path/to/checkout --root crates/gpui [--root crates/gpui_parley]
    inventory.py --repo /path/to/checkout --root crates/gpui --json out.json --summary
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

DEP_TABLES = ("dependencies", "build-dependencies", "dev-dependencies")


def load_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def workspace_members(repo: Path, root_manifest: dict) -> list[Path]:
    """Expand the workspace member list, following globs the way cargo does."""
    workspace = root_manifest.get("workspace", {})
    excluded = {str(e).rstrip("/") for e in as_list(workspace.get("exclude"))}
    dirs: list[Path] = []
    for member in as_list(workspace.get("members")):
        member = str(member).rstrip("/")
        if any(ch in member for ch in "*?["):
            for matched in sorted(repo.glob(member)):
                if matched.is_dir():
                    dirs.append(matched)
        else:
            candidate = repo / member
            if candidate.is_dir():
                dirs.append(candidate)
    return [d for d in dirs if str(d.relative_to(repo)) not in excluded]


def inherited_version(package: dict, root_manifest: dict, member: dict) -> str | None:
    version = package.get("version")
    if isinstance(version, dict) and version.get("workspace"):
        return root_manifest.get("workspace", {}).get("package", {}).get("version")
    if isinstance(version, str):
        return version
    inherited = member.get("version")
    if isinstance(inherited, dict) and inherited.get("workspace"):
        return root_manifest.get("workspace", {}).get("package", {}).get("version")
    if isinstance(inherited, str):
        return inherited
    return None


def dep_specs(manifest: dict) -> list[tuple[str, str, object]]:
    """Every dependency declaration as (name, where, spec), including per-target."""
    found: list[tuple[str, str, object]] = []
    for table in DEP_TABLES:
        for name, spec in (manifest.get(table) or {}).items():
            found.append((name, table, spec))
    for target, cfg in (manifest.get("target") or {}).items():
        if not isinstance(cfg, dict):
            continue
        for table in DEP_TABLES:
            for name, spec in (cfg.get(table) or {}).items():
                found.append((name, f"target.{target}.{table}", spec))
    return found


def resolve(name: str, spec, workspace_deps: dict, patches: dict) -> dict:
    """Normalize a dependency declaration into a classified record.

    `path_base` records what a relative `path` is relative to: cargo resolves
    paths inherited from `[workspace.dependencies]` against the workspace root
    and paths written in a member manifest against that member's directory.
    """
    record = {
        "kind": "registry",
        "path": None,
        "base": "member",
        "git": None,
        "version": None,
        "package": None,
        "via_patch": False,
    }
    if isinstance(spec, str):
        record["version"] = spec
        if name in patches:
            record.update(resolve(name, patches[name], workspace_deps, {}))
            record["via_patch"] = True
        return record

    if not isinstance(spec, dict):
        record["kind"] = "unknown"
        return record

    if spec.get("workspace"):
        gate = spec
        spec = workspace_deps.get(name)
        if isinstance(spec, str):
            spec = {"version": spec}
        spec = dict(spec) if isinstance(spec, dict) else {}
        if not spec:
            record["kind"] = "missing-workspace-dep"
            return record
        record["base"] = "root"
        # The member may refine the inherited declaration.
        for key in ("features", "default-features", "optional", "package"):
            if key in gate:
                spec[key] = gate[key]

    record["package"] = spec.get("package")
    record["version"] = spec.get("version")
    if spec.get("path"):
        record["kind"] = "path"
        record["path"] = spec["path"]
    elif spec.get("git"):
        record["kind"] = "git"
        record["git"] = spec["git"]
    elif not spec.get("version") and name in patches:
        # A registry dependency redirected by `[patch]`. The patch is a
        # workspace-level concern and is stripped from a published manifest, so
        # the declaration already carries everything publishing needs.
        record.update(resolve(name, patches[name], workspace_deps, {}))
        record["via_patch"] = True
    return record


def patch_map(root_manifest: dict) -> dict:
    """`[patch]` entries keyed by the name a dependency would use."""
    patched: dict[str, object] = {}
    for entries in (root_manifest.get("patch") or {}).values():
        if isinstance(entries, dict):
            for name, spec in entries.items():
                patched[name] = spec
    return patched


def relative_to(repo: Path, path: Path) -> str | None:
    try:
        return str(path.resolve().relative_to(repo))
    except ValueError:
        return None


def path_target(repo: Path, crate_dir: str, record: dict) -> Path:
    if record["base"] == "root":
        return (repo / record["path"]).resolve()
    return (repo / crate_dir / record["path"]).resolve()


def collect(repo: Path) -> dict:
    """Load every crate reachable from the workspace members by path dependency.

    Cargo treats a member's path dependencies as workspace members even when
    they are missing from the `members` list, and this monorepo leans on that:
    the rearchitected crates are only partly listed. Discovery therefore
    follows path edges instead of trusting the list.
    """
    root_manifest = load_toml(repo / "Cargo.toml")
    workspace_deps = root_manifest.get("workspace", {}).get("dependencies", {}) or {}
    patches = patch_map(root_manifest)

    crates: dict[str, dict] = {}
    by_dir: dict[str, str] = {}
    visited: set[str] = set()
    pending = list(workspace_members(repo, root_manifest))

    while pending:
        directory = pending.pop()
        if str(directory) in visited:
            continue
        visited.add(str(directory))
        manifest_path = directory / "Cargo.toml"
        if not manifest_path.exists():
            continue
        manifest = load_toml(manifest_path)
        package = manifest.get("package")
        if not package or "name" not in package:
            continue
        name = package["name"]
        relative = relative_to(repo, directory)
        if relative is None:
            continue
        lib = manifest.get("lib", {}) or {}
        deps: dict[str, list[dict]] = {}
        for dep_name, where, spec in dep_specs(manifest):
            deps.setdefault(dep_name, []).append(
                {"where": where, **resolve(dep_name, spec, workspace_deps, patches)}
            )
        crates[name] = {
            "dir": relative,
            "version": inherited_version(package, root_manifest, manifest),
            "lib_name": lib.get("name") or name.replace("-", "_"),
            "lib_path": lib.get("path"),
            "instrumented_lib": lib.get("path") is not None,
            "publish": package.get("publish", True),
            "license": (package.get("license") or manifest.get("license")),
            "license_file": (package.get("license-file") or manifest.get("license-file")),
            "description": (package.get("description") or manifest.get("description")),
            "repository": (package.get("repository") or manifest.get("repository")),
            "readme": (package.get("readme") or manifest.get("readme")),
            "has_build_rs": (directory / "build.rs").exists(),
            "deps": deps,
        }
        by_dir[relative] = name
        for records in deps.values():
            for record in records:
                if record["kind"] == "path":
                    pending.append(path_target(repo, relative, record))

    # Resolve path dependencies to package names by directory.
    missing: list[str] = []
    for name, crate in crates.items():
        for records in crate["deps"].values():
            for record in records:
                if record["kind"] != "path":
                    continue
                relative = relative_to(repo, path_target(repo, crate["dir"], record))
                if relative is not None and relative in by_dir:
                    record["path_package"] = by_dir[relative]
                else:
                    record["path_package"] = None
                    missing.append(f"{name} -> {record['path']}")
    return {"repo": str(repo), "crates": crates, "by_dir": by_dir, "missing": missing}


def closure(crates: dict, roots: list[str]) -> dict[str, list[str]]:
    """Reachable in-repo crates per dependency kind (normal, build, dev)."""
    kinds = {
        "normal": ("dependencies",),
        "build": ("build-dependencies",),
        "dev": ("dev-dependencies",),
    }
    reach: dict[str, list[str]] = {}
    for kind, tables in kinds.items():
        seen: set[str] = set()
        queue = [r for r in roots if r in crates]
        unknown = [r for r in roots if r not in crates]
        if unknown:
            raise SystemExit(f"unknown root crates: {', '.join(unknown)}")
        while queue:
            name = queue.pop()
            if name in seen:
                continue
            seen.add(name)
            crate = crates[name]
            for dep_name, records in crate["deps"].items():
                for record in records:
                    if not _table_matches(record["where"], tables):
                        continue
                    target = record.get("path_package")
                    if target:
                        queue.append(target)
                        break
        reach[kind] = sorted(seen)
    return reach


def _table_matches(where: str, tables: tuple[str, ...]) -> bool:
    tail = where.split(".")[-1]
    return tail in tables


def publish_order(crates: dict, names) -> list[str]:
    """Dependency-first order over an in-repo closure.

    Cycles are tolerated rather than reported: gpui and its platform crates
    reference each other through test-only and example-only edges, which cargo
    can represent but a strict topological sort cannot. A crate is emitted only
    after every dependency that can be emitted first.
    """
    wanted = {name for name in names if name in crates}
    order: list[str] = []
    state: dict[str, int] = {}

    def visit(name: str) -> None:
        if state.get(name):
            return
        state[name] = 1
        for records in crates[name]["deps"].values():
            for record in records:
                dep = record.get("path_package")
                if dep and dep != name and dep in wanted:
                    visit(dep)
        state[name] = 2
        order.append(name)

    for name in sorted(wanted):
        visit(name)
    return order


def report(repo: Path, roots: list[str]) -> dict:
    collected = collect(repo)
    crates = collected["crates"]
    reached = closure(crates, roots)
    in_closure = set(reached["normal"]) | set(reached["build"])

    git_only = {}
    unpublished = {}
    for name in reached["normal"] + reached["build"]:
        crate = crates[name]
        if not publishable(crate):
            unpublished[name] = str(crate["publish"])
        externals = sorted(
            {
                (dep_name, record.get("package") or dep_name, record["git"])
                for dep_name, records in crate["deps"].items()
                for record in records
                if record["kind"] == "git" and _table_matches(record["where"], ("dependencies", "build-dependencies"))
            }
        )
        if externals:
            git_only[name] = [
                {"name": dep, "package": package, "git": git} for dep, package, git in externals
            ]

    missing = [
        entry
        for entry in collected["missing"]
        if entry.split(" -> ")[0] in in_closure
    ]

    return {
        "repo": collected["repo"],
        "roots": roots,
        "reachable": reached,
        "counts": {kind: len(names) for kind, names in reached.items()},
        "unpublishable": unpublished,
        "git_only_deps": git_only,
        "missing_path_deps": missing,
        "crates": crates,
    }


def publishable(crate: dict) -> bool:
    """Whether cargo would accept this crate for publishing."""
    publish = crate["publish"]
    if publish is False:
        return False
    if isinstance(publish, list) and not publish:
        return False
    if isinstance(publish, str) and publish == "false":
        return False
    return True


def print_summary(result: dict) -> None:
    print(f"repo: {result['repo']}")
    print(f"roots: {', '.join(result['roots'])}")
    print(f"counts: {result['counts']}")
    reached = result["reachable"]
    print("\n-- normal-dependency closure --")
    for name in reached["normal"]:
        crate = result["crates"][name]
        flags = []
        if not publishable(crate):
            flags.append(f"publish={crate['publish']!r}")
        if crate["has_build_rs"]:
            flags.append("build.rs")
        print(f"  {name:<24} {str(crate['version']):<10} {crate['dir']:<34} {' '.join(flags)}")
    if reached["build"]:
        print("\n-- build-dependency-only --")
        print("  " + ", ".join(sorted(set(reached["build"]) - set(reached["normal"]))))
    if result["unpublishable"]:
        print("\n-- unpublishable in closure --")
        for name, publish in result["unpublishable"].items():
            print(f"  {name}: publish={publish!r}")
    if result["git_only_deps"]:
        print("\n-- git-only (non-in-repo) deps --")
        for name, deps in result["git_only_deps"].items():
            for dep in deps:
                print(f"  {name} -> {dep['name']} (package {dep['package']}) {dep['git']}")
    if result["missing_path_deps"]:
        print("\n-- path deps leaving the checkout --")
        for entry in sorted(set(result["missing_path_deps"])):
            print(f"  {entry}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--root", action="append", required=True)
    parser.add_argument("--json")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--with-manifests", action="store_true",
                        help="include the full crate table in --json output")
    args = parser.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve()
    if not (repo / "Cargo.toml").exists():
        print(f"no workspace manifest at {repo}", file=sys.stderr)
        return 2

    result = report(repo, args.root)
    if args.summary or not args.json:
        print_summary(result)
    if args.json:
        payload = result
        if not args.with_manifests:
            payload = {k: v for k, v in result.items() if k != "crates"}
        Path(args.json).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
