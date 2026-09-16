/** Front-end controller: talks to the local API, renders state, owns the charts. */

import { StripChart } from "./chart.js";

const SERIES_VARS = ["--series-1", "--series-2", "--series-3", "--series-4"];

const state = {
  info: null,
  device: null,
  telemetry: null,
  settings: {},
  channels: [],
  charts: {},
  recorder: { active: false },
  sequence: { running: false },
  update: null,
  connected: false,
  /** Setpoint inputs the user is actively editing, so polling can't yank them. */
  editing: new Set(),
  samples: [],
  tableVisible: false,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// --------------------------------------------------------------------------- //
// HTTP helpers
// --------------------------------------------------------------------------- //

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok || (payload && payload.ok === false)) {
    const message = (payload && (payload.error || payload.detail)) || `HTTP ${response.status}`;
    throw new Error(message);
  }
  return payload;
}

function toast(message, kind = "info", timeout = 4200) {
  const host = $("#toast-host");
  const node = document.createElement("div");
  node.className = `toast toast--${kind}`;
  node.textContent = message;
  host.appendChild(node);
  setTimeout(() => node.remove(), timeout);
}

const fmt = (value, decimals) => (Number.isFinite(value) ? value.toFixed(decimals) : "—");

// --------------------------------------------------------------------------- //
// Theme
// --------------------------------------------------------------------------- //

function applyTheme(choice) {
  document.documentElement.dataset.theme = choice;
  $$("[data-theme-choice]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.themeChoice === choice));
  });
  // Charts read their colours from CSS variables; force a repaint.
  requestAnimationFrame(() => Object.values(state.charts).forEach((c) => c.draw()));
}

function initTheme() {
  const stored = localStorage.getItem("gpd-theme") || "system";
  applyTheme(stored);
  $$("[data-theme-choice]").forEach((button) => {
    button.addEventListener("click", () => {
      const choice = button.dataset.themeChoice;
      localStorage.setItem("gpd-theme", choice);
      applyTheme(choice);
      api("/api/settings", { method: "POST", body: { theme: choice } }).catch(() => {});
    });
  });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if ((document.documentElement.dataset.theme || "system") === "system") applyTheme("system");
  });
}

// --------------------------------------------------------------------------- //
// Channel cards
// --------------------------------------------------------------------------- //

function channelColorVar(index) {
  return SERIES_VARS[(index - 1) % SERIES_VARS.length];
}

function buildChannels(channels) {
  state.channels = channels;
  const host = $("#channels");
  host.innerHTML = "";

  channels.forEach((channel) => {
    const colorVar = channelColorVar(channel.index);
    const card = document.createElement("article");
    card.className = "card channel";
    card.style.setProperty("--channel-color", `var(${colorVar})`);
    card.dataset.channel = channel.index;
    card.innerHTML = `
      <div class="channel__head">
        <span class="channel__swatch" aria-hidden="true"></span>
        <span class="channel__name">${channel.label}</span>
        <span class="mode-badge" data-mode="cv" data-role="mode" title="Regulation mode">CV</span>
        <span class="channel__rating">${channel.max_voltage} V · ${channel.max_current} A</span>
      </div>
      <div class="readouts">
        <div class="readout readout--primary">
          <span class="readout__value" data-role="voltage">0.000<span class="readout__unit">V</span></span>
          <span class="readout__label">Voltage</span>
        </div>
        <div class="readout readout--primary">
          <span class="readout__value" data-role="current">0.000<span class="readout__unit">A</span></span>
          <span class="readout__label">Current</span>
        </div>
        <div class="readout readout--dim">
          <span class="readout__value" data-role="power">0.00<span class="readout__unit">W</span></span>
          <span class="readout__label">Power</span>
        </div>
      </div>
      <div class="setpoints">
        ${setpointMarkup("voltage", "Voltage set", "V", channel.max_voltage, 0.01)}
        ${setpointMarkup("current", "Current limit", "A", channel.max_current, 0.001)}
        <div class="preset-row" data-role="presets"></div>
      </div>`;
    host.appendChild(card);
    wireChannel(card, channel);
  });

  buildCharts();
  buildProtectionForms();
  buildSequencerDefaults();
}

function setpointMarkup(kind, label, unit, max, step) {
  return `
    <div class="setpoint" data-kind="${kind}">
      <div class="setpoint__row">
        <span class="setpoint__label">${label}</span>
        <input class="input input--num setpoint__input" type="number" min="0" max="${max}"
               step="${step}" value="0" data-role="${kind}-input" aria-label="${label}" disabled>
        <span class="setpoint__unit">${unit}</span>
      </div>
      <input type="range" min="0" max="${max}" step="${step}" value="0"
             data-role="${kind}-slider" aria-label="${label} slider" disabled>
    </div>`;
}

