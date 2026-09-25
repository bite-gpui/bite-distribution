# The contract

§3–§6 of the [bite-gpui distribution specification](../DESIGN.md). Sections keep
their numbers across all of these files, so `§6` means the same thing here, in
the other chapters, and in `targets.toml`.

What is published: the target branches, the collection of crates they
resolve to, the names those crates take, and the versions.

## 3. Inputs and provenance

Twelve targets, one per branch. `.dist/targets.toml` is the source of truth and
`.dist/pipeline/naming.py --verify` checks it against the manifests.

| target | lineage | publishes as | upstream |
| --- | --- | --- | --- |
| `bite_v1.14.x` | zed | `bite-gp-*` | `1.14.2` |
| `bite_v1.15.x` | zed | `bite-gp-*` | `1.15.1` |
| `bite_v1.16.x` | zed | `bite-gp-*` | `1.16.3` |
| `bite_v1.17.x` | zed | `bite-gp-*` | `1.17.2` |
| `bite_v1.18.x` | zed | `bite-gp-*` | `1.18.1` |
| `bite_v1.19.x` | zed | `bite-gp-*` | `1.19.2` |
| `bite_v1.20.2` | zed | `bite-gp-*` | `1.20.2` |
| `bite_v1.21.0-pre` | zed | `bite-gp-*` | `1.21.0-pre` |
| `bite_v1.21.0` | zed | `bite-gp-*` | `1.21.0` |
| `bite_v1.22.0-pre` | zed | `bite-gp-*` | `1.22.0-pre` |
| `bite_master` | zed | `bite-gp-*` | — (rolling) |
| `bite_ce_main` | ce | `bite-gp-ce-*`, `bite-gpui-ce` | — (rolling) |

`upstream` is the zed release a branch retargets, recorded for provenance. The
published version follows from it and from the target's `amendment` (§6), so
neither is repeated here: `pipeline/targets.py --tags` prints the current tag and
version for every target, and that command — not this table — is what a release
reads. *(Verified 2026-09-25 against `targets.toml`.)*

All twelve live in one repository, `git@github.com:bite-gpui/bite-gpui.git`. They share
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
select directly) are the roots; from there `.dist/pipeline/inventory.py` walks in-checkout
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
| `bite_v1.14.x` | 31 | the release line's set, which the other nine share: `media`, `sum_tree`, `zlog` included |
| `bite_master` | 30 | 1.14 minus `media` |
| `bite_ce_main` | 26 | ce's own `gpui_ce_*` set replaces the vendored zed crates |

Union across targets: **50 source packages**. The release-line closure did not
change over the ten branches — 1.14 and 1.21.0-pre resolve to exactly the same
31 crates, and `bite_master` is that set minus `media` — but verification re-measures
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
name claimed by two lineages. `bite-gpui` was unclaimed when this was written and
is now taken by our own first release; `bite-gpui-ce` is still unclaimed. `gpui`
itself is neither — it is owned by upstream zed and has been published seven
times. That is the strongest argument for the prefix: the name we are
reimplementing is already taken, by the people we are reimplementing.
*(Verified 2026-09-25: `gpui` has exactly seven versions — three yanked — and its
owners are `zed-industries`; `bite-gpui` is at `1.20.203`; `bite-gpui-ce` 404s.)*

One consequence of preserving lib names: both facades export lib `gpui`, so a
project depends on `bite-gpui` or `bite-gpui-ce`, never both. That is inherent —
each is a drop-in for the same crate — and it is the price of `use gpui::…`
continuing to compile.

## 6. Versions

Each upstream patch release gets **a hundred slots**. `1.20.2` publishes as
`1.20.200`; amendments to the same retarget take 201..299; `1.20.3` starts at
300. The gap is the point — an amendment can never collide with the next
release, versions still sort in upstream order, and no retarget will need
anything close to a hundred amendments. `pipeline/targets.py` refuses a table that does
not fit the scheme (an upstream patch of 100 or more, an amendment outside
0..99).

**The version comes from a tag on our branch, not from zed's tag.** Every
release target's tip carries an annotated tag named `bite_` plus the published
version:

| branch | tag | publishes |
| --- | --- | --- |
| `bite_v1.14.x` | `bite_1.14.202` | `1.14.202` |
| `bite_v1.20.2` | `bite_1.20.203` | `1.20.203` |
| `bite_v1.21.0-pre` | `bite_1.21.0-pre.2` | `1.21.0-pre.2` |

Three of the twelve, to show the shape; `pipeline/targets.py --tags` prints all of
them and is what a release reads. *(Verified 2026-09-25 against `targets.toml`.)*

This replaced an earlier design that derived the version from the upstream
`v1.20.2` tag's presence on the branch's ancestry. That failed in CI for a real
reason worth recording: `actions/checkout` fetches neither tags nor history at
the default depth, so the tag was simply absent, and even with
`fetch-depth: 0` the derivation would have needed the full history of a zed-sized
fork on every job. A tag **at the tip** needs no history at all — `git tag
--points-at HEAD` compares OIDs — so a depth-1 checkout suffices and the
workflow fetches only `refs/tags/bite_*`.

Four consequences, all deliberate:

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
  but must agree on `1.14`. `pipeline/targets.py --validate` runs this, so a typo is
  caught in the `table` job in seconds.
- **Fixing the source costs a version bump on a release branch.** A new commit
  moves the tip, the tag no longer points at it, and staging refuses until the
  branch is re-tagged: the fix is `amendment += 1`, push, tag. Rolling targets
  pay nothing, because they are dated at staging time. That is the intended
  price of the version being a contract rather than a label, and it is the thing
  to weigh when verification finds a source-level defect. ce's broken example was
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
snapshot. The cost is that `*` prefers a release over the tip — see [§13.3](decisions.md).

ce has a scheme of its own in flight — its prerelease workflow publishes
`<committed major + 1>.0.0-alpha.N`, so `gpui-ce` is on the `1.0.0-alpha.N` line
while its committed version is 0.2.2. Because the two lineages do not share crate
names (§5, [§13.2](decisions.md)), this project does not have to interoperate with
it, and does not.
