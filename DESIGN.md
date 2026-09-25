# bite-gpui distribution — design

Status: **as built, 2026-09-25.** The zed lineage is published — all
thirty-one of its names are on crates.io at `1.20.203` — and `bite_v1.21.0` is
staged and clean but not yet released ([§11](docs/release.md)). Where a section
still says "will", "should" or "recommended", it records something not yet done;
[§13](docs/decisions.md) records each decision and its status. Numbers marked
*verified* were checked against `targets.toml`, a staged tree or crates.io on
that date.

## The chapters

The design is split by what it answers, not by length. Numbers are stable
across the files — `§6` is `§6` everywhere, including in the comments in
`targets.toml` and `pipeline/` — so this file is the map rather than the whole
document.

| sections | file | what it covers |
| --- | --- | --- |
| §1–§2 | this file | why the project exists, and the ruling it follows |
| §3–§6 | [docs/contract.md](docs/contract.md) | targets, the closure, the published names, versions |
| §7–§9 | [docs/staging.md](docs/staging.md) | how a branch becomes a publishable tree |
| §10 | [docs/verification.md](docs/verification.md) | what a staged tree must pass |
| §11–§12 | [docs/release.md](docs/release.md) | publish order, rate limits, the workflows |
| §13 | [docs/decisions.md](docs/decisions.md) | each decision and its status |

## 1. What this publishes, and why

The rearchitected gpui exists as twelve branches in one repository. That is
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
sequence — `1.20.2` stable, `1.21.0-pre` newest — is the precedent
[§6](docs/contract.md) follows.

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
carries 1.14's. They therefore publish into two name families
([§5](docs/contract.md)) rather than into one family distinguished only by a version
number. See [§13.2](docs/decisions.md) for the alternative we rejected and why.