function wireChannel(card, channel) {
  const index = channel.index;

  ["voltage", "current"].forEach((kind) => {
    const input = card.querySelector(`[data-role="${kind}-input"]`);
    const slider = card.querySelector(`[data-role="${kind}-slider"]`);
    const key = `${index}-${kind}`;

    const commit = (value) => {
      const max = kind === "voltage" ? channel.max_voltage : channel.max_current;
      const clamped = Math.max(0, Math.min(Number(value) || 0, max));
      input.value = clamped.toFixed(kind === "voltage" ? 2 : 3);
      slider.value = clamped;
      api(`/api/channel/${index}/${kind}`, { method: "POST", body: { value: clamped } }).catch(
        (error) => toast(error.message, "error"),
      );
    };

    input.addEventListener("focus", () => state.editing.add(key));
    input.addEventListener("blur", () => {
      state.editing.delete(key);
      commit(input.value);
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") input.blur();
    });

    slider.addEventListener("pointerdown", () => state.editing.add(key));
    slider.addEventListener("input", () => {
      input.value = Number(slider.value).toFixed(kind === "voltage" ? 2 : 3);
    });
    const release = () => {
      if (!state.editing.has(key)) return;
      state.editing.delete(key);
      commit(slider.value);
    };
    slider.addEventListener("pointerup", release);
    slider.addEventListener("change", release);
  });

  // Quick-set chips for the voltages people actually use.
  const presets = [3.3, 5, 9, 12, 15, 24].filter((v) => v <= channel.max_voltage);
  const presetHost = card.querySelector('[data-role="presets"]');
  presets.forEach((volts) => {
    const chip = document.createElement("button");
    chip.className = "preset-chip";
    chip.type = "button";
    chip.textContent = `${volts} V`;
    chip.addEventListener("click", () => {
      if (!state.connected) return;
      const input = card.querySelector('[data-role="voltage-input"]');
      const slider = card.querySelector('[data-role="voltage-slider"]');
      input.value = volts.toFixed(2);
      slider.value = volts;
      api(`/api/channel/${channel.index}/voltage`, { method: "POST", body: { value: volts } }).catch(
        (error) => toast(error.message, "error"),
      );
    });
    presetHost.appendChild(chip);
  });
}

function renderChannels(telemetry) {
  telemetry.channels.forEach((reading) => {
    const card = document.querySelector(`.channel[data-channel="${reading.channel}"]`);
    if (!card) return;

    const set = (role, value, decimals, unit) => {
      const node = card.querySelector(`[data-role="${role}"]`);
      if (node) node.innerHTML = `${fmt(value, decimals)}<span class="readout__unit">${unit}</span>`;
    };
    set("voltage", reading.voltage, 3, "V");
    set("current", reading.current, 3, "A");
    set("power", reading.power, 2, "W");

    const badge = card.querySelector('[data-role="mode"]');
    // The CV/CC badge only means something while the output is live.
    const mode = telemetry.output ? reading.mode : "cv";
    badge.dataset.mode = mode;
    badge.textContent = mode.toUpperCase();
    badge.style.visibility = telemetry.output ? "visible" : "hidden";

    [["voltage", reading.voltage_set, 2], ["current", reading.current_set, 3]].forEach(
      ([kind, value, decimals]) => {
        if (state.editing.has(`${reading.channel}-${kind}`)) return;
        const input = card.querySelector(`[data-role="${kind}-input"]`);
        const slider = card.querySelector(`[data-role="${kind}-slider"]`);
        if (input && document.activeElement !== input) input.value = value.toFixed(decimals);
        if (slider) slider.value = value;
      },
    );
  });
}

// --------------------------------------------------------------------------- //
// Charts
// --------------------------------------------------------------------------- //

function buildCharts() {
  const stack = $("#chart-stack");
  stack.innerHTML = "";
  Object.values(state.charts).forEach((chart) => chart.destroy());
  state.charts = {};

  const specs = [
    { id: "voltage", title: "Voltage", unit: "V", decimals: 3, minSpan: 1 },
    { id: "current", title: "Current", unit: "A", decimals: 3, minSpan: 0.1 },
    { id: "power", title: "Power", unit: "W", decimals: 2, minSpan: 1 },
  ];

  specs.forEach((spec) => {
    const series = state.channels.map((channel) => ({
      key: String(channel.index),
      label: channel.label,
      colorVar: channelColorVar(channel.index),
    }));

    const card = document.createElement("div");
    card.className = "card chart-card";
    card.innerHTML = `
      <div class="chart-card__head">
        <span class="chart-card__title">${spec.title}</span>
        <span class="chart-card__hint">${spec.unit}</span>
        <div class="legend">
          ${series
            .map(
              (s) => `<span class="legend__item">
                  <span class="legend__swatch" style="background:var(${s.colorVar})"></span>
                  ${s.label}
                  <span class="legend__value" data-legend="${spec.id}-${s.key}">—</span>
                </span>`,
            )
            .join("")}
        </div>
      </div>
      <div class="chart-canvas"></div>`;
    stack.appendChild(card);

    state.charts[spec.id] = new StripChart(card.querySelector(".chart-canvas"), {
      unit: spec.unit,
      decimals: spec.decimals,
      minSpan: spec.minSpan,
      series,
    });
  });

  const windowSeconds = Number($("#chart-window").value);
  Object.values(state.charts).forEach((chart) => chart.setWindow(windowSeconds));
  buildSampleTableHead();
}

