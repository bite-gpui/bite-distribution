# bite-gpui distribution

Publishes the `bite_*` gpui branches as installable crates: for each target
branch, carve the path-dependency closure out of the monorepo, rename the
packages, and release them to crates.io as `bite-gpui` / `bite-gp-*` (zed
lineage) and `bite-gpui-ce` / `bite-gp-ce-*` (community-edition lineage).

The design is in [DESIGN.md](DESIGN.md). Read §13 of it first — it lists the
decisions still open, one of which has legal weight.

All ten target branches live in one repository, `Vanuan/bite-gpui`: eight
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
| `targets.toml` | the ten target branches, their lineage, version scheme and URLs |
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
python3 naming.py --verify --lineage-repo zed=. --lineage-repo ce=.tools/wt-ce

# the whole union of published names
python3 naming.py --table

# stage a branch and see what it produced
python3 stage.py --target bite_v1.20.2 --source src
python3 publish.py --stage src --list
python3 publish.py --stage src --dry-run
```

## Releasing

The version is the version the branch tip is **tagged** with, so a release is:

```sh
python3 targets.py --tags --select default      # what the tag should be called
git tag -a bite_1.20.200 -m bite_1.20.200 && git push origin bite_1.20.200
```

Staging refuses to run for a release target whose tip is untagged, and names the
command. That is deliberate: a commit on a release branch cannot ship without the
version changing, and the version is what a consumer pins.

## CI

- `.github/workflows/ci.yml` — pull requests and `main`: validate the table and
  naming rule, then stage one target per lineage through the gate. Dispatch with
  `all` for every branch.
- `.github/actions/gate/action.yml` — the gate, shared with the release
  workflow: system deps, resolve, check, clippy `-D warnings`, test, package
  file sets, publish dry run.
- `.github/workflows/release.yml` — manual dispatch with a confirmation input,
  `dry_run` defaulting to true, and the real publish behind the `crates-io`
  environment.

The workflows need `SOURCE_READ_TOKEN` if the source repository is private, and
`CARGO_REGISTRY_TOKEN` in the `crates-io` environment for a real publish.

## Validated so far

Both lineages stage from a realistic CI checkout. `bite_v1.20.2` was cloned at
`--depth 1` with tags fetched the way the workflow does, staged to 31 crates at
`1.20.200` (39 dependency rewrites, 9 git substitutions, 5 licences copied);
`bite_ce_main` staged to 26 crates, 32 rewrites, reporting 4 withheld rather than
failing.

Also verified: `cargo metadata` resolves the whole staged workspace; every
staged package keeps its original lib name (`bite-gpui` exports `gpui`,
`bite-gp-platform` exports `gpui_platform`, `bite-gp-util` exports `util`);
`publish.py --dry-run` packages and compiles a leaf crate in isolation; the
version scheme's edges (amendment bounds, prereleases, patch overflow); and that
an untagged branch is refused with the exact command to fix it.

Staging and the gate between them have found six things a design document would
not have: the lib name is derived from the package name unless pinned,
`collections` already carried a version that had to be overridden rather than
added, ce declares its path dependencies in each member instead of the workspace
table, `[profile.dev.package]` lists package names that must not be rewritten,
the version cannot be derived from a tag the checkout does not have, and the gate
was checking with default features where the project checks with all of them.

Not yet run: the compile gates locally (this host is at 98% disk and a full
closure build needs tens of GB — CI runs them), the wasm32 check for `gpui_web`
(DESIGN §10), and any real publish.
