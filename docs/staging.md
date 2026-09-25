# Staging

§7–§9 of the [bite-gpui distribution design](../DESIGN.md). Sections keep
their numbers across all of these files, so `§6` means the same thing here, in
the other chapters, and in `targets.toml`.

How a branch is turned into a publishable tree: the algorithm, the
dependency surgery it does, and the licences and provenance it writes.

## 7. Staging Algorithm

`pipeline/stage.py --target <name> --source <checkout>` rewrites a throwaway checkout of
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
2. **Measure** the closure with `pipeline/inventory.py`, and resolve the version from the
   release tag at the branch tip ([§6](contract.md)).
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
   crate that depends on a renamed one but is not itself published has to keep
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

The report is the handoff: `pipeline/publish.py` reads it for the order, names and
versions, so the two scripts cannot disagree about what is being released.

## 8. Dependency Surgery

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

The `source` column is the git URL the branch points at, which is not always the
crate's own home: `async-tar`'s URL is `zed-industries/async-tar`, but what it
publishes is upstream's `async-tar`, and the substitute above is the registry
release of it.

The first three are zed's own forks published under `-zed` prereleases, so the
substitution is faithful. `calloop`, `wasm_thread`, `async-tar` and `proptest`
are upstream crates taken from a rev, so the substitution changes bytes and wants
a diff check before the first publish of each. `proptest` is only reached through
test features.

**A `[patch]` is not a substitution.** `calloop` reaches `gpui_linux` through
`[patch.crates-io]`, so the manifest already names a registry version and needs
no edit at all — cargo does not carry `[patch]` into a published manifest. The
stager reports those separately (`patched deps: calloop`) rather than counting
them as fixes. The consequence to keep in mind: for a patched dependency, the
verification builds the git source while consumers get the registry one.

**Per-target differences are real, not hypothetical.** `bite_v1.14.x` declares
`async-tar` as registry `"0.6"`; 1.20.2, 1.21.0-pre and master all take it from a
fork. A hard-coded substitution list would have been wrong for one group or the
other; the closure scan finds this per target.

**`wgsl-rs` blocks four of ce's crates.** It is a non-optional dependency of
`gpui_ce_render`, `gpui_ce_wgpu`, `gpui_ce_apple` and `gpui_ce_windows`, all of
which are in ce's closure, and cargo refuses to publish a crate whose dependency
has no version. Only `0.0.0-reserved` exists on crates.io, so there is nothing to
substitute. Options, in preference order: ask upstream to publish; vendor it into
the `bite-gpui` namespace with lib name `wgsl_rs` intact, so `use` sites are
unchanged; or leave ce unshippable, which is the current state.

What the pipeline does about it is deliberately not "fail":

- **staging withholds the crates and warns.** The tree still builds, because a
  git dependency resolves locally, so the affected crates are named in the
  report (`blocked`) and removed from the publish order. Staging succeeds, so CI
  can check the other seventeen ce crates instead of stopping at a known
  blocker. `pipeline/stage.py --strict` restores the hard failure for anyone who wants it.
- **withholding is transitive.** A crate that depends on a withheld crate is
  withheld too, because cargo will not package it either: `cargo package`
  normalises the dependency into a version requirement and then has to resolve
  it. ce is where this matters — `gpui_ce_apple` is held back by `wgsl-rs`, and
  `gpui_ce_macos` reaches it from a `cfg(target_os = "macos")` table, which
  cargo resolves for packaging regardless of the target being built. That takes
  ce from four crates withheld to nine: the four, plus `gpui_ce_macos`,
  `gpui_ce_platform`, `gpui_ce_linux`, `gpui_ce_web` and `gpui_ce_gpui_parley`.
  Only real dependencies count; a withheld *dev*-dependency does not stop a crate
  being published, which is what `no_verify` is for.
- **the dry run skips what is withheld**, and prints why.
- **a release refuses.** `pipeline/publish.py` will not publish a target with withheld
  crates unless `--allow-partial` is passed deliberately: a release is all of its
  crates or none of them, and a partial one published by accident cannot be
  withdrawn.

A branch-level failure that hides a release's worth of checking is worse than a
warning that names nine.

One crate reaches outside `crates/`: `perf` lives in `tooling/perf` and is pulled
in by `util_macros`. It is copied like any other crate and published as
`bite-gp-perf`.

## 9. Licensing and Attribution

Measured across the 50-crate union: **44 Apache-2.0, 4 GPL-3.0-or-later, 2
inherited from their workspace.**

The GPL crates are `path`, `zlog`, `ztracing`, `ztracing_macro` — zed licenses
these under GPL-3.0-or-later, and they are load-bearing: `gpui` depends on
`path` and `ztracing`. Publishing them is permissible (GPL allows
redistribution with source and license intact), and GPL-3.0 is compatible with
Apache-2.0, so an Apache-2.0 crate may depend on them. But it means the
published graph is GPL at those nodes, and the facade's combined work inherits
that. **This is a licensing decision for the user, not a technical one** — see
[§13.4](decisions.md). The alternatives are publishing them GPL-labelled as designed, or
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
- records the origin in `[package.metadata.bite]` ([§3](contract.md)).

Not yet done, and worth deciding with [§13.4](decisions.md): per-file licence
headers, and the copyleft audit as a *check* rather than a measurement. Today the
GPL crates are reported by the closure scan (they show up in the stage report's
`license` field) but nothing fails on them.
