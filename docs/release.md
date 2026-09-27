# Release & CI Workflows

§11–§12 of the [bite-gpui distribution design](../DESIGN.md). Sections keep
their numbers across all of these files, so `§6` means the same thing here, in
the other chapters, and in `targets.toml`.

Publishing a staged tree in dependency order under crates.io's rate
limits, and the workflows that run all of it.

## 11. Publish Order and Rate Limits

`pipeline/publish.py --stage <dir>` reads the stage report, publishes in dependency
order, and handles the three things a bare loop gets wrong. `--commands` prints
that same sequence as plain `cargo publish` lines, for running the first release
by hand, derived from the same report so it cannot drift.

**Resolving a first release.** `cargo publish` resolves a dependency that has a
`version` from the registry, not from its `path`, and it resolves *before* it
uploads: the packaged manifest is normalised, then resolved — dev-dependencies
included, for every target — and only then is the tarball sent. A first release
cannot be published without help. The second crate names a version of the first
that is not on the registry yet, and no ordering fixes it for `gpui_macros`,
which names the facade from a *dev*-dependency while the facade is published
last. So every crate in the set is patched to its staged directory
(`--config patch.crates-io.<name>.path=…`), which resolves the local release
graph. `--no-verify` does not avoid this — it skips the compilation, and the
resolution happened before that. Measured on 1.20.203: 14 of 31 crates package
without the patches, 31 of 31 with them.

A patch is a resolution override and never part of the artifact. What is uploaded
is the normalised manifest, naming versions and no paths — `[dev-dependencies.gpui]
version = "1.20.203"`, `package = "bite-gpui"` — and cargo refuses to package a
path dependency for publication anyway. The same entries are written to
`.cargo/config.toml` in the staged tree, which is what lets a hand-run
`cargo publish` command work; that file sits outside every crate directory, so it
is not packaged either, and `pipeline/publish.py --commands` writes it alongside the
commands it prints.

**Resuming.** A version already on crates.io is skipped, so an interrupted run
continues rather than restarting, and `--only <crate>` re-runs from a failure
without republishing what already landed. crates.io versions are immutable, so
this is the only way a multi-crate release is sane.

**Visibility.** crates.io accepts an upload before its API reports the version,
and the next crate in the order depends on the one just uploaded, so the
publisher waits for the registry to acknowledge each upload before continuing.
Its API answers 403 to a request that carries no `User-Agent`, which is a
silent trap for anything polling it by hand.

**Rate limits are the real cost of a first release.** crates.io allows a burst of
five new crate *names* per account and then one every ten minutes, and a burst of
thirty new *versions* of existing crates followed by one a minute. It answers the
sixth new name with 429 and the time the next attempt is allowed, so a target's
first release of thirty-one names takes about four hours and its second takes
about half an hour. That is why the publisher waits for the named time and
retries instead of failing, and why it then paces the rest of the release by the
interval the refusal implied: the server states the rule, so the publisher learns
it rather than hard-coding it, and `--gap` seeds the interval for a run that
already knows it.

**A first publish also claims the names.** crates.io assigns a new crate name to
the token that uploads it first, and adding an owner afterwards is a crate at a
time. Publishing with a token that belongs to the `bite-gpui` organisation, or
adding the organisation as an owner as soon as the first crate of each name
lands, is the difference between one step and thirty-one.

**A name has to exist before anything may name it, and that is the wall a first
release hits.** crates.io validates every dependency *name* in a manifest before
it accepts an upload, dev-dependencies included, so a crate cannot name one that
is not there yet. Six of 1.20.203's crates name the facade from their
dev-dependencies, and the facade is published last by construction, because its
own normal dependencies include three of them. Neither ordering nor `--no-verify`
resolves that: the compilation is skipped and the name check still happens.

