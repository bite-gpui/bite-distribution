# bite-gpui distribution

Publishes the `bite_*` gpui branches as installable crates: for each target
branch, carve the path-dependency closure out of the monorepo, rename the
packages, and release them to crates.io as `bite-gpui` / `bite-gp-*` (zed
lineage) and `bite-gpui-ce` / `bite-gp-ce-*` (community-edition lineage).

The design is in [DESIGN.md](DESIGN.md). Read §13 of it first — it lists the
decisions still open, one of which has legal weight.

All twelve target branches live in one repository, `bite-gpui/bite-gpui`: ten
descend from zed releases, `bite_master` from zed's main, and `bite_ce_main`
from the community edition's fork point. They share an ancestor but have
diverged, so each target declares its lineage rather than having it inferred
from the remote, and the zed lineage owns the unprefixed `bite-gp-*` names while
the community edition's are prefixed `ce`:

```toml
bite-gpui = "1.20"          # zed 1.20, rearchitected
bite-gpui-ce = "0.20260922.0"   # the community-edition transplant
```

This directory is ignored by the zed clone, the same way `.tools/` is; it is not
part of any published crate.

## Pipeline

```
targets.toml
    │
    ├─ targets.py ──► validate the table, build the CI matrix
    ├─ naming.py  ──► source package → published crate name, verified per target
    │
    ├─ stage.py ───► mutate a throwaway checkout of the branch in place:
    │                 rename packages, pin lib names, set versions,
    │                 raise publish, rewrite workspace deps,
    │                 substitute git deps, licence + provenance
    │                 └─► dist-stage.json   (the handoff)
    │
    └─ publish.py ─► dependency-ordered publish from the report,
                     resumable, with local-graph dry runs
```

The report is what ties the last two together: `publish.py` takes the order,
names and versions from it, so the two scripts cannot disagree about what is
being released.

## Tools

| file | what it does |
| --- | --- |
| `targets.toml` | the twelve target branches, their lineage, version scheme and URLs |
| `inventory.py` | resolves a checkout's in-path dependency closure, and the publish order |
| `naming.py` | source package → published crate name; `--verify` checks it per target |
| `targets.py` | validates and selects targets; `--tags` says what to tag; `--matrix` and `--env` feed CI |
| `stage.py` | carves a target into a publishable state |
| `publish.py` | publishes a staged target in dependency order |

`inventory.py` is the load-bearing one and is useful on its own — the closures
it reports are measured, not assumed, which is how the project knows the zed
release line is 31 crates, ce is 26, and which seven git dependencies the
closure needs replaced.

```sh
# what would be published for this branch?
python3 inventory.py --repo . --root gpui --root gpui_parley --summary

# every target, and the tag its branch must carry
python3 targets.py --tags --select all

# every target's closure, with names and collisions checked
python3 naming.py --verify --lineage-repo zed=. --lineage-repo ce=.tools/worktrees/wt-ce

# the whole union of published names
python3 naming.py --table

# stage a branch and see what it produced
python3 stage.py --target bite_v1.20.2 --source src
python3 publish.py --stage src --list
python3 publish.py --stage src --dry-run
```

## Releasing

The version is the version the branch tip is **tagged** with, so the tag is the
release, and pushing it is what publishes:

```sh
python3 targets.py --tags --select all       # what each branch must be tagged
git tag -a bite_1.21.0 -m bite_1.21.0 && git push origin bite_1.21.0
```

`.github/workflows/release.yml` runs on that push: it resolves the tag back to
its target (`targets.py --for-tag`), runs the gate on the branch, and publishes
with the token in the `crates-io` environment — no `cargo login`, no local
credential file, and the release is reproducible from the tag alone. Staging
refuses a release target whose tip is untagged and names the command, so the tag
is the only thing that can name the version and a release cannot drift from it.

Everything else is a dispatch, which is also the dry-run path:

```sh
# stage and verify a target without uploading
gh workflow run release.yml -f target=bite_v1.21.0 -f dry_run=true

# a rolling target has no release tag to push, so it is named explicitly
gh workflow run release.yml -f target=bite_ce_main -f confirm=bite_ce_main -f dry_run=false
```

