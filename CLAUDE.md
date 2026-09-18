# GPD Control — notes for Claude

A native desktop application for GW Instek GPD-series programmable DC power
supplies. Python throughout: a serial/instrument layer with no UI knowledge, and
a PySide6 (Qt) widget UI on top.

## There is no web version

The UI is **native Qt widgets**. There is no HTML, no embedded browser, no
webview, no local HTTP server, and no `--web` flag. The app must stay fully
usable offline; the only network call in the whole program is the optional
release check, which fails quietly.

`tests/test_ui.py::TestNoWebAnywhere` enforces this — it asserts that
`gpd3303s.server` and `gpd3303s.desktop` cannot be imported, that no web
framework appears in `pyproject.toml`, that the CLI offers no web flags, and
that nothing opens a socket. The CI wheel check fails if any web asset is
packaged. Do not weaken those tests; they exist because the UI was once a web
page and that was explicitly rejected.

## Keep the README current

**Any change that alters what the README describes must update the README in the
same commit.** The README is the only documentation users see, so a stale one is
a bug like any other. Changes that affect it include:

- Anything a user types: CLI flags, install commands, the one-liner.
- Anything a user sees: the window layout, views, status bar, controls.
- Supported models, channel ratings, baud rates, or protocol behaviour.
- Install or update mechanics, including the desktop entry and the icon.
- Troubleshooting: if a failure mode is found and fixed, say so there.

Not every change touches it — an internal refactor or a new test usually does
not. Judge by whether a user's experience or instructions changed. When in
doubt, re-read the affected README section and confirm it is still true. The
test count quoted under Development is part of that.

## Layout

| Path | Role |
|---|---|
| `src/gpd3303s/protocol.py` | Command encoding and response parsing. No I/O. |
| `src/gpd3303s/device.py` | Serial transport, polling thread, protection watchdog, discovery |
| `src/gpd3303s/simulator.py` | In-process fake supply |
| `src/gpd3303s/sequencer.py` | Timed setpoint sequences |
| `src/gpd3303s/recorder.py` | CSV logging |
| `src/gpd3303s/updater.py` | GitHub release checks and self-upgrade |
| `src/gpd3303s/assets.py` | Where the packaged icon lives. No Qt import, ever. |
| `src/gpd3303s/doctor.py` | `--doctor`: which build is installed, and what shadows it. No Qt import. |
| `src/gpd3303s/desktop_entry.py` | The Linux menu entry. No Qt import. |
| `packaging/` | The single-file standalone Linux build (PyInstaller). |
| `src/gpd3303s/cli.py` | Argument parsing; builds the app and runs the Qt loop |
| `src/gpd3303s/ui/app.py` | `MainWindow`: app bar, sidebar, instrument panel, status bar |
| `src/gpd3303s/ui/views.py` | Monitor, Sequencer, Memory, Protection, Console |
| `src/gpd3303s/ui/channel.py` | Per-channel readouts and setpoint controls |
| `src/gpd3303s/ui/chart.py` | Strip chart drawn with `QPainter` |
| `src/gpd3303s/ui/theme.py` | Light/dark palettes, `QPalette` and the Qt stylesheet |
| `src/gpd3303s/ui/bridge.py` | Device callbacks → Qt signals, for thread safety |
| `src/gpd3303s/resources/` | Application icon (`icon.png`, `icon.svg`) |

The dependency arrow points one way: `ui/` imports the instrument layer, never
the reverse. Anything under `ui/` is the only place PySide6 may be imported, and
`cli.py` imports `ui/` late, inside `run_app`, so every other flag works on a
machine that cannot load Qt at all.

That last part is load-bearing, not tidiness. `--icon-path` once imported
`ui/app.py` to find the icon, and on a box without Qt's system libraries it
died with `ImportError: libEGL.so.1` — which is exactly the box the installer
runs on when it asks a fresh installation where its icon is. `assets.py` exists
so that lookup needs no Qt, and `tests/test_cli.py` runs the CLI with every
PySide6 import refused to keep it that way. Anything a non-GUI flag needs
belongs outside `ui/`.

## Commands

```sh
uv venv && uv pip install -e ".[dev]"
uv run pytest                              # no hardware needed; the simulator covers it
QT_QPA_PLATFORM=offscreen uv run pytest    # headless (this is what CI does)
uv run gpd3303s --simulate                 # the app against the simulator
```

Qt needs `libegl1 libxkbcommon-x11-0 libdbus-1-3` on a bare Linux image; CI
installs them. For a real window on a headless box, `Xvfb :99 -screen 0
1440x960x24` plus `DISPLAY=:99 QT_QPA_PLATFORM=xcb`.

## Threading

