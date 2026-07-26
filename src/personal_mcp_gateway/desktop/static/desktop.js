/* Poyi Control Center — desktop renderer.
   All gateway I/O happens in Python and arrives through pywebview.api, so this
   file never performs a network request of its own.

   Layout: a slim fleet strip on top, then one full-width peer section per
   project — each keeping its source project's own art (instrument / paper /
   sport / neutral) — then the global activity chart, event center and the
   extension widgets. Sections re-render only when their data actually changed. */
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
  fsDot: el("fsDot"),
  heroValue: el("heroValue"),
  heroTitle: el("heroTitle"),
  meterCompact: el("meterCompact"),
  meterFillCompact: el("meterFillCompact"),
  heroValueCompact: el("heroValueCompact"),
  heroLabelCompact: el("heroLabelCompact"),
  tileCalls: el("tileCalls"),
  tileRate: el("tileRate"),
  tileUptime: el("tileUptime"),
  tileProbe: el("tileProbe"),
  projectSections: el("projectSections"),
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
  repairHint: el("repairHint"),
  sbDot: el("sbDot"),
  sbState: el("sbState"),
  sbSync: el("sbSync"),
  sbProbe: el("sbProbe"),
  sbGuard: el("sbGuard"),
  btnTop: el("btnTop"),
  btnCompact: el("btnCompact"),
  btnRefresh: el("btnRefresh"),
  btnRepair: el("btnRepair"),
  btnRepairOffline: el("btnRepairOffline"),
};

/* Per-project art direction: the section carries the source project's own
   surface, type and accent (docs/integrations). Accents mark identity only —
   status stays with the reserved status colors, charts keep their own series. */
const PROJECT_STYLE = {
  foxlink: {
    flavor: "instrument",
    accent: "#007A55",
    display: "FocusLink",
    tagline: "专注 · 时间仪器",
    groups: ["FocusLink"],
  },
  watch: {
    flavor: "sport",
    accent: "#B6FF39",
    display: "步序 · 间歇跑",
    tagline: "训练 · 睡眠 · 手表",
    groups: ["步序 · 间歇跑"],
  },
  journal: {
    flavor: "paper",
    accent: "#A85F27",
    display: "拾光 · 日记复盘",
    tagline: "记录 · 回看 · 复盘",
    groups: ["拾光日记"],
  },
  personal: {
    flavor: "neutral",
    accent: "#7c6cff",
    display: "Personal Gateway",
    tagline: "总机房 · 隧道与看护",
    groups: [],
  },
};
const SECTION_ORDER = ["foxlink", "watch", "journal", "personal"];

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

let lastDataKey = "";

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
    renderGuard(null);
    lastDataKey = "";
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
  dom.sbSync.textContent = `同步 ${clockOf(data.generatedAt || payload.fetchedAt)}`;
  dom.sbProbe.textContent = `探测 ${num(data.probeDurationMs)}ms`;
  renderGuard(data.fleet || null);

  // Skip the DOM rebuild when nothing but timestamps changed — the board polls
  // every 4s and most passes carry identical data.
  const key = JSON.stringify([
    payload.status,
    data.targets,
    data.widgets,
    data.summary,
    data.events,
    data.errors,
    data.activity,
    data.fleet,
  ]);
  if (key === lastDataKey) return;
  lastDataKey = key;

  renderStrip(payload, online, total, gateway, summary, data);
  renderSections(data);
  renderChart(data.activity && Array.isArray(data.activity.hourly) ? data.activity.hourly : []);
  renderEvents(data);
  renderExtensionWidgets(Array.isArray(data.widgets) ? data.widgets : []);
  renderCompact(data, payload);
}

function setStatusChrome(status, count, label) {
  dom.tbChipDot.dataset.status = status;
  dom.sbDot.dataset.status = status;
  if (dom.fsDot) dom.fsDot.dataset.status = status;
  dom.tbCount.textContent = count;
  dom.tbLabel.textContent = label;
  dom.sbState.textContent = status === "disconnected" ? "DISCONNECTED" : "CONNECTED";
}

function renderGuard(fleet) {
  const state = fleet && fleet.watchdog ? fleet.watchdog.state : null;
  if (state === "running") {
    dom.sbGuard.textContent = "看护在线";
    dom.sbGuard.dataset.state = "ok";
  } else if (state) {
    dom.sbGuard.textContent = state === "missing" ? "看护未安装" : "看护离线";
    dom.sbGuard.dataset.state = "bad";
  } else {
    dom.sbGuard.textContent = "看护 —";
    dom.sbGuard.dataset.state = "";
  }
}

function renderStrip(payload, online, total, gateway, summary, data) {
  dom.heroValue.textContent = `${online}/${total}`;
  const titles = {
    online: "所有系统正常运行",
    degraded: "部分链路已降级",
    offline: "存在离线项目",
  };
  dom.heroTitle.textContent = titles[payload.status] || "状态未知";
  dom.tileCalls.textContent = compact(summary.calls24h);
  dom.tileRate.textContent = `${num(summary.successRate, 100).toFixed(1)}%`;
  dom.tileUptime.textContent = duration(gateway.uptimeSeconds);
  dom.tileProbe.textContent = `${num(data.probeDurationMs)}ms`;
}

