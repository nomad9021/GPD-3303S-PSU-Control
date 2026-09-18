# GPD Control

A native desktop application for **GW Instek GPD-series** programmable DC power
supplies — a replacement for the bundled Windows software, with a cleaner
interface, real light and dark themes, live charting, data logging, and a step
sequencer.

It is a normal desktop program: it finds your supply on its own, works entirely
offline, and updates itself. Runs on Windows, macOS and Linux.

| | |
|---|---|
| **Supported** | GPD-2303S · GPD-3303S · GPD-3303D · GPD-4303S |
| **Connection** | USB (virtual COM port) or RS-232, 9600 / 57600 / 115200 baud (9600 from the factory) |
| **Requires** | Python 3.9+ (the installer sets up its own environment) |
| **Interface** | Native Qt widgets — no browser, no web page, no local server |

---

## Install

### Linux: just download it

```sh
curl -fLO https://github.com/nomad9021/GPD-3303S-PSU-Control/releases/latest/download/GPD-Control-x86_64
chmod +x GPD-Control-x86_64
./GPD-Control-x86_64
```

That URL always points at the newest release, so it never goes stale.

That single file contains Python, Qt and the app. Nothing is installed, nothing
goes on your `PATH`, and no older copy can shadow it — the thing that runs is the
file you ran. This is the shortest path to a working app, and the one to reach
for if an install has ever misbehaved.

To put it in your applications menu, move it somewhere permanent first:

```sh
mkdir -p ~/.local/bin && mv GPD-Control-x86_64 ~/.local/bin/
~/.local/bin/GPD-Control-x86_64 --install-desktop-entry
```

The menu entry points at that exact file by absolute path, so it launches the
app you downloaded rather than whatever `PATH` happens to resolve.

### Or install it from source

**Linux / macOS**

