# Architecture Specification: `bite-gpui` Distribution Engine

| Attribute | Specification |
| --- | --- |
| **Status** | Active — as built 2026-09-25 |
| **Published state** | The zed lineage is published: thirty-one crates at `1.20.203`. `bite_v1.21.0` is staged and clean, not released |
| **Source of truth** | `targets.toml`, verified 2026-09-25 |
| **Registry** | crates.io |

Numbers marked *verified* in the chapters were checked against `targets.toml`, a
staged tree or crates.io on that date.

## Document Index & Module Mapping

Section numbers are canonical across the documentation, the pipeline
(`pipeline/`) and the target table (`targets.toml`): `§6` is `§6` in a chapter,
in a comment in `targets.toml`, and here. This file is the map.

| Section | Document | Scope |
| --- | --- | --- |
| §1–§2 | this file | motivation, strategy, governing invariants |
| §3–§6 | [docs/contract.md](docs/contract.md) | target topologies, the dependency closure, crate naming, versioning |
| §7–§9 | [docs/staging.md](docs/staging.md) | branch → staged tree: algorithm, dependency surgery, licensing |
| §10 | [docs/verification.md](docs/verification.md) | staged-tree validation, and what each check exists to catch |
| §11–§12 | [docs/release.md](docs/release.md) | publish order, registry rate limits, CI workflows |
| §13 | [docs/decisions.md](docs/decisions.md) | each decision, its status, and the evidence |

## 1. Distribution Requirements & Strategy

### 1.1 Problem Statement

The rearchitected gpui is maintained as twelve branches in one repository. That
is useful to us and useless to anyone else: a branch cannot be `cargo add`ed, and
a `git`/`rev` pin is neither a version nor something the ecosystem's tooling can
resolve.

### 1.2 Target Resolution Model

The engine turns each branch into a versioned, publishable crate topology on
crates.io, so a consumer names a dependency instead of vendoring a branch:

```toml
# a zed-lineage retarget, tracking upstream zed 1.20
bite-gpui = "1.20"

# the community-edition transplant
bite-gpui-ce = "0.20260922.1"
```

They then get the same library the branch would have given them.

### 1.3 Reference Pipelines

The shape comes from two references.

- **Closure extraction — `longbridge/gpui-kit`.** Its
  `script/bump-gpui.ts` establishes the mechanism: walk the path-dependency
  closure, remap packages, prune what cannot be published, aggregate licences,
  validate with `cargo publish --dry-run`, publish resumably. The one
  substantive change here is the input — gpui-kit walks a zed *commit*, this
  walks the repository's own `bite_*` branches.
- **Versioning — `iamnbutler/gpui-unofficial`.** It publishes zed releases as
  `gpui-unofficial` (plus `*-gpui-unofficial` helpers), and its sequence —
  `1.20.2` stable, `1.21.0-pre` newest — is the precedent [§6](docs/contract.md)
  follows.

## 2. Governing Architectural Invariants

Everything the engine emits has to satisfy three invariants. The first two are
what make the output a drop-in; the third is what keeps the two lineages from
being mistaken for one another.

### Invariant 1 — upstream surface preservation

- **Rule.** A crate named after upstream carries upstream's public surface
  unchanged.
- **Extension pattern.** Anything a fork adds or changes lives in a fork-marked
  crate or an `*Ext` extension trait, and the fork's facade re-exports both.
  Decorate traits; do not fork them.

### Invariant 2 — library target name preservation (`[lib] name`)

- **Rule.** `package.name` moves into the `bite-*` namespace; the library target
  name does not. `bite-gpui` keeps lib name `gpui`, `bite-gp-platform` keeps
  `gpui_platform`, `bite-gp-collections` keeps `collections`. The full mapping and
  the rule that derives it are [§5](docs/contract.md)'s; `naming.py --verify`
  re-derives it on every run rather than trusting this paragraph.
- **Impact.** `use gpui::…` compiles unchanged in an existing project, so
  migrating to the published crates changes the line in `Cargo.toml` and nothing
  else. That is what makes the output a drop-in rather than a porting exercise.

### Invariant 3 — lineage namespace isolation

- **Rule.** The zed-lineage retarget and the community-edition transplant
  contain *different code under the same upstream crate names*: ce's
  `gpui_platform` carries ce's surface, the 1.14 retarget carries 1.14's.
- **Impact.** The two lineages publish into isolated name families
  ([§5](docs/contract.md)) rather than into one family told apart only by a
  version number, because a version range spanning both would silently mix them
  ([§13.2](docs/decisions.md)).