/* ---------- project sections ---------- */

function probeChip(label, probe) {
  const chip = make("div", "pv-chip");
  const dot = make("i", "dot");
  dot.dataset.status = probe && probe.ok ? "online" : "offline";
  const service = probe && probe.service ? probe.service : null;
  let detail = "不可用";
  if (probe && probe.ok) {
    detail = probe.latencyMs == null ? "就绪" : `${num(probe.latencyMs)}ms`;
  } else if (service && service.state && service.state !== "running") {
    detail = service.state === "missing" ? "服务未安装" : "服务已停止";
  }
  if (service && service.name) chip.title = `${service.name} · ${service.state || "unknown"}`;
  chip.append(dot, make("span", null, label), make("em", null, detail));
  return chip;
}

function sectionDataCards(target, widgets, gateway, fleet) {
  const style = PROJECT_STYLE[target.id];
  const cards = [];
  if (target.id === "personal") {
    const stats = [
      ["本次在线", duration(gateway.uptimeSeconds)],
      ["累计调用", compact(gateway.callsTotal)],
      ["调用失败", compact(gateway.callsFailed)],
      ["看护服务", fleet && fleet.watchdog && fleet.watchdog.state === "running" ? "在线" : "异常"],
    ];
    for (const [label, value] of stats) {
      const card = make("article", "pd-stat");
      card.append(make("strong", null, String(value)), make("span", null, label));
      cards.push(card);
    }
    return cards;
  }
  const mine = widgets.filter((w) => style.groups.includes(w.group || ""));
  for (const widget of mine) {
    const card = make("article", `pd-widget${widget.ok ? "" : " error"}`);
    const head = make("div", "pd-widget-head");
    head.append(make("strong", null, widget.title || widget.id));
    if (widget.subtitle) head.append(make("small", null, widget.subtitle));
    card.append(head, widgetBody(widget));
    cards.push(card);
  }
  if (!cards.length) {
    const empty = make("p", "pd-empty", "尚未配置数据卡 — 编辑 board-widgets.yaml 即可点亮");
    cards.push(empty);
  }
  return cards;
}

function sectionTail(target, data) {
  const rows = [];
  const events = Array.isArray(data.events) ? data.events : [];
  for (const event of events.filter((e) => e.target === target.id).slice(0, 3)) {
    const row = make("div", "pt-row");
    const dot = make("i", "dot");
    dot.dataset.status = event.toState === "online" ? "online" : event.toState === "degraded" ? "degraded" : "offline";
    row.append(
      dot,
      make("span", null, event.toState === "online" ? "已恢复" : `${event.fromState} → ${event.toState}`),
      make("time", null, clockOf(event.occurredAt))
    );
    rows.push(row);
  }
  const recent = data.activity && Array.isArray(data.activity.recent) ? data.activity.recent : [];
  for (const item of recent.filter((r) => (r.module || "") === target.id).slice(0, 3)) {
    const row = make("div", "pt-row");
    const mark = make("i", "pt-mark", "↗");
    if (item.result !== "success") mark.classList.add("fail");
    row.append(
      mark,
      make("span", null, String(item.tool || "")),
      make("time", null, `${num(item.durationMs)}ms`)
    );
    rows.push(row);
  }
  return rows;
}

function renderSections(data) {
  const targets = Array.isArray(data.targets) ? data.targets : [];
  const widgets = Array.isArray(data.widgets) ? data.widgets : [];
  const byId = new Map(targets.map((t) => [t.id, t]));
  const ordered = [];
  for (const id of SECTION_ORDER) if (byId.has(id)) ordered.push(byId.get(id));
  for (const t of targets) if (!SECTION_ORDER.includes(t.id)) ordered.push(t);

  const sections = ordered.map((target) => {
    const style = PROJECT_STYLE[target.id] || {
      flavor: "neutral",
      accent: target.accent || "#8878ff",
      display: target.name,
      tagline: target.description || "",
      groups: [],
    };
    const section = make("section", `proj proj-${style.flavor}`);
    section.style.setProperty("--p-accent", style.accent);
    section.dataset.state = target.state || "offline";

    const head = make("header", "proj-head");
    const naming = make("div", "proj-naming");
    naming.append(make("h2", null, style.display));
    const sub = make("p", "proj-tagline");
    sub.textContent = `${style.tagline}${target.version ? ` · v${String(target.version).replace(/^v/, "")}` : ""}`;
    naming.append(sub);
    const state = make("div", "proj-state");
    const dot = make("i", "dot");
    dot.dataset.status = target.state || "offline";
    const stateText = { online: "正常", degraded: "降级", offline: "离线" }[target.state] || "未知";
    state.append(dot, make("b", null, stateText));
    head.append(naming, state);

    const vitals = make("div", "proj-vitals");
    vitals.append(probeChip("MCP", target.mcp));
    if (target.tunnel) vitals.append(probeChip("隧道", target.tunnel));

    const dataZone = make("div", "proj-data");
    for (const card of sectionDataCards(target, widgets, data.gateway || {}, data.fleet || null)) {
      dataZone.append(card);
    }

    const tail = make("div", "proj-tail");
    const tailRows = sectionTail(target, data);
    if (tailRows.length) {
      for (const row of tailRows) tail.append(row);
    } else {
      tail.append(make("p", "pt-quiet", "近期安静，无状态变化"));
    }

    section.append(head, vitals, dataZone, tail);
    return section;
  });
  replace(dom.projectSections, sections);
}