It is settled by reserving the name **once**: `bite-gpui` was published as
`0.0.0-reserved`, a stub, before the first crate that names it, and the six then
published with their manifests untouched. Later releases need nothing, because
they publish new versions of a name that already exists, and every published
manifest stays faithful — which dropping those dev-dependencies would not: the six
crates' own test suites would stop compiling from the registry, on every release,
forever. That also corrects how [§10](verification.md) talks about them: they were
never merely "unverifiable", they were **unpublishable**, and what makes them
publishable is a reservation rather than an edit.

**Branch order is free, but the newest branch should still go first.** The ten
release targets publish the same names at different versions (`1.14.202`,
`1.15.102`, …), and crates.io accepts a version below the current maximum — that
is how a patch to an old major line ships. Nothing breaks if an older branch
goes first, except that the version a consumer resolves by default is the
highest one, so publishing the current release first is what makes
`cargo add bite-gpui` mean the current release.

Two caveats, both inherited from ce's own release and both deliberate. Some
crates are published with `--no-verify`, because verification resolves the root
package's dev-dependencies for every target and a first release has not uploaded
the crate those dev-dependencies point at yet — either the facade, which is
published last, or a zed-internal crate that is never published at all. The set
is derived from the manifests by `pipeline/stage.py` (reported as `no_verify`) rather than
hand-kept, and the workspace-wide clippy pass covers the
compilation that skips. And the order comes from the manifests rather than a
hand-kept list, because the two lineages and ten release targets have
different leaf sets.

**Status, once the first release is done.** The zed lineage's names are published
from `1.20.203`, which changes what the next release costs rather than merely
proving the pipeline. New *versions* of names that already exist are the cheap case
— a burst of thirty, then one a minute — so a release of a target whose name set is
unchanged is minutes. `bite_v1.21.0` was that case: the same names as the release
before it, thirty-one of them, all live. A later release of the same target carries
thirty, because `bite-gp-parley` left the set for a repository of its own. The expensive case is a new name family, and it
is paid once per lineage rather than once per release: the five-name burst, the
hours of pacing, and the one-time reservation of a name that must exist before
anything may name it.

## 12. CI

### The Workflows

| workflow | runs on | what it does |
| --- | --- | --- |
| `release-branch.yml` | dispatch | the entry point: resolve a branch, tag it, verify it through `verify.yml`, and dispatch `release.yml` |
| `ci.yml` | pull requests, pushes to `main`, dispatch | validate the table and the naming rule, lint the CI wiring, plan the matrix, then stage and verify one target per lineage |
| `verify.yml` | dispatch, or called by `ci.yml` or `release-branch.yml` | stage one target and run the checks over it, and leave the receipt a release reads |
| `tag.yml` | dispatch | resolve a branch to the tag it must carry, push it, and dispatch `release.yml` |
| `release.yml` | dispatch | require that receipt, then stage and publish inside the `crates-io` environment |
| `sync-release-target-options.yml` | schedule, dispatch | rewrite the dispatch `options:` lists from the table and the branches, opening a pull request when one drifts |

### `ci.yml`

Pull requests, pushes to `main`, and dispatch:

- **table** validates `targets.toml`, then runs the naming rule against a real set
  of one target per lineage, so a new crate that would land on a taken name fails
  long before a release rather than during one;
- **lint** runs `checks/workflows.py`: actionlint over `.github/workflows`, and a
  pass over the `inputs:` and `outputs:` of the actions those workflows use. The
  second pass exists because actionlint does not read the `steps:` of a composite
  action — its documentation says so — so the four actions `verify` is built from,
  and every call site inside them, are invisible to it. The actionlint binary is
  fetched from its release page and verified against a checksum pinned in the
  script, so a run uses the version the file names. `plan` needs this job, so a
  wiring mistake fails in seconds rather than after a matrix leg has staged a
  target;
