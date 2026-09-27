# bite-gpui distribution

Publishes the `bite_*` branches as installable crates: for each target branch,
extract its GPUI crates set — the crates it depends on — out of the monorepo,
rename them, and release them to crates.io as `bite-gpui` / `bite-gp-*` (zed
lineage) and `bite-gpui-ce` / `bite-gp-ce-*` (community-edition lineage).

The design is the [architecture specification](DESIGN.md), which is the map: the
chapters are in `docs/`, and they keep their section numbers, so `§6` is `§6`
wherever it is cited — here, in the chapters, and in the comments in
`targets.toml` and `pipeline/`. Start with [docs/decisions.md](docs/decisions.md),
where each decision carries its status, including the one with legal weight.

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

## Layout

```
pipeline/        the release pipeline, run in order
  targets.py       validate the table, select targets, feed CI
  inventory.py     the GPUI crates set a checkout reaches, and the publish order
  naming.py        source package → published crate name, verified per target
  stage.py         carve a target into a publishable state
  publish.py       publish a staged target, in dependency order
  tag_release.py   resolve a branch to the tag it must carry, push it
  sync_release_target_options.py
                   rewrite the dispatch options from the table and branches
checks/          what a staged tree must pass, plus one check on the repository
  check_reads.py       packaged file sets, and paths that escape the crate
  apple_build_check.py the Apple crate's cbindgen header is generated
  test_isolated.py     each tarball compiles with no siblings, as crates.io sees it
  workflows.py         the CI wiring: actionlint over the workflows, and a pass over
                       the actions' own inputs and outputs
.github/         ci.yml, verify.yml, tag.yml, release-branch.yml, release.yml,
                 sync-release-target-options.yml, actions/verify/ (over preflight,
                 code-checks and build-checks) and actions/build-env/ (the system
                 packages, python, toolchain)
targets.toml     the twelve target branches, the table the pipeline reads
docs/            the design by chapter: contract (§3–§6), staging (§7–§9),
                 verification (§10), release (§11–§12), decisions (§13)
DESIGN.md        the index and the map over those chapters
README.md  rust-toolchain.toml
```

The scripts in `pipeline/` import each other by bare name and add their own
directory to `sys.path`, so they have to stay together. Most of the `checks/`
scripts take the stage as an argument and read nothing relative to themselves, so
they run from the stage like `python3 "$GITHUB_WORKSPACE/checks/check_reads.py"
--stage .`; `workflows.py` is the one that reads the repository instead, so it runs
from the repository root.

## Pipeline

```
targets.toml
    │
    ├─ pipeline/targets.py ──► validate the table, build the CI matrix
    ├─ pipeline/naming.py  ──► source package → published crate name, verified per target
    │
    ├─ pipeline/stage.py ───► mutate a throwaway checkout of the branch in place:
    │                          rename packages, pin lib names, set versions,
    │                          raise publish, rewrite workspace deps,
    │                          substitute git deps, licence + provenance
    │                          └─► dist-stage.json   (the handoff)
    │
    └─ pipeline/publish.py ─► dependency-ordered publish from the report,
                              resumable, with local-graph dry runs
```

The report is what ties the last two together: `pipeline/publish.py` takes the order,
names and versions from it, so the two scripts cannot disagree about what is
being released.

## Tools

| file | what it does |
| --- | --- |
| `targets.toml` | the twelve target branches, their lineage, version scheme and URLs |
| `pipeline/inventory.py` | resolves the GPUI crates set a checkout reaches by path dependency, and the publish order |
| `pipeline/naming.py` | source package → published crate name; `--verify` checks it per target |
| `pipeline/targets.py` | validates and selects targets; `--tags` says what to tag, `--for-tag` and `--for-branch` resolve the other way, `--matrix` and `--env` feed CI |
| `pipeline/stage.py` | carves a target into a publishable state |
| `pipeline/tag_release.py` | resolves a branch to the tag it must carry, refuses to move one, pushes it |
| `pipeline/sync_release_target_options.py` | rewrites the dispatch `options:` lists from the table and a branch list, filtered to release branches; `--check` reports drift |
| `pipeline/publish.py` | publishes a staged target in dependency order |
| `checks/check_reads.py` | every file a package names is packaged, and none escapes the crate |
| `checks/apple_build_check.py` | the Apple crate's cbindgen shader header is generated |
| `checks/test_isolated.py` | each tarball compiles alone, the way crates.io ships it |
| `checks/workflows.py` | lints the CI wiring: actionlint over the workflows, and the actions' inputs and outputs |