```sh
curl -fsSL https://raw.githubusercontent.com/nomad9021/GPD-3303S-PSU-Control/main/install.sh | sh
```

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/nomad9021/GPD-3303S-PSU-Control/main/install.ps1 | iex
```

The installer picks the most isolated tool you already have — `uv`, then `pipx`,
then a private virtual environment — installs the newest release, and puts a
`gpd3303s` launcher on your PATH. Nothing is installed into your system Python.

Then:

```sh
gpd3303s
```

On Linux the installer also adds **GPD Control** to your applications menu with
its own icon, so you can launch it from the desktop like any other program.

The installer prints the version it installed and checks that it is the one your
shell will actually run. If an older copy is earlier on your `PATH`, it says so
and exits non-zero rather than reporting success — see
[I installed it but I'm still getting the old version](#i-installed-it-but-im-still-getting-the-old-version).

### Checking what you have

```sh
gpd3303s --doctor
```

Prints the version, where it is installed, whether Qt can load, and **every**
`gpd3303s` on your `PATH` with the one your shell picks marked. It needs no
window and no hardware, so it works even when the app won't start.

### Installing from the release files instead

Every release also attaches a wheel and a source archive. To install one by hand
from the [releases page](https://github.com/nomad9021/GPD-3303S-PSU-Control/releases/latest):

```sh
pip install "https://github.com/nomad9021/GPD-3303S-PSU-Control/releases/download/v1.1.1/gpd3303s_control-1.1.1-py3-none-any.whl"
```

There are no optional extras to choose — everything the app needs is a plain
dependency. Doing it by hand skips what the installer handles for you: the
isolated environment, the PATH launcher, and the Linux menu entry and icon.

### It is a real application

The window is drawn with **native Qt widgets** (PySide6). There is no embedded
browser, no HTML, no local HTTP server, and no network access at all beyond the
optional release check — which fails quietly if you are offline. Unplug the
network and every feature still works.

The layout is an application layout: a title bar with the connection state, a
sidebar for the sections, an instrument panel that stays put while you work, and
a status bar along the bottom showing the link, the output state, the CV/CC mode
of each channel, recording state and total power. Charts are drawn directly by
the app rather than by a charting library.

### No hardware yet?

```sh
gpd3303s --simulate
```

This connects to a built-in simulator that speaks the real protocol and models a
resistive load, so you can explore every feature — including CV/CC transitions —
without a supply on the bench.

---

## Using it

### Connecting

**You should not have to do anything.** On launch the app searches every serial
port at every supported baud rate for something that answers `*IDN?` as a GPD,
and connects to it. If you plug the supply in later, press **Find supply**.

Failing that, pick a port and baud rate from the instrument bar and press
**Connect**; **Rescan** re-reads the list of ports. On Windows the supply
appears as a `COM` port once GW Instek's USB driver is installed; on Linux it is
usually `/dev/ttyUSB0`, on macOS `/dev/tty.usbserial-*`.

The baud rate has to match the instrument's own setting (`Utility` on the front
panel). **The factory default is 9600** — a mismatch is the usual reason a
connection appears to succeed but every reading stays at zero, which is exactly
what auto-detection avoids.

From a terminal, `gpd3303s --detect` reports what it finds and exits.

On connect the app sends `REMOTE`, which is what lets the instrument accept
setpoints from the host; on disconnect it sends `LOCAL` to give the front panel
back. Quitting the app always hands control back to the panel.

> **Linux permissions.** Opening `/dev/ttyUSB*` requires membership of the
> `dialout` group:
> ```sh
> sudo usermod -aG dialout "$USER"   # then log out and back in
> ```
> The installer checks this and tells you if it's missing.

### Themes

**Light**, **Auto** and **Dark** sit in the title bar. Auto follows your
desktop's own light/dark preference. The choice is remembered between runs.

### Channel control

Each channel gets a panel with live voltage, current and power, a **CV/CC** badge
showing which loop is regulating, and setpoints you can drive by typing a value,
dragging the slider, or hitting a quick-set button (3.3 V, 5 V, 9 V, 12 V, 15 V,
24 V).

Setpoints are clamped to the channel's rating before they're sent, and the
display never fights you: a field you are editing is left alone until you commit
it with <kbd>Enter</kbd> or by clicking away. Sliders send on release rather than
on every pixel, so a 9600-baud link is not flooded while you drag.

### Switching one channel off

Each channel has its own **On / Off** switch in its header, so you can power one
rail down and leave the other running — useful when you're bringing up a board
one supply at a time.

The instrument has a single output switch for both channels and no per-channel
command, so "off" here means **parked**: the channel is driven to 0 V / 0 A while
its setpoint is remembered and written back when you switch it on again. Editing
the setpoint while a channel is parked changes what it will return to, and the
other channel is never touched.

> Parking is not isolation. The output terminals are still connected, at 0 V
> into a 0 A limit. For anything that needs a real break, pull the lead.

### What each channel has drawn

Under the quick-set buttons, each channel shows the charge and energy it has
drawn — **mAh** and **mWh**, switching to Ah and Wh once the numbers get large —
along with the voltage and current range it has covered. That makes the app
usable as a bench coulomb counter for battery and sleep-current work.

**Reset totals** in the Monitor toolbar zeroes the counters and the min/max
marks. They also reset whenever you connect. A stall — a suspended laptop, a
stuck link — cannot invent charge: any gap longer than five seconds is treated
as a pause rather than as load.

### Master bar

* **Output** — one toggle for both channels, as on the instrument.
* **Tracking** — Independent, Series or Parallel.
* **Beeper** — on or off.
* **Total** — combined output power.

> The GPD-3303S's fixed 2.5 V / 3.3 V / 5 V rail is switched by the front panel
> only; there is no remote command for it, so it isn't shown here.

### Monitor

Three strip charts — voltage, current and power — each on its own axis, with both
channels overlaid, a live value in the legend, a direct label at the trace end,
and a crosshair readout on hover. Choose a window from 30 seconds to 15 minutes
and a poll rate to match your link.

**Start CSV log** writes every sample to a timestamped CSV with one row per poll
and a column triplet per channel.

### Sequencer

The feature the vendor software doesn't have: a table of steps, each applying
setpoints and holding for a dwell time, with a loop count, a progress bar, and an
automatic output-off at the end. Use it for burn-in, staircase ramps, or
repeatable test profiles.

**Save…** and **Load…** write and read the whole sequence — steps, loop count and
the output-off choice — as a JSON file, so a test profile can be kept alongside
the rest of a project. A file saved against a different supply loads what fits;
channels the connected instrument doesn't have are left at zero.

### Memory

The instrument's own **M1–M4** slots, each with Save and Recall, plus named local
presets stored by the app for as many setups as you like.

### Protection

The instrument has no programmable OVP/OCP, so the app enforces trip points on
the host: arm a channel, set over-voltage, over-current and over-power limits,
and the output is dropped within one poll of a breach, with a banner naming the
cause.

This is a **software** limit polled a few times a second — it is a convenience,
not a safety interlock. It cannot react faster than the poll interval, and it
will not protect anything if the app is closed or the cable is pulled. Use real
fusing and the instrument's own current limit for anything that matters.

### Console

Send raw commands and read the replies, with a built-in reference table.
Anything ending in `?` is treated as a query.

### Keyboard

| Key | Action |
|---|---|
| <kbd>Space</kbd> | Toggle the output |
| <kbd>Esc</kbd> | Disable the output immediately |

---

## Updating

The app checks GitHub for new releases in the background and raises a banner
when one lands; **Update now** upgrades in place using whichever tool installed
it, and tells you to restart when it's done. **Dismiss** puts the banner away
until the next check.

If you are offline the check fails silently and the app carries on — it is the
only network call the app ever makes. An installation the app didn't perform (a
distro package, a source checkout) gets the banner without the button, since it
has no upgrade path to drive.

From the command line:

```sh
gpd3303s --check-update
gpd3303s --update
```

To turn the background check off, set `"check_for_updates": false` in
`settings.json` — `gpd3303s --where` prints its path.

---

## Command line

```
gpd3303s                     open the application window (finds the supply for you)
gpd3303s --simulate          use the built-in simulator
gpd3303s --detect            find an attached supply, print it, and exit
gpd3303s --connect COM3      connect to a specific port at startup
gpd3303s --no-autoconnect    do not search for an instrument at startup
gpd3303s --list-ports        print every detected serial port
gpd3303s --doctor            report which build is installed and where
gpd3303s --install-desktop-entry   add it to the Linux applications menu
gpd3303s --where             print config and log locations
gpd3303s --icon-path         print the path to the application icon
gpd3303s --check-update      check for a newer release
gpd3303s --update            install the newest release
gpd3303s --version
```

Settings live in your platform's config directory and CSV logs in its data
directory; `--where` prints both.

---

## The protocol

GPD supplies do not speak full SCPI. They use a compact ASCII command set,
newline-terminated, where only queries produce a response.

| Command | Meaning |
|---|---|
| `*IDN?` | Identify the instrument |
| `VSET<n>:<v>` / `VSET<n>?` | Set / query a voltage setpoint |
| `ISET<n>:<a>` / `ISET<n>?` | Set / query a current limit |
| `VOUT<n>?` / `IOUT<n>?` | Measured output |
| `OUT1` / `OUT0` | Enable / disable the output |
| `TRACK0` / `TRACK1` / `TRACK2` | Independent / series / parallel |
| `BEEP1` / `BEEP0` | Beeper on / off |
| `SAV<n>` / `RCL<n>` | Save / recall memory 1–4 |
| `STATUS?` | Eight status bits |
| `ERR?` | Last error |
| `REMOTE` / `LOCAL` | Take / release host control |
| `BAUD<n>` | `0` 115200, `1` 57600, `2` 9600 |

`STATUS?` returns eight `0`/`1` characters, least-significant bit first:

| Bit | Meaning |
|---|---|
| 0 | CH1 regulation mode (0 = CC, 1 = CV) |
| 1 | CH2 regulation mode |
| 2–3 | Tracking: `01` independent, `11` series, `10` parallel |
| 4 | Beeper enabled |
| 5 | Output enabled |
| 6–7 | Baud rate: `00` 115200, `01` 57600, `10` 9600 |

Firmware revisions differ in whether measurements carry a unit suffix
(`5.000` vs `5.000V`), so every response is parsed leniently.

Other details taken from the manual and enforced in `tests/test_manual_conformance.py`:

* Commands are capped at **15 characters**, are case-insensitive, and terminate
  with `\n` (or `\r\n`).
* Minimum response time is **10 ms at 115200 baud**, and longer on slower links,
  so the inter-command delay scales with the negotiated rate. It is paid before
  the *next* command rather than after the last, and only after a command the
  instrument did not answer — a reply is proof it has finished.
* Rated output is **0–30 V / 0–3 A** per main channel (60 V in series, 6 A in
  parallel). The command parser accepts up to 32 V / 3.2 A, but setpoints are
  clamped to the rated figures.
* On the GPD-3303S, CH3 is a fixed 2.5 / 3.3 / 5 V rail switched on the front
  panel — there is no remote command for it, so it is not shown.

---

## Development

```sh
git clone https://github.com/nomad9021/GPD-3303S-PSU-Control
cd GPD-3303S-PSU-Control
uv venv && uv pip install -e ".[dev]"
uv run pytest                    # 287 tests, no hardware needed
uv run gpd3303s --simulate
```

On a headless machine, run the tests against Qt's offscreen backend:

```sh
QT_QPA_PLATFORM=offscreen uv run pytest
```

Layout:

| Path | Role |
|---|---|
| `src/gpd3303s/protocol.py` | Command encoding and response parsing (no I/O) |
| `src/gpd3303s/device.py` | Serial transport, polling thread, protection watchdog, discovery |
| `src/gpd3303s/simulator.py` | In-process fake supply |
| `src/gpd3303s/sequencer.py` | Timed setpoint sequences |
| `src/gpd3303s/recorder.py` | CSV logging |
| `src/gpd3303s/updater.py` | GitHub release checks and self-upgrade |
| `src/gpd3303s/ui/app.py` | The main window: app bar, sidebar, instrument panel, status bar |
| `src/gpd3303s/ui/views.py` | Monitor, Sequencer, Memory, Protection and Console views |
| `src/gpd3303s/ui/channel.py` | Per-channel readouts and setpoint controls |
| `src/gpd3303s/ui/chart.py` | Strip chart, drawn with `QPainter` |
| `src/gpd3303s/ui/theme.py` | Light and dark palettes and the Qt stylesheet |
| `src/gpd3303s/ui/bridge.py` | Marshals device callbacks onto the GUI thread as Qt signals |
| `src/gpd3303s/doctor.py` | `--doctor`: which build is installed, and what shadows it |
| `src/gpd3303s/desktop_entry.py` | `--install-desktop-entry`: the Linux menu entry |
| `packaging/build-linux-app.sh` | Builds the single-file standalone Linux app |

The device layer knows nothing about the UI: it pushes telemetry to callbacks,
and `bridge.py` turns those into Qt signals so the polling thread never touches a
widget. Traffic the other way goes through the same file's `CommandQueue`: every
setpoint, output toggle and memory recall is a serial write that waits on the
instrument, so it runs on a worker thread rather than freezing the window.

Chart colours come from a palette validated for colour-vision deficiency in both
themes; every series also carries a direct end label and a live-value legend so
colour is never the only cue.

---

## Troubleshooting

### I installed it but I'm still getting the old version

Almost always a second copy earlier on your `PATH`. A `pip install` run as root
leaves one in `/usr/local/bin`, which comes before `~/.local/bin` nearly
everywhere, so it keeps winning however many times you reinstall.

```sh
gpd3303s --doctor
```

That lists every `gpd3303s` on your `PATH` and marks the one your shell runs.
Delete the ones you don't want:

```sh
sudo rm /usr/local/bin/gpd3303s      # whatever --doctor points at
hash -r                              # forget the shell's cached path
gpd3303s --doctor                    # confirm
```

The installer performs this check itself and exits non-zero rather than claiming
success when it has been shadowed.

If `--doctor` reports **"this install still carries the retired web build"**,
that copy predates the native app — remove it and reinstall with the one-liner.

**The port isn't listed.** Install GW Instek's USB driver (Windows), or check
`dmesg` after plugging in (Linux). `gpd3303s --list-ports` shows what the app can
see.

**Connects but readings stay at zero.** The baud rate doesn't match the
instrument's `Utility` setting. Press **Find supply**, which tries every rate,
or set it by hand — the factory default is 9600.

**"Permission denied" on Linux.** Add yourself to the `dialout` group (above).

**Values look stale.** Check the poll rate in the Monitor toolbar; 2.5 Hz is a
good default. A poll is five queries, which at the factory 9600 baud costs well
under a tenth of the 400 ms interval, so the rate you pick is the rate you get.
At 5 Hz with the 15-minute chart window on screen the drawing starts to be the
expensive part, not the link.

**The window won't open on a minimal Linux install.** Qt needs a few system
libraries that desktop images already have but containers and server installs
often don't:

```sh
sudo apt install libegl1 libxkbcommon-x11-0 libdbus-1-3     # Debian / Ubuntu
sudo dnf install libglvnd-egl libxkbcommon-x11 dbus-libs    # Fedora
```

The terminal commands don't need any of that: `--detect`, `--list-ports`,
`--where` and the rest work on a machine that cannot open a window at all, so
you can still check the link from a shell.

**The front panel is locked after using the app.** The instrument stays in
remote mode until it is told otherwise. The app sends `LOCAL` when it
disconnects, so use Disconnect or close the window; if it was killed outright,
press `Local` on the panel or power-cycle it.

---

## License

MIT — see [LICENSE](LICENSE).

Not affiliated with or endorsed by Good Will Instrument Co., Ltd.