- **plan** turns the selector into a matrix via `pipeline/targets.py`;
- **stage** (matrix) checks the target branch out, stages it, and runs verification.
  A pull request stages one target per lineage (`default`); dispatch with `all`
  when a change could affect every branch. Staging all twelve on every push is
  hours of CI for a change to a script. The matrix carries a runner label per
  leg, so `platforms` can add a `macos-14` leg to every selected target — the
  only way the Apple backends' build script is ever compiled, since it is behind
  `cfg(target_os = "macos")` and a Linux runner never sees it. It defaults to
  Linux alone, and the labels are declared once, in `pipeline/targets.py --matrix`, rather
  than in the workflow.

`.github/actions/verify/action.yml` is the entry point for those checks.
`verify.yml` is the workflow around it, and the only place it is run: `ci.yml`
calls that workflow rather than copying its steps, so there is one definition of
staging and verifying a target.

Those two workflows also cache the cargo registry and git checkouts under a key
built from the lockfile, with a shared restore prefix. The first runs spent most of their
time downloading dependencies, and while different targets have different
lockfiles — so the *build* cache cannot be shared — the crates they download are
almost the same.

The isolated build unpacks the archives into `isolated/` inside the staged
checkout, with `isolated/target` as its own target directory. That is
deliberately outside `target/` — the directory the build cache is keyed on — so a
second, isolated set of artifacts is never cached, and the directory is
disposable between runs.

### `release-branch.yml`

The entry point: dispatch it with a branch of the source repository and it does
the whole sequence in one run — resolve the target, tag the branch
(`pipeline/tag_release.py`, the same script `tag.yml` runs), verify the target by
calling `verify.yml`, and dispatch `release.yml`. It wires the three steps rather
than reimplementing them, so the checks and the publish keep the definitions
`verify.yml` and `release.yml` already are.

It is what releases a branch one command at a time instead of three with a wait
in the middle. The version is the one the table declares (§6), so a branch that
has not been released needs nothing bumped; new content for an already-published
target is still an `amendment` bump ([§6](contract.md)) first. A dispatch is a
report unless `dry_run` is off and `confirm` repeats the branch, and the tag
refusal is `pipeline/tag_release.py`'s — a tag at a different commit fails the
run before anything is verified or published.

### `tag.yml`

The manual release step: dispatch it with a branch of the source repository and it
resolves the target, derives the tag, pushes it, and dispatches `release.yml`.

Both of those workflows' dispatch inputs are `choice` lists of the source
repository's branches, not free text, and `pipeline/targets.py --validate` compares them
against the table: GitHub fills a dispatch dropdown from the workflow file and from
nothing that can read `targets.toml`, so the list is the one part of the table that
has to be written twice, and a target added to one and not the other would be a
target nobody can select. The validation runs wherever `--validate` does, which
includes the `table` job of `ci.yml`.

It has to dispatch the publish rather than let a trigger do it, for two reasons.
The narrower one is GitHub's recursion rule: a tag pushed with a repository's own
`GITHUB_TOKEN` does not start another workflow run. The one that settles it is
which repository this is — the `bite_*` tags live in the *source* repository, and
this workflow lives here, so a `push: tags` trigger would be watching the wrong
repository and could never fire at all. `tag.yml` exists so the tag ends up in the
right place and the release starts anyway; release-on-tag would mean putting the
trigger in the source repository, where the tags are, dispatching this workflow,
and `--for-tag` is the lookup it would need.

`pipeline/tag_release.py` carries the judgement, so it is testable without CI: it resolves
the branch to its target, computes the version from the table, asks the *remote*
what tags it has, and then either pushes or reports. Exactly one state is refused,
and it is the one not to override: a tag that names a *different* commit, because
the content under a published version is not allowed to change and the answer is an
`amendment` bump ([§6](contract.md)) that mints a new version rather than a moved
tag. A tip that already carries its own tag is **not** refused — that is a release
that did not finish, and finishing it means publishing, which `pipeline/publish.py`
makes idempotent by skipping what already landed. A report-only run exits zero
whatever it finds — checking is an answer, not a failure — and an exit status that
refuses belongs only to a run that was asked to push.

