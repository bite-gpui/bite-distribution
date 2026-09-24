# bite-gpui distribution — design

Status: design for review. Nothing here is published yet; no crate name or
version has been claimed on crates.io.

## 1. What this publishes, and why

The rearchitected gpui exists as nine branches in two repositories. That is
useful to us and useless to anyone else: you cannot `cargo add` a branch, and
you cannot pin one meaningfully. This project turns each branch into a set of
installable crates, so that

```toml
# a zed-lineage retarget, matching upstream zed 1.20
bite-gpui = "1.20"

# the community-edition transplant
bite-gpui-ce = "0.20260922.1"
```

gives a consumer the same library they would get by vendoring the branch.

We follow the shape of `longbridge/gpui-kit/script/bump-gpui.ts`, which solves
the same problem for zed's `gpui-pre-*` line — fetch, carve the path-dep
closure, rename packages, prune what cannot be published, sort out licensing,
`cargo publish --dry-run`, then publish with resume. The one substantive change
is the input: gpui-kit walks a zed *commit*, we walk our own `bite_*` branches.
`iamnbutler/gpui-unofficial` is the second reference: it publishes zed releases
as `gpui-unofficial` (plus `*-gpui-unofficial` helpers), and its version
sequence — `1.20.2` stable, `1.21.0-pre` newest — is the precedent §6 follows.

## 2. Governing rule

From the migration project's rulings, and binding here:

> Crates named after upstream carry upstream's public surface unchanged.
> Anything a fork adds or changes lives in a fork-marked crate or an `*Ext`
> trait, and the fork's facade re-exports both. Decorate traits — do not fork
> them.

Two consequences shape this design.

**`[lib] name` is preserved; only the package name changes.** `bite-gpui` keeps
lib name `gpui`, `bite-gp-platform` keeps `gpui_platform`, `bite-gp-collections`
keeps `collections`. Every `use gpui::…` in an existing project keeps compiling;
only the line in `Cargo.toml` changes. This is what makes the published crates a
drop-in rather than a porting exercise.

**The two lineages do not share names.** The zed-lineage retarget and the
community-edition transplant contain *different code under the same upstream
crate names* — ce's `gpui_platform` carries ce's surface, the 1.14 retarget
carries 1.14's. They therefore publish into two name families (§5) rather than
into one family distinguished only by a version number. See §13.2 for the
alternative we rejected and why.

## 3. Inputs and provenance

Ten targets, one per branch. `.dist/targets.toml` is the source of truth and
`.dist/naming.py --verify` checks it against the manifests.

| target | lineage | publishes as | upstream | tag | publishes |
| --- | --- | --- | --- | --- | --- |
| `bite_v1.14.x` | zed | `bite-gp-*` | `1.14.2` | `bite_1.14.200` | `1.14.200` |
| `bite_v1.15.x` | zed | `bite-gp-*` | `1.15.1` | `bite_1.15.100` | `1.15.100` |
| `bite_v1.16.x` | zed | `bite-gp-*` | `1.16.3` | `bite_1.16.300` | `1.16.300` |
| `bite_v1.17.x` | zed | `bite-gp-*` | `1.17.2` | `bite_1.17.200` | `1.17.200` |
| `bite_v1.18.x` | zed | `bite-gp-*` | `1.18.1` | `bite_1.18.100` | `1.18.100` |
| `bite_v1.19.x` | zed | `bite-gp-*` | `1.19.2` | `bite_1.19.200` | `1.19.200` |
| `bite_v1.20.2` | zed | `bite-gp-*` | `1.20.2` | `bite_1.20.200` | `1.20.200` |
| `bite_v1.21.0-pre` | zed | `bite-gp-*` | `1.21.0-pre` | `bite_1.21.0-pre` | `1.21.0-pre` |
| `bite_master` | zed | `bite-gp-*` | — | — | CalVer |
| `bite_ce_main` | ce | `bite-gp-ce-*`, `bite-gpui-ce` | — | — | CalVer |

