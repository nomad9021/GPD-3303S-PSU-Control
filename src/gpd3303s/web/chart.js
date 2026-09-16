/**
 * Canvas strip chart for live instrument telemetry.
 *
 * One measure per chart instance — voltage, current and power each get their own
 * y-axis rather than being crammed onto shared axes. Colours are read from CSS
 * custom properties so a theme switch repaints without re-instantiating.
 */

const PADDING = { top: 10, right: 62, bottom: 18, left: 46 };
const LINE_WIDTH = 2;
const MARKER_RADIUS = 3.5;

function cssVar(element, name, fallback) {
  const value = getComputedStyle(element).getPropertyValue(name).trim();
  return value || fallback;
}

/** Choose a "nice" axis step so gridlines land on readable numbers. */
function niceStep(range, targetTicks) {
  if (!(range > 0)) return 1;
  const rough = range / targetTicks;
  const magnitude = Math.pow(10, Math.floor(Math.log10(rough)));
  const normalised = rough / magnitude;
  let step;
  if (normalised <= 1) step = 1;
  else if (normalised <= 2) step = 2;
  else if (normalised <= 5) step = 5;
  else step = 10;
  return step * magnitude;
}

export class StripChart {
  /**
   * @param {HTMLElement} host   container that the canvas fills
   * @param {object} options     {unit, decimals, series:[{key,label,colorVar}], minSpan}
   */
  constructor(host, options) {
    this.host = host;
    this.options = Object.assign({ unit: "", decimals: 3, minSpan: 1, series: [] }, options);

    this.canvas = document.createElement("canvas");
    this.ctx = this.canvas.getContext("2d");
    host.appendChild(this.canvas);

    this.tooltip = document.createElement("div");
    this.tooltip.className = "chart-tooltip";
    host.appendChild(this.tooltip);

    /** @type {{t:number, values:Record<string,number>}[]} */
    this.samples = [];
    this.windowSeconds = 120;
    this.hover = null;
    this._frame = null;

    this._onResize = () => this.resize();
    this._observer = new ResizeObserver(this._onResize);
    this._observer.observe(host);

    host.addEventListener("pointermove", (event) => this._onPointerMove(event));
    host.addEventListener("pointerleave", () => {
      this.hover = null;
      this.tooltip.dataset.visible = "false";
      this.scheduleDraw();
    });

    this.resize();
  }

  destroy() {
    this._observer.disconnect();
    if (this._frame) cancelAnimationFrame(this._frame);
  }