### `release.yml`

Dispatch only. It takes the target, a
confirmation input that must repeat it for a real publish, and `dry_run`
defaulting to true, which makes it the dry-run path as well, and the only way to
release a rolling target: `bite_master` and `bite_ce_main` publish on the CalVer
series, so both lineages could push a tag of the same name and the tag would not
say which branch meant it. `--for-tag` refuses such a tag with that reason rather
than guessing.

It has two jobs and runs no checks: `prepare` resolves the target, and `publish`
stages it and publishes it. **Verification is `verify.yml`'s**, and `prepare`
refuses a real publish without its receipt:

```sh
gh workflow run verify.yml -f target=bite_v1.21.0
```

A dispatched verification uploads `verified-<target>-<version>` — the stage report,
which records the target, branch, commit and version it checked — and `prepare`
asks the artifacts API for that name. So a release is checked against a
verification of the same target at the same version, and since the version is the
tag and staging refuses an untagged tip (§6), that is also the commit. A dry run
is exempt — it publishes nothing, and iterating on the publisher is what it is for.

**`require_verify`** is that check's off switch, default on. Turning it off
publishes without a receipt and says so in a warning; it is for when the receipt
itself is what is broken — the artifact has aged out, or the repository it lands
in is misconfigured — not for skipping the checks, and not for a first release of
a name.

Verification is kept out of this workflow because it takes minutes, and a release
workflow that ran it could not be used to iterate on the publisher itself — a
missing token, a rate limit, a crate whose manifest crates.io rejects — where
those minutes are the thing being waited on and not what is being tested.

The remaining input serves the resume loop: **`only`** passes `--only` to
`publish.py`, publishing just the named crates. The publish log names the crate a
failure stopped at, and this republishes it without replaying the ones that
already landed. It cannot smuggle out a partial release, because the withheld
refusal is computed over the whole release before the selection is applied.

`publish` is gated by `needs.prepare.result == 'success' && dry_run == 'false'`.
`needs` alone would already skip it when `prepare` fails; the result is named
because a failed job does not reliably expose the outputs its earlier steps wrote.
A note from the design this replaced: a job cannot escape a failed or skipped
dependency with a plain condition, only with `always()`.

The publish job runs behind the `crates-io` environment — required reviewers and
the token belong there, and it is the only place a dispatch can turn into an
upload — under a single `registry-publish` concurrency group, because the release
targets share crate names and crates.io versions are immutable. Because only a
job that names the environment can see the secret, the token is checked as that
job's first step after checkout: failing there costs seconds, where letting
`publish.py` find it empty costs a staging run first. It runs no checks — those
belong to `ci.yml`, and holding the registry lock through a test suite is how a
release ends up half done. It does keep the publish log as an artifact, which is
what names the crate a rate limit stopped the run at and when the next attempt is
allowed.

### `sync-release-target-options.yml`

The dispatch workflows need the target table twice: once where it is read, and
once as a `choice` input's `options:` list, because GitHub fills a dropdown from
the workflow file and from nothing that can read `targets.toml`. `--validate`
fails when the two copies drift, but it cannot fix them. This is the fix, on a
schedule: `pipeline/sync_release_target_options.py` rewrites the `options:`
blocks — the branches from the source repository's branch list, the target names
from the table — and a run that changed anything opens a pull request against
`main`. The rewrite touches only those blocks — a YAML round trip would drop the
comments the workflows are written in — so a diff is the options and nothing
else.

The branches are filtered to the shapes `targets.is_release_branch` names —
`bite_v<M>.<m>.<p>`, its `-pre` preview, `bite_master`, `bite_ce_main` and
`bite_ce_v<M>.<m>.<p>` — so a working branch such as `bite_v1.21.0-some-feature`
is never offered. A branch that is release-shaped but has no target is written and
fails `--validate`, which is the drift the job surfaces rather than papering over:
a new release line is a `targets.toml` entry to add.