`pipeline/inventory.py` is the load-bearing one and is useful on its own — the sets
it reports are measured, not assumed, which is how the project knows the zed
release line is 31 crates, ce is 26, and which seven git dependencies the
set needs replaced.

```sh
# Run from the zed clone root, where `.dist/` is; the commands that read a
# *checkout of a branch* say so, because that is a different tree.

# what would be published for this branch? (from a checkout of it)
python3 .dist/pipeline/inventory.py --repo . --root gpui --root gpui_parley --summary

# what tag does a branch need, and does it exist yet?
python3 .dist/pipeline/tag_release.py --branch bite_v1.21.0 --repo <a checkout of it>

# every target, and the tag its branch must carry
python3 .dist/pipeline/targets.py --tags --select all

# every target's GPUI crates set, with names and collisions checked
python3 .dist/pipeline/naming.py --verify --lineage-repo zed=. --lineage-repo ce=.tools/worktrees/wt-ce

# the whole union of published names
python3 .dist/pipeline/naming.py --table

# stage a branch and see what it produced (from the directory holding the checkout)
python3 .dist/pipeline/stage.py --target bite_v1.20.2 --source src
python3 .dist/pipeline/publish.py --stage src --list
python3 .dist/pipeline/publish.py --stage src --dry-run
```

`naming.py` and `stage.py` take their checkout as a path, so they resolve it
against the current directory — the `--lineage-repo` paths above are relative to
the zed clone, which is why these run from its root rather than from `.dist/`.

## Releasing

`release-branch.yml` is the entry point: one dispatch per branch does the whole
sequence — resolve the target, tag the branch, verify it, and release it. A
dispatch is a report unless told otherwise:

```sh
gh workflow run release-branch.yml -f branch=bite_v1.14.x                      # report: which tag, does it exist
gh workflow run release-branch.yml -f branch=bite_v1.14.x -f dry_run=false -f confirm=bite_v1.14.x
```

The version is the version `targets.toml` declares for the target, so a branch
that has not been released already names its next version and there is nothing to
bump; to release new content for a target that is already published, bump its
`amendment` in `targets.toml` first (DESIGN §6). The three steps underneath are
workflows of their own, which is what to dispatch directly when only one of them
is what you are doing.

Verification takes minutes and publishing must not wait for it, so verifying the
target is its own dispatch that leaves the receipt a release reads:

```sh
gh workflow run verify.yml -f target=bite_v1.21.0
```

Then release. The version is the version the branch tip is **tagged** with, so a
release is a tag. The `Tag` workflow creates it and starts the publish:

```sh
gh workflow run tag.yml -f branch=bite_v1.21.0                     # report: which tag, does it exist
gh workflow run tag.yml -f branch=bite_v1.21.0 -f dry_run=false    # tag if needed, then publish
```

`branch` and `target` are `choice` lists of the **source repository's** branches —
`bite-gpui/bite-gpui` — not free text, so a typo cannot start a run against a
branch that does not exist, and `pipeline/targets.py --validate` fails if either list and
`targets.toml` ever disagree. (The dispatch dialog's *other* dropdown, "Use
workflow from", selects this repository, because that is where the workflow file
lives; it is not the branch being released.)

`tag.yml` resolves the branch to its target, derives the tag from the table
(`pipeline/tag_release.py`), pushes the tag when there is one to push, and dispatches the
publish. `dry_run` defaults to true and only reports — a report is an answer, not a
failure, so it exits zero and puts the verdict in its summary (`untagged`,
`already-tagged`, `conflict`, `rolling`).

**A branch whose tip already carries its tag is not an error**, and needs no flag:
that is the ordinary state of a release that did not finish, and finishing it is
publishing. Re-publishing a version that is already on crates.io is a no-op there,
so a re-run resumes rather than duplicates — which is why a rate limit or a tooling
bug is recovered by simply running the same command again. A **`conflict`** is the
one refusal, and it is not overridable: it means the tag names a *different* commit,
so the content under a published version changed, and the answer is to bump the
target's `amendment` (DESIGN §6) rather than to move a tag. A rolling target has no
tag at all, and `tag.yml` says so and dispatches the publish directly.

