# GPD Control — notes for Claude

A desktop application for GW Instek GPD-series programmable DC power supplies.
Python backend driving the serial link; the UI is HTML rendered in the OS
webview through pywebview, not a web page.

## Keep the README current

**Any change that alters what the README describes must update the README in the
same commit.** The README is the only documentation users see, so a stale one is
a bug like any other. Changes that affect it include:

- Anything a user types: CLI flags, install commands, the one-liner.
- Anything a user sees: the window layout, views, status bar, controls.
- Supported models, channel ratings, baud rates, or protocol behaviour.
- Install or update mechanics, including the extras and renderer fallback.
- Troubleshooting: if a failure mode is found and fixed, say so there.

Not every change touches it — an internal refactor or a new test usually does
not. Judge by whether a user's experience or instructions changed. When in
doubt, re-read the affected README section and confirm it is still true.

## Layout

| Path | Role |
|---|---|
| `src/gpd3303s/protocol.py` | Command encoding and response parsing. No I/O. |
| `src/gpd3303s/device.py` | Serial transport, polling thread, protection watchdog, discovery |
| `src/gpd3303s/simulator.py` | In-process fake supply |
| `src/gpd3303s/server.py` | HTTP API and SSE telemetry |
| `src/gpd3303s/desktop.py` | Native window and the server behind it |
| `src/gpd3303s/sequencer.py` | Timed setpoint sequences |
| `src/gpd3303s/recorder.py` | CSV logging |
| `src/gpd3303s/updater.py` | GitHub release checks and self-upgrade |
| `src/gpd3303s/web/` | UI — plain HTML, CSS and ES modules, no build step |

## Commands

```sh
uv venv && uv pip install -e ".[dev,desktop]"
uv run pytest                 # no hardware needed; the simulator covers it
uv run gpd3303s --simulate    # native window against the simulator
uv run gpd3303s --web         # same UI in a browser, for headless work
```

## The protocol is specified, not guessed

`tests/test_manual_conformance.py` pins the wire format to the official
*GPD-X303S Programming Manual*, including its own worked examples. Treat that
file as the source of truth and cite the manual when changing it. Things the
manual settles and that are easy to get wrong:

- Factory baud is **9600**. At the wrong rate the instrument returns nothing,
  which looks like "connected but every reading is zero".
- `REMOTE` on connect, `LOCAL` on disconnect. Without `REMOTE` some units ignore
  setpoints; without `LOCAL` the front panel stays locked after exit.
- `STATUS?` is 8 bits, **least significant first**.
- Commands are capped at 15 characters.
- Rated output is 0–30 V / 0–3 A per main channel, though the command parser
  accepts up to 32 V / 3.2 A. Setpoints are clamped to the rated figures.

## UI conventions

The shell is a fixed app bar, sidebar, pinned instrument panel, scrolling view
and status bar. `tests/test_ui_shell.py` asserts its structure — including that
each shell region owns exactly one CSS rule, after `.toolbar` was once reused
for two different things and silently broke the layout.

Chart colours come from a palette validated for colour-vision separation in both
themes. Never a dual-axis chart: one measure per axis.

## Releasing

Tag `vX.Y.Z` must match `__version__`; the workflow refuses otherwise. Push the
tag and the release workflow builds and publishes.

**If a release run fails, do not use "Re-run jobs".** A re-run replays the
workflow file *as it existed at the tag*, so a fix landed on `main` afterwards
is not picked up and it fails the same way. Use **Actions → Release → Run
workflow** with the tag as input: that takes the workflow from `main` and checks
out the tag's code. The publish step is idempotent and will repair a
half-published release.