function pushSample(telemetry) {
  const t = telemetry.timestamp;
  const buckets = { voltage: {}, current: {}, power: {} };
  telemetry.channels.forEach((reading) => {
    const key = String(reading.channel);
    buckets.voltage[key] = reading.voltage;
    buckets.current[key] = reading.current;
    buckets.power[key] = reading.power;
    ["voltage", "current", "power"].forEach((id) => {
      const node = document.querySelector(`[data-legend="${id}-${key}"]`);
      if (node) node.textContent = fmt(reading[id], id === "power" ? 2 : 3);
    });
  });
  Object.entries(buckets).forEach(([id, values]) => state.charts[id]?.push(t, values));

  state.samples.push(telemetry);
  if (state.samples.length > 400) state.samples.shift();
  if (state.tableVisible) renderSampleTable();
}

function buildSampleTableHead() {
  const head = $("#sample-table thead");
  head.innerHTML = `<tr><th>Time</th>${state.channels
    .map((c) => `<th style="text-align:right">${c.label} V</th><th style="text-align:right">${c.label} A</th><th style="text-align:right">${c.label} W</th>`)
    .join("")}</tr>`;
}

function renderSampleTable() {
  const body = $("#sample-table tbody");
  const rows = state.samples.slice(-60).reverse();
  body.innerHTML = rows
    .map((sample) => {
      const time = new Date(sample.timestamp * 1000).toLocaleTimeString();
      const cells = sample.channels
        .map(
          (c) =>
            `<td class="num">${fmt(c.voltage, 3)}</td><td class="num">${fmt(c.current, 3)}</td><td class="num">${fmt(c.power, 2)}</td>`,
        )
        .join("");
      return `<tr><td>${time}</td>${cells}</tr>`;
    })
    .join("");
}

// --------------------------------------------------------------------------- //
// Connection & master controls
// --------------------------------------------------------------------------- //

async function refreshPorts(preferred) {
  const { ports } = await api("/api/ports");
  const select = $("#port-select");
  const previous = preferred || select.value || state.settings.last_port;
  // A <select> is as wide as its longest option, and some USB adapters report
  // very long descriptions; trim them so the control stays a sensible size.
  const trim = (text) => (text.length > 38 ? `${text.slice(0, 37)}…` : text);
  select.innerHTML = ports
    .map((p) => {
      const label = p.description && p.description !== p.device
        ? `${p.device} — ${p.description}`
        : p.device;
      return `<option value="${p.device}" title="${p.description || p.device}">${trim(label)}</option>`;
    })
    .join("");
  if (previous && ports.some((p) => p.device === previous)) select.value = previous;
}

async function toggleConnection() {
  const button = $("#connect-btn");
  button.disabled = true;
  try {
    if (state.connected) {
      await api("/api/disconnect", { method: "POST" });
      toast("Disconnected");
    } else {
      const result = await api("/api/connect", {
        method: "POST",
        body: { port: $("#port-select").value, baud_rate: Number($("#baud-select").value) },
      });
      state.device = result.device;
      buildChannels(result.device.channels);
      applyTelemetry(result.telemetry);
      toast(`Connected to ${result.telemetry.identity || result.telemetry.port}`, "success");
    }
  } catch (error) {
    toast(error.message, "error");
  } finally {
    button.disabled = false;
  }
}

function setConnected(connected, telemetry) {
  state.connected = connected;
  $("#connect-btn").textContent = connected ? "Disconnect" : "Connect";
  $("#connect-btn").classList.toggle("btn--primary", !connected);
  $("#status-dot").dataset.state = connected ? "on" : telemetry?.error ? "error" : "off";
  $("#status-dot").setAttribute("aria-label", connected ? "Connected" : "Disconnected");
  $("#device-identity").textContent = connected
    ? telemetry.identity || telemetry.port
    : telemetry?.error || "Not connected";

  const dot2 = $("#status-dot-2");
  if (dot2) dot2.dataset.state = connected ? "on" : telemetry?.error ? "error" : "off";
  $("#status-connection").textContent = connected
    ? `Connected · ${telemetry.port}${state.settings.baud_rate ? ` · ${state.settings.baud_rate} baud` : ""}`
    : telemetry?.error || "Disconnected";

  $$("input[type=range], .setpoint__input, #output-toggle, #beep-toggle, #record-btn, #console-input, #console-send, #seq-run")
    .forEach((node) => { node.disabled = !connected; });
  $$("#tracking-group button, #memory-slots button").forEach((node) => { node.disabled = !connected; });
  $("#port-select").disabled = connected;
  $("#baud-select").disabled = connected;
  $("#refresh-ports").disabled = connected;
  $("#detect-btn").disabled = connected;
}