function renderCompact(data, payload) {
  const summary = data.summary || {};
  const online = num(summary.online);
  const total = num(summary.total);
  dom.heroValueCompact.textContent = `${online}/${total}`;
  dom.heroLabelCompact.textContent = payload.statusLabel || "在线";
  setMeter(dom.meterCompact, dom.meterFillCompact, online, total, payload.status);
  dom.compactCalls.textContent = compact(summary.calls24h);
  dom.compactRate.textContent = `${num(summary.successRate, 100).toFixed(1)}%`;

  const targets = Array.isArray(data.targets) ? data.targets : [];
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

function setMeter(meter, fill, online, total, status) {
  const ratio = total > 0 ? Math.min(1, online / total) : 0;
  meter.dataset.status = status;
  fill.style.strokeDasharray = String(CIRCUMFERENCE);
  fill.style.strokeDashoffset = String(CIRCUMFERENCE * (1 - ratio));
}

/* ---------- chart / events / extension widgets ---------- */

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

function renderExtensionWidgets(widgets) {
  // Project-owned widgets live inside their sections; the extension panel keeps
  // everything else (memos, agenda, project progress, custom cards).
  const claimed = new Set();
  for (const style of Object.values(PROJECT_STYLE)) {
    for (const g of style.groups) claimed.add(g);
  }
  const rest = widgets.filter((w) => !claimed.has(w.group || ""));
  dom.widgetCount.textContent = String(rest.length);
  const nodes = [];
  let currentGroup = null;
  for (const widget of rest) {
    const group = widget.group || null;
    if (group !== currentGroup) {
      currentGroup = group;
      if (group) {
        const head = make("div", "widget-group-head");
        if (widget.accent) head.style.setProperty("--w-accent", widget.accent);
        head.append(make("i"), make("span", null, group));
        nodes.push(head);
      }
    }
    const flavor = `flavor-${widget.flavor || "neutral"}`;
    const card = make("article", `widget-card ${flavor}${widget.ok ? "" : " error"}`);
    if (widget.accent) card.style.setProperty("--w-accent", widget.accent);
    const head = make("div", "widget-head");
    const heading = make("div");
    heading.append(make("strong", null, widget.title || widget.id));
    if (widget.subtitle) heading.append(make("small", null, widget.subtitle));
    head.append(heading, make("span", "widget-chip", widget.type || ""));
    card.append(head, widgetBody(widget));
    nodes.push(card);
  }
  replace(dom.widgetGrid, nodes);
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

/* ---------- one-click repair ---------- */

let repairArmTimer = null;

function disarmRepair() {
  if (repairArmTimer) window.clearTimeout(repairArmTimer);
  repairArmTimer = null;
  dom.btnRepair.classList.remove("arming");
  dom.btnRepair.title = "一键修复全部服务";
  dom.btnRepairOffline.classList.remove("arming");
  dom.btnRepairOffline.textContent = "一键修复全部服务";
}

async function requestRepair(button) {
  const bridge = api();
  if (!bridge || !bridge.repair_fleet) return;
  // First click arms, second click within 5s fires: no dialogs in a tray app.
  if (!button.classList.contains("arming")) {
    disarmRepair();
    button.classList.add("arming");
    if (button === dom.btnRepairOffline) button.textContent = "再点一次确认修复";
    button.title = "再点一次确认修复";
    repairArmTimer = window.setTimeout(disarmRepair, 5000);
    return;
  }
  disarmRepair();
  let result = null;
  try {
    result = await bridge.repair_fleet();
  } catch (error) {
    result = { ok: false, message: "修复请求失败" };
  }
  const message = result && result.message ? result.message : "修复请求已发送";
  dom.repairHint.textContent = message;
  dom.sbState.textContent = message;
  window.setTimeout(() => pull(true), 3000);
}

/* ---------- controls ---------- */

function bindControls() {
  el("btnRefresh").addEventListener("click", async () => {
    dom.btnRefresh.classList.add("spinning");
    await pull(true);
    dom.btnRefresh.classList.remove("spinning");
  });
  el("btnRetry").addEventListener("click", () => pull(true));
  dom.btnRepair.addEventListener("click", () => requestRepair(dom.btnRepair));
  dom.btnRepairOffline.addEventListener("click", () => requestRepair(dom.btnRepairOffline));
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
    if (bridge) {
      lastDataKey = "";
      render(await bridge.set_theme(next));
    }
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
