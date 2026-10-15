# @CRATE@

Part of **[bite-gpui](@SITE@)** — Zed's [GPUI](https://github.com/zed-industries/zed) re-cut into a
modular, acyclic stack of crates: install a tier on its own, or the whole stack behind one facade.

This package is the published build of `@ORIGINAL@`, staged from the `@BRANCH@` line of this
project's repository at `@VERSION@`.

- **Website and guides** — <@SITE@>
- **The stack, tier by tier** — <@SITE@/#layers>
- **Source, and the branches this is built from** — <@REPOSITORY@>

## Install

```sh
# this crate
cargo add @CRATE@
@FACADE_ADD@
```

## Hello, window

```rust
use gpui::*;

fn main() {
    gpui::application().run(|cx: &mut App| {
        cx.open_window(WindowOptions::default(), |_, cx| {
            cx.new(|_| MyView)
        })
        .unwrap();
    });
}
```

## Swap a seam

Every seam in the stack is a trait an implementation can replace without forking the engine.
[`@PARLEY@`](@SITE@/parley) is the published second implementation of `TextSystem`, and swapping it
in is one builder call:

```sh
cargo add @PARLEY@
```

```rust
use std::cell::RefCell;
use std::rc::Rc;
use gpui::*;
use gpui_parley::ParleyTextSystem;

fn main() {
    let metrics = Rc::new(RefCell::new(PhaseMetrics::default()));

    application()
        .with_text_system(ParleyTextSystem::new())
        .with_frame_pipeline({
            let metrics = metrics.clone();
            move |_window_id| {
                Box::new(
                    StandardImmediatePipeline
                        .max_fps(60)
                        .instrumented(metrics.clone())
                )
            }
        })
        .run(|cx: &mut App| { /* … */ });
}
```

## Headless test — no display server, no GPU

With the `test-support` feature, a window is drawn in-process:

```rust
#[gpui::test]
fn renders_without_a_display(cx: &mut TestAppContext) {
    let window = cx.update(|cx| {
        cx.open_window(Default::default(), |_, cx| cx.new(|_| MyView))
            .unwrap()
    });
    let mut cx = VisualTestContext::from_window(window.into(), cx);

    // The window is drawn in-process: no display server, no GPU.
    let bounds = cx.debug_bounds("submit").unwrap();
    assert_eq!(bounds.size.height, px(32.));
}
```

## Guides

- [Wraps & swaps](@SITE@/swaps) — the two extension tiers, and the published crates behind them
- [Parley](@SITE@/parley) — how to swap the text engine, and the measured trade that comes with it
- [Morphorm](@SITE@/morphorm) — how to swap the layout solver, and its conformance numbers
- [Pass](@SITE@/pass) — how to wrap the frame pipeline, and the rate and ledger that come with it
- [Demos](@SITE@/demos) — projects built on the published crates
- [Compare](@SITE@/compare) — the comparisons that can actually be run

## Provenance and licence

Derived from Zed's gpui: restructured, renamed and published as `@CRATE@` under `@LICENSE@`.
`NOTICE` records the modifications the licence asks to be made prominent, and `LICENSE-*` carries
the licence text.

    source:   @REPOSITORY@
    branch:   @BRANCH@@UPSTREAM@
    version:  @VERSION@
    commit:   @COMMIT@