function applyTelemetry(telemetry) {
  state.telemetry = telemetry;
  setConnected(telemetry.connected, telemetry);

  if (telemetry.connected && telemetry.channels.length) {
    if (state.channels.length !== telemetry.channels.length) {
      api("/api/info").then((info) => buildChannels(info.device.channels));
      return;
    }
    renderChannels(telemetry);
    pushSample(telemetry);
  }

  const toggle = $("#output-toggle");
  toggle.setAttribute("aria-pressed", String(telemetry.output));
  $("#output-label").textContent = telemetry.output ? "Output On" : "Output Off";

  $("#beep-toggle").checked = telemetry.beep;
  $$("#tracking-group button").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.tracking === telemetry.tracking));
  });

  const total = telemetry.channels.reduce((sum, c) => sum + (c.power || 0), 0);
  $("#total-power").textContent = total.toFixed(2);
  $("#status-power").textContent = total.toFixed(2);

  const output = $("#status-output");
  output.textContent = telemetry.output ? "Output on" : "Output off";
  output.dataset.on = String(telemetry.output);

  // CV/CC only means something while the output is live.
  $("#status-modes").innerHTML = telemetry.connected && telemetry.output
    ? telemetry.channels
        .map((c) => `<span data-mode="${c.mode}">CH${c.channel} ${c.mode.toUpperCase()}</span>`)
        .join("")
    : "";

  if (telemetry.trip) {
    $("#trip-detail").textContent = telemetry.trip;
    $("#trip-banner").hidden = false;
  }
}

// --------------------------------------------------------------------------- //
// Protection
// --------------------------------------------------------------------------- //

function buildProtectionForms() {
  const host = $("#protection-forms");
  host.innerHTML = "";
  const stored = state.settings.protection || {};

  state.channels.forEach((channel) => {
    const saved = stored[String(channel.index)] || {};
    const form = document.createElement("div");
    form.className = "stack";
    form.innerHTML = `
      <div class="row">
        <span class="channel__swatch" style="background:var(${channelColorVar(channel.index)})"></span>
        <strong>${channel.label}</strong>
        <span class="spacer"></span>
        <label class="switch">
          <input type="checkbox" data-role="prot-enabled" ${saved.enabled ? "checked" : ""}>
          <span class="switch__track"></span>
          <span class="field-inline__label">Armed</span>
        </label>
      </div>
      <div class="row">
        <label style="flex:1">
          <span class="field-label">Over-voltage (V)</span>
          <input class="input input--num" style="width:100%" type="number" min="0" step="0.1"
                 max="${channel.max_voltage}" data-role="prot-ovp"
                 value="${saved.over_voltage ?? channel.max_voltage}">
        </label>
        <label style="flex:1">
          <span class="field-label">Over-current (A)</span>
          <input class="input input--num" style="width:100%" type="number" min="0" step="0.01"
                 max="${channel.max_current}" data-role="prot-ocp"
                 value="${saved.over_current ?? channel.max_current}">
        </label>
        <label style="flex:1">
          <span class="field-label">Over-power (W)</span>
          <input class="input input--num" style="width:100%" type="number" min="0" step="0.5"
                 data-role="prot-opp"
                 value="${saved.over_power ?? (channel.max_voltage * channel.max_current).toFixed(1)}">
        </label>
      </div>`;
    host.appendChild(form);

    const submit = () => {
      api(`/api/protection/${channel.index}`, {
        method: "POST",
        body: {
          enabled: form.querySelector('[data-role="prot-enabled"]').checked,
          over_voltage: form.querySelector('[data-role="prot-ovp"]').value,
          over_current: form.querySelector('[data-role="prot-ocp"]').value,
          over_power: form.querySelector('[data-role="prot-opp"]').value,
        },
      }).catch((error) => toast(error.message, "error"));
    };
    form.querySelectorAll("input").forEach((input) => input.addEventListener("change", submit));
  });
}

// --------------------------------------------------------------------------- //
// Memory & presets
// --------------------------------------------------------------------------- //

