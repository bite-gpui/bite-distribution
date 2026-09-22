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
| `targets.py` | validates and selects targets; emits the CI matrix and `--env` lines |
| `stage.py` | carves a target into a publishable state |
| `publish.py` | publishes a staged target in dependency order |

`inventory.py` is the load-bearing one and is useful on its own — the closures
it reports are measured, not assumed, which is how the project knows the zed
release line is 31 crates, ce is 26, and which seven git dependencies the
closure needs replaced.

```sh
# what would be published for this branch?
python3 inventory.py --repo . --root gpui --root gpui_parley --summary

# every target's closure, with names and collisions checked
python3 naming.py --verify --lineage-repo zed=. --lineage-repo ce=.tools/wt-ce

# the whole union of published names
python3 naming.py --table

# stage a branch and see what it produced
git -C .. worktree add --detach wt/bite_v1.20.2 bite_v1.20.2
python3 stage.py --target bite_v1.20.2 --source wt/bite_v1.20.2
python3 publish.py --stage wt/bite_v1.20.2 --list
python3 publish.py --stage wt/bite_v1.20.2 --dry-run
```

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

`bite_v1.21.0-pre` was staged end to end: 31 crates, 39 dependency rewrites (9 of
them git substitutions), 5 licences copied, one patched dependency; `cargo
metadata` resolved the whole workspace; every staged package kept its original
lib name (`bite-gpui` exports `gpui`, `bite-gp-platform` exports `gpui_platform`,
`bite-gp-util` exports `util`, `bite-gp-gpui-util` exports `gpui_util`); and
`publish.py --dry-run` packaged and compiled a leaf crate in isolation.

`bite_ce_main` was staged too: 26 crates, 32 dependency rewrites, no problems.
It stops at the one real blocker — `wgsl-rs` has no registry version, so four of
its crates cannot be published yet (DESIGN §8). That failure is the check
working, not a defect.

Staging found four bugs that a design document would not have: the lib name is
derived from the package name unless pinned, `collections` already carried a
version that had to be overridden rather than added, ce declares its path
dependencies in each member instead of the workspace table, and
`[profile.dev.package]` lists package names that must not be rewritten.

Not yet run: the gate on a branch that has never been staged, and any real
publish.
