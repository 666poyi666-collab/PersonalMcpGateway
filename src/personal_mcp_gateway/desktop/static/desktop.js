/* Poyi Control Center — desktop renderer.
   All gateway I/O happens in Python and arrives through pywebview.api, so this
   file never performs a network request of its own. */
"use strict";

const POLL_MS = 4000;
const CIRCUMFERENCE = 2 * Math.PI * 52;

const el = (id) => document.getElementById(id);
const root = document.documentElement;

const dom = {
  body: document.body,
  tbCount: el("tbCount"),
  tbLabel: el("tbLabel"),
  tbChipDot: document.querySelector("#tbChip .dot"),
  heroValue: el("heroValue"),
  heroTitle: el("heroTitle"),
  heroSub: el("heroSub"),
  meter: el("meter"),
  meterFill: el("meterFill"),
  meterCompact: el("meterCompact"),
  meterFillCompact: el("meterFillCompact"),
  heroValueCompact: el("heroValueCompact"),
  heroLabelCompact: el("heroLabelCompact"),
  tileCalls: el("tileCalls"),
  tileCallsFoot: el("tileCallsFoot"),
  tileRate: el("tileRate"),
  tileRateFoot: el("tileRateFoot"),
  tileUptime: el("tileUptime"),
  tileProbe: el("tileProbe"),
  projectGrid: el("projectGrid"),
  plot: el("plot"),
  plotEmpty: el("plotEmpty"),
  yAxis: el("yAxis"),
  chartTip: el("chartTip"),
  chartPanel: document.querySelector(".chart-panel"),
  eventList: el("eventList"),
  eventCount: el("eventCount"),
  widgetGrid: el("widgetGrid"),
  widgetCount: el("widgetCount"),
  compactList: el("compactList"),
  compactCalls: el("compactCalls"),
  compactRate: el("compactRate"),
  offlineScreen: el("offlineScreen"),
  offlineHint: el("offlineHint"),
  sbDot: el("sbDot"),
  sbState: el("sbState"),
  sbSync: el("sbSync"),
  sbProbe: el("sbProbe"),
  btnTop: el("btnTop"),
  btnCompact: el("btnCompact"),
  btnRefresh: el("btnRefresh"),
};

/* ---------- helpers ---------- */

const num = (value, fallback = 0) => (Number.isFinite(Number(value)) ? Number(value) : fallback);

function compact(value) {
  const n = num(value);
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e4) return `${(n / 1e3).toFixed(1)}K`;
  return n.toLocaleString("zh-CN");
}