function buildMemorySlots() {
  const host = $("#memory-slots");
  const slots = state.device?.memory_slots || 4;
  host.innerHTML = "";
  for (let slot = 1; slot <= slots; slot += 1) {
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `
      <strong style="width:34px">M${slot}</strong>
      <span class="muted spacer">Instrument memory slot ${slot}</span>
      <button class="btn btn--sm" data-action="save" ${state.connected ? "" : "disabled"}>Save</button>
      <button class="btn btn--sm" data-action="recall" ${state.connected ? "" : "disabled"}>Recall</button>`;
    row.querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          await api(`/api/memory/${slot}/${button.dataset.action}`, { method: "POST" });
          toast(`M${slot} ${button.dataset.action === "save" ? "saved" : "recalled"}`, "success");
        } catch (error) {
          toast(error.message, "error");
        }
      });
    });
    host.appendChild(row);
  }
}

function renderPresets() {
  const host = $("#preset-list");
  const presets = state.settings.presets || [];
  if (!presets.length) {
    host.innerHTML = `<div class="empty-state">No presets saved yet</div>`;
    return;
  }
  host.innerHTML = "";
  presets.forEach((preset, index) => {
    const row = document.createElement("div");
    row.className = "row";
    const summary = preset.channels
      .map((c) => `CH${c.channel} ${c.voltage.toFixed(2)} V / ${c.current.toFixed(3)} A`)
      .join(" · ");
    row.innerHTML = `
      <div class="spacer">
        <div><strong>${preset.name}</strong></div>
        <div class="muted">${summary}</div>
      </div>
      <button class="btn btn--sm" data-action="apply">Apply</button>
      <button class="btn btn--sm btn--ghost" data-action="delete" aria-label="Delete preset">✕</button>`;

    row.querySelector('[data-action="apply"]').addEventListener("click", async () => {
      try {
        for (const channel of preset.channels) {
          await api(`/api/channel/${channel.channel}/voltage`, { method: "POST", body: { value: channel.voltage } });
          await api(`/api/channel/${channel.channel}/current`, { method: "POST", body: { value: channel.current } });
        }
        toast(`Applied “${preset.name}”`, "success");
      } catch (error) {
        toast(error.message, "error");
      }
    });
    row.querySelector('[data-action="delete"]').addEventListener("click", async () => {
      const next = presets.filter((_, i) => i !== index);
      state.settings = await api("/api/settings", { method: "POST", body: { presets: next } });
      renderPresets();
    });
    host.appendChild(row);
  });
}

// --------------------------------------------------------------------------- //
// Sequencer
// --------------------------------------------------------------------------- //

function sequencerStepRow(step, index) {
  const row = document.createElement("div");
  row.className = "seq-step";
  row.dataset.index = index;
  const channelInputs = state.channels
    .map((channel) => {
      const values = step.channels[String(channel.index)] || {};
      return `
        <label>
          <span class="field-label">${channel.label} V / A</span>
          <div class="row" style="gap:4px;flex-wrap:nowrap">
            <input class="input input--num" style="width:100%" type="number" min="0" step="0.01"
                   max="${channel.max_voltage}" data-field="voltage" data-channel="${channel.index}"
                   value="${values.voltage ?? 0}">
            <input class="input input--num" style="width:100%" type="number" min="0" step="0.001"
                   max="${channel.max_current}" data-field="current" data-channel="${channel.index}"
                   value="${values.current ?? 0}">
          </div>
        </label>`;
    })
    .join("");

  row.innerHTML = `
    <span class="seq-step__index">${index + 1}</span>
    <label><span class="field-label">Label</span>
      <input class="input" style="width:100%" data-field="label" value="${step.label || ""}" placeholder="optional"></label>
    <label><span class="field-label">Dwell (s)</span>
      <input class="input input--num" style="width:100%" type="number" min="0.1" step="0.1"
             data-field="duration" value="${step.duration}"></label>
    ${channelInputs}
    <button class="btn btn--sm btn--ghost" data-action="remove" aria-label="Remove step">✕</button>`;

  row.querySelector('[data-action="remove"]').addEventListener("click", () => {
    state.sequenceSteps.splice(index, 1);
    renderSequencer();
  });
  row.querySelectorAll("input").forEach((input) => {
    input.addEventListener("change", () => collectSequencerSteps());
  });
  return row;
}

function collectSequencerSteps() {
  const steps = [];
  $$("#seq-steps .seq-step").forEach((row) => {
    const step = {
      label: row.querySelector('[data-field="label"]').value,
      duration: Number(row.querySelector('[data-field="duration"]').value) || 1,
      channels: {},
      output: true,
    };
    row.querySelectorAll("[data-channel]").forEach((input) => {
      const key = String(input.dataset.channel);
      step.channels[key] = step.channels[key] || {};
      step.channels[key][input.dataset.field] = Number(input.value) || 0;
    });
    steps.push(step);
  });
  state.sequenceSteps = steps;
  return steps;
}

function renderSequencer() {
  const host = $("#seq-steps");
  host.innerHTML = "";
  state.sequenceSteps.forEach((step, index) => host.appendChild(sequencerStepRow(step, index)));
}