  resize() {
    const dpr = window.devicePixelRatio || 1;
    const rect = this.host.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    this.width = rect.width;
    this.height = rect.height;
    this.canvas.width = Math.round(rect.width * dpr);
    this.canvas.height = Math.round(rect.height * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.draw();
  }

  setWindow(seconds) {
    this.windowSeconds = seconds;
    this.scheduleDraw();
  }

  clear() {
    this.samples = [];
    this.scheduleDraw();
  }

  /** @param {number} t epoch seconds  @param {Record<string, number>} values */
  push(t, values) {
    this.samples.push({ t, values });
    // Keep a little more than the widest selectable window so switching
    // windows does not reveal an empty chart.
    const cutoff = t - 1000;
    while (this.samples.length && this.samples[0].t < cutoff) this.samples.shift();
    if (this.samples.length > 20000) this.samples.splice(0, this.samples.length - 20000);
    this.scheduleDraw();
  }

  scheduleDraw() {
    if (this._frame) return;
    this._frame = requestAnimationFrame(() => {
      this._frame = null;
      this.draw();
    });
  }

  _visible() {
    if (!this.samples.length) return [];
    const now = this.samples[this.samples.length - 1].t;
    const from = now - this.windowSeconds;
    let start = this.samples.length - 1;
    while (start > 0 && this.samples[start - 1].t >= from) start -= 1;
    return this.samples.slice(start);
  }

  _scale(visible) {
    let max = 0;
    for (const sample of visible) {
      for (const series of this.options.series) {
        const value = sample.values[series.key];
        if (Number.isFinite(value) && value > max) max = value;
      }
    }
    // Always include zero and leave headroom so the trace never touches the top.
    const span = Math.max(max * 1.15, this.options.minSpan);
    return { min: 0, max: span };
  }

  _onPointerMove(event) {
    const rect = this.host.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const visible = this._visible();
    if (visible.length < 2 || x < PADDING.left || x > this.width - PADDING.right) {
      this.hover = null;
      this.tooltip.dataset.visible = "false";
      this.scheduleDraw();
      return;
    }

    const plotW = this.width - PADDING.left - PADDING.right;
    const t0 = visible[0].t;
    const t1 = visible[visible.length - 1].t;
    const span = Math.max(t1 - t0, 0.001);
    const targetTime = t0 + ((x - PADDING.left) / plotW) * span;

    let nearest = visible[0];
    let bestDelta = Infinity;
    for (const sample of visible) {
      const delta = Math.abs(sample.t - targetTime);
      if (delta < bestDelta) {
        bestDelta = delta;
        nearest = sample;
      }
    }

    this.hover = { sample: nearest, t0, t1 };
    this._renderTooltip(nearest, t1, x, y);
    this.scheduleDraw();
  }

  _renderTooltip(sample, latestTime, x, y) {
    const { decimals, unit } = this.options;
    const age = latestTime - sample.t;
    const rows = this.options.series
      .map((series) => {
        const value = sample.values[series.key];
        const text = Number.isFinite(value) ? `${value.toFixed(decimals)} ${unit}` : "—";
        const color = cssVar(this.host, series.colorVar, "#888");
        return `<div class="chart-tooltip__row">
            <span class="legend__swatch" style="background:${color}"></span>
            <span>${series.label}</span><span>${text}</span>
          </div>`;
      })
      .join("");

    this.tooltip.innerHTML =
      `<div class="chart-tooltip__time">${age < 1 ? "now" : `−${age.toFixed(1)} s`}</div>${rows}`;
    this.tooltip.dataset.visible = "true";

    // Flip the tooltip to the other side of the cursor near the right edge.
    const tipWidth = this.tooltip.offsetWidth;
    const left = x + 14 + tipWidth > this.width ? x - 14 - tipWidth : x + 14;
    this.tooltip.style.left = `${Math.max(2, left)}px`;
    this.tooltip.style.top = `${Math.max(2, Math.min(y - 10, this.height - this.tooltip.offsetHeight - 2))}px`;
  }

  draw() {
    const ctx = this.ctx;
    if (!ctx || !this.width) return;

    const grid = cssVar(this.host, "--grid-line", "rgba(0,0,0,.08)");
    const axis = cssVar(this.host, "--axis-line", "rgba(0,0,0,.2)");
    const muted = cssVar(this.host, "--text-muted", "#888");
    const surface = cssVar(this.host, "--surface-1", "#fff");

    ctx.clearRect(0, 0, this.width, this.height);

    const plotW = this.width - PADDING.left - PADDING.right;
    const plotH = this.height - PADDING.top - PADDING.bottom;
    if (plotW <= 0 || plotH <= 0) return;

    const visible = this._visible();
    const { min, max } = this._scale(visible);
    const yFor = (value) => PADDING.top + plotH - ((value - min) / (max - min)) * plotH;

    // --- gridlines and y labels (recessive) ---
    const step = niceStep(max - min, 4);
    ctx.font = "10px var(--font-mono, monospace)";
    ctx.textBaseline = "middle";
    ctx.textAlign = "right";
    for (let value = min; value <= max + 1e-9; value += step) {
      const y = Math.round(yFor(value)) + 0.5;
      ctx.beginPath();
      ctx.strokeStyle = grid;
      ctx.lineWidth = 1;
      ctx.moveTo(PADDING.left, y);
      ctx.lineTo(this.width - PADDING.right, y);
      ctx.stroke();

      ctx.fillStyle = muted;
      const label = step >= 1 ? value.toFixed(0) : value.toFixed(step >= 0.1 ? 1 : 2);
      ctx.fillText(label, PADDING.left - 7, y);
    }

    // --- baseline ---
    ctx.beginPath();
    ctx.strokeStyle = axis;
    ctx.lineWidth = 1;
    const baseY = Math.round(yFor(min)) + 0.5;
    ctx.moveTo(PADDING.left, baseY);
    ctx.lineTo(this.width - PADDING.right, baseY);
    ctx.stroke();

    if (visible.length < 2) {
      ctx.fillStyle = muted;
      ctx.textAlign = "center";
      ctx.font = "12px var(--font-sans, sans-serif)";
      ctx.fillText("Waiting for samples…", PADDING.left + plotW / 2, PADDING.top + plotH / 2);
      return;
    }

    const t0 = visible[0].t;
    const t1 = visible[visible.length - 1].t;
    const tSpan = Math.max(t1 - t0, 0.001);
    const xFor = (t) => PADDING.left + ((t - t0) / tSpan) * plotW;

    // --- x labels ---
    ctx.font = "10px var(--font-mono, monospace)";
    ctx.fillStyle = muted;
    ctx.textBaseline = "top";
    ctx.textAlign = "left";
    ctx.fillText(`−${Math.round(tSpan)} s`, PADDING.left, this.height - PADDING.bottom + 5);
    ctx.textAlign = "right";
    ctx.fillText("now", this.width - PADDING.right, this.height - PADDING.bottom + 5);

    // --- series ---
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    for (const series of this.options.series) {
      const color = cssVar(this.host, series.colorVar, "#888");
      ctx.beginPath();
      ctx.strokeStyle = color;
      ctx.lineWidth = LINE_WIDTH;
      let started = false;
      for (const sample of visible) {
        const value = sample.values[series.key];
        if (!Number.isFinite(value)) continue;
        const x = xFor(sample.t);
        const y = yFor(value);
        if (started) ctx.lineTo(x, y);
        else {
          ctx.moveTo(x, y);
          started = true;
        }
      }
      ctx.stroke();

      // Direct label at the live end — identity without relying on colour alone.
      const last = visible[visible.length - 1];
      const lastValue = last.values[series.key];
      if (Number.isFinite(lastValue)) {
        const x = xFor(last.t);
        const y = yFor(lastValue);

        // A surface ring keeps the marker legible where the two traces cross.
        ctx.beginPath();
        ctx.arc(x, y, MARKER_RADIUS + 1.5, 0, Math.PI * 2);
        ctx.fillStyle = surface;
        ctx.fill();
        ctx.beginPath();
        ctx.arc(x, y, MARKER_RADIUS, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();

        ctx.font = "600 11px var(--font-mono, monospace)";
        ctx.fillStyle = cssVar(this.host, "--text-primary", "#000");
        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        ctx.fillText(lastValue.toFixed(this.options.decimals), x + 8, y);
      }
    }

    // --- crosshair ---
    if (this.hover) {
      const x = Math.round(xFor(this.hover.sample.t)) + 0.5;
      ctx.beginPath();
      ctx.strokeStyle = axis;
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.moveTo(x, PADDING.top);
      ctx.lineTo(x, PADDING.top + plotH);
      ctx.stroke();
      ctx.setLineDash([]);

      for (const series of this.options.series) {
        const value = this.hover.sample.values[series.key];
        if (!Number.isFinite(value)) continue;
        const y = yFor(value);
        ctx.beginPath();
        ctx.arc(x, y, MARKER_RADIUS + 1.5, 0, Math.PI * 2);
        ctx.fillStyle = surface;
        ctx.fill();
        ctx.beginPath();
        ctx.arc(x, y, MARKER_RADIUS, 0, Math.PI * 2);
        ctx.fillStyle = cssVar(this.host, series.colorVar, "#888");
        ctx.fill();
      }
    }
  }
}