function duration(seconds) {
  const s = Math.max(0, Math.floor(num(seconds)));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d}天${h}小时`;
  if (h) return `${h}小时${m}分`;
  if (m) return `${m}分钟`;
  return `${s}秒`;
}

function clockOf(value) {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

function hourOf(bucket) {
  const date = new Date(bucket);
  if (Number.isNaN(date.getTime())) return "--:00";
  return `${String(date.getHours()).padStart(2, "0")}:00`;
}

function niceMax(value) {
  const n = Math.max(1, Math.ceil(num(value)));
  const magnitude = 10 ** Math.floor(Math.log10(n));
  for (const step of [1, 2, 2.5, 5, 10]) {
    const candidate = step * magnitude;
    if (candidate >= n) return candidate;
  }
  return 10 * magnitude;
}

function replace(node, children) {
  node.replaceChildren(...children);
}

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/* ---------- bridge ---------- */

let bridgeReady = false;
let timer = null;

function api() {
  return window.pywebview && window.pywebview.api ? window.pywebview.api : null;
}

async function pull(force) {
  const bridge = api();
  if (!bridge) return;
  try {
    const payload = force ? await bridge.refresh() : await bridge.snapshot();
    if (payload) render(payload);
  } catch (error) {
    console.warn("snapshot failed", error);
  }
}

/* ---------- rendering ---------- */

function render(payload) {
  const view = payload.view || {};
  applyView(view);

  if (!payload.connected || !payload.data) {
    dom.body.classList.add("disconnected");
    dom.offlineScreen.hidden = false;
    dom.offlineHint.textContent = payload.consecutiveFailures
      ? `连续 ${payload.consecutiveFailures} 次重试未成功 · ${clockOf(payload.fetchedAt)}`
      : "";
    setStatusChrome("disconnected", "—", "网关未连接");
    dom.sbSync.textContent = clockOf(payload.fetchedAt);
    dom.sbProbe.textContent = "—";
    return;
  }

  dom.body.classList.remove("disconnected");
  dom.offlineScreen.hidden = true;

  const data = payload.data;
  const summary = data.summary || {};
  const gateway = data.gateway || {};
  const online = num(summary.online);
  const total = num(summary.total);

  setStatusChrome(payload.status, `${online}/${total}`, payload.statusLabel || "");
  renderHero(payload, online, total, gateway);
  renderTiles(summary, gateway, data);
  renderProjects(Array.isArray(data.targets) ? data.targets : []);
  renderChart(data.activity && Array.isArray(data.activity.hourly) ? data.activity.hourly : []);
  renderEvents(data);
  renderWidgets(Array.isArray(data.widgets) ? data.widgets : []);

  dom.sbSync.textContent = `同步 ${clockOf(data.generatedAt || payload.fetchedAt)}`;
  dom.sbProbe.textContent = `探测 ${num(data.probeDurationMs)}ms`;
}

function setStatusChrome(status, count, label) {
  dom.tbChipDot.dataset.status = status;
  dom.sbDot.dataset.status = status;
  dom.tbCount.textContent = count;
  dom.tbLabel.textContent = label;
  dom.sbState.textContent = status === "disconnected" ? "DISCONNECTED" : "CONNECTED";
}

function setMeter(meter, fill, online, total, status) {
  const ratio = total > 0 ? Math.min(1, online / total) : 0;
  meter.dataset.status = status;
  fill.style.strokeDasharray = String(CIRCUMFERENCE);
  fill.style.strokeDashoffset = String(CIRCUMFERENCE * (1 - ratio));
}

function renderHero(payload, online, total, gateway) {
  dom.heroValue.textContent = `${online}/${total}`;
  dom.heroValueCompact.textContent = `${online}/${total}`;
  dom.heroLabelCompact.textContent = payload.statusLabel || "在线";
  setMeter(dom.meter, dom.meterFill, online, total, payload.status);
  setMeter(dom.meterCompact, dom.meterFillCompact, online, total, payload.status);

  const titles = {
    online: "所有系统正常运行",
    degraded: "部分链路已降级",
    offline: "存在离线项目",
  };
  dom.heroTitle.textContent = titles[payload.status] || "状态未知";
  dom.heroSub.textContent =
    `Gateway ${gateway.state === "online" ? "在线" : "启动中"} · ` +
    `版本 ${gateway.version || "—"} · 集中监控 ${total} 个独立项目`;
}

function renderTiles(summary, gateway, data) {
  const calls = num(summary.calls24h);
  const failures = num(summary.failures24h);
  dom.tileCalls.textContent = compact(calls);
  dom.tileCallsFoot.textContent = calls ? `其中失败 ${compact(failures)}` : "跨项目工具调用";
  dom.tileRate.textContent = `${num(summary.successRate, 100).toFixed(1)}%`;
  dom.tileRateFoot.textContent = `失败请求 ${compact(failures)}`;
  dom.tileUptime.textContent = duration(gateway.uptimeSeconds);
  dom.tileProbe.textContent = `${num(data.probeDurationMs)}ms`;
  dom.compactCalls.textContent = compact(calls);
  dom.compactRate.textContent = `${num(summary.successRate, 100).toFixed(1)}%`;
}

function componentRow(label, probe) {
  const row = make("div", "pc-row");
  const dot = make("i", "dot");
  dot.dataset.status = probe && probe.ok ? "online" : "offline";
  row.append(dot, make("span", null, label), make("em", null, probe && probe.ok ? `${num(probe.latencyMs)}ms` : "不可用"));
  return row;
}

function renderProjects(targets) {
  const cards = targets.map((target) => {
    const card = make("article", "project-card");

    const top = make("div", "pc-top");
    const name = make("div", "pc-name");
    name.append(make("strong", null, target.name || target.id));
    name.append(make("small", null, target.version ? `v${String(target.version).replace(/^v/, "")}` : target.description || ""));
    const state = make("div", "pc-state");
    const dot = make("i", "dot");
    dot.dataset.status = target.state || "offline";
    const stateText = { online: "正常", degraded: "降级", offline: "离线" }[target.state] || "未知";
    state.append(dot, make("span", null, stateText));
    top.append(name, state);

    const rows = make("div", "pc-rows");
    rows.append(componentRow("MCP Server", target.mcp));
    if (target.tunnel) rows.append(componentRow("Secure Tunnel", target.tunnel));

    card.append(top, rows);
    return card;
  });
  replace(dom.projectGrid, cards.length ? cards : [make("p", "empty-note", "尚未配置监控目标")]);

  const rows = targets.map((target) => {
    const row = make("div", "compact-row");
    const dot = make("i", "dot");
    dot.dataset.status = target.state || "offline";
    const meta = make("div", "cr-meta");
    const mcpDot = make("i", "dot mini");
    mcpDot.dataset.status = target.mcp && target.mcp.ok ? "online" : "offline";
    const tunDot = make("i", "dot mini");
    tunDot.dataset.status = !target.tunnel ? "disconnected" : target.tunnel.ok ? "online" : "offline";
    meta.append(mcpDot, tunDot, make("span", null, target.mcp && target.mcp.ok ? `${num(target.mcp.latencyMs)}ms` : "—"));
    row.append(dot, make("strong", null, target.name || target.id), meta);
    return row;
  });
  replace(dom.compactList, rows.length ? rows : [make("p", "empty-note", "尚未配置监控目标")]);
}

function renderChart(hourly) {
  const totals = hourly.map((bucket) => num(bucket.calls));
  const peak = Math.max(0, ...totals);
  const scale = niceMax(peak);
  const empty = peak === 0;

  dom.plotEmpty.hidden = !empty;
  replace(dom.yAxis, [
    make("span", null, empty ? "" : compact(scale)),
    make("span", null, empty ? "" : compact(scale / 2)),
    make("span", null, empty ? "" : "0"),
  ]);

  const bars = hourly.map((bucket) => {
    const calls = num(bucket.calls);
    const failures = Math.min(calls, num(bucket.failures));
    const ok = calls - failures;
    const column = make("div", "hour");
    column.dataset.tip = `${hourOf(bucket.bucket)} · 调用 ${compact(calls)} · 失败 ${compact(failures)}`;

    if (calls === 0) {
      column.append(make("div", "seg zero"));
      return column;
    }
    if (ok > 0) {
      const seg = make("div", "seg ok" + (failures > 0 ? "" : " cap"));
      seg.style.height = `max(2px, ${(ok / scale) * 100}%)`;
      column.append(seg);
    }
    if (failures > 0) {
      const seg = make("div", "seg fail cap");
      seg.style.height = `max(2px, ${(failures / scale) * 100}%)`;
      column.append(seg);
    }
    return column;
  });
  replace(dom.plot, bars);
}

function renderEvents(data) {
  const rows = [];
  const events = Array.isArray(data.events) ? data.events : [];
  const errors = Array.isArray(data.errors) ? data.errors : [];

  for (const event of events.slice(0, 12)) {
    const recovered = event.toState === "online";
    const row = make("div", "event-row");
    const dot = make("i", "dot");
    dot.dataset.status = recovered ? "online" : event.toState === "degraded" ? "degraded" : "offline";
    const copy = make("div", "event-copy");
    copy.append(make("strong", null, `${event.name || event.target} ${recovered ? "已恢复" : "状态变化"}`));
    copy.append(make("p", null, `${event.fromState || "?"} → ${event.toState || "?"}`));
    row.append(dot, copy, make("time", null, clockOf(event.occurredAt)));
    rows.push(row);
  }

  for (const error of errors.slice(0, 8)) {
    const row = make("div", "event-row");
    const dot = make("i", "dot");
    dot.dataset.status = "offline";
    const copy = make("div", "event-copy");
    copy.append(make("strong", null, String(error.code || error.error || "异常")));
    copy.append(make("p", null, String(error.message || error.summary || error.module || "")));
    row.append(dot, copy, make("time", null, clockOf(error.createdAt || error.created_at)));
    rows.push(row);
  }

  dom.eventCount.textContent = String(rows.length);
  replace(dom.eventList, rows.length ? rows : [make("p", "empty-note", "暂无状态变化或异常")]);
}

/* ---------- board widgets (扩展面板) ---------- */

function widgetBody(widget) {
  const body = make("div", "widget-body");
  if (!widget.ok) {
    body.append(make("p", "widget-error", widget.error || "模块出错"));
    return body;
  }
  const data = widget.data || {};
  if (widget.kind === "stat") {
    const wrap = make("div", "widget-stat");
    wrap.append(make("strong", null, data.value == null ? "—" : String(data.value)));
    wrap.append(make("span", null, data.label || ""));
    if (data.note) wrap.append(make("small", null, data.note));
    body.append(wrap);
    return body;
  }
  if (widget.kind === "keyvalue") {
    const rows = make("div", "widget-kv");
    for (const pair of Array.isArray(data.pairs) ? data.pairs : []) {
      const row = make("div");
      row.append(make("span", null, pair.label), make("b", null, pair.value));
      rows.append(row);
    }
    body.append(rows);
    return body;
  }
  if (widget.kind === "list") {
    const items = Array.isArray(data.items) ? data.items : [];
    if (!items.length) {
      body.append(make("div", "widget-empty", data.empty || "暂无内容"));
      return body;
    }
    const rows = make("div", "widget-rows");
    for (const item of items) {
      const row = make("div", "widget-row");
      if (item.state) {
        const dot = make("i", "dot");
        dot.dataset.status = item.state;
        row.append(dot);
      }
      const info = make("div", "widget-row-info");
      info.append(make("strong", null, item.title || ""));
      if (item.subtitle) info.append(make("small", null, item.subtitle));
      row.append(info);
      if (item.value != null) row.append(make("em", null, String(item.value)));
      rows.append(row);
    }
    body.append(rows);
    return body;
  }
  const text = make("div", "widget-text");
  for (const line of String(data.body || "").split("\n")) {
    if (line) text.append(make("p", null, line));
  }
  body.append(text);
  return body;
}

function renderWidgets(widgets) {
  dom.widgetCount.textContent = String(widgets.length);
  const cards = widgets.map((widget) => {
    const card = make("article", widget.ok ? "widget-card" : "widget-card error");
    const head = make("div", "widget-head");
    const heading = make("div");
    heading.append(make("strong", null, widget.title || widget.id));
    if (widget.subtitle) heading.append(make("small", null, widget.subtitle));
    head.append(heading, make("span", "widget-chip", widget.type || ""));
    card.append(head, widgetBody(widget));
    return card;
  });
  replace(dom.widgetGrid, cards);
}

/* ---------- view state ---------- */

function applyView(view) {
  if (view.theme) root.dataset.theme = view.theme;
  dom.body.classList.toggle("compact", Boolean(view.compact));
  dom.btnCompact.setAttribute("aria-pressed", String(Boolean(view.compact)));
  dom.btnTop.setAttribute("aria-pressed", String(Boolean(view.onTop)));
  dom.body.classList.remove("booting");
}

/* ---------- chart tooltip ---------- */

dom.plot.addEventListener("mouseover", (event) => {
  const column = event.target.closest(".hour");
  if (!column || !dom.chartPanel.contains(column)) return;
  const panel = dom.chartPanel.getBoundingClientRect();
  const bar = column.getBoundingClientRect();
  dom.chartTip.textContent = column.dataset.tip;
  dom.chartTip.hidden = false;
  dom.chartTip.style.left = `${bar.left - panel.left + bar.width / 2}px`;
  dom.chartTip.style.top = `${bar.top - panel.top - 6}px`;
});
dom.plot.addEventListener("mouseleave", () => {
  dom.chartTip.hidden = true;
});

/* ---------- controls ---------- */

function bindControls() {
  el("btnRefresh").addEventListener("click", async () => {
    dom.btnRefresh.classList.add("spinning");
    await pull(true);
    dom.btnRefresh.classList.remove("spinning");
  });
  el("btnRetry").addEventListener("click", () => pull(true));
  el("btnMin").addEventListener("click", () => api() && api().minimize());
  el("btnClose").addEventListener("click", () => api() && api().hide_to_tray());
  el("btnTop").addEventListener("click", async () => {
    const next = dom.btnTop.getAttribute("aria-pressed") !== "true";
    const bridge = api();
    if (bridge) render(await bridge.set_on_top(next));
  });
  el("btnCompact").addEventListener("click", async () => {
    const next = !dom.body.classList.contains("compact");
    const bridge = api();
    if (bridge) render(await bridge.set_compact(next));
  });
  el("btnTheme").addEventListener("click", async () => {
    const next = root.dataset.theme === "dark" ? "light" : "dark";
    const bridge = api();
    if (bridge) render(await bridge.set_theme(next));
  });
}

function start() {
  if (bridgeReady) return;
  bridgeReady = true;
  bindControls();
  pull(false);
  timer = window.setInterval(() => pull(false), POLL_MS);
}

window.addEventListener("pywebviewready", start);
if (api()) start();
window.addEventListener("beforeunload", () => timer && window.clearInterval(timer));