function buildSequencerDefaults() {
  if (state.sequenceSteps?.length) {
    renderSequencer();
    return;
  }
  const blank = (voltage) => ({
    label: "",
    duration: 5,
    output: true,
    channels: Object.fromEntries(
      state.channels.map((c) => [String(c.index), { voltage, current: c.max_current / 2 }]),
    ),
  });
  state.sequenceSteps = [blank(3.3), blank(5)];
  renderSequencer();
}

function renderSequenceState(sequence) {
  state.sequence = sequence;
  const running = sequence.running;
  $("#seq-run").hidden = running;
  $("#seq-stop").hidden = !running;
  $("#seq-status").textContent = running
    ? `${sequence.message} — step ${sequence.current_step + 1}/${sequence.total_steps}, loop ${sequence.current_loop + 1}/${sequence.loops}`
    : sequence.message || "Idle";

  $$("#seq-steps .seq-step").forEach((row) => {
    row.dataset.active = String(running && Number(row.dataset.index) === sequence.current_step);
  });

  const done = running && sequence.total_steps
    ? ((sequence.current_loop * sequence.total_steps + sequence.current_step) /
        (sequence.loops * sequence.total_steps)) * 100
    : 0;
  $("#seq-bar").style.width = `${done}%`;
}

// --------------------------------------------------------------------------- //
// Console
// --------------------------------------------------------------------------- //

function logConsole(text, kind) {
  const log = $("#console-log");
  const line = document.createElement("div");
  line.className = `console-log__line console-log__line--${kind}`;
  line.textContent = text;
  log.appendChild(line);
  while (log.children.length > 500) log.firstChild.remove();
  log.scrollTop = log.scrollHeight;
}

async function sendConsole() {
  const input = $("#console-input");
  const command = input.value.trim();
  if (!command) return;
  logConsole(`> ${command}`, "tx");
  input.value = "";
  try {
    const result = await api("/api/raw", { method: "POST", body: { command } });
    if (result.response !== null && result.response !== undefined) {
      logConsole(result.response || "(empty response)", "rx");
    }
  } catch (error) {
    logConsole(`! ${error.message}`, "err");
  }
}

// --------------------------------------------------------------------------- //
// Updates
// --------------------------------------------------------------------------- //

function renderUpdate(info) {
  state.update = info;
  const banner = $("#update-banner");
  if (!info || !info.update_available || sessionStorage.getItem("gpd-update-dismissed") === info.latest_version) {
    banner.hidden = true;
    return;
  }
  $("#update-title").textContent = `Version ${info.latest_version} is available`;
  $("#update-detail").textContent = `You are running ${info.current_version}.` +
    (info.can_self_update ? "" : " This install cannot update itself — re-run the installer.");
  $("#update-notes").href = info.release_url || "#";
  $("#update-notes").hidden = !info.release_url;
  $("#update-apply").hidden = !info.can_self_update;
  banner.hidden = false;
}

// --------------------------------------------------------------------------- //
// Event stream
// --------------------------------------------------------------------------- //

function connectStream() {
  const source = new EventSource("/api/stream");
  source.addEventListener("telemetry", (event) => applyTelemetry(JSON.parse(event.data)));
  source.addEventListener("sequence", (event) => renderSequenceState(JSON.parse(event.data)));
  source.addEventListener("error", () => {
    // EventSource reconnects on its own; surface the gap without spamming toasts.
    $("#status-dot").dataset.state = "error";
  });
  return source;
}

// --------------------------------------------------------------------------- //
// Wiring
// --------------------------------------------------------------------------- //

