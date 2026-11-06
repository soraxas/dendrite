## Decisions

- **Links become `<button className="link">`.** A shared `LinkButton` component replaces the `<a onClick>` pattern for in-app actions. Real navigations use `<a href="#/…">` from `router.href`, which also gives middle-click and "open in new tab" for free.
- **Archive order.** Archive the oldest changes first, so later deltas apply on top of their baselines. Run `openspec validate --all` after each one.
- **The test registry.** Prefer a fixture that snapshots and restores `task._STEPS` around the CLI check test over changing `dendrite check`'s behaviour.