**Configure the `crates-io` environment with required reviewers before relying on
the tag trigger.** The environment is the approval gate and the only place the
token is reachable; without reviewers, pushing a tag publishes unattended. Dry
runs exist so a target can be validated before its tag is pushed at all.

## CI

- `.github/workflows/ci.yml` — pull requests and `main`: validate the table and
  naming rule, then stage one target per lineage through the gate. Dispatch with
  `all` for every branch.
- `.github/actions/gate/action.yml` — the gate, shared with the release
  workflow: system deps, resolve, check, clippy `-D warnings`, test, package
  file sets, publish dry run.
- `.github/workflows/release.yml` — a `bite_*` tag push, or manual dispatch.
  Dispatch takes the target, a confirmation that must repeat it for a real
  publish, and `dry_run` (default true); a tag push resolves the target from the
  tag and publishes. Both paths run the gate, and only the publish job names the
  `crates-io` environment, so `CARGO_REGISTRY_TOKEN` reaches exactly one step of
  one job behind an approval.

The workflows need `SOURCE_READ_TOKEN` if the source repository is private, and
`CARGO_REGISTRY_TOKEN` as a secret on the `crates-io` environment for a real
publish. Neither is reachable from a pull request: the release workflow has no
pull-request trigger. A local `cargo login` is now only for hand runs from a
prepared stage tree, which is how the first release was done.

## Validated so far

Both lineages stage from a realistic CI checkout. `bite_v1.20.2` was cloned at
`--depth 1` with tags fetched the way the workflow does, staged to 31 crates at
`1.20.200` (39 dependency rewrites, 9 git substitutions, 5 licences copied),
splitting into 30 native, 1 wasm-only and 24 with tests; `bite_ce_main` staged to
26 crates, 35 rewrites, reporting 4 withheld rather than failing.

Also verified: `cargo metadata` resolves the whole staged workspace for both
lineages — 775 packages for ce, including the crates outside its closure that
depend on renamed ones; every staged package keeps its original lib name
(`bite-gpui` exports `gpui`, `bite-gp-platform` exports `gpui_platform`,
`bite-gp-util` exports `util`); `publish.py --dry-run` packages and compiles a
leaf crate in isolation; the version scheme's edges (amendment bounds,
prereleases, patch overflow); and that an untagged branch is refused with the
exact command to fix it.

The gate has also found a real source defect and it is fixed on the branch:
ce's `gpui_wgpu` example called `Bounds::centered` without the `BoundsExt` trait
in scope, which every other example gets through gpui's prelude. It is ce-only —
those examples do not exist on the zed-lineage branches. A rolling branch pays
nothing for a source fix; a tagged release branch pays an amendment bump and a
new tag (DESIGN §6).

Staging and the gate between them have found eight things a design document
would not have: the lib name is derived from the package name unless pinned,
`collections` already carried a version that had to be overridden rather than
added, ce declares its path dependencies in each member instead of the workspace
table, `[profile.dev.package]` lists package names that must not be rewritten,
the version cannot be derived from a tag the checkout does not have, the gate was
checking with default features where the project checks with all of them, and
crates *outside* the closure that depend on renamed ones break the whole
workspace even though they are not published.

Not yet run: the compile gates locally (this host is at 98% disk and a full
closure build needs tens of GB — CI runs them) and the wasm32 check for
`gpui_web` (DESIGN §10).

## Published

Every crate of `bite_v1.20.2` is on crates.io at `1.20.203` — the zed lineage's
31 names. The first release was published by hand from a prepared stage tree,
which is why `publish.py` writes the `.cargo/config.toml` a plain
`cargo publish` needs; it was rate-limited partway and resumed with
`--only <crate>`, which is the behaviour that makes a multi-crate release
survivable. Releases from here go through the workflow instead.

`bite_v1.21.0` is the next one, and it is staged and clean at `1.21.0`: 31
crates, 39 dependency rewrites, 5 licences copied, 30 native and 1 wasm-only
(`bite-gp-web`), 24 with tests, none withheld, no unresolved or dev-only git
dependencies. Its name set is **identical** to `bite_v1.20.2`'s, so it publishes
new *versions* of names that already exist rather than new names — the 30-version
burst and one a minute (DESIGN §11), not the five-name burst that made the first
release take hours. It needs no `0.0.0-reserved` step either, for the same
reason: every name it depends on is already on the registry.