The device poller runs on its own thread and must never touch a widget.
`ui/bridge.py` exists solely to convert its callbacks into Qt signals, which Qt
queues onto the GUI thread. The update checker and the in-place upgrade do the
same thing via `MainWindow.update_found` / `update_applied`. Any new background
worker follows that pattern rather than calling into widgets directly.

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

- The shell is an app bar, a sidebar, a pinned instrument panel, a stacked view
  and a status bar. Sections live in `SECTIONS` and must match the stack order.
- Qt stylesheets beat `QFont` sizes, and a per-widget stylesheet beats the
  app-level one. Size a readout in its own stylesheet, not with `setPointSize`.
- Setpoint widgets guard in-progress edits: polling must never overwrite a field
  the user is typing in. Sliders send on release, not on every step — the link
  can be 9600 baud.
- Chart colours come from a palette validated for colour-vision separation in
  both themes, and every series also carries a direct end label and a live-value
  legend so colour is never the only cue. Never a dual-axis chart: one measure
  per axis.
- Stick to ASCII plus well-supported glyphs in widget text; some glyphs render
  as boxes under the bundled Qt fonts.

## Two builds must never share a version number

`__version__` is the only thing a user can point at to say which build they
have. When the UI was replaced, both the old web build and the new native one
reported `1.0.0`, so a stale launcher earlier on PATH kept winning and nothing
in the output revealed it — the user reinstalled repeatedly and kept getting the
web page. Bump the version in the same change that alters what the app *is*.

`gpd3303s --doctor` exists for the same reason. It reports the version, the
install location, whether Qt loads, any leftover web module, and **every**
`gpd3303s` on PATH with the winning one marked; it exits non-zero when there is
more than one or when a retired module is present. Both installers run the same
shadow check and refuse to report success when they have been shadowed. Keep
`doctor.py` Qt-free: it has to run on the machine where the window won't open.

## Per-channel output is parking, not switching

The GPD has one output switch for both channels and no per-channel command.
`set_channel_enabled(ch, False)` therefore drives that channel to 0 V / 0 A and
remembers its setpoints in `PowerSupply._parked`, writing them back on re-enable.
Consequences worth keeping straight:

- `set_voltage` / `set_current` on a parked channel update the remembered target
  instead of writing to the instrument. The panel keeps showing the setpoint, so
  it has to mean "what it will return to".
- `_refresh_setpoints` skips parked channels; the instrument really is at 0 V and
  reading that back would erase the remembered value.
- A connect clears all parking. Never present parking as isolation in the UI or
  the docs — the terminals are still connected.

## A release can be older than the code

The one-liner asked GitHub for the *latest release* and installed that. The
latest release was v1.0.0, carrying the retired web interface, while the default
branch was already 1.1.0 with the native app — so every `curl | sh` installed
the web UI and looked broken, no matter how many times it was run. This was the
real cause of "I reinstalled and still get the web page"; PATH shadowing was a
second, smaller problem on top.

`resolve_ref` in `install.sh` now compares the newest release against
`__version__` on the default branch and takes whichever is newer, so it
self-heals once a current release exists. `apply_update` refuses any tag that is
not strictly newer than what is running, so "update" can never walk backwards
onto an older build. `tests/test_no_downgrade.py` pins both.

When cutting a release, tag the commit that carries the bumped `__version__` —
the workflow compares them and will refuse a tag placed on an earlier commit.

## The standalone Linux build is the escape hatch

`packaging/build-linux-app.sh` freezes the app into one file with PyInstaller:
Python, Qt and the code, no pip, no virtualenv, nothing on PATH. When an install
misbehaves, that is what to reach for, because the thing that runs is the file
you ran. The release workflow builds it, verifies it reports the tag's version
and says "native Qt widgets" under `env -i`, and refuses to publish without it.

## Read and write text with an explicit encoding

`open(path)`, `read_text()` and `write_text(data)` use the locale's encoding,
which is **cp1252 on Windows**. Every file here has em dashes in it, so a bare
read passes on Linux and macOS and dies on Windows with `UnicodeDecodeError:
'charmap' codec can't decode byte 0x8f`. That broke all three Windows jobs once
while every Linux job stayed green.

Always pass `encoding="utf-8"`. `tests/test_text_encoding.py` walks `src/` and
`tests/` with an AST check and fails on any text call that doesn't. Note that
`read_text(encoding)` takes it first but `write_text(data, encoding)` takes it
second.

## Releasing

Tag `vX.Y.Z` must match `__version__`; the workflow refuses otherwise. Push the
tag and the release workflow builds and publishes.

**If a release run fails, do not use "Re-run jobs".** A re-run replays the
workflow file *as it existed at the tag*, so a fix landed on `main` afterwards
is not picked up and it fails the same way. Use **Actions → Release → Run
workflow** with the tag as input: that takes the workflow from `main` and checks
out the tag's code. The publish step is idempotent and will repair a
half-published release.