`upstream` is the zed release a branch retargets, recorded for provenance; the
released version is derived from it and declared by the branch's tag (§6).
`targets.py --tags` prints the tag each selected target must carry.

All ten live in one repository, `git@github.com:bite-gpui/bite-gpui.git`. They share
a common ancestor — ce is a fork of zed, not a separate lineage — but they have
diverged since, so what a target publishes differs even where the crate names are
the same. That is why the table declares a lineage per target instead of
inferring one from the remote: `bite_ce_main` forked at the community edition's
baseline and `bite_master` descends from zed's `main` rather than from any
release. One repository also means the workflows need a read token for one
repository, and the naming check needs two checkouts of it at two refs rather
than checkouts of two repositories.

Every published crate records its origin in the manifest, so a consumer can tell
what they actually got:

```toml
[package.metadata.bite]
source = "https://github.com/bite-gpui/bite-gpui"
branch = "bite_v1.20.2"
commit = "<sha>"
upstream = "1.20.2"
amendment = 0
original = "gpui"
```

## 4. Which crates

The closure is computed from the manifests, not from a list. `crates/gpui` (and
`crates/gpui_parley`, which nothing depends on but which consumers want to
select directly) are the roots; from there `.dist/inventory.py` walks in-checkout
path dependencies.

Two things make this less trivial than it sounds, and both are handled:

- **The workspace `members` list is not complete.** `crates/gpui_authoring`,
  `crates/gpui_engine`, `crates/gpui_engine_default`, `crates/gpui_apple` and
  others are absent from zed's root manifest. Cargo admits them because they are
  path dependencies of members, so discovery follows path edges instead of
  trusting the list.
- **Inherited paths are workspace-root relative.** `gpui = { path = "crates/gpui" }`
  in `[workspace.dependencies]` resolves against the workspace root, while a path
  written in a member resolves against that member. The inventory records which
  base each path came from.

Measured closures (normal + build reachability):

| target | crates | notes |
| --- | --- | --- |
| `bite_v1.14.x` | 31 | largest: also carries `media`, `sum_tree`, `zlog` |
| `bite_master` | 30 | 1.14 minus `media` |
| `bite_ce_main` | 26 | ce's own `gpui_ce_*` set replaces the vendored zed crates |

Union across targets: **50 source packages**. The release-line closure did not
change over the eight branches — 1.14 and 1.21.0-pre resolve to exactly the same
31 crates, and `bite_master` is that set minus `media` — but the gate re-measures
per materialized worktree rather than assuming it.

## 5. Published names

The zed lineage owns the short names.

| source package | published as | lib name |
| --- | --- | --- |
| `gpui` (the facade) | `bite-gpui` | `gpui` |
| `gpui_X` | `bite-gp-X` | `gpui_X` |
| anything else | `bite-gp-<name>` | unchanged |
| `gpui_util` | `bite-gp-gpui-util` | `gpui_util` |

The ce lineage prefixes `ce`, so a name never lies about which code it contains:

