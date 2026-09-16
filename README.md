# GPD Control

A modern control panel for **GW Instek GPD-series** programmable DC power supplies —
a replacement for the bundled Windows software, with a cleaner interface, real
light and dark themes, live charting, data logging, and a step sequencer.

Runs on Windows, macOS and Linux. One-line install, and it updates itself.

| | |
|---|---|
| **Supported** | GPD-2303S · GPD-3303S · GPD-3303D · GPD-4303S |
| **Connection** | USB (virtual COM port) or RS-232, 9600 / 57600 / 115200 baud |
| **Requires** | Python 3.9+ (the installer sets up its own environment) |

---

## Install

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

The app starts a local server and opens your browser. Nothing is exposed to the
network: it binds to `127.0.0.1` only.

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

Pick a port from the dropdown and press **Connect**. On Windows the supply shows
up as a `COM` port once GW Instek's USB driver is installed; on Linux it is
usually `/dev/ttyUSB0`, on macOS `/dev/tty.usbserial-*`.

The baud rate must match the instrument's own setting (`Utility` on the front
panel). 115200 is the factory default.

> **Linux permissions.** Opening `/dev/ttyUSB*` requires membership of the
> `dialout` group:
> ```sh
> sudo usermod -aG dialout "$USER"   # then log out and back in
> ```
> The installer checks this and tells you if it's missing.

### Channel control

Each channel gets a card with live voltage, current and power, a **CV/CC** badge
showing which loop is regulating, and setpoints you can drive by typing a value,
dragging the slider, or hitting a quick-set chip (3.3 V, 5 V, 12 V…).

Setpoints are clamped to the channel's rating before they're sent, and the
display never fights you: a field you're editing is left alone until you commit
it with <kbd>Enter</kbd> or by clicking away.

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
and a crosshair tooltip on hover. Choose a window from 30 seconds to 15 minutes.

**Start CSV log** writes every sample to a timestamped CSV with one row per poll
and a column triplet per channel. **Table view** shows the same samples as text.

### Sequencer

The feature the vendor software doesn't have: a list of steps, each applying
setpoints and holding for a dwell time, with a loop count and an automatic
output-off at the end. Use it for burn-in, staircase ramps, or repeatable test
profiles. Sequences export and import as JSON.

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

The app checks GitHub for new releases in the background and shows a banner when
one lands; **Update now** upgrades in place using whichever tool installed it.

From the command line:

```sh
gpd3303s --check-update
gpd3303s --update
```

Turn the background check off in the app's settings (it's stored in
`settings.json`, see `gpd3303s --where`).

---

## Command line

```
gpd3303s                     start the app and open a browser
gpd3303s --simulate          auto-connect to the built-in simulator
gpd3303s --connect COM3      auto-connect to a port at startup
gpd3303s --list-ports        print detected serial ports
gpd3303s --no-browser        start the server without opening a browser
gpd3303s --port 9000         serve on a different port
gpd3303s --where             print config and log locations
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

---

## Development

```sh
git clone https://github.com/nomad9021/GPD-3303S-PSU-Control
cd GPD-3303S-PSU-Control
uv venv && uv pip install -e ".[dev]"
uv run pytest                    # 72 tests, no hardware needed
uv run gpd3303s --simulate
```

Layout:

| Path | Role |
|---|---|
| `src/gpd3303s/protocol.py` | Command encoding and response parsing (no I/O) |
| `src/gpd3303s/device.py` | Serial transport, polling thread, protection watchdog |
| `src/gpd3303s/simulator.py` | In-process fake supply |
| `src/gpd3303s/server.py` | HTTP API and SSE telemetry stream |
| `src/gpd3303s/sequencer.py` | Timed setpoint sequences |
| `src/gpd3303s/recorder.py` | CSV logging |
| `src/gpd3303s/updater.py` | GitHub release checks and self-upgrade |
| `src/gpd3303s/web/` | UI — plain HTML, CSS and ES modules, no build step |

The UI has no bundler and no dependencies. Edit the files in `web/` and reload.

Chart colours come from a palette validated for colour-vision deficiency in both
themes; the light-mode steps for channels 3–4 sit below 3:1 against the surface,
which is why every series also carries a direct end label, a live-value legend
and a table view.

---

## Troubleshooting

**The port isn't listed.** Install GW Instek's USB driver (Windows), or check
`dmesg` after plugging in (Linux). `gpd3303s --list-ports` shows what the app can
see.

**Connects but readings stay at zero.** The baud rate probably doesn't match the
instrument's `Utility` setting. Try 115200, 57600, then 9600.

**"Permission denied" on Linux.** Add yourself to the `dialout` group (above).

**Values look stale.** Lower the poll interval in the Monitor toolbar. Very short
intervals over a slow serial link can queue up; 2.5 Hz is a good default.

---

## License

MIT — see [LICENSE](LICENSE).

Not affiliated with or endorsed by Good Will Instrument Co., Ltd.
