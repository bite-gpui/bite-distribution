# Decisions

§13 of the [bite-gpui distribution design](../DESIGN.md). Sections keep
their numbers across all of these files, so `§6` means the same thing here, in
the other chapters, and in `targets.toml`.

Each decision the design rested on, its status, and the evidence.

## 13. Decisions

Each heading carries its own status: decided, in force, or open.

| decision | status | outcome |
| --- | --- | --- |
| 13.1 where the project lives | decided: `bite-gpui/distribution` | publishing is a separate concern from the architecture work |
| 13.2 two namespaces | decided: two | one crate name would otherwise cover two different crates |
| 13.3 what `bite_master` publishes as | in force: the CalVer series | a release requirement must not resolve to a master snapshot |
| 13.4 the GPL crates | decided by shipping: published GPL-labelled | it is what zed does, and GPL-3.0 is Apache-compatible |
| 13.5 crate metadata and ownership | partly done | `repository` and empty descriptions are staging's; the rest is not |

### 13.1 Where the Project Lives — **decided: `bite-gpui/distribution`**
`.dist/` is ignored in the zed clone (like `.tools/`). It lives at
`git@github.com:bite-gpui/distribution.git`, as its own repository, because
publishing is a separate concern from the architecture work and its history
should not be tangled with it. `.tools/` is likewise its own repository,
`bite-gpui/tools`, since the migration scripts are a third concern again.

### 13.2 Two Namespaces — **decided: two**

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

So ce is not an unpublished fork adjacent to this project; it is a fork with a
live distribution. Sharing one namespace with the zed lineage would put two different
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

### 13.3 What `bite_master` Publishes As — **in force: the CalVer series**
The CalVer rolling series, so a release requirement never resolves to a master
snapshot; that is what `targets.toml` declares today. The alternative is a
prerelease of the forthcoming release (`1.22.0-master.YYYYMMDD`), which makes
master sort newest but invents a release number that upstream may never use. If
`bite_master` should simply not be published, that is also a clean answer — say
so and it comes out of `targets.toml`.

### 13.4 The GPL Crates — **decided by shipping: published GPL-labelled**
`path`, `zlog`, `ztracing`, `ztracing_macro` are GPL-3.0-or-later and are in
`gpui`'s dependency graph. The choice was to publish them GPL-labelled — it is
what zed does, and GPL-3.0 is Apache-compatible — or to eliminate the dependency
so the published graph stays Apache-only.

**The first release took the first option, so this is no longer open.** All four
are on crates.io at `1.20.203` with their licence text and origin recorded as
[§9](staging.md) describes, and `gpui` depends on two of them, so the published
zed lineage is GPL
at those nodes. Reversing it now means yanking four names and republishing the
facade without the dependency. The legal weight has not gone away, it has moved:
what is still open is telling consumers, which is the per-file headers and the
copyleft audit as a check rather than a measurement, both noted in [§9](staging.md).

*(Verified 2026-09-25: `bite-gp-path`, `bite-gp-zlog`, `bite-gp-ztracing` and
`bite-gp-ztracing-macro` are all published at `1.20.203`.)*

### 13.5 Crate Metadata and Ownership — **partly done**
The crates need `description`, `keywords`, `categories`, `homepage`, and a
`repository`. In the 1.14 target's crates, 18 of 31 have no description, 29 have
no per-crate README (only `gpui` does), most set no `repository` at all and 3 still
point at `zed-industries/zed`.

Done by staging: `pipeline/stage.py` writes `repository` as the bite-gpui URL on
every crate, and fills a description only where the source had none, generically
("`<package>` — part of the bite-gpui rearchitecture of zed's gpui"). The owner
account and token exist; the first release used them.

Not done: a description that names the upstream origin. Because the rule is
"fill only if empty", the facade still reads *"Zed's GPU-accelerated UI
framework"* with nothing to say it is a build of zed v1.20.2; nor are
`keywords`/`categories`/`homepage` set by this pipeline rather than inherited from
the source (`homepage` is `https://gpui.rs`), nor are there per-crate READMEs.

By what the pipeline does and does not:

```
done by staging (pipeline/stage.py)
  ├── repository     the bite-gpui workspace URL, on every crate
  └── description    filled only where the source had none
still outstanding
  ├── a description naming the upstream origin — the facade especially
  ├── keywords, categories and homepage — inherited from the source today
  └── a per-crate README
```

*(Verified 2026-09-25 against the published `bite-gpui` 1.20.203 and
`pipeline/stage.py`.)*