| source package | published as |
| --- | --- |
| `gpui-ce` (the facade) | `bite-gpui-ce` |
| `gpui_ce_X` (ce's own crate) | `bite-gp-ce-X` |
| `gpui_X` (the reference's crate) | `bite-gp-ce-gpui-X` |

Why ce's two families are spelled differently: ce contains *both* `gpui_platform`
(the reference's leaf) and `gpui_ce_platform` (ce's backend selector). Stripping
`gpui_` from both would collide on `platform`. Keeping the `gpui` token on the
reference's crates and dropping the redundant `ce` from ce's own crates is the
only split that stays mechanical and collision-free.

Why `gpui_util` is the single exception: the union contains both zed's `util`
and `gpui_util`, and the stripping rule sends both to `bite-gp-util`. zed's
`util` is the upstream-named crate and keeps the short name; `gpui_util` keeps
its full spelling. `--verify` re-derives this on every run rather than trusting
the comment.

Verified: 57 distinct (lineage, package) pairs → 57 names, no collisions, and no
name claimed by two lineages. `bite-gpui` and `bite-gpui-ce` are both unclaimed
on crates.io, but `gpui` itself is not — it is owned by upstream zed and has
been published seven times. That is the strongest argument for the prefix: the
name we are reimplementing is already taken, by the people we are
reimplementing.

One consequence of preserving lib names: both facades export lib `gpui`, so a
project depends on `bite-gpui` or `bite-gpui-ce`, never both. That is inherent —
each is a drop-in for the same crate — and it is the price of `use gpui::…`
continuing to compile.

## 6. Versions

Each upstream patch release gets **a hundred slots**. `1.20.2` publishes as
`1.20.200`; amendments to the same retarget take 201..299; `1.20.3` starts at
300. The gap is the point — an amendment can never collide with the next
release, versions still sort in upstream order, and no retarget will need
anything close to a hundred amendments. `targets.py` refuses a table that does
not fit the scheme (an upstream patch of 100 or more, an amendment outside
0..99).

**The version comes from a tag on our branch, not from zed's tag.** Every
release target's tip carries an annotated tag named `bite_` plus the published
version:

| branch | tag | publishes |
| --- | --- | --- |
| `bite_v1.14.x` | `bite_1.14.200` | `1.14.200` |
| `bite_v1.20.2` | `bite_1.20.200` | `1.20.200` |
| `bite_v1.21.0-pre` | `bite_1.21.0-pre` | `1.21.0-pre` |

This replaced an earlier design that derived the version from the upstream
`v1.20.2` tag's presence on the branch's ancestry. That failed in CI for a real
reason worth recording: `actions/checkout` fetches neither tags nor history at
the default depth, so the tag was simply absent, and even with
`fetch-depth: 0` the derivation would have needed the full history of a zed-sized
fork on every job. A tag **at the tip** needs no history at all — `git tag
--points-at HEAD` compares OIDs — so a depth-1 checkout suffices and the
workflow fetches only `refs/tags/bite_*`.

Three consequences, all deliberate:

- **A release target must be tagged to be staged.** The failure names the
  command to run: `git tag -a bite_1.20.201 -m bite_1.20.201`. Pushing a commit
  to a release branch therefore invalidates CI until the branch is re-tagged,
  which is the discipline that keeps the version a contract rather than a
  label: content cannot change without the version changing.
- **The tag is the version**, so nothing derives it at resolve time beyond the
  table's `upstream` and `amendment`. To release new content for an upstream
  release that is already published, bump `amendment`, push the commit, tag it.
- **The branch name is checked against the table without any git at all.**
  `bite_v1.20.2` must declare `1.20.2`; `bite_v1.14.x` may leave the patch open
  but must agree on `1.14`. `targets.py --validate` runs this, so a typo is
  caught in the `table` job in seconds.
- **Fixing the source costs a version bump on a release branch.** A new commit
  moves the tip, the tag no longer points at it, and staging refuses until the
  branch is re-tagged: the fix is `amendment += 1`, push, tag. Rolling targets
  pay nothing, because they are dated at staging time. That is the intended
  price of the version being a contract rather than a label, and it is the thing
  to weigh when the gate finds a source-level defect. ce's broken example was
  worth fixing under a rolling branch, where it cost nothing; the same fix on a
  release branch would want to be certain, because it is nine tags.

`1.21.0-pre` keeps its prerelease tag, because the release it previews is what
`1.21.0` will be; an amendment appends a numeric identifier (`1.21.0-pre.1`),
which semver orders above the bare prerelease.

**Rolling targets use CalVer: `0.YYYYMMDD.N`.** Neither `bite_master` nor
`bite_ce_main` has a release to name itself after, and a date-shaped tag would
have to be pushed daily just to keep CI green, so these derive from the date and
publish untagged. `N` is the amendment slot. The `0.*` rolling series and the
`1.*` release series coexist on the same crate name, which is deliberate: a
requirement like `bite-gpui = "1.20"` never silently resolves to a master
snapshot. The cost is that `*` prefers a release over the tip — see §13.3.

ce has a scheme of its own in flight — its prerelease workflow publishes
`<committed major + 1>.0.0-alpha.N`, so `gpui-ce` is on the `1.0.0-alpha.N` line
while its committed version is 0.2.2. Because the two lineages do not share
crate names (§5, §13.2), this project does not have to interoperate with it,
and does not.

## 7. Staging algorithm

`stage.py --target <name> --source <checkout>` rewrites a throwaway checkout of
the target branch **in place**, and writes a `dist-stage.json` report beside it.
It does not copy the closure into a synthetic workspace.

That is a deliberate choice with a measurement behind it: **zero** closure
crates are referenced by a direct member path — every one of them is reached
through `[workspace.dependencies]` inheritance. A copy would therefore have to
re-derive the entire workspace model (inherited `version`/`edition`/`lints`,
per-target tables, `[patch]`) and would diverge from it silently the first time
a branch changed shape. A checkout is ~100 MB without a target directory, so
materialising one per target is cheaper than getting the model right twice, and
it keeps cargo itself as the source of truth.

1. **Materialize** a checkout of the branch tip (CI checks the branch out
   directly; locally, `git worktree add --detach .dist/wt/<target> <branch>`).
2. **Measure** the closure with `inventory.py`, and resolve the version from the
   release tag at the branch tip (§6).
3. **Rename** each closure crate's `[package] name` — and **pin `[lib] name`**.
   These crates declare `[lib] path` without a name, so their lib name is
   *derived from the package name*. Without this step `bite-gpui` exports crate
   `bite_gpui` and every `use gpui::…` in the ecosystem stops compiling. The
   first staged run did exactly that, and `cargo metadata` is how it surfaced.
4. **Set the version** on every `[package]`, *overriding* rather than adding:
   `collections` already carried `version = "0.1.0"`, and leaving it there
   produced a dependency on 0.1.0 while the package became 1.21.0-pre. Cargo's
   resolver caught the mismatch across the whole workspace.
5. **Raise `publish`** — drop the crate-level `publish = false` (11 of the 1.14
   closure inherit zed's workspace default) and flip the workspace default to
   true.
6. **Rewrite every reference to a renamed crate**, in every manifest of the
   checkout, not only the closure's. Cargo resolves the whole workspace, so a
   crate that depends on one we renamed but is not itself published has to keep
   resolving: ce's `gpui_ce_elements`, `gpui_ce_tokio` and `gpui_ce_zed_util`
   are outside its closure, and leaving them alone fails the workspace with
   `no matching package named gpui-ce found`. The reference gains `package` (the
   published name) and `version` (the target version); a manifest outside the
   closure gets that and nothing else — no provenance, no licence, no bump.
7. **Replace git dependencies** that have a registry equivalent (§8), scoped to
   the closure and to whichever manifest actually declares them.
8. **License and provenance** (§9).
9. **Re-read the result and check it** (`verify_staged`): every renamed crate
   carries the right name, version and lib name; every dependency on one
   resolves to the published name and version; and **no manifest anywhere still
   names a package that was renamed**. That last check is what would have caught
   the `gpui_ce_elements` failure before the resolver did. A crate with no name
   in the naming rule is fatal; a withheld crate (§8) is reported, not fatal.

The report is the handoff: `publish.py` reads it for the order, names and
versions, so the two scripts cannot disagree about what is being released.

## 8. Dependency surgery

Every external dependency in the closure was checked against crates.io. The
closure needs git dependencies replaced by registry versions; one has no
registry equivalent.

| dep in the source | source | registry substitute |
| --- | --- | --- |
| `zed-font-kit` | `zed-industries/font-kit` | `zed-font-kit 0.14.1-zed` ✅ |
| `zed-scap` | `zed-industries/scap` | `zed-scap 0.0.8-zed` ✅ |
| `zed-xim` | `zed-industries/xim-rs` | `zed-xim 0.4.0-zed` ✅ |
| `async-tar` | `zed-industries/async-tar` | `async-tar 0.6.1` ✅ |
| `wasm_thread` | `zed-industries/wasm_thread` | `wasm_thread 0.3.3` ✅ |
| `proptest` | `proptest-rs/proptest` | `proptest 1.11.0` ✅ |
| `wgsl-rs` | `schell/wgsl-rs` | ❌ only `0.0.0-reserved` exists |

Staged against `bite_v1.21.0-pre`, that is nine rewrites: `proptest` and `scap`
in the workspace table, `font-kit` in three manifests, `wasm_thread` in two,
`xim` in one, `async-tar` in one.

The first three are zed's own forks published under `-zed` prereleases, so the
substitution is faithful. `calloop`, `wasm_thread`, `async-tar` and `proptest`
are upstream crates zed takes from a rev, so the substitution changes bytes and
wants a diff check before the first publish of each. `proptest` is only reached
through test features.

**A `[patch]` is not a substitution.** `calloop` reaches `gpui_linux` through
`[patch.crates-io]`, so the manifest already names a registry version and needs
no edit at all — cargo does not carry `[patch]` into a published manifest. The
stager reports those separately (`patched deps: calloop`) rather than counting
them as fixes. The consequence to keep in mind: for a patched dependency, the
gate builds the git source while consumers get the registry one.

**Per-target differences are real, not hypothetical.** `bite_v1.14.x` declares
`async-tar` as registry `"0.6"`; 1.20.2, 1.21.0-pre and master all take it from a
fork. A hard-coded substitution list would have been wrong for one group or the
other; the closure scan finds this per target.

**`wgsl-rs` blocks four of ce's crates.** It is a non-optional dependency of
`gpui_ce_render`, `gpui_ce_wgpu`, `gpui_ce_apple` and `gpui_ce_windows`, all of
which are in ce's closure, and cargo refuses to publish a crate whose dependency
has no version. Only `0.0.0-reserved` exists on crates.io, so there is nothing to
substitute. Options, in preference order: ask upstream to publish; vendor it into
our namespace with lib name `wgsl_rs` intact, so `use` sites are unchanged; or
leave ce unshippable, which is the current state.

What the pipeline does about it is deliberately not "fail":

- **staging withholds the crates and warns.** The tree still builds, because a
  git dependency resolves locally, so the affected crates are named in the
  report (`blocked`) and removed from the publish order. Staging succeeds, so CI
  can check the other twenty-two ce crates instead of stopping at a known
  blocker. `stage.py --strict` restores the hard failure for anyone who wants it.
- **the dry run skips what is withheld**, and prints why.
- **a release refuses.** `publish.py` will not publish a target with withheld
  crates unless `--allow-partial` is passed deliberately: a release is all of its
  crates or none of them, and ce's facade depends on the withheld ones.

A branch-level failure that hides twenty-two crates' worth of checking is worse
than a warning that names four.

One crate reaches outside `crates/`: `perf` lives in `tooling/perf` and is pulled
in by `util_macros`. It is copied like any other crate and published as
`bite-gp-perf`.

## 9. Licensing and attribution

Measured across the 50-crate union: **44 Apache-2.0, 4 GPL-3.0-or-later, 2
inherited from their workspace.**

The GPL crates are `path`, `zlog`, `ztracing`, `ztracing_macro` — zed licenses
these under GPL-3.0-or-later, and they are load-bearing: `gpui` depends on
`path` and `ztracing`. Publishing them is permissible (GPL allows
redistribution with source and license intact), and GPL-3.0 is compatible with
Apache-2.0, so an Apache-2.0 crate may depend on them. But it means the
published graph is GPL at those nodes, and the facade's combined work inherits
that. **This is a licensing decision for the user, not a technical one** — see
§13.4. The alternatives are publishing them GPL-labelled as designed, or
removing the dependency so the published graph is Apache-only.

What the stager does for every crate:

- copies the licence text the manifest declares, from the repository root:
  `LICENSE-APACHE` for Apache crates (7 of the 1.14 closure declare Apache
  without carrying the file) and `LICENSE-GPL` for the GPL ones (`path` and
  `zlog` each lack one of the two);
- writes a `NOTICE` for Apache crates naming the upstream project, the branch,
  the commit and the fact that the crate was modified and renamed — Apache-2.0
  §4(b) requires modified files to carry a prominent notice that they were
  changed. GPL crates do not get one; their licence is already in the tree;
- records the origin in `[package.metadata.bite]` (§3).

Not yet done, and worth deciding with §13.4: per-file licence headers, and the
copyleft audit as a *gate* rather than a measurement. Today the GPL crates are
reported by the closure scan (they show up in the stage report's `license`
field) but nothing fails on them.

## 10. Gates

Run by `.github/actions/gate/action.yml` against the staged checkout, per
target, before anything is published. All of them use `--all-features`: the
migration project gated the closure with default features and with all of them,
and only the latter compiles the code behind `bench-support` and `profiler`.
With default features that code is reported dead, which is how the gate first
failed — on `gpui_authoring::Window::present_if_needed`, whose only callers are
in `bench_context` and `profiler::hang`.

1. `targets.py --validate` and `naming.py --verify` — the table is well formed,
   and every closure crate has a name that no other crate needs (§12).
2. `cargo metadata --format-version 1` — the whole workspace resolves. Cheap,
   and it is what caught the stale-version rename in §7 step 4.
3. `cargo check -p <native closure> --all-targets --all-features` — zero warnings.
4. `cargo clippy -p <native closure> --all-targets --all-features -- -D warnings`.
5. `cargo test -p <native closure> --all-features`.
6. `cargo package --list --allow-dirty` for every publishable crate — the file
   set that would be uploaded, which is where a missing licence or a `build.rs`
   reading an excluded file shows up.
7. `publish.py --dry-run` — packages each crate and compiles it in isolation,
   which is the only check that the published artifact builds. `--allow-dirty`
   is required throughout because staging rewrites the tree in place.

The closure is passed as explicit `-p` flags rather than `--workspace`: the
checkout carries all ~250 zed crates, and checking them is neither wanted nor
affordable on every push. Withheld crates (§8) are still checked — they build,
they just cannot be published — so the gate covers 55 of the 57 names in the
union.

**System packages are the union of two lists**, and the first gate run failed for
want of the second. ce's publish job installs nine packages, which was enough to
*package* crates but not to *link test binaries*: the run died at the linker with
`unable to find library -lX11-xcb` while linking `gpui_macros`'s `render_test`,
which pulls the whole stack. zed keeps its Debian/Ubuntu list in `script/linux`,
and that is the source of truth for the rest — minus its gtk and webkit entries,
which are for editor crates the closure does not contain.

**The gate is bounded by the runner, and two settings keep it inside that.**
A debug build of the closure exhausted a runner's disk, and the linker died with
a bus error — on Linux, the signal a process gets when the file-backed page it
is writing to cannot be extended. So:

- `CARGO_PROFILE_DEV_DEBUG=0` and `CARGO_PROFILE_TEST_DEBUG=0` override the
  branch's `debug = "limited"`. Nothing here is debugged and every artifact is
  thrown away; turning debug information off shrinks the link step by about an
  order of magnitude, and shortens the build, which was the other complaint.
- the test step uses `--lib --tests` rather than `--all-targets`, because
  `cargo test`'s default selection also *builds every example* and the facade
  declares twenty-five of them, each linking the whole stack. Examples are still
  type-checked by check and clippy, which do not link; what is given up is
  verifying that they link, which no consumer ever does.
- the test step covers only the crates that declare tests (24 of the 30 in
  1.20.2), computed from the sources rather than listed. A test binary links the
  whole stack, so testing a crate that has none is pure cost; its lib is still
  checked.

**The wasm-only crates are excluded from the native run and are not yet checked
at all.** `gpui_web` includes its modules under
`cfg(any(target_family = "wasm", test))` while their dependencies (`gpui_engine`,
`gpui_platform`) are declared only under the wasm dependency table, so on Linux
its lib compiles to nothing and its lib *test* target cannot resolve those
imports. zed's own CI does not check it natively either. A `wasm32-unknown-unknown`
check is the missing piece; zed's recipe for it is a nightly toolchain with
`-Zbuild-std` and `-C target-feature=+atomics,+bulk-memory,+mutable-globals`.
This is the one published crate family the gate does not build, and it is the
next thing to fix here.

A target that fails any gate is not publishable. Gate failure is per target, so
`bite_v1.18.x` failing does not hold back `bite_v1.20.2`.

## 11. Publish order and rate limits

`publish.py --stage <dir>` reads the stage report, publishes in dependency
order, and handles the three things a bare loop gets wrong.

**Verifying a first release.** `cargo publish` resolves a dependency that has a
`version` from the registry, not from its `path`, so a dependency-ordered *first*
release fails at the second crate: its freshly-versioned predecessor has not
been uploaded yet. For dry runs, every crate in the set is patched into the
temporary verification resolution
(`--config patch.crates-io.<name>.path=…`), so each tarball is still packaged and
compiled, but against the local release graph instead of stale registry
versions. The patches are command-line only and never reach a published
manifest. ce's release recipe does the same, and needed it for the same reason.

**Resuming.** A version already on crates.io is skipped, so an interrupted run
continues rather than restarting, and `--only <crate>` re-runs from a failure
without republishing what already landed. crates.io versions are immutable, so
this is the only way a multi-crate release is sane.

**Visibility.** crates.io accepts an upload before its API reports the version,
and the next crate in the order depends on the one just uploaded, so the
publisher waits for the registry to acknowledge each upload before continuing.

Two caveats, both inherited from ce's own release and both deliberate:
`bite-gpui` and `bite-gpui-ce` are published with `--no-verify`, because the
facades have test-only crate cycles with the platform crates that cargo cannot
represent in one verification lockfile — the workspace-wide `cargo check` in the
gate covers the compilation that skips. And the order comes from the manifests
rather than a hand-kept list, because the two lineages and eight release targets
have different leaf sets.

## 12. CI

`.github/workflows/ci.yml` — pull requests, pushes to `main`, and dispatch:

- **table** validates `targets.toml`, then runs the naming rule against a real
  closure per lineage, so a new crate that would land on a taken name fails long
  before a release rather than during one;
- **plan** turns the selector into a matrix via `targets.py`;
- **stage** (matrix) checks the target branch out, stages it, and runs the gate.
  A pull request stages one target per lineage (`default`); dispatch with `all`
  when a change could affect every branch. Ten cold builds of gpui on every push
  is hours of CI for a change to a script.

`.github/actions/gate/action.yml` is the gate itself, shared by both workflows,
so "the release workflow runs the same checks as a pull request" holds by
construction rather than by review.

Both workflows cache the cargo registry and git checkouts under a key built from
the lockfile, with a shared restore prefix. The first runs spent most of their
time downloading dependencies, and while different targets have different
lockfiles — so the *build* cache cannot be shared — the crates they download are
almost the same.

`.github/workflows/release.yml` — manual `workflow_dispatch` taking the target
name, a confirmation input that must repeat it, and `dry_run` defaulting to
true. The publish job runs behind the `crates-io` environment (add required
reviewers and the token there) under a single `registry-publish` concurrency
group, because the eight release targets share crate names and crates.io
versions are immutable. It does not re-run the full gate: that already passed on
the same commit, and holding the registry lock through a test suite is how a
release ends up half done.

Adding a push trigger for a branch is a one-line change once that target's dry
runs are trustworthy; it is deliberately not there yet.

## 13. Open decisions

### 13.1 Where the project lives
`.dist/` is ignored in the zed clone (like `.tools/`) and currently unpublished.
For CI it needs a home. Recommended: its own repository
`git@github.com:bite-gpui/dist.git`, since publishing is a separate concern from
the architecture work and its history should not be tangled with it. The
alternative is a directory in the existing `bite-gpui/tools` repo.

### 13.2 Two namespaces — **decided: two**

Settled: the ce transplant publishes as `bite-gp-ce-*` with `bite-gpui-ce` as its
facade, and the branch lives in the main repository alongside the zed-lineage
branches. The reasoning is worth keeping, because the evidence arrived late:

**ce already publishes sixteen crates of its own** — `gpui-ce` (0.2.2) plus
`gpui_ce_platform`, `gpui_ce_util`, `gpui_ce_collections`, `gpui_ce_scheduler`,
`gpui_ce_sum_tree`, `gpui_ce_media`, `gpui_ce_refineable`,
`gpui_ce_derive_refineable`, `gpui_ce_linux`, `gpui_ce_macos`, `gpui_ce_windows`,
`gpui_ce_web`, `gpui_ce_wgpu`, `gpui_ce_macros`, `gpui_ce_shared_string` — from
its own workflows, in its pre-rearchitecture layout. What ce does *not* publish
is exactly the new layer: `gpui_ce_types`, `gpui_ce_apple`, `gpui_ce_render`,
and the reference's `gpui_types`, `gpui_platform`, `gpui_engine`,
`gpui_engine_default`, `gpui_authoring`, `gpui_runtime`, `gpui_parley`.

So ce is not an unpublished fork sitting next to us; it is a fork with a live
distribution. Sharing one namespace with the zed lineage would put two different
`gpui_platform`s under one crate name, told apart only by whether the version
looks like a zed release, and a version range spanning both would silently mix
them. The cost is that a ce user writes `bite-gpui-ce` instead of `bite-gpui`,
and its shared crates read `bite-gp-ce-gpui-platform`.

Still ce's call, and outside this project: whether ce's new crates are *also*
published under ce's existing names (`gpui_ce_platform` at a new version, new
`gpui_types`, …) so ce's current users get the rearchitecture. If that happens,
ce's `release.yml` and `prerelease.yml` and this workflow would be publishing
into one namespace, and would need the `registry-publish` concurrency group and
one owner per name.

### 13.3 What `bite_master` publishes as
Recommended: the CalVer rolling series, so a release requirement never resolves
to a master snapshot. The alternative is a prerelease of the forthcoming release
(`1.22.0-master.YYYYMMDD`), which makes master sort newest but invents a release
number that upstream may never use. If `bite_master` should simply not be
published, that is also a clean answer — say so and it comes out of
`targets.toml`.

### 13.4 The GPL crates
`path`, `zlog`, `ztracing`, `ztracing_macro` are GPL-3.0-or-later and are in
`gpui`'s dependency graph. Publish them GPL-labelled (recommended; it is what
zed does, and GPL-3.0 is Apache-compatible), or eliminate the dependency so the
published graph stays Apache-only? This is the only decision here with legal
weight.

### 13.5 Crate metadata and ownership
The crates need `description`, `keywords`, `categories`, `homepage`, and a
`repository`. In the 1.14 closure, 18 of 31 have no description, 29 have no
per-crate README (only `gpui` does), most set no `repository` at all and 3 still
point at `zed-industries/zed`. Recommended: point `repository` at the bite-gpui
repository and state the upstream origin in `description` (e.g. "… — zed's
GPU-accelerated UI framework, rearchitected; built from zed v1.20.2"). A
crates.io owner account and token are also needed before the first publish.