function wireGlobalControls() {
  $("#connect-btn").addEventListener("click", toggleConnection);

  $("#detect-btn").addEventListener("click", async () => {
    const button = $("#detect-btn");
    const original = button.innerHTML;
    button.disabled = true;
    button.textContent = "Searching…";
    try {
      // Probing walks every port and baud rate, so this can take a few seconds.
      const result = await api("/api/detect", { method: "POST" });
      state.device = result.device;
      buildChannels(result.device.channels);
      applyTelemetry(result.telemetry);
      await refreshPorts(result.found.port);
      $("#baud-select").value = String(result.found.baud_rate);
      toast(`Found ${result.found.identity} on ${result.found.port} at ${result.found.baud_rate} baud`, "success", 6000);
    } catch (error) {
      toast(error.message, "error", 7000);
    } finally {
      button.disabled = false;
      button.innerHTML = original;
    }
  });
  $("#refresh-ports").addEventListener("click", () => refreshPorts().catch(() => {}));

  $("#output-toggle").addEventListener("click", async () => {
    const enabling = $("#output-toggle").getAttribute("aria-pressed") !== "true";
    if (enabling && state.settings.confirm_output_on) {
      if (!window.confirm("Enable the output?")) return;
    }
    try {
      await api("/api/output", { method: "POST", body: { enabled: enabling } });
      if (enabling) $("#trip-banner").hidden = true;
    } catch (error) {
      toast(error.message, "error");
    }
  });

  $("#beep-toggle").addEventListener("change", (event) => {
    api("/api/beep", { method: "POST", body: { enabled: event.target.checked } }).catch((error) =>
      toast(error.message, "error"),
    );
  });

  $$("#tracking-group button").forEach((button) => {
    button.addEventListener("click", () => {
      api("/api/tracking", { method: "POST", body: { mode: button.dataset.tracking } }).catch(
        (error) => toast(error.message, "error"),
      );
    });
  });

  $("#trip-dismiss").addEventListener("click", () => { $("#trip-banner").hidden = true; });

  // --- sidebar navigation ---
  $$(".sidebar button[data-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      $$(".sidebar button[data-tab]").forEach((b) =>
        b.setAttribute("aria-selected", String(b === button)),
      );
      $$(".panel").forEach((panel) => {
        panel.hidden = panel.id !== `panel-${button.dataset.tab}`;
      });
      // The view was display:none, so the canvases had no size to lay out to.
      $(".views").scrollTop = 0;
      if (button.dataset.tab === "monitor") {
        requestAnimationFrame(() => Object.values(state.charts).forEach((c) => c.resize()));
      }
    });
  });

  // --- monitor toolbar ---
  $("#chart-window").addEventListener("change", (event) => {
    const seconds = Number(event.target.value);
    Object.values(state.charts).forEach((chart) => chart.setWindow(seconds));
    api("/api/settings", { method: "POST", body: { chart_window_s: seconds } }).catch(() => {});
  });

  $("#poll-interval").addEventListener("change", (event) => {
    api("/api/settings", { method: "POST", body: { poll_interval: Number(event.target.value) } })
      .catch((error) => toast(error.message, "error"));
  });

  $("#clear-chart").addEventListener("click", () => {
    Object.values(state.charts).forEach((chart) => chart.clear());
    state.samples = [];
    renderSampleTable();
  });

  $("#toggle-table").addEventListener("click", () => {
    state.tableVisible = !state.tableVisible;
    $("#table-card").hidden = !state.tableVisible;
    $("#toggle-table").setAttribute("aria-pressed", String(state.tableVisible));
    if (state.tableVisible) renderSampleTable();
  });

  $("#record-btn").addEventListener("click", async () => {
    try {
      if (state.recorder.active) {
        const result = await api("/api/recorder/stop", { method: "POST" });
        state.recorder = result.recorder;
        toast(`Saved ${result.recorder.rows} rows`, "success");
      } else {
        const result = await api("/api/recorder/start", { method: "POST", body: {} });
        state.recorder = result.recorder;
        toast("Recording to CSV", "success");
      }
      renderRecorder();
    } catch (error) {
      toast(error.message, "error");
    }
  });

  // --- sequencer ---
  $("#seq-add").addEventListener("click", () => {
    collectSequencerSteps();
    state.sequenceSteps.push({
      label: "",
      duration: 5,
      output: true,
      channels: Object.fromEntries(
        state.channels.map((c) => [String(c.index), { voltage: 0, current: c.max_current / 2 }]),
      ),
    });
    renderSequencer();
  });

  $("#seq-run").addEventListener("click", async () => {
    try {
      await api("/api/sequence/start", {
        method: "POST",
        body: {
          steps: collectSequencerSteps(),
          loops: Number($("#seq-loops").value) || 1,
          stop_output_at_end: $("#seq-stop-output").checked,
        },
      });
    } catch (error) {
      toast(error.message, "error");
    }
  });

  $("#seq-stop").addEventListener("click", () => {
    api("/api/sequence/stop", { method: "POST" }).catch((error) => toast(error.message, "error"));
  });

  $("#seq-export").addEventListener("click", () => {
    const blob = new Blob([JSON.stringify(collectSequencerSteps(), null, 2)], {
      type: "application/json",
    });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "gpd-sequence.json";
    link.click();
    URL.revokeObjectURL(link.href);
  });

  $("#seq-import").addEventListener("click", () => {
    const picker = document.createElement("input");
    picker.type = "file";
    picker.accept = "application/json";
    picker.addEventListener("change", async () => {
      const file = picker.files?.[0];
      if (!file) return;
      try {
        const parsed = JSON.parse(await file.text());
        if (!Array.isArray(parsed)) throw new Error("Expected a JSON array of steps");
        state.sequenceSteps = parsed;
        renderSequencer();
        toast(`Loaded ${parsed.length} steps`, "success");
      } catch (error) {
        toast(`Import failed: ${error.message}`, "error");
      }
    });
    picker.click();
  });

  // --- presets ---
  $("#preset-save").addEventListener("click", async () => {
    const name = $("#preset-name").value.trim();
    if (!name) return toast("Give the preset a name", "error");
    if (!state.telemetry?.channels?.length) return toast("Connect first", "error");
    const preset = {
      name,
      channels: state.telemetry.channels.map((c) => ({
        channel: c.channel,
        voltage: c.voltage_set,
        current: c.current_set,
      })),
    };
    const presets = [...(state.settings.presets || []), preset];
    state.settings = await api("/api/settings", { method: "POST", body: { presets } });
    $("#preset-name").value = "";
    renderPresets();
    toast(`Saved “${name}”`, "success");
  });

  // --- console ---
  $("#console-send").addEventListener("click", sendConsole);
  $("#console-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") sendConsole();
  });
  $("#console-clear").addEventListener("click", () => { $("#console-log").innerHTML = ""; });

  // --- updates ---
  $("#update-dismiss").addEventListener("click", () => {
    if (state.update?.latest_version) {
      sessionStorage.setItem("gpd-update-dismissed", state.update.latest_version);
    }
    $("#update-banner").hidden = true;
  });

  $("#update-apply").addEventListener("click", async () => {
    const button = $("#update-apply");
    button.disabled = true;
    button.textContent = "Updating…";
    try {
      const result = await api("/api/update/apply", { method: "POST" });
      if (result.ok) {
        toast("Updated — restart GPD Control to use the new version.", "success", 10000);
        $("#update-banner").hidden = true;
      } else {
        toast(result.output || "Update failed", "error", 9000);
      }
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
      button.textContent = "Update now";
    }
  });

  $("#check-update-btn").addEventListener("click", async () => {
    toast("Checking for updates…");
    try {
      const info = await api("/api/update?refresh=true");
      renderUpdate(info);
      if (info.error) toast(info.error, "error");
      else if (!info.update_available) toast(`You are up to date (${info.current_version})`, "success");
    } catch (error) {
      toast(error.message, "error");
    }
  });

  // --- keyboard shortcuts ---
  document.addEventListener("keydown", (event) => {
    if (event.target.matches("input, textarea, select")) return;
    if (event.key === " " && state.connected) {
      event.preventDefault();
      $("#output-toggle").click();
    }
    if (event.key === "Escape" && state.telemetry?.output) {
      api("/api/output", { method: "POST", body: { enabled: false } })
        .then(() => toast("Output disabled", "success"))
        .catch(() => {});
    }
  });
}

