# Verification

§10 of the [bite-gpui distribution design](../DESIGN.md). Sections keep
their numbers across all of these files, so `§6` means the same thing here, in
the other chapters, and in `targets.toml`.

What a staged tree must pass before any of its crates is published, and
what each check exists to catch.

## 10. Verification

Run by `verify.yml` against the staged checkout, per target: it is the one
definition of what verifying a target means, dispatched to verify on demand and
called by `ci.yml` for the targets a pull request stages. A dispatched run also
leaves a receipt — `verified-<target>-<version>`, the stage report itself — which
is what `release.yml` requires before it will publish ([§11](release.md)). It is
one entry point over three actions, so there is one copy of the sequence rather
than one per workflow, and a phase can also be run on its own:

| action | what it is | cost |
| --- | --- | --- |
| `.github/actions/preflight` | the stage report, `cargo metadata`, and the two static packaging checks | seconds |
| `.github/actions/code-checks` | clippy, and the tests of the GPUI crates set | minutes |
| `.github/actions/build-checks` | the publish dry run, then the isolated build | minutes |

The system packages a Linux leg links against are not a phase. They are the
environment rather than a check, and a release compiles the same set through
`cargo publish`'s verification build — so they are in `.github/actions/build-env`,
together with python and the pinned toolchain, which `verify.yml` and `release.yml`
both prepare before they touch the set. One definition, because when it was two
the release's half was missing `fontconfig` and a build script panicked rather than
failing usefully.

They run cheapest first, so a crate that cannot be staged or a path that escapes
its archive is reported in under a minute rather than after compiling a whole set
of crates. That ordering is the point: the defect classes this project exists
to catch are the cheap ones to detect and the expensive ones to discover late.

The scripts live in two directories: the pipeline that produces the stage in
`pipeline/`, and the checks that judge it in `checks/`. The actions invoke them by
path under `$GITHUB_WORKSPACE`, so a check can run from any working directory; the
`pipeline/` modules must stay together, because they import each other by bare
name and add their own directory to `sys.path`.

**Clippy is the compile.** `cargo clippy --all-targets --all-features -- -D
warnings` type-checks the same targets a plain `cargo check` would and adds the
lints on top, and because clippy runs under a compiler wrapper that
re-fingerprints the workspace, a check step ahead of it recompiled every crate in
the set for nothing. There is one step, and the lints are the reason it is
worth having.

**`--all-features` throughout.** The migration project checked the set with
default features and with all of them, and only the latter compiles the code
behind `bench-support` and `profiler`. With default features that code is
reported dead, which is how verification first failed — on
`gpui_authoring::Window::present_if_needed`, whose only callers are in
`bench_context` and `profiler::hang`.

In order:

1. `pipeline/targets.py --validate` and `pipeline/naming.py --verify` — the table
   is well formed, and every crate in the set has a name that no other crate needs
   ([§12](release.md)). The workflow runs this before staging, not the action.
2. `cargo metadata --format-version 1` — the whole workspace resolves. Cheap, and
   it is what caught the stale-version rename in [§7](staging.md) step 4. `check_reads.py`
   packages each crate, so it has to resolve first.
3. `checks/check_reads.py` — for every publishable crate, `cargo package --list`
   gives the file set that would be uploaded, and every path the crate names at
   build time has to be inside it. See below.
