# Architecture Specification: `bite-gpui` Distribution Engine

| Attribute | Specification |
| --- | --- |
| **Status** | Active — as built 2026-09-25 |
| **Published state** | The Zed lineage is published: thirty-one crates at `1.20.203`, and `bite_v1.21.0` is part-published — a release resumes with `--only` ([§12](docs/release.md)) |
| **Source specification** | `targets.toml`, verified 2026-09-25 |
| **Registry** | crates.io |

Quantities and version identifiers marked *verified* were checked against
`targets.toml`, a staged tree or crates.io on that date.

## Document Index & Module Mapping

Section numbers are canonical across the documentation, the pipeline
(`pipeline/`), and the target table (`targets.toml`): `§6` is `§6` in a chapter,
in a comment in `targets.toml`, and here. This file is the map.

| Section | Document | Scope & Coverage |
| --- | --- | --- |
| §1–§2 | `DESIGN.md` | distribution requirements, strategy, governing invariants |
| §3–§6 | [`docs/contract.md`](docs/contract.md) | target topologies, the GPUI crates set, crate naming, versioning |
| §7–§9 | [`docs/staging.md`](docs/staging.md) | branch-to-tree transformation, dependency remapping, licensing |
| §10 | [`docs/verification.md`](docs/verification.md) | staged-tree validation and the verification suite |
| §11–§12 | [`docs/release.md`](docs/release.md) | publish ordering, registry rate limits, CI workflows |
| §13 | [`docs/decisions.md`](docs/decisions.md) | each decision, its status, and the evidence |

## 1. Distribution Requirements & Strategy

### 1.1 Problem Statement

The rearchitected GPUI is maintained as twelve branches in one repository,
`git@github.com:bite-gpui/bite-gpui.git`. A branch is not a dependency: it cannot
be `cargo add`ed, and a `git`/`rev` pin is neither a version nor something the
ecosystem's tooling can resolve.

### 1.2 Target Resolution Model

The engine turns each branch into a versioned, publishable crate topology on
crates.io, so a consumer names a dependency instead of vendoring a branch:

```
bite_* branch
    │  inventory.py — walk the path dependencies (§4)
    ▼
the GPUI crates set
    │  naming.py — published name per package, lib name pinned (§5)
    ▼
those crates, renamed
    │  stage.py — versions, dependency rewrites, licences, provenance (§7–§9)
    ▼
staged tree ──► verification (§10)
    │  publish.py — dependency order, resumable (§11)
    ▼
crates.io
```

```toml
# a Zed-lineage retarget, tracking upstream Zed 1.20
bite-gpui = "1.20"

# the Community Edition (CE) transplant
bite-gpui-ce = "0.20260922.1"
```

They then get the same library the branch would have given them.

### 1.3 Reference Pipelines

The shape comes from two references.

- **GPUI crate extraction — `longbridge/gpui-kit`.** Its `script/bump-gpui.ts`
  establishes the mechanism: follow the path dependencies, remap packages,
  prune what cannot be published, aggregate licences, validate with `cargo
  publish --dry-run`, and publish resumably. The one substantive change here is
  the input — gpui-kit walks a Zed *commit*, this walks the repository's own
  `bite_*` branches.
- **Versioning — `iamnbutler/gpui-unofficial`.** It publishes Zed releases as
  `gpui-unofficial` (plus `*-gpui-unofficial` helpers), and its sequence —
  `1.20.2` stable, `1.21.0-pre` newest — is the precedent [§6](docs/contract.md)
  follows.

## 2. Governing Architectural Invariants

Everything the engine emits has to satisfy three invariants. Each one names the
check that enforces it; one of them is not enforced at all, and says so.

### Invariant 1 — Upstream Surface Preservation

- **Rule.** A crate named after upstream carries upstream's public surface
  unchanged.
- **Extension pattern.** Anything a fork adds or changes lives in a fork-marked
  crate or an `*Ext` extension trait, and the fork's facade re-exports both.
  Decorate traits; do not fork them.
- **Enforcement — none.** Nothing in this pipeline compares surfaces. The
  invariant is a property of how the source branches were built, and a staged
  tree is only ever checked to compile ([§10](docs/verification.md)). An AST diff
  of the exported items between an upstream crate and its `bite-*` counterpart is
  the missing piece.

### Invariant 2 — Library Target Name Preservation (`[lib] name`)

- **Rule.** `package.name` moves into the `bite-*` namespace; the library target
  name does not:
  - `bite-gpui` keeps lib name `gpui`
  - `bite-gp-platform` keeps lib name `gpui_platform`
  - `bite-gp-collections` keeps lib name `collections`

  The full mapping, and the rule that derives it, are [§5](docs/contract.md)'s.
- **Enforcement.** `stage.py` pins `[lib] name` before staging and asserts it did
  not move afterwards ([§7](docs/staging.md), steps 3 and 9), and `naming.py
  --verify` fails any crate that declares none ([§10](docs/verification.md)).
- **Impact.** `use gpui::…` compiles unchanged in an existing project, so
  migrating to the published crates changes the line in `Cargo.toml` and nothing
  else. That is what makes the output a drop-in rather than a porting exercise.

### Invariant 3 — Lineage Namespace Isolation

- **Rule.** The Zed-lineage retarget and the Community Edition (CE) transplant
  contain *different code under the same upstream crate names*: CE's
  `gpui_platform` carries CE's surface, the 1.14 retarget carries 1.14's.
- **Enforcement.** `naming.py --verify` fails a published name that two packages,
  or two lineages, would both claim ([§10](docs/verification.md)); the families
  themselves are [§5](docs/contract.md)'s.
- **Impact.** The two lineages publish into isolated name families rather than
  into one family told apart only by a version number, so a version range
  spanning both cannot silently mix them ([§13.2](docs/decisions.md)).