function renderRecorder() {
  const active = state.recorder.active;
  const button = $("#record-btn");
  button.innerHTML = active
    ? `<span class="recording-dot"></span> Stop logging`
    : `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="8"/></svg> Start CSV log`;
  button.classList.toggle("btn--danger", active);
  $("#record-status").textContent = active
    ? `Recording · ${state.recorder.rows || 0} rows`
    : state.recorder.path
      ? `Saved ${state.recorder.rows} rows`
      : "Not recording";
  $("#record-download").hidden = !state.recorder.path || active;
  $("#status-recording").hidden = !active;
  $("#status-recording-sep").hidden = !active;
}

// --------------------------------------------------------------------------- //
// Boot
// --------------------------------------------------------------------------- //

async function boot() {
  initTheme();
  wireGlobalControls();

  const info = await api("/api/info");
  state.info = info;
  state.settings = info.settings;
  state.device = info.device;
  state.recorder = info.recorder;

  $("#footer-version").textContent = `v${info.version}`;
  $("#footer-version").title = `GPD Control ${info.version} · logs in ${info.data_dir}`;

  $("#baud-select").innerHTML = info.baud_rates
    .map((rate) => `<option value="${rate}">${rate} baud</option>`)
    .join("");
  $("#baud-select").value = String(info.settings.baud_rate || 115200);
  $("#chart-window").value = String(info.settings.chart_window_s || 120);
  $("#poll-interval").value = String(info.settings.poll_interval || 0.4);

  await refreshPorts(info.settings.last_port);
  buildChannels(info.device.channels);
  buildMemorySlots();
  renderPresets();
  renderRecorder();
  applyTelemetry(info.telemetry);
  renderSequenceState(info.sequence);
  renderUpdate(info.update);

  connectStream();

  // Refresh the recorder row periodically so the row counter advances.
  setInterval(async () => {
    if (!state.recorder.active) return;
    try {
      state.recorder = await api("/api/recorder");
      renderRecorder();
    } catch { /* transient */ }
  }, 2000);

  // Re-render memory buttons whenever the connection state flips.
  const observer = new MutationObserver(() => buildMemorySlots());
  observer.observe($("#status-dot"), { attributes: true, attributeFilter: ["data-state"] });

  if (info.settings.auto_connect && !info.telemetry.connected) toggleConnection();
}

boot().catch((error) => {
  document.body.innerHTML =
    `<div class="empty-state"><h2>Could not start</h2><p>${error.message}</p></div>`;
});