The tag is still the contract: staging refuses a release target whose tip is
untagged and names the command. But it is not a trigger, and it cannot be one from
here — the tags live in the source repository, and a `push: tags` trigger in this
one would be watching the wrong repository and could never fire. A tag pushed by
hand therefore starts nothing; run `tag.yml` for that branch, which is the same run
that would have pushed the tag itself, and it will find nothing to tag and publish.

A rolling target, or a bare dry run of the pipeline, goes straight to dispatch:

```sh
gh workflow run release.yml -f target=bite_ce_main -f confirm=bite_ce_main -f dry_run=false
gh workflow run release.yml -f target=bite_v1.21.0 -f dry_run=true
```

`release.yml` refuses a real publish without the receipt for that target and
version, and names the `verify.yml` command to produce it; a dry run is exempt,
because it publishes nothing. **`require_verify`** turns that check off and
publishes with a warning: it is for when the receipt itself is what is broken —
it has aged out, or the repository it lands in is misconfigured — not for
skipping the checks, and not for a first release of a name.

**Configure the `crates-io` environment with required reviewers.** It is the
approval gate and the only place the registry token is reachable, and it is the
last thing between a tagged release and crates.io.

If you want release-on-tag — pushing `bite_1.21.0` to the source repository and
having it publish — the trigger has to live *there*, since that is where the tags
are. A ten-line workflow in `bite-gpui/bite-gpui` that runs
`gh workflow run release.yml --repo bite-gpui/distribution -f target=...` is the
whole of it; `pipeline/targets.py --for-tag` is the lookup it would need.

## CI

- `.github/workflows/ci.yml` — pull requests and `main`: validate the table and
  naming rule, then stage one target per lineage through verification. Dispatch
  with `all` for every branch.
- `.github/workflows/ci.yml`'s `lint` job — `checks/workflows.py`, which runs
  actionlint over the workflow files and checks the wiring actionlint cannot see,
  the call sites inside the composite actions. It runs before `plan`, so a wiring
  mistake fails in seconds instead of after the matrix has staged a target.
- `.github/workflows/verify.yml` — stage one target and run the checks over it.
  The only definition of that sequence: `ci.yml` calls it for the targets a pull
  request stages, `release-branch.yml` calls it before it releases, and dispatching
  it directly is how a target is verified on its own. A run that leaves the receipt
  uploads `verified-<target>-<version>`, which `release.yml` reads.
- `.github/actions/build-env/action.yml` — what compiling the set needs, in
  one place: the Linux system packages, python 3.13 and the pinned toolchain. Both
  `verify.yml` and the release job prepare it, which is the point — `cargo publish`
  verifies a release by building the packaged crate as the root of its own build,
  so the release job compiles the set too.
- `.github/actions/verify/action.yml` — the three phase actions `verify.yml` runs,
  cheapest first: `preflight` (the stage report, `cargo metadata`, and the two
  static packaging checks — `checks/check_reads.py` and the Apple check,
  `checks/apple_build_check.py`, where the lineage has one), `code-checks` (clippy
  `-D warnings`, then the set's tests), and `build-checks` (the publish dry
  run, then the isolated build, `checks/test_isolated.py`).
- `.github/workflows/release-branch.yml` — the entry point, and what releases a
  branch in one dispatch. It resolves the target, tags the branch
  (`pipeline/tag_release.py`, the same script `tag.yml` runs), verifies the target
  by calling `verify.yml`, and dispatches `release.yml` — `tag.yml` and the
  verification it assumes, as one run. A dispatch is a report unless `dry_run` is
  off and `confirm` repeats the branch.
- `.github/workflows/tag.yml` — the manual release step. Dispatch it with a branch
  from the source repository; it resolves the tag, refuses to move one, pushes it,
  and dispatches `release.yml`.
- `.github/workflows/release.yml` — dispatch only, because the tags it would want
  to trigger on live in the source repository. It takes the target, a confirmation
  that must repeat it for a real publish, and `dry_run` (default true). It refuses
  a real publish without a verification receipt unless `require_verify` is turned
  off. Only its publish job names the `crates-io` environment, so
  `CARGO_REGISTRY_TOKEN` reaches exactly one step of one job behind an approval.