4. `checks/apple_build_check.py` — the Apple crate's build script feeds cbindgen five
   shader sources and sits behind `cfg(target_os = "macos")`, which cargo
   evaluates against the *host*: no runner this project has ever ran it. This
   strips that gate and the two shader-compilation calls (both asserted, so a
   change in the script's shape fails loudly), points the script at the crate's
   own `vendor/` and `metal_renderer.rs`, and so runs cbindgen on Linux. Step 3
   finds the same class of defect statically from the archive; this one shows
   cbindgen's own message. It takes whichever crate feeds cbindgen on that branch
   — `gpui_macos` for 1.14 to 1.16, `gpui_apple` from 1.17.

   Two details are about the harness rather than the crate, and both cost a CI run
   to find. The generated crate declares the `runtime_shaders` feature and allows
   the `unused` lint group, because removing the shader-compilation calls is what
   makes the binding and the two functions dead, and the checks build with
   `-D warnings` — four errors caused by the transformation rather than by the
   crate. And a stage with *no* cbindgen build script is a skip with a notice, not
   a failure: ce is built on 1.14, which never had the step, so verification runs
   this on a lineage with nothing in it. The skip is narrow — an Apple build
   script that still calls cbindgen and is not recognised still fails, because
   passing there would lose this check's cover.
5. `cargo clippy -p <native crates> --all-targets --all-features -- -D warnings`.
6. `cargo test -p <tested crates> --all-features --lib --tests`.
7. `pipeline/publish.py --dry-run` — packages each crate and compiles it as the root of a
   build, which is the only check that the published artifact builds. Resolving a
   root's *dev*-dependencies is part of that, so a crate whose dev-dependencies
   cannot resolve is published with `--no-verify` and its compilation is skipped.
   `--allow-dirty` is required throughout because staging rewrites the tree in
   place.
8. `checks/test_isolated.py` — packages and unpacks every publishable archive, then
   compiles a generated crate that depends on all of them. That is what closes
   the `no_verify` gap above: a consumer resolves a dependency's normal
   dependencies and never its dev-dependencies. It is also the only check that
   fails when an archive cannot stand on its own. It compiles with `cargo check`
   and default features — the workspace-wide `--all-features` clippy pass has
   already covered the code behind the non-default ones, and the codegen of a whole
   set of crates is what does not fit on a runner.

**Verification has to be able to see a crate that reads outside itself, and it could
not.** `gpui_apple`'s build script fed cbindgen five shader sources located by
walking up out of the crate — `CARGO_MANIFEST_DIR/../gpui_types/src/color.rs` —
which the workspace layout made work and which every published tarball failed on
with `ParseCannotOpenFile`. Nothing in the checks noticed: the code is behind
`#[cfg(target_os = "macos")]`, so a Linux runner never compiles it, and
`gpui_apple` is in `no_verify`, so the dry run never extracted its tarball
either. Step 3 is the answer. It reads the paths a build script names, plus the
`include_bytes!`/`include_str!`/`#[path]` of every source in the archive, and a
path that resolves outside the crate — or inside it but not packaged — fails.
The sources are now vendored under `crates/gpui_apple/vendor/`, which the
archive carries, and the same fix is on every branch back to 1.14.

The rule is deliberately coarse, because the failure it exists for does not need
a clever one: a literal that names something existing relative to the crate
directory is taken to be a path the build reads, and no `join` chain is
followed, since the upward step is its own literal. A reference inside
`#[cfg(test)]`, or under `tests/`, `examples/` or `benches/`, is a warning rather
than a failure — it is not compiled when the crate is built as a dependency, so
it cannot break the published artifact. The fonts `gpui_wgpu`'s tests and
`gpui_authoring`'s image fixture read from `../../../assets/` are of that kind,
and they are why the distinction is drawn rather than the check being made
stricter.

The set is passed as explicit `-p` flags rather than `--workspace`: the
checkout carries all ~250 zed crates, and checking them is neither wanted nor
affordable on every push. Withheld crates ([§8](staging.md)) are still checked —
they build, they just cannot be published — so verification covers 55 of the 57
names in the union.

**System packages are the union of two lists**, and the first run failed for
want of the second. ce's publish job installs nine packages, which was enough to
*package* crates but not to *link test binaries*: the run died at the linker with
`unable to find library -lX11-xcb` while linking `gpui_macros`'s `render_test`,
which pulls the whole stack. zed keeps its Debian/Ubuntu list in `script/linux`,
and that is the source of truth for the rest — minus its gtk and webkit entries,
which are for editor crates the set does not contain.

**Verification is bounded by the runner, and two settings keep it inside that.**
A debug build of the set exhausted a runner's disk, and the linker died with
a bus error — on Linux, the signal a process gets when the file-backed page it
is writing to cannot be extended. So:

- `CARGO_PROFILE_DEV_DEBUG=0` and `CARGO_PROFILE_TEST_DEBUG=0` override the
  branch's `debug = "limited"`. Nothing here is debugged and every artifact is
  thrown away; turning debug information off shrinks the link step by about an
  order of magnitude, and shortens the build, which was the other complaint.
- the test step uses `--lib --tests` rather than `--all-targets`, because
  `cargo test`'s default selection also *builds every example* and the facade
  declares twenty-five of them, each linking the whole stack. Examples are still
  type-checked by clippy, which does not link; what is given up is
  verifying that they link, which no consumer ever does.
- the test step covers only the crates that declare tests (24 of the 30 in
  1.20.2), computed from the sources rather than listed. A test binary links the
  whole stack, so testing a crate that has none is pure cost; its lib is still
  checked.

**The toolchain is pinned, so the bar does not move.** A run failed on a
`useless_conversion` lint that rust-1.98.0 had grown and the code, written under
1.95.0, had never seen: six sites where `gpui::hsla` already returns the `Hsla`
a field wants. Nothing about publishing requires chasing a moving compiler — it
means fixing branches to satisfy rules that did not exist when they were written
— so both build workflows name the toolchain the branches were built and verified with,
and `rust-toolchain.toml` records it for local runs. Bumping it is a deliberate
act, taken together with a run that fixes whatever the newer toolchain reports.

**The wasm-only crates are excluded from the native run and are not yet checked
at all.** `gpui_web` includes its modules under
`cfg(any(target_family = "wasm", test))` while their dependencies (`gpui_engine`,
`gpui_platform`) are declared only under the wasm dependency table, so on Linux
its lib compiles to nothing and its lib *test* target cannot resolve those
imports. zed's own CI does not check it natively either. A `wasm32-unknown-unknown`
check is the missing piece; zed's recipe for it is a nightly toolchain with
`-Zbuild-std` and `-C target-feature=+atomics,+bulk-memory,+mutable-globals`.
This is the one published crate family verification does not build, and it is the
next thing to fix here.

A target that fails verification is not publishable. Failure is per target, so
`bite_v1.18.x` failing does not hold back `bite_v1.20.2`.