- `.github/workflows/sync-release-target-options.yml` — a scheduled run that
  keeps the dispatch `options:` lists in step with `targets.toml` and the source
  repository's branches (`pipeline/sync_release_target_options.py`, rewriting only
  those blocks so the workflows' comments survive), opening a pull request when
  one drifts. It filters the branch list to the release-branch shapes, so a
  working branch is never offered; `pipeline/targets.py --validate` is what fails
  when a release branch has no target.

One environment and three secrets. The environment is `crates-io`, holding
`CARGO_REGISTRY_TOKEN` for a real publish and (recommended) required reviewers.

The repo secrets are optional or required depending on which repository they read:

- **`SOURCE_READ_TOKEN`** — only needed if the source repository is private. Every
  checkout that reads it falls back to the run's own `github.token`, so an unset
  read token is not an error.
- **`SOURCE_WRITE_TOKEN`** with `contents: write` on the source repository — needed
  only when a run actually has a tag to push, which `tag.yml` decides after
  resolving the state: a dry run, a branch that is already tagged, and a rolling
  target all need read access at most. When it *is* needed it has no fallback,
  because `github.token` is scoped to *this* repository and cannot push a tag to
  another one. It must be a **repository** secret, not an environment secret: the
  tag job names no environment, so an environment secret is not visible to it. Both
  the workflow and `pipeline/tag_release.py` check and fail with that explanation rather
  than letting `actions/checkout` fail with `Input required and not supplied:
  token`.

None of them is reachable from a pull request: no workflow here has a
pull-request trigger. A local `cargo login` is only for hand runs from a prepared
stage tree, which is how the first release was done.

## Validated so far

Both lineages stage from a realistic CI checkout. `bite_v1.20.2` was cloned at
`--depth 1` with tags fetched the way the workflow does, staged to 31 crates at
`1.20.200` — since amended to `1.20.203` by the hand release (§6) — with 39
dependency rewrites, 9 git substitutions and 5 licences copied, splitting into 30
native, 1 wasm-only and 24 with tests; `bite_ce_main` staged to 26 crates, 35
rewrites, reporting nine withheld rather than failing (four blocked by `wgsl-rs`,
five more by depending on them — DESIGN §8).

Also verified: `cargo metadata` resolves the whole staged workspace for both
lineages — 775 packages for ce, including the crates outside the set that
depend on renamed ones; every staged package keeps its original lib name
(`bite-gpui` exports `gpui`, `bite-gp-platform` exports `gpui_platform`,
`bite-gp-util` exports `util`); `pipeline/publish.py --dry-run` packages and compiles a
leaf crate in isolation; the version scheme's edges (amendment bounds,
prereleases, patch overflow); and that an untagged branch is refused with the
exact command to fix it.

Verification has also found a real source defect and it is fixed on the branch:
ce's `gpui_wgpu` example called `Bounds::centered` without the `BoundsExt` trait
in scope, which every other example gets through gpui's prelude. It is ce-only —
those examples do not exist on the zed-lineage branches. A rolling branch pays
nothing for a source fix; a tagged release branch pays an amendment bump and a
new tag (DESIGN §6).

Staging and verification between them have found eight things a design document
would not have: the lib name is derived from the package name unless pinned,
`collections` already carried a version that had to be overridden rather than
added, ce declares its path dependencies in each member instead of the workspace
table, `[profile.dev.package]` lists package names that must not be rewritten,
the version cannot be derived from a tag the checkout does not have, the checks
were running with default features where the project checks with all of them, and
crates *outside* the set that depend on renamed ones break the whole
workspace even though they are not published.

Not yet run: the compile checks locally (this host is at 98% disk and a full
build of the set needs tens of GB — CI runs them) and the wasm32 check for
`gpui_web` (DESIGN §10).

## Published

Every crate of `bite_v1.20.2` is on crates.io at `1.20.203` — the zed lineage's
31 names. The first release was published by hand from a prepared stage tree,
which is why `pipeline/publish.py` writes the `.cargo/config.toml` a plain
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
