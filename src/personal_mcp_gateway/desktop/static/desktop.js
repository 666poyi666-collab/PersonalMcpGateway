/* Poyi Control Center — desktop renderer.
   All gateway I/O happens in Python and arrives through pywebview.api, so this
   file never performs a network request of its own.

   Layout: a slim fleet strip on top, then a freeform project canvas where
   each project keeps its own art (instrument / paper / sport / neutral),
   followed by the global activity and extension views. Sections re-render
   only when their data actually changed. */
"use strict";

const POLL_MS = 4000;
const CIRCUMFERENCE = 2 * Math.PI * 52;

const el = (id) => document.getElementById(id);
const root = document.documentElement;

const dom = {
  body: document.body,
  stage: el("stage"),
  board: el("board"),
  titlebarDrag: document.querySelector(".tb-drag"),
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
  offlineTitle: el("offlineTitle"),
  offlineHint: el("offlineHint"),
  repairHint: el("repairHint"),
  sbDot: el("sbDot"),
  sbState: el("sbState"),
  sbSync: el("sbSync"),
  sbProbe: el("sbProbe"),
  sbGuard: el("sbGuard"),
  btnTop: el("btnTop"),
  btnDesktop: el("btnDesktop"),
  btnCompact: el("btnCompact"),
  btnRefresh: el("btnRefresh"),
  btnRepair: el("btnRepair"),
  btnRepairOffline: el("btnRepairOffline"),
  viewClock: el("viewClock"),
  btnCapture: el("btnCapture"),
  btnLayout: el("btnLayout"),
  btnLayoutReset: el("btnLayoutReset"),
  btnWidgetEdit: el("btnWidgetEdit"),
  btnWidgetReset: el("btnWidgetReset"),
  btnWidgetHide: el("btnWidgetHide"),
  btnWidgetQuit: el("btnWidgetQuit"),
  overviewRail: el("overviewRail"),
  matrixHeading: el("matrixHeading"),
  matrixFitState: el("matrixFitState"),
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
    display: "步序",
    eyebrow: "INTERVAL ENGINE / OWW221",
    tagline: "间歇训练 · 睡眠恢复",
    groups: ["步序 · 间歇跑"],
  },
  journal: {
    flavor: "paper",
    accent: "#A85F27",
    display: "拾光 · 日记复盘",
    tagline: "记录 · 回看 · 复盘",
    groups: ["拾光日记"],
  },
  bzsjk: {
    flavor: "discipline",
    accent: "#FF5C4D",
    display: "不做手机控",
    eyebrow: "LOCAL DISCIPLINE / FOCUSLINK",
    tagline: "监督锁机 · 本地维护",
    groups: [],
  },
  personal: {
    flavor: "gateway",
    accent: "#63D8FF",
    display: "Personal Gateway",
    eyebrow: "MCP ROUTING FABRIC",
    tagline: "本机能力路由 · 连接与自愈",
    groups: [],
  },
};
const SECTION_ORDER = ["foxlink", "watch", "journal", "personal", "bzsjk"];
const LAYOUT_SCALE = 1000;
const PROJECT_LAYOUT_VERSION = 3;
const MIN_TILE_WIDTH = 120;
const MIN_TILE_HEIGHT = 104;
const RENDER_MIN_TILE_WIDTH = 92;
const RENDER_MIN_TILE_HEIGHT = 82;
const MAX_TILE_WIDTH = 20000;
const MAX_TILE_HEIGHT = 20000;
const MAX_WORKSPACE_POSITION = 100000;
const CANVAS_PADDING = 18;
const AUTO_SCROLL_MARGIN = 58;
const AUTO_SCROLL_MAX = 20;
const RECOVERY_BANNER_FAILURES = 3;
const FAILURE_DISPLAY_CAP = 999;
const SNAP_DISTANCE = 10;
const MIDDLE_MOUSE_BUTTON = 1;
const MIDDLE_MOUSE_BUTTONS_MASK = 4;
const DEFAULT_TILE_LAYOUT = {
  foxlink: { x: 0, y: 0, w: 280, h: 440, order: 0 },
  watch: { x: 288, y: 0, w: 380, h: 440, order: 1 },
  journal: { x: 676, y: 0, w: 324, h: 440, order: 2 },
  personal: { x: 0, y: 450, w: 720, h: 550, order: 3 },
  bzsjk: { x: 730, y: 450, w: 270, h: 550, order: 4 },
};
const TILE_SIZE_PRESETS = {
  small: { width: 0.28, height: 0.34 },
  medium: { width: 0.46, height: 0.5 },
  large: { width: 0.7, height: 0.68 },
};
const OFFLINE_MATRIX_TARGETS = SECTION_ORDER.map((id) => {
  const style = PROJECT_STYLE[id];
  return {
    id,
    name: style.display,
    description: style.tagline,
    state: "unknown",
    mcp: null,
    tunnel: null,
    sync: {
      compliance: "unknown",
      dataPlane: "unknown",
      pcOff: { readAvailable: false, writeAvailable: false, continuedSync: false },
      snapshotState: "unknown",
      observation: { result: "unknown" },
    },
  };
});
let projectLayout = {};
let projectLayoutVersion = PROJECT_LAYOUT_VERSION;
let layoutMode = false;
let activeTileInteraction = null;
let activeCanvasPan = null;
let layoutFrame = 0;
let interactionScrollFrame = 0;
let canvasPanFrame = 0;
let projectCanvasWidth = 0;
let projectViewportWidth = 0;
let projectViewportHeight = 0;
let projectResizeObserver = null;
let sectionsRendered = false;
let windowResizeTimer = 0;
let projectResizeFrame = 0;

// Renderer-owned animations are deliberately short and compositor-only.  The
// window host can dispatch dozens of resize observations per second, so motion
// is suppressed while a window/tile resize is active and restarted afterwards.
const renderAnimations = new WeakMap();
const exitAnimations = new WeakMap();
const exitTimers = new WeakMap();
const densityTimers = new WeakMap();
const densityFrames = new WeakMap();

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

function briefDuration(seconds) {
  const s = Math.max(0, Math.floor(num(seconds)));
  const days = Math.floor(s / 86400);
  const hours = Math.floor((s % 86400) / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m`;
  return `${s}s`;
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

function renderKey(node, key) {
  if (node && node.nodeType === Node.ELEMENT_NODE) {
    node.dataset.renderKey = String(key);
  }
  return node;
}

function nodeRenderKey(node) {
  return node && node.nodeType === Node.ELEMENT_NODE
    ? node.getAttribute("data-render-key")
    : null;
}

function sameNodeShape(current, desired) {
  if (!current || !desired || current.nodeType !== desired.nodeType) return false;
  if (current.nodeType !== Node.ELEMENT_NODE) return true;
  return current.namespaceURI === desired.namespaceURI && current.localName === desired.localName;
}

function renderMotionAllowed() {
  if (
    !dom.body
    || dom.body.classList.contains("window-resizing")
    || activeTileInteraction
    || activeCanvasPan
  ) return false;
  return !(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
}

function playRenderAnimation(node, keyframes, options) {
  if (!node || typeof node.animate !== "function" || !renderMotionAllowed()) return null;
  const previous = renderAnimations.get(node);
  if (previous) previous.cancel();
  const animation = node.animate(keyframes, options);
  renderAnimations.set(node, animation);
  animation.finished.catch(() => {}).finally(() => {
    if (renderAnimations.get(node) === animation) renderAnimations.delete(node);
  });
  return animation;
}

function animateRenderEnter(node) {
  playRenderAnimation(
    node,
    [
      { opacity: 0, transform: "translate3d(0, 7px, 0) scale(.985)" },
      { opacity: 1, transform: "translate3d(0, 0, 0) scale(1)" },
    ],
    { duration: 240, easing: "cubic-bezier(.2,.8,.2,1)" },
  );
}

function animateRenderUpdate(node) {
  if (!node || node.childElementCount > 0) return;
  playRenderAnimation(
    node,
    [
      { opacity: 0.42, transform: "translate3d(0, 3px, 0)" },
      { opacity: 1, transform: "translate3d(0, 0, 0)" },
    ],
    { duration: 210, easing: "cubic-bezier(.2,.8,.2,1)" },
  );
}

function removeWithMotion(node, onRemoved = null) {
  const pending = exitAnimations.get(node);
  if (pending) {
    pending.cancel();
    exitAnimations.delete(node);
  }
  const pendingTimer = exitTimers.get(node);
  if (pendingTimer) {
    window.clearTimeout(pendingTimer);
    exitTimers.delete(node);
  }
  if (!node.isConnected || !renderMotionAllowed() || typeof node.animate !== "function") {
    node.remove();
    if (onRemoved) onRemoved();
    return;
  }
  if (node.nodeType === Node.ELEMENT_NODE) {
    node.dataset.renderExiting = "true";
    node.setAttribute("aria-hidden", "true");
    node.style.pointerEvents = "none";
  }
  const animation = node.animate(
    [
      { opacity: 1, transform: "translate3d(0, 0, 0) scale(1)" },
      { opacity: 0, transform: "translate3d(0, -5px, 0) scale(.985)" },
    ],
    { duration: 170, easing: "cubic-bezier(.4,0,1,1)", fill: "forwards" },
  );
  exitAnimations.set(node, animation);
  const finish = () => {
    if (exitAnimations.get(node) !== animation) return;
    exitAnimations.delete(node);
    const timer = exitTimers.get(node);
    if (timer) window.clearTimeout(timer);
    exitTimers.delete(node);
    node.remove();
    if (onRemoved) onRemoved();
  };
  // Background/minimized WebViews can throttle animation timelines while JS
  // timers keep advancing.  The fallback prevents exited rows accumulating.
  exitTimers.set(node, window.setTimeout(finish, 520));
  animation.finished.catch(() => {}).finally(finish);
}

function syncAttributes(current, desired) {
  const previousHeight = current.style ? current.style.height : "";
  const previousStrokeOffset = current.style ? current.style.strokeDashoffset : "";
  const previousTrafficHeight = current.style ? current.style.getPropertyValue("--gw-bar-height") : "";
  let changed = false;
  let detailedMotion = false;
  for (const attribute of [...current.attributes]) {
    if (!desired.hasAttribute(attribute.name)) {
      current.removeAttribute(attribute.name);
      changed = true;
    }
  }
  for (const attribute of [...desired.attributes]) {
    if (current.getAttribute(attribute.name) !== attribute.value) {
      current.setAttribute(attribute.name, attribute.value);
      changed = true;
    }
  }
  if (current.matches(".seg") && previousHeight !== current.style.height) {
    detailedMotion = true;
    playRenderAnimation(
      current,
      [{ height: previousHeight || "0px" }, { height: current.style.height || "0px" }],
      { duration: 320, easing: "cubic-bezier(.2,.8,.2,1)" },
    );
  } else if (current.matches(".wi-score-value")
      && previousStrokeOffset !== current.style.strokeDashoffset) {
    detailedMotion = true;
    playRenderAnimation(
      current,
      [
        { strokeDashoffset: previousStrokeOffset || "100" },
        { strokeDashoffset: current.style.strokeDashoffset || "100" },
      ],
      { duration: 460, easing: "cubic-bezier(.2,.8,.2,1)" },
    );
  } else if (current.matches(".gw-traffic-bar")
      && previousTrafficHeight !== current.style.getPropertyValue("--gw-bar-height")) {
    detailedMotion = true;
    playRenderAnimation(
      current,
      [
        { opacity: 0.55, transform: "scaleY(.72)", transformOrigin: "50% 100%" },
        { opacity: 1, transform: "scaleY(1)", transformOrigin: "50% 100%" },
      ],
      { duration: 260, easing: "cubic-bezier(.2,.8,.2,1)" },
    );
  }
  if (!detailedMotion && changed
      && (current.matches(".dot, .seg, .gw-traffic-bar") || current.childElementCount === 0)) {
    animateRenderUpdate(current);
  }
}

function patchRenderNode(current, desired) {
  if (current.nodeType === Node.TEXT_NODE) {
    if (current.data !== desired.data) {
      current.data = desired.data;
      animateRenderUpdate(current.parentElement);
    }
    return current;
  }
  if (current.nodeType !== Node.ELEMENT_NODE) return current;
  const exiting = exitAnimations.get(current);
  if (exiting) {
    exiting.cancel();
    exitAnimations.delete(current);
    const exitTimer = exitTimers.get(current);
    if (exitTimer) window.clearTimeout(exitTimer);
    exitTimers.delete(current);
    delete current.dataset.renderExiting;
    current.removeAttribute("aria-hidden");
    current.style.pointerEvents = "";
  }
  syncAttributes(current, desired);
  reconcileRenderChildren(current, [...desired.childNodes]);
  return current;
}

function shouldFlipChildren(container) {
  return container.matches(
    ".jr-ledger, .gw-service-rows, .gw-event-list, .proj-tail, .widget-rows, #eventList, #widgetGrid, #compactList",
  );
}

function reconcileRenderChildren(container, desiredChildren, { animateNew = true, animateExit = true } = {}) {
  const existing = [...container.childNodes];
  const keyed = new Map();
  for (const child of existing) {
    const key = nodeRenderKey(child);
    if (key == null) continue;
    if (!keyed.has(key)) keyed.set(key, []);
    keyed.get(key).push(child);
  }
  const used = new Set();
  const resolved = [];
  const oldRects = new Map();
  const flip = renderMotionAllowed() && shouldFlipChildren(container) && container.isConnected;
  if (flip) {
    for (const child of existing) {
      if (nodeRenderKey(child) != null && child.nodeType === Node.ELEMENT_NODE) {
        oldRects.set(child, child.getBoundingClientRect());
      }
    }
  }

  for (let index = 0; index < desiredChildren.length; index += 1) {
    const desired = desiredChildren[index];
    const key = nodeRenderKey(desired);
    let current = key == null ? null : (keyed.get(key) || []).find((candidate) => (
      !used.has(candidate) && sameNodeShape(candidate, desired)
    ));
    if (!current) {
      const indexed = existing[index];
      if (key == null && indexed && !used.has(indexed) && nodeRenderKey(indexed) == null
          && sameNodeShape(indexed, desired)) {
        current = indexed;
      }
    }
    if (!current && key == null) {
      current = existing.find((candidate) => (
        !used.has(candidate) && nodeRenderKey(candidate) == null && sameNodeShape(candidate, desired)
      ));
    }
    if (current) {
      used.add(current);
      resolved.push(patchRenderNode(current, desired));
    } else {
      resolved.push(desired);
    }
  }

  let cursor = container.firstChild;
  for (let index = 0; index < resolved.length; index += 1) {
    const child = resolved[index];
    const isNew = !used.has(child);
    if (child !== cursor) container.insertBefore(child, cursor);
    cursor = child.nextSibling;
    if (isNew && animateNew && container.isConnected) animateRenderEnter(child);
  }

  for (const child of existing) {
    if (used.has(child)) continue;
    if (animateExit) removeWithMotion(child);
    else child.remove();
  }

  if (flip) {
    for (const child of resolved) {
      const first = oldRects.get(child);
      if (!first || child.nodeType !== Node.ELEMENT_NODE) continue;
      const last = child.getBoundingClientRect();
      const dx = first.left - last.left;
      const dy = first.top - last.top;
      if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) continue;
      playRenderAnimation(
        child,
        [
          { transform: `translate3d(${dx}px, ${dy}px, 0)` },
          { transform: "translate3d(0, 0, 0)" },
        ],
        { duration: 260, easing: "cubic-bezier(.2,.8,.2,1)" },
      );
    }
  }
}

function replace(node, children) {
  reconcileRenderChildren(node, children);
}

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function setRenderText(node, value) {
  if (!node) return;
  const next = value == null ? "" : String(value);
  if (node.textContent === next) return;
  node.textContent = next;
  animateRenderUpdate(node);
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
    dom.body.classList.remove("recovering");
    dom.offlineScreen.hidden = num(payload.consecutiveFailures) < RECOVERY_BANNER_FAILURES;
    setRenderText(dom.offlineTitle, "本机网关暂不可用");
    const failures = Math.min(FAILURE_DISPLAY_CAP, Math.max(0, num(payload.consecutiveFailures)));
    const failureText = failures >= FAILURE_DISPLAY_CAP ? `${FAILURE_DISPLAY_CAP}+` : failures;
    setRenderText(dom.offlineHint, failures
      ? `已连续重试 ${failureText} 次 · ${clockOf(payload.fetchedAt)}`
      : "");
    setStatusChrome("disconnected", "—", "网关未连接");
    setRenderText(dom.sbSync, clockOf(payload.fetchedAt));
    setRenderText(dom.sbProbe, "—");
    renderGuard(null);
    if (!sectionsRendered) {
      renderSections({
        refreshIntervalSeconds: POLL_MS / 1000,
        summary: {
          total: OFFLINE_MATRIX_TARGETS.length,
          online: 0,
          degraded: 0,
          offline: OFFLINE_MATRIX_TARGETS.length,
        },
        targets: OFFLINE_MATRIX_TARGETS,
        widgets: [],
        activity: { hourly: [], recent: [] },
        events: [],
        fleet: {},
      });
    }
    lastDataKey = "";
    return;
  }

  dom.body.classList.remove("disconnected");
  const recoveryVisible = Boolean(payload.stale)
    && num(payload.consecutiveFailures) >= RECOVERY_BANNER_FAILURES;
  dom.body.classList.toggle("recovering", recoveryVisible);
  dom.offlineScreen.hidden = !recoveryVisible;
  if (recoveryVisible) {
    setRenderText(dom.offlineTitle, "实时连接中断，矩阵仍可查看");
    const failures = Math.min(FAILURE_DISPLAY_CAP, Math.max(0, num(payload.consecutiveFailures)));
    const failureText = failures >= FAILURE_DISPLAY_CAP ? `${FAILURE_DISPLAY_CAP}+` : failures;
    setRenderText(dom.offlineHint, `显示上次有效数据 · 后台重试 ${failureText} 次`);
  }

  const data = payload.data;
  const summary = data.summary || {};
  const gateway = data.gateway || {};
  const online = num(summary.online);
  const total = num(summary.total);

  setStatusChrome(
    payload.status,
    `${online}/${total}`,
    recoveryVisible ? "同步恢复中" : (payload.statusLabel || ""),
  );
  if (recoveryVisible) setRenderText(dom.sbState, "RECOVERING");
  setRenderText(dom.sbSync, `同步 ${clockOf(data.generatedAt || payload.fetchedAt)}`);
  setRenderText(dom.sbProbe, `探测 ${num(data.probeDurationMs)}ms`);
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
  if (!layoutMode) renderSections(data);
  renderChart(data.activity && Array.isArray(data.activity.hourly) ? data.activity.hourly : []);
  renderEvents(data);
  renderExtensionWidgets(Array.isArray(data.widgets) ? data.widgets : []);
  renderCompact(data, payload);
}

function setStatusChrome(status, count, label) {
  dom.tbChipDot.dataset.status = status;
  dom.sbDot.dataset.status = status;
  if (dom.fsDot) dom.fsDot.dataset.status = status;
  setRenderText(dom.tbCount, count);
  setRenderText(dom.tbLabel, label);
  setRenderText(dom.sbState, status === "disconnected" ? "DISCONNECTED" : "CONNECTED");
}

function renderGuard(fleet) {
  const state = fleet && fleet.watchdog ? fleet.watchdog.state : null;
  if (state === "running") {
    setRenderText(dom.sbGuard, "看护在线");
    dom.sbGuard.dataset.state = "ok";
  } else if (state) {
    setRenderText(dom.sbGuard, state === "missing" ? "看护未安装" : "看护离线");
    dom.sbGuard.dataset.state = "bad";
  } else {
    setRenderText(dom.sbGuard, "看护 —");
    dom.sbGuard.dataset.state = "";
  }
}

function renderStrip(payload, online, total, gateway, summary, data) {
  setRenderText(dom.heroValue, `${online}/${total}`);
  const titles = {
    online: "所有系统正常运行",
    degraded: "部分链路已降级",
    offline: "存在离线项目",
  };
  setRenderText(dom.heroTitle, titles[payload.status] || "状态未知");
  setRenderText(dom.tileCalls, compact(summary.calls24h));
  setRenderText(dom.tileRate, `${num(summary.successRate, 100).toFixed(1)}%`);
  setRenderText(dom.tileUptime, duration(gateway.uptimeSeconds));
  setRenderText(dom.tileProbe, `${num(data.probeDurationMs)}ms`);
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

function syncChip(sync) {
  const chip = make("div", "sync-chip");
  const plane = sync && sync.dataPlane ? sync.dataPlane : "unknown";
  const pcOff = sync && sync.pcOff ? sync.pcOff : {};
  const observation = sync && sync.observation ? sync.observation : {};
  const snapshotLabels = {
    fresh: "快照新鲜",
    stale: "快照已过期",
    incomplete: "快照不完整",
    never_synced: "尚未同步",
    unknown: "快照待确认",
  };
  let mode = "同步未声明";
  let ability = "关机状态未知";
  let detail = "未接入统一同步契约";

  if (plane === "cloud_primary") {
    mode = "云端主库";
    ability = pcOff.readAvailable && pcOff.writeAvailable ? "关机可读写" : "关机能力受限";
    detail = pcOff.continuedSync ? "多端持续同步" : "本机副本暂停上行";
  } else if (plane === "snapshot_mirror") {
    mode = "云端快照";
    ability = pcOff.readAvailable ? "关机可读快照" : "关机不可读";
    detail = `${snapshotLabels[sync.snapshotState] || "快照待确认"} · 关机后不再更新`;
  } else if (plane === "local_only") {
    mode = "本机数据";
    ability = "关机后离线";
    detail = "依赖 Windows 运行时";
  }

  chip.dataset.plane = plane;
  chip.dataset.state = sync && sync.compliance ? sync.compliance : "unknown";
  chip.append(make("span", null, mode), make("strong", null, ability), make("small", null, detail));
  chip.title = observation.lastSuccessfulPushAt
    ? `${detail} · 上次成功 ${clockOf(observation.lastSuccessfulPushAt)}`
    : detail;
  return chip;
}

function gatewayProbeState(probe) {
  if (probe && probe.ok) return "online";
  if (probe && probe.service && probe.service.state === "running") return "degraded";
  return "offline";
}

function gatewayRouteNode(code, label, value, detail, status) {
  const node = make("div", "gw-route-node");
  node.dataset.status = status;
  node.append(
    make("span", "gw-node-code", code),
    make("i", "gw-node-port"),
    make("strong", null, label),
    make("b", null, value),
    make("small", null, detail),
  );
  return node;
}

function gatewayRouteLink(status) {
  const link = make("i", "gw-route-link");
  link.dataset.status = status;
  link.setAttribute("aria-hidden", "true");
  return link;
}

function gatewayLatestSignal(data) {
  const signal = make("div", "gw-signal");
  const errors = Array.isArray(data.errors) ? data.errors : [];
  const events = Array.isArray(data.events) ? data.events : [];
  const error = errors[0];
  const event = events[0];
  let status = "online";
  let label = "NO ACTIVE INCIDENTS";
  let title = "当前链路稳定";
  let detail = "看护服务持续巡检，无待处理异常";
  let time = clockOf(data.generatedAt);

  if (error) {
    status = "offline";
    label = "LATEST ERROR";
    title = String(error.code || error.error || "网关异常");
    detail = String(error.message || error.summary || error.module || "等待下一轮诊断");
    time = clockOf(error.createdAt || error.created_at);
  } else if (event) {
    status = event.toState === "online" ? "online" : event.toState === "degraded" ? "degraded" : "offline";
    label = status === "online" ? "LATEST RECOVERY" : "LATEST TRANSITION";
    title = `${event.name || event.target || "链路"} ${status === "online" ? "已恢复" : "状态变化"}`;
    detail = `${event.fromState || "?"} -> ${event.toState || "?"}`;
    time = clockOf(event.occurredAt);
  }

  signal.dataset.status = status;
  const head = make("div", "gw-signal-head");
  const dot = make("i", "dot");
  dot.dataset.status = status;
  head.append(dot, make("span", null, label), make("time", null, time));
  signal.append(head, make("strong", null, title), make("p", null, detail));

  const timeline = make("div", "gw-event-list");
  const entries = [];
  const remainingErrors = errors.slice(error ? 1 : 0);
  const remainingEvents = events.slice(event && !error ? 1 : 0);
  for (const item of remainingErrors) {
    entries.push({
      key: `error:${item.id || item.createdAt || item.created_at || item.code || item.error || entries.length}`,
      status: "offline",
      title: String(item.code || item.error || "网关异常"),
      detail: String(item.module_id || item.module || item.message || "已脱敏错误"),
      time: clockOf(item.createdAt || item.created_at),
    });
  }
  for (const item of remainingEvents) {
    entries.push({
      key: `event:${item.id || item.occurredAt || `${item.target || item.name}:${item.toState || "?"}`}`,
      status: item.toState === "online" ? "online" : item.toState === "degraded" ? "degraded" : "offline",
      title: String(item.name || item.target || "链路状态"),
      detail: `${item.fromState || "?"} -> ${item.toState || "?"}`,
      time: clockOf(item.occurredAt),
    });
  }
  if (!entries.length) {
    const targets = Array.isArray(data.targets) ? data.targets : [];
    for (const target of targets) {
      const style = PROJECT_STYLE[target.id] || {};
      entries.push({
        key: `target:${target.id}`,
        status: target.state || "offline",
        title: String(style.display || target.name || target.id),
        detail: `MCP ${target.mcp && target.mcp.ok ? `${num(target.mcp.latencyMs)}ms` : "--"} / LINK ${target.tunnel && target.tunnel.ok ? `${num(target.tunnel.latencyMs)}ms` : "--"}`,
        time: "LIVE",
      });
    }
  }
  for (const entry of entries.slice(0, 5)) {
    const row = renderKey(make("div", "gw-event-row"), entry.key);
    row.dataset.status = entry.status;
    const rowDot = make("i", "dot mini");
    rowDot.dataset.status = entry.status;
    const copy = make("div");
    copy.append(make("strong", null, entry.title), make("small", null, entry.detail));
    row.append(rowDot, copy, make("time", null, entry.time));
    timeline.append(row);
  }
  signal.append(timeline);
  return signal;
}

function gatewayConsole(target, data) {
  const gateway = data.gateway || {};
  const summary = data.summary || {};
  const fleet = data.fleet || {};
  const targets = Array.isArray(data.targets) ? data.targets : [];
  const probes = targets.flatMap((item) => [item.mcp, item.tunnel].filter(Boolean));
  const runningServices = probes.filter((probe) => probe.service && probe.service.state === "running").length;
  const totalServices = probes.length;
  const onlineRoutes = num(summary.online);
  const totalRoutes = num(summary.total);
  const fleetState = num(summary.offline) > 0 ? "offline" : num(summary.degraded) > 0 ? "degraded" : "online";
  const coreState = gatewayProbeState(target.mcp);
  const relayState = gatewayProbeState(target.tunnel);
  const guardState = fleet.watchdog && fleet.watchdog.state === "running" ? "online" : "offline";

  const console = make("div", "gateway-console");
  const route = make("div", "gw-route");
  route.append(
    gatewayRouteNode("01", "FLEET", `${onlineRoutes}/${totalRoutes}`, "项目路由", fleetState),
    gatewayRouteLink(fleetState),
    gatewayRouteNode("02", "MCP CORE", coreState === "online" ? "READY" : "FAULT", `${num(target.mcp && target.mcp.latencyMs)}ms`, coreState),
    gatewayRouteLink(coreState === "online" && relayState === "online" ? "online" : "offline"),
    gatewayRouteNode("03", "SECURE LINK", relayState === "online" ? "OPEN" : "CLOSED", `${num(target.tunnel && target.tunnel.latencyMs)}ms`, relayState),
    gatewayRouteLink(relayState === "online" && guardState === "online" ? "online" : "offline"),
    gatewayRouteNode("04", "WATCHDOG", guardState === "online" ? "ARMED" : "CHECK", fleet.repairSupported ? "可自愈" : "仅监控", guardState),
  );

  const metrics = make("div", "gw-metrics");
  const metricData = [
    ["24H CALLS", compact(summary.calls24h), `${compact(summary.failures24h)} 失败`, compact(summary.calls24h)],
    ["SUCCESS", `${num(summary.successRate, 100).toFixed(1)}%`, "过去 24 小时", `${Math.round(num(summary.successRate, 100))}%`],
    ["PROBE", `${num(data.probeDurationMs)}ms`, "全链路巡检", `${num(data.probeDurationMs)}ms`],
    ["UPTIME", duration(gateway.uptimeSeconds), `v${gateway.version || "?"}`, briefDuration(gateway.uptimeSeconds)],
  ];
  for (const [label, value, note, compactValue] of metricData) {
    const metric = make("div", "gw-metric");
    const reading = make("strong", null, value);
    reading.dataset.compact = compactValue;
    metric.append(make("span", null, label), reading, make("small", null, note));
    metrics.append(metric);
  }

  const body = make("div", "gw-console-body");
  const serviceMatrix = make("section", "gw-service-matrix");
  const matrixHead = make("header");
  matrixHead.append(
    make("span", null, "SERVICE FABRIC"),
    make("b", null, `${runningServices}/${totalServices} RUNNING`),
  );
  const rows = make("div", "gw-service-rows");
  targets.forEach((item, index) => {
    const style = PROJECT_STYLE[item.id] || {};
    const row = renderKey(make("div", "gw-service-row"), item.id || item.name || index);
    const state = item.state || "offline";
    row.dataset.status = state;
    const dot = make("i", "dot mini");
    dot.dataset.status = state;
    row.append(
      make("span", "gw-service-code", String(index + 1).padStart(2, "0")),
      dot,
      make("strong", null, style.display || item.name || item.id),
      make("span", null, `MCP ${item.mcp && item.mcp.ok ? `${num(item.mcp.latencyMs)}ms` : "--"}`),
      make("span", null, `LINK ${item.tunnel && item.tunnel.ok ? `${num(item.tunnel.latencyMs)}ms` : "--"}`),
    );
    rows.append(row);
  });
  serviceMatrix.append(matrixHead, rows);

  const telemetry = make("section", "gw-telemetry");
  const traffic = make("div", "gw-traffic");
  const trafficHead = make("header");
  trafficHead.append(make("span", null, "24H TRAFFIC"), make("b", null, `${compact(summary.calls24h)} REQUESTS`));
  const bars = make("div", "gw-traffic-bars");
  const hourly = data.activity && Array.isArray(data.activity.hourly) ? data.activity.hourly : [];
  const peak = Math.max(1, ...hourly.map((bucket) => num(bucket.calls)));
  for (const bucket of hourly) {
    const bar = renderKey(make("i", "gw-traffic-bar"), bucket.bucket || bars.childElementCount);
    bar.style.setProperty("--gw-bar-height", `${Math.max(2, num(bucket.calls) / peak * 100)}%`);
    bar.dataset.failed = num(bucket.failures) > 0 ? "true" : "false";
    bar.title = `${hourOf(bucket.bucket)} · ${compact(bucket.calls)} 次 · ${compact(bucket.failures)} 失败`;
    bars.append(bar);
  }
  if (!hourly.length) {
    for (let index = 0; index < 24; index += 1) {
      bars.append(renderKey(make("i", "gw-traffic-bar"), `empty:${index}`));
    }
  }
  traffic.append(trafficHead, bars);
  telemetry.append(traffic, gatewayLatestSignal(data));
  body.append(serviceMatrix, telemetry);
  const continuity = syncChip(target.sync);
  continuity.classList.add("gw-sync-chip");
  console.append(route, metrics, continuity, body);
  return console;
}

function widgetPairs(widget) {
  const pairs = new Map();
  const rows = widget && widget.ok && widget.data && Array.isArray(widget.data.pairs)
    ? widget.data.pairs
    : [];
  for (const pair of rows) {
    if (!pair || pair.label == null || pair.value == null) continue;
    pairs.set(String(pair.label), String(pair.value));
  }
  return pairs;
}

function watchMetric(label, value, className = "") {
  const metric = make("div", `wi-metric${className ? ` ${className}` : ""}`);
  metric.append(make("span", null, label), make("strong", null, value || "—"));
  return metric;
}

function watchScoreDial(rawScore) {
  const parsed = Number.parseFloat(String(rawScore));
  const hasScore = Number.isFinite(parsed);
  const score = hasScore ? clamp(parsed, 0, 100) : 0;
  const dial = make("div", "wi-score");
  dial.dataset.available = String(hasScore);

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 88 88");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", hasScore ? `睡眠评分 ${score}` : "暂无睡眠评分");
  const track = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  track.setAttribute("class", "wi-score-track");
  track.setAttribute("cx", "44");
  track.setAttribute("cy", "44");
  track.setAttribute("r", "35");
  const value = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  value.setAttribute("class", "wi-score-value");
  value.setAttribute("cx", "44");
  value.setAttribute("cy", "44");
  value.setAttribute("r", "35");
  value.setAttribute("pathLength", "100");
  value.style.strokeDasharray = "100";
  value.style.strokeDashoffset = String(100 - score);
  svg.append(track, value);

  const reading = make("div", "wi-score-reading");
  reading.append(make("strong", null, hasScore ? String(Math.round(score)) : "—"), make("span", null, "SLEEP SCORE"));
  dial.append(svg, reading);
  return dial;
}

function watchConsole(target, widgets) {
  const workouts = widgets.find((widget) => widget.id === "watch_workouts");
  const sleep = widgets.find((widget) => widget.id === "watch_sleep");
  const workoutPairs = widgetPairs(workouts);
  const sleepPairs = widgetPairs(sleep);
  const workoutReady = Boolean(workouts && workouts.ok);
  const sleepReady = Boolean(sleep && sleep.ok);
  const readyCount = Number(workoutReady) + Number(sleepReady);
  const dataState = readyCount === 2 ? "online" : readyCount === 1 ? "degraded" : "offline";
  const stateLabel = dataState === "online" ? "DATA LOCKED" : dataState === "degraded" ? "PARTIAL SYNC" : "SYNC WAIT";

  const console = make("div", "watch-console");
  console.dataset.dataState = dataState;
  console.setAttribute("aria-label", "步序训练与恢复数据");

  const head = make("header", "wi-console-head");
  const live = make("div", "wi-live-mark");
  const dot = make("i", "dot mini");
  dot.dataset.status = target.state || "offline";
  live.append(dot, make("span", null, "RUN / RECOVER"));
  head.append(live, make("b", null, stateLabel));

  const run = make("section", "wi-run");
  const runPrimary = make("div", "wi-run-primary");
  runPrimary.append(
    make("span", null, "TOTAL DISTANCE"),
    make("strong", null, workoutPairs.get("总距离") || "—"),
    make("small", null, `${workoutPairs.get("训练次数") || "—"} SESSIONS`),
  );
  const runMetrics = make("div", "wi-run-metrics");
  runMetrics.append(
    watchMetric("ACTIVE", workoutPairs.get("累计活动")),
    watchMetric("AVG HEART", workoutPairs.get("平均心率")),
  );
  const plan = make("div", "wi-plan");
  plan.append(
    make("span", null, "LATEST PLAN"),
    make("strong", null, workoutPairs.get("最近计划") || (workoutReady ? "暂无最近计划" : "等待训练汇总")),
  );
  run.append(runPrimary, runMetrics, plan);

  const recovery = make("section", "wi-recovery");
  const recoveryHead = make("div", "wi-recovery-head");
  recoveryHead.append(make("span", null, "RECOVERY"), make("b", null, sleepReady ? "SLEEP READY" : "DEVICE WAIT"));
  recovery.append(
    recoveryHead,
    watchScoreDial(sleepPairs.get("睡眠评分") || "—"),
    watchMetric("DURATION", sleepPairs.get("睡眠时长"), "wi-sleep-duration"),
    watchMetric("HEART RANGE", sleepPairs.get("心率区间"), "wi-heart-range"),
  );

  if (dataState !== "online") {
    const pending = [];
    if (!workoutReady) pending.push("训练");
    if (!sleepReady) pending.push("睡眠");
    const note = make("p", "wi-sync-note", `${pending.join(" / ")}数据等待手机或手表恢复`);
    recovery.append(note);
  }

  console.append(head, run, recovery);
  return console;
}

function focusConsole(widgets) {
  const widget = widgets.find((item) => item.id === "focus_today");
  const ready = Boolean(widget && widget.ok && widget.data);
  const value = ready && widget.data.value ? String(widget.data.value) : "—";
  const label = ready && widget.data.label ? String(widget.data.label) : "等待今日专注数据";
  const note = ready && widget.data.note ? String(widget.data.note) : "本机恢复后自动刷新";
  const console = make("div", "focus-console");
  console.dataset.dataState = ready ? "online" : "offline";

  const head = make("header", "fl-head");
  head.append(make("span", null, "TEMPORAL FIELD / TODAY"), make("b", null, ready ? "TODAY VERIFIED" : "DATA WAIT"));
  const reading = make("div", "fl-reading");
  reading.append(make("strong", null, value), make("span", null, label), make("small", null, note));
  const ribbon = make("div", "fl-ribbon");
  ribbon.setAttribute("aria-hidden", "true");
  for (let index = 0; index < 18; index += 1) {
    const segment = make("i");
    segment.style.setProperty("--segment", String(index));
    ribbon.append(segment);
  }
  const foot = make("footer", "fl-foot");
  foot.append(make("span", null, "TODAY WINDOW"), make("b", null, ready ? "MCP READ" : "NO SAMPLE"));
  console.append(head, reading, ribbon, foot);
  return console;
}

function journalConsole(widgets) {
  const recent = widgets.find((item) => item.id === "journal_recent");
  const count = widgets.find((item) => item.id === "journal_count");
  const recentReady = Boolean(recent && recent.ok && recent.data);
  const items = recent && recent.ok && recent.data && Array.isArray(recent.data.items)
    ? recent.data.items.slice(0, 4)
    : [];
  const total = count && count.ok && count.data && count.data.value != null
    ? String(count.data.value)
    : "—";
  const console = make("div", "journal-console");
  console.dataset.dataState = recentReady ? "online" : "offline";

  const head = make("header", "jr-head");
  const headCopy = make("div");
  headCopy.append(make("span", null, "REVIEW LEDGER"), make("strong", null, "最近记录"));
  const countBlock = make("div", "jr-count");
  countBlock.append(make("b", null, total), make("small", null, "TOTAL ENTRIES"));
  head.append(headCopy, countBlock);

  const ledger = make("div", "jr-ledger");
  if (!items.length) {
    ledger.append(make("p", "jr-empty", recentReady ? "还没有日记，留白也是今天的一部分" : "日记数据源暂未返回"));
  }
  items.forEach((item, index) => {
    const identity = item.id || `${item.value || ""}:${item.title || ""}:${item.subtitle || ""}` || index;
    const row = renderKey(make("article", "jr-entry"), identity);
    row.dataset.entryIndex = String(index);
    const date = make("time", null, item.value || "—");
    const copy = make("div");
    copy.append(make("strong", null, item.title || "未命名记录"));
    if (item.subtitle) copy.append(make("small", null, item.subtitle));
    row.append(date, copy);
    ledger.append(row);
  });
  const foot = make("footer", "jr-foot");
  const note = count && count.ok && count.data && count.data.note ? String(count.data.note) : "等待同步状态";
  foot.append(make("span", null, "LAST REVISION"), make("b", null, note));
  console.append(head, ledger, foot);
  return console;
}

function bzsjkTargetFromWidgets(widgets) {
  const projects = widgets.find((item) => item.id === "projects");
  const items = projects && projects.ok && projects.data && Array.isArray(projects.data.items)
    ? projects.data.items
    : [];
  const projectItem = items.find((item) => String(item.title || "").trim() === "不做手机控");
  if (!projectItem) return null;
  return {
    id: "bzsjk",
    name: "不做手机控",
    description: "本地监督与锁机维护项目",
    state: "local",
    version: null,
    mcp: null,
    tunnel: null,
    projectItem,
    sync: {
      compliance: "exempt",
      dataPlane: "local_only",
      pcOff: { readAvailable: false, writeAvailable: false, continuedSync: false },
      snapshotState: "not_applicable",
      observation: { result: "unknown" },
    },
  };
}

function bzsjkConsole(target, widgets) {
  const item = target.projectItem || {};
  const focus = widgets.find((widget) => widget.id === "focus_today");
  const focusReady = Boolean(focus && focus.ok);
  const status = String(item.value || "状态待读取");
  const subtitle = String(item.subtitle || "本地源码 · 未声明云端运行时");
  const branch = subtitle.split("·")[0].trim() || "LOCAL";
  const console = make("div", "bz-console");
  const head = make("header", "bz-head");
  head.append(make("span", null, "DISCIPLINE CORE / LOCAL"), make("b", null, "NO CLOUD CLAIM"));
  const main = make("section", "bz-main");
  main.append(make("span", null, "RUNTIME POLICY"), make("strong", null, "LOCAL"), make("small", null, "关机后无云同步"));
  const metrics = make("div", "bz-metrics");
  const rows = [
    ["REPOSITORY", status],
    ["BRANCH", branch],
    ["FOCUSLINK", focusReady ? "数据可读" : "等待恢复"],
  ];
  for (const [label, value] of rows) {
    const metric = make("div", "bz-metric");
    metric.append(make("span", null, label), make("strong", null, value));
    metrics.append(metric);
  }
  const foot = make("footer", "bz-foot");
  foot.append(make("span", null, "NETWORK BOUNDARY"), make("b", null, "仅本机 / 用户指定链路"));
  console.append(head, main, metrics, foot);
  return console;
}

function projectCore(target, widgets, data) {
  const core = make("div", "proj-core");
  let label = "LIVE STATE";
  let value = target.state === "online" ? "READY" : target.state === "degraded" ? "CHECK" : "OFFLINE";

  if (target.id === "personal") {
    const summary = data.summary || {};
    label = "ROUTES ONLINE";
    value = `${num(summary.online)}/${num(summary.total)}`;
  } else if (target.id === "foxlink") {
    const focus = widgets.find((widget) => widget.id === "focus_today");
    label = "TODAY FOCUS";
    if (focus && focus.ok && focus.data && focus.data.value) value = String(focus.data.value);
  } else if (target.id === "watch") {
    const workouts = widgets.find((widget) => widget.id === "watch_workouts");
    const pairs = widgetPairs(workouts);
    label = "TOTAL DISTANCE";
    value = pairs.get("总距离") || value;
  } else if (target.id === "journal") {
    const count = widgets.find((widget) => widget.id === "journal_count");
    label = "TOTAL ENTRIES";
    if (count && count.ok && count.data && count.data.value != null) value = String(count.data.value);
  } else if (target.id === "bzsjk") {
    label = "LOCAL PROJECT";
    value = target.projectItem && target.projectItem.value ? String(target.projectItem.value) : "LOCAL";
  }

  core.append(make("span", null, label), make("strong", null, value));
  return core;
}

function sectionDataCards(target, widgets, data) {
  const style = PROJECT_STYLE[target.id];
  const cards = [];
  if (target.id === "personal") {
    return [gatewayConsole(target, data)];
  }
  if (target.id === "watch") {
    return [watchConsole(target, widgets)];
  }
  if (target.id === "foxlink") {
    return [focusConsole(widgets)];
  }
  if (target.id === "journal") {
    return [journalConsole(widgets)];
  }
  if (target.id === "bzsjk") {
    return [bzsjkConsole(target, widgets)];
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
    const identity = event.id || event.occurredAt || `${event.target}:${event.fromState}:${event.toState}`;
    const row = renderKey(make("div", "pt-row"), `event:${identity}`);
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
    const identity = item.id || item.occurredAt || item.createdAt || `${item.tool}:${item.durationMs}:${item.result}`;
    const row = renderKey(make("div", "pt-row"), `activity:${identity}`);
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

function projectSectionSignature(target, index, widgets, data) {
  const style = PROJECT_STYLE[target.id] || { groups: [] };
  const widgetIds = {
    foxlink: new Set(["focus_today"]),
    watch: new Set(["watch_workouts", "watch_sleep"]),
    journal: new Set(["journal_recent", "journal_count"]),
    bzsjk: new Set(["projects", "focus_today"]),
  };
  const ids = widgetIds[target.id] || new Set();
  const relevantWidgets = target.id === "personal"
    ? []
    : widgets.filter((widget) => ids.has(widget.id) || style.groups.includes(widget.group || ""));
  const targetEvents = Array.isArray(data.events)
    ? data.events.filter((event) => event.target === target.id)
    : [];
  const targetActivity = data.activity && Array.isArray(data.activity.recent)
    ? data.activity.recent.filter((item) => (item.module || "") === target.id)
    : [];
  const gatewayData = target.id === "personal" ? {
    summary: data.summary,
    gateway: data.gateway,
    fleet: data.fleet,
    targets: data.targets,
    hourly: data.activity && data.activity.hourly,
    events: data.events,
    errors: data.errors,
    refreshIntervalSeconds: data.refreshIntervalSeconds,
  } : null;
  return JSON.stringify([index, target, relevantWidgets, targetEvents, targetActivity, gatewayData]);
}

function buildProjectSection(target, index, widgets, data) {
  const style = PROJECT_STYLE[target.id] || {
    flavor: "neutral",
    accent: target.accent || "#8878ff",
    display: target.name,
    tagline: target.description || "",
    groups: [],
  };
  const section = renderKey(
    make("section", `proj proj-${style.flavor} project-${target.id}`),
    target.id,
  );
  section.style.setProperty("--p-accent", style.accent);
  section.style.setProperty("--tile-index", String(index));
  section.dataset.state = target.state || "offline";
  section.dataset.projectId = target.id;

  const head = make("header", "proj-head");
  const naming = make("div", "proj-naming");
  if (style.eyebrow) naming.append(make("span", "proj-eyebrow", style.eyebrow));
  naming.append(make("h2", null, style.display));
  const sub = make("p", "proj-tagline");
  sub.textContent = `${style.tagline}${target.version ? ` · v${String(target.version).replace(/^v/, "")}` : ""}`;
  naming.append(sub);
  const state = make("div", "proj-state");
  const dot = make("i", "dot");
  dot.dataset.status = target.state || "offline";
  const stateText = { online: "正常", degraded: "降级", offline: "离线", local: "本机" }[target.state] || "未知";
  state.append(dot, make("b", null, stateText));
  const grip = make("span", "tile-grip");
  grip.setAttribute("aria-hidden", "true");
  head.append(naming, state, projectCore(target, widgets, data));
  if (target.id === "personal") {
    const identity = make("div", "gw-identity");
    identity.append(
      make("span", null, "CORE  /  LOCAL :8761"),
      make("span", null, `POLL  /  ${num(data.refreshIntervalSeconds, 4)} SEC`),
      make("span", null, "MODE  /  AUTO RECOVERY"),
    );
    head.append(identity);
  }
  head.append(make("span", "proj-index", String(index + 1).padStart(2, "0")), grip);

  const vitals = make("div", "proj-vitals");
  if (target.mcp) vitals.append(probeChip("MCP", target.mcp));
  if (target.tunnel) vitals.append(probeChip("隧道", target.tunnel));
  if (target.id !== "personal") vitals.append(syncChip(target.sync));

  const dataZone = make("div", "proj-data");
  if (target.id === "personal") dataZone.classList.add("gateway-data");
  if (target.id === "watch") dataZone.classList.add("watch-data");
  for (const card of sectionDataCards(target, widgets, data)) dataZone.append(card);

  const tail = make("div", "proj-tail");
  const tailRows = sectionTail(target, data);
  if (tailRows.length) {
    for (const row of tailRows) tail.append(row);
  } else {
    tail.append(renderKey(make("p", "pt-quiet", "近期安静，无状态变化"), "quiet"));
  }

  const sizeBadge = renderKey(make("span", "tile-size-badge"), "size");
  sizeBadge.setAttribute("aria-hidden", "true");
  const editToolbar = renderKey(make("div", "tile-edit-toolbar"), "edit-toolbar");
  editToolbar.setAttribute("aria-label", "磁贴快捷尺寸");
  for (const [preset, label] of [["small", "小"], ["medium", "中"], ["large", "大"]]) {
    const button = renderKey(make("button", null, label), `preset:${preset}`);
    button.type = "button";
    button.dataset.tilePreset = preset;
    button.title = `${label}尺寸`;
    button.setAttribute("aria-label", `设为${label}尺寸`);
    editToolbar.append(button);
  }
  section.append(head, vitals, dataZone, tail, sizeBadge, editToolbar);
  const handleLabels = {
    n: "调整磁贴上边缘",
    ne: "调整磁贴右上角",
    e: "调整磁贴右边缘",
    se: "调整磁贴右下角",
    s: "调整磁贴下边缘",
    sw: "调整磁贴左下角",
    w: "调整磁贴左边缘",
    nw: "调整磁贴左上角",
  };
  for (const [edge, label] of Object.entries(handleLabels)) {
    const handle = renderKey(make("button", `tile-handle tile-handle-${edge}`), `handle:${edge}`);
    handle.type = "button";
    handle.dataset.edge = edge;
    handle.title = label;
    handle.setAttribute("aria-label", label);
    section.append(handle);
  }
  return section;
}

function projectTiles() {
  return [...dom.projectSections.querySelectorAll('.proj:not([data-render-exiting="true"])')];
}

function patchProjectSection(current, desired) {
  const dynamicClasses = [...current.classList].filter((name) => (
    name.startsWith("tile-") || name === "interacting" || name === "settling"
  ));
  current.className = desired.className;
  current.classList.add(...dynamicClasses);
  current.dataset.state = desired.dataset.state;
  current.dataset.projectId = desired.dataset.projectId;
  current.dataset.renderKey = desired.dataset.renderKey;
  current.style.setProperty("--p-accent", desired.style.getPropertyValue("--p-accent"));
  current.style.setProperty("--tile-index", desired.style.getPropertyValue("--tile-index"));
  reconcileRenderChildren(current, [...desired.childNodes]);
}

function renderSections(data) {
  const targets = Array.isArray(data.targets) ? [...data.targets] : [];
  const widgets = Array.isArray(data.widgets) ? data.widgets : [];
  const bzsjk = bzsjkTargetFromWidgets(widgets);
  if (bzsjk && !targets.some((target) => target.id === "bzsjk")) targets.push(bzsjk);
  const byId = new Map(targets.map((t) => [t.id, t]));
  const ordered = [];
  for (const id of SECTION_ORDER) if (byId.has(id)) ordered.push(byId.get(id));
  for (const t of targets) if (!SECTION_ORDER.includes(t.id)) ordered.push(t);
  const sourceOrder = new Map(ordered.map((target, index) => [target.id, index]));
  ordered.sort((first, second) => {
    const a = projectLayout[first.id] && Number.isFinite(Number(projectLayout[first.id].order))
      ? Number(projectLayout[first.id].order) : sourceOrder.get(first.id);
    const b = projectLayout[second.id] && Number.isFinite(Number(projectLayout[second.id].order))
      ? Number(projectLayout[second.id].order) : sourceOrder.get(second.id);
    return a - b;
  });

  installLayoutChrome();
  const existing = new Map(projectTiles().map((section) => [section.dataset.projectId, section]));
  const nextSections = [];
  const added = [];
  let structureChanged = existing.size !== ordered.length;
  for (let index = 0; index < ordered.length; index += 1) {
    const target = ordered[index];
    const signature = projectSectionSignature(target, index, widgets, data);
    let section = existing.get(target.id);
    if (!section) {
      section = buildProjectSection(target, index, widgets, data);
      section._renderSignature = signature;
      added.push(section);
      structureChanged = true;
    } else if (section._renderSignature !== signature) {
      patchProjectSection(section, buildProjectSection(target, index, widgets, data));
      section._renderSignature = signature;
    }
    existing.delete(target.id);
    nextSections.push(section);
  }

  let sectionCursor = dom.projectSections.firstChild;
  for (const section of nextSections) {
    if (section !== sectionCursor) dom.projectSections.insertBefore(section, sectionCursor);
    sectionCursor = section.nextSibling;
  }
  for (const section of existing.values()) {
    structureChanged = true;
    removeWithMotion(section, () => {
      if (!activeTileInteraction) updateProjectCanvasSize(null, true);
    });
  }

  if (structureChanged || !sectionsRendered) applyProjectLayout(!sectionsRendered);
  if (sectionsRendered) for (const section of added) animateRenderEnter(section);
  configureTileEditing();
  sectionsRendered = true;
}

function renderCompact(data, payload) {
  const summary = data.summary || {};
  const online = num(summary.online);
  const total = num(summary.total);
  setRenderText(dom.heroValueCompact, `${online}/${total}`);
  setRenderText(dom.heroLabelCompact, payload.statusLabel || "在线");
  setMeter(dom.meterCompact, dom.meterFillCompact, online, total, payload.status);
  setRenderText(dom.compactCalls, compact(summary.calls24h));
  setRenderText(dom.compactRate, `${num(summary.successRate, 100).toFixed(1)}%`);

  const targets = Array.isArray(data.targets) ? data.targets : [];
  const rows = targets.map((target) => {
    const row = renderKey(make("div", "compact-row"), target.id);
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
  const previousOffset = fill.style.strokeDashoffset || String(CIRCUMFERENCE);
  const nextOffset = String(CIRCUMFERENCE * (1 - ratio));
  fill.style.strokeDasharray = String(CIRCUMFERENCE);
  fill.style.strokeDashoffset = nextOffset;
  if (previousOffset !== nextOffset) {
    playRenderAnimation(
      fill,
      [{ strokeDashoffset: previousOffset }, { strokeDashoffset: nextOffset }],
      { duration: 420, easing: "cubic-bezier(.2,.8,.2,1)" },
    );
  }
}

/* ---------- chart / events / extension widgets ---------- */

function renderChart(hourly) {
  const totals = hourly.map((bucket) => num(bucket.calls));
  const peak = Math.max(0, ...totals);
  const scale = niceMax(peak);
  const empty = peak === 0;

  dom.plotEmpty.hidden = !empty;
  replace(dom.yAxis, [
    renderKey(make("span", null, empty ? "" : compact(scale)), "max"),
    renderKey(make("span", null, empty ? "" : compact(scale / 2)), "mid"),
    renderKey(make("span", null, empty ? "" : "0"), "min"),
  ]);

  const bars = hourly.map((bucket) => {
    const calls = num(bucket.calls);
    const failures = Math.min(calls, num(bucket.failures));
    const ok = calls - failures;
    const column = renderKey(make("div", "hour"), bucket.bucket);
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
    const identity = event.id || event.occurredAt || `${event.target}:${event.fromState}:${event.toState}`;
    const row = renderKey(make("div", "event-row"), `event:${identity}`);
    const dot = make("i", "dot");
    dot.dataset.status = recovered ? "online" : event.toState === "degraded" ? "degraded" : "offline";
    const copy = make("div", "event-copy");
    copy.append(make("strong", null, `${event.name || event.target} ${recovered ? "已恢复" : "状态变化"}`));
    copy.append(make("p", null, `${event.fromState || "?"} → ${event.toState || "?"}`));
    row.append(dot, copy, make("time", null, clockOf(event.occurredAt)));
    rows.push(row);
  }

  for (const error of errors.slice(0, 8)) {
    const identity = error.id || error.createdAt || error.created_at || error.code || error.error;
    const row = renderKey(make("div", "event-row"), `error:${identity}`);
    const dot = make("i", "dot");
    dot.dataset.status = "offline";
    const copy = make("div", "event-copy");
    copy.append(make("strong", null, String(error.code || error.error || "异常")));
    copy.append(make("p", null, String(error.message || error.summary || error.module || "")));
    row.append(dot, copy, make("time", null, clockOf(error.createdAt || error.created_at)));
    rows.push(row);
  }

  setRenderText(dom.eventCount, String(rows.length));
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
      const row = renderKey(make("div"), pair.label);
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
    for (let index = 0; index < items.length; index += 1) {
      const item = items[index];
      const identity = item.id || `${item.title || ""}:${item.subtitle || ""}:${item.value || ""}` || index;
      const row = renderKey(make("div", "widget-row"), identity);
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
  setRenderText(dom.widgetCount, String(rest.length));
  const nodes = [];
  let currentGroup = null;
  for (const widget of rest) {
    const group = widget.group || null;
    if (group !== currentGroup) {
      currentGroup = group;
      if (group) {
        const head = renderKey(make("div", "widget-group-head"), `group:${group}`);
        if (widget.accent) head.style.setProperty("--w-accent", widget.accent);
        head.append(make("i"), make("span", null, group));
        nodes.push(head);
      }
    }
    const flavor = `flavor-${widget.flavor || "neutral"}`;
    const card = renderKey(
      make("article", `widget-card ${flavor}${widget.ok ? "" : " error"}`),
      `widget:${widget.id}`,
    );
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
  if (!layoutMode && view.projectLayout && typeof view.projectLayout === "object") {
    projectLayout = view.projectLayout;
    const incomingVersion = Number(view.projectLayoutVersion);
    projectLayoutVersion = Number.isFinite(incomingVersion)
      ? Math.max(1, incomingVersion)
      : Object.keys(projectLayout).length ? 1 : PROJECT_LAYOUT_VERSION;
  }
  const desktopMode = Boolean(view.desktopMode);
  if (desktopMode && dom.body.dataset.view !== "overview") selectView("overview");
  dom.body.classList.toggle("compact", Boolean(view.compact));
  dom.body.classList.toggle("desktop-mode", desktopMode);
  dom.btnCompact.setAttribute("aria-pressed", String(Boolean(view.compact)));
  dom.btnTop.setAttribute("aria-pressed", String(Boolean(view.onTop)));
  dom.btnDesktop.setAttribute("aria-pressed", String(desktopMode));
  dom.btnDesktop.title = desktopMode ? "退出桌面模式" : "固定到桌面";
  dom.btnDesktop.setAttribute("aria-label", dom.btnDesktop.title);
  dom.btnCompact.disabled = desktopMode;
  dom.btnTop.disabled = desktopMode;
  if (dom.titlebarDrag) {
    dom.titlebarDrag.classList.toggle("pywebview-drag-region", !desktopMode);
  }
  dom.body.classList.remove("booting");
}

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function defaultGeometryForTile(projectId, index) {
  return { ...(DEFAULT_TILE_LAYOUT[projectId] || {
    x: index % 2 ? 510 : 0,
    y: Math.floor(index / 2) * 510,
    w: 490,
    h: 500,
    order: index,
  }) };
}

function layoutBounds(layout) {
  let right = 0;
  let bottom = 0;
  for (const saved of Object.values(layout || {})) {
    if (!saved || typeof saved !== "object") continue;
    if (!["x", "y", "w", "h"].every((key) => Number.isFinite(Number(saved[key])))) continue;
    right = Math.max(right, num(saved.x) + num(saved.w));
    bottom = Math.max(bottom, num(saved.y) + num(saved.h));
  }
  return { right: Math.max(1, right), bottom: Math.max(1, bottom) };
}

function legacyTileIsAloneInRow(projectId, saved) {
  const top = num(saved.y);
  const bottom = top + num(saved.h);
  return Object.entries(projectLayout).every(([otherId, other]) => {
    if (otherId === projectId || !other || typeof other !== "object") return true;
    const otherTop = num(other.y);
    const otherBottom = otherTop + num(other.h);
    return otherBottom <= top + 1 || otherTop >= bottom - 1;
  });
}

function geometryFromUnits(units, viewportWidth, viewportHeight) {
  const horizontalScale = viewportWidth / LAYOUT_SCALE;
  const verticalScale = viewportHeight / LAYOUT_SCALE;
  return {
    x: clamp(num(units.x) * horizontalScale, 0, MAX_WORKSPACE_POSITION),
    y: clamp(num(units.y) * verticalScale, 0, MAX_WORKSPACE_POSITION),
    w: clamp(num(units.w) * horizontalScale, RENDER_MIN_TILE_WIDTH, MAX_TILE_WIDTH),
    h: clamp(num(units.h) * verticalScale, RENDER_MIN_TILE_HEIGHT, MAX_TILE_HEIGHT),
    order: clamp(num(units.order), 0, 999),
  };
}

function geometryForTile(projectId, index, viewportWidth, viewportHeight, sourceVersion) {
  const saved = projectLayout[projectId] || {};
  const fallback = defaultGeometryForTile(projectId, index);
  const hasFreeform = ["x", "y", "w", "h"].every((key) => Number.isFinite(Number(saved[key])));
  if (hasFreeform) {
    if (sourceVersion >= PROJECT_LAYOUT_VERSION) {
      return geometryFromUnits({ ...saved, order: num(saved.order, fallback.order) }, viewportWidth, viewportHeight);
    }

    // v1/v2 stored a mixture of 1000-wide and absolute-pixel geometry. Normalize
    // the whole saved canvas once so every tile, including off-screen tiles, is
    // brought into the current window without losing its relative arrangement.
    const bounds = layoutBounds(projectLayout);
    const units = {
      x: num(saved.x) / bounds.right * LAYOUT_SCALE,
      y: num(saved.y) / bounds.bottom * LAYOUT_SCALE,
      w: num(saved.w) / bounds.right * LAYOUT_SCALE,
      h: num(saved.h) / bounds.bottom * LAYOUT_SCALE,
      order: num(saved.order, fallback.order),
    };
    // The old free canvas often left a single second-row tile stranded at its
    // absolute pixel width. It is safe to fill that otherwise empty row while
    // keeping multi-tile rows and all ordering intact.
    const hasBzsjkTile = projectTiles().some((tile) => tile.dataset.projectId === "bzsjk");
    if (projectId === "personal" && hasBzsjkTile) {
      Object.assign(units, DEFAULT_TILE_LAYOUT.personal);
    } else if (units.x < 12 && units.w < 900 && legacyTileIsAloneInRow(projectId, saved)) {
      units.x = 0;
      units.w = LAYOUT_SCALE;
    }
    return geometryFromUnits(units, viewportWidth, viewportHeight);
  }
  return geometryFromUnits({
    ...fallback,
    order: clamp(num(saved.order, fallback.order), 0, 999),
  }, viewportWidth, viewportHeight);
}

function rectOfTile(tile) {
  return {
    left: num(tile.dataset.left),
    top: num(tile.dataset.top),
    width: num(tile.dataset.width, MIN_TILE_WIDTH),
    height: num(tile.dataset.height, MIN_TILE_HEIGHT),
  };
}

function setTileDensity(tile, width, height) {
  const area = width * height;
  const summaryOnly = (width < 185 && height < 165)
    || (projectViewportWidth < 500 && height < 165 && width < 450)
    || area < 24000;
  const micro = (width < 145 && height < 125) || area < 15500;
  const widthClass = width < 280 ? "small" : width < 480 ? "medium" : "large";
  const heightClass = height < 150 ? "short" : height < 225 ? "medium" : "tall";
  const narrow = width < 390;
  const densityKey = `${widthClass}:${heightClass}:${narrow ? 1 : 0}:${summaryOnly ? 1 : 0}:${micro ? 1 : 0}`;
  const densityChanged = Boolean(tile.dataset.densityKey && tile.dataset.densityKey !== densityKey);
  tile.dataset.densityKey = densityKey;
  tile.dataset.widthClass = widthClass;
  tile.dataset.heightClass = heightClass;
  tile.classList.toggle("tile-narrow", narrow);
  tile.classList.toggle("tile-summary", summaryOnly);
  tile.classList.toggle("tile-tiny", summaryOnly);
  tile.classList.toggle("tile-micro", micro);
  if (densityChanged && tile.isConnected) {
    tile.classList.remove("density-changing");
    // Restart the compositor-only settle animation without a synchronous
    // layout read. A forced offsetWidth here used to hitch the exact resize
    // frame on which a tile crossed a density boundary.
    const previousFrame = densityFrames.get(tile);
    if (previousFrame) window.cancelAnimationFrame(previousFrame);
    const previous = densityTimers.get(tile);
    if (previous) window.clearTimeout(previous);
    const frame = window.requestAnimationFrame(() => {
      densityFrames.delete(tile);
      tile.classList.add("density-changing");
      densityTimers.set(tile, window.setTimeout(() => {
        tile.classList.remove("density-changing");
        densityTimers.delete(tile);
      }, 240));
    });
    densityFrames.set(tile, frame);
  }
}

function setTileRect(tile, rect) {
  const clean = (value) => Math.round(value * 10) / 10;
  const left = clean(rect.left);
  const top = clean(rect.top);
  const width = clean(rect.width);
  const height = clean(rect.height);
  tile.dataset.left = String(left);
  tile.dataset.top = String(top);
  tile.dataset.width = String(width);
  tile.dataset.height = String(height);
  tile.style.left = `${left}px`;
  tile.style.top = `${top}px`;
  tile.style.width = `${width}px`;
  tile.style.height = `${height}px`;
  const badge = tile.querySelector(".tile-size-badge");
  if (badge) badge.textContent = `${Math.round(width)} × ${Math.round(height)}`;
  setTileDensity(tile, width, height);
}

function applyTileSizePreset(tile, presetName) {
  const preset = TILE_SIZE_PRESETS[presetName];
  if (!preset || !layoutMode) return;
  const viewportWidth = Math.max(MIN_TILE_WIDTH, projectViewportWidth || workspaceViewportWidth());
  const viewportHeight = Math.max(MIN_TILE_HEIGHT, projectViewportHeight || workspaceViewportHeight());
  const current = rectOfTile(tile);
  const width = clamp(viewportWidth * preset.width, MIN_TILE_WIDTH, MAX_TILE_WIDTH);
  const height = clamp(viewportHeight * preset.height, MIN_TILE_HEIGHT, MAX_TILE_HEIGHT);
  const rect = constrainTileRect({
    left: clamp(current.left, 0, Math.max(0, viewportWidth - width)),
    top: clamp(current.top, 0, Math.max(0, viewportHeight - height)),
    width,
    height,
  }, "se");
  setTileRect(tile, rect);
  tile.classList.add("settling");
  window.setTimeout(() => tile.classList.remove("settling"), 300);
  updateProjectCanvasSize(rect, true);
  persistTileLayout().catch(() => {});
}

function installLayoutChrome() {
  if (dom.projectSections.querySelector(".layout-guide-v")) return;
  const vertical = renderKey(make("i", "layout-guide layout-guide-v"), "chrome:vertical");
  vertical.setAttribute("aria-hidden", "true");
  const horizontal = renderKey(make("i", "layout-guide layout-guide-h"), "chrome:horizontal");
  horizontal.setAttribute("aria-hidden", "true");
  const badge = renderKey(make("span", "layout-snap-badge", "对齐 OK"), "chrome:badge");
  badge.setAttribute("role", "status");
  badge.setAttribute("aria-live", "polite");
  dom.projectSections.append(vertical, horizontal, badge);
}

function workspaceViewportWidth() {
  if (!dom.board) return 0;
  const style = window.getComputedStyle(dom.board);
  const horizontalPadding = num(Number.parseFloat(style.paddingLeft)) + num(Number.parseFloat(style.paddingRight));
  return Math.max(MIN_TILE_WIDTH, Math.floor(dom.board.clientWidth - horizontalPadding));
}

function workspaceViewportHeight() {
  if (!dom.stage || !dom.board) return 0;
  const boardStyle = window.getComputedStyle(dom.board);
  const verticalPadding = num(Number.parseFloat(boardStyle.paddingTop)) + num(Number.parseFloat(boardStyle.paddingBottom));
  const railHeight = dom.overviewRail ? dom.overviewRail.getBoundingClientRect().height : 92;
  const headingHeight = dom.matrixHeading ? dom.matrixHeading.getBoundingClientRect().height : 38;
  const deck = dom.overviewRail ? dom.overviewRail.parentElement : null;
  const deckStyle = deck ? window.getComputedStyle(deck) : null;
  const deckBorders = deckStyle
    ? num(Number.parseFloat(deckStyle.borderTopWidth)) + num(Number.parseFloat(deckStyle.borderBottomWidth))
    : 0;
  // Keep the last row inside the locked overview after borders and subpixel
  // layout values are rounded by the browser.
  const available = Math.floor(
    dom.stage.clientHeight - verticalPadding - railHeight - headingHeight - deckBorders,
  ) - 6;
  dom.body.classList.toggle("overview-fits", available >= 260);
  return Math.max(260, available);
}

function updateProjectCanvasSize(extraRect = null, allowShrink = true) {
  const viewportWidth = workspaceViewportWidth();
  const viewportHeight = workspaceViewportHeight();
  projectViewportWidth = viewportWidth;
  projectViewportHeight = viewportHeight;
  let right = extraRect ? extraRect.left + extraRect.width : 0;
  let bottom = extraRect ? extraRect.top + extraRect.height : 0;
  for (const tile of projectTiles()) {
    const rect = rectOfTile(tile);
    right = Math.max(right, rect.left + rect.width);
    bottom = Math.max(bottom, rect.top + rect.height);
  }
  const widthOverflow = right > viewportWidth + 2;
  const heightOverflow = bottom > viewportHeight + 2;
  const wantedWidth = layoutMode && widthOverflow
    ? Math.ceil(right + CANVAS_PADDING)
    : Math.max(viewportWidth, widthOverflow ? Math.ceil(right) : viewportWidth);
  const wantedHeight = Math.max(
    viewportHeight,
    heightOverflow ? Math.ceil(bottom + (layoutMode ? CANVAS_PADDING : 0)) : viewportHeight,
  );
  const currentWidth = Number.parseFloat(dom.projectSections.style.width) || wantedWidth;
  const currentHeight = Number.parseFloat(dom.projectSections.style.height) || wantedHeight;
  projectCanvasWidth = allowShrink ? wantedWidth : Math.max(currentWidth, wantedWidth);
  dom.projectSections.style.width = `${projectCanvasWidth}px`;
  dom.projectSections.style.height = `${allowShrink ? wantedHeight : Math.max(currentHeight, wantedHeight)}px`;
}

function applyProjectLayout(animate = false) {
  const viewportWidth = workspaceViewportWidth();
  const viewportHeight = workspaceViewportHeight();
  if (viewportWidth <= 0 || viewportHeight <= 0) return;
  projectViewportWidth = viewportWidth;
  projectViewportHeight = viewportHeight;
  projectCanvasWidth = viewportWidth;
  dom.projectSections.style.width = `${viewportWidth}px`;
  dom.projectSections.style.height = `${viewportHeight}px`;
  dom.projectSections.classList.add("free-layout");
  const tiles = projectTiles();
  const sourceVersion = projectLayoutVersion;
  tiles.forEach((tile, index) => {
    const geometry = geometryForTile(tile.dataset.projectId, index, viewportWidth, viewportHeight, sourceVersion);
    const rect = {
      left: geometry.x,
      top: geometry.y,
      width: geometry.w,
      height: geometry.h,
    };
    tile.style.zIndex = String(geometry.order + 1);
    setTileRect(tile, rect);
    if (animate) {
      playRenderAnimation(
        tile,
        [
          { opacity: 0, transform: "translateY(14px) scale(.985)" },
          { opacity: 1, transform: "translateY(0) scale(1)" },
        ],
        {
          duration: 360,
          delay: index * 45,
          easing: "cubic-bezier(.2,.8,.2,1)",
          fill: "both",
        },
      );
    }
  });
  updateProjectCanvasSize();
  if (sourceVersion < PROJECT_LAYOUT_VERSION && Object.keys(projectLayout).length) {
    projectLayoutVersion = PROJECT_LAYOUT_VERSION;
    projectLayout = tileLayoutFromDom();
    const bridge = api();
    if (bridge && bridge.set_project_layout) {
      bridge.set_project_layout(projectLayout).catch(() => {});
    }
  }
}

function tileLayoutFromDom() {
  const layout = {};
  const tiles = projectTiles();
  const viewportWidth = Math.max(1, projectViewportWidth || workspaceViewportWidth());
  const viewportHeight = Math.max(1, projectViewportHeight || workspaceViewportHeight());
  const ranked = [...tiles].sort((first, second) => num(first.style.zIndex) - num(second.style.zIndex));
  const orderByTile = new Map(ranked.map((tile, order) => [tile, order]));
  tiles.forEach((tile) => {
    const rect = rectOfTile(tile);
    layout[tile.dataset.projectId] = {
      x: Math.round(rect.left / viewportWidth * LAYOUT_SCALE),
      y: Math.round(rect.top / viewportHeight * LAYOUT_SCALE),
      w: Math.round(rect.width / viewportWidth * LAYOUT_SCALE),
      h: Math.round(rect.height / viewportHeight * LAYOUT_SCALE),
      order: orderByTile.get(tile) || 0,
    };
  });
  return layout;
}

async function persistTileLayout() {
  projectLayout = tileLayoutFromDom();
  projectLayoutVersion = PROJECT_LAYOUT_VERSION;
  const bridge = api();
  if (bridge && bridge.set_project_layout) await bridge.set_project_layout(projectLayout);
}

async function resetTileLayout() {
  const bridge = api();
  projectLayout = {};
  projectLayoutVersion = PROJECT_LAYOUT_VERSION;
  applyProjectLayout(true);
  if (bridge && bridge.reset_project_layout) {
    const payload = await bridge.reset_project_layout();
    if (payload) render(payload);
  }
}

function configureTileEditing() {
  const changed = dom.body.classList.contains("layout-mode") !== layoutMode;
  dom.body.classList.toggle("layout-mode", layoutMode);
  dom.btnLayout.setAttribute("aria-pressed", String(layoutMode));
  dom.btnLayout.title = layoutMode ? "完成并保存磁贴布局" : "编辑磁贴布局";
  dom.btnLayout.setAttribute("aria-label", dom.btnLayout.title);
  dom.btnWidgetEdit.textContent = layoutMode ? "完成编辑" : "编辑磁贴";
  dom.btnWidgetEdit.title = layoutMode ? "完成并保存磁贴布局" : "进入自由布局，移动或缩放磁贴";
  dom.btnWidgetEdit.setAttribute("aria-pressed", String(layoutMode));
  if (dom.matrixFitState) {
    dom.matrixFitState.textContent = layoutMode ? "拖动卡片 · 边角缩放 · 自动保存" : "AUTO FIT";
  }
  if (!layoutMode) hideAlignmentChrome();
  if (changed) {
    const tiles = projectTiles();
    tiles.forEach((tile, index) => {
      if (typeof tile.animate !== "function") return;
      tile.animate(
        layoutMode
          ? [
              { transform: "translateY(0) scale(1)" },
              { transform: "translateY(-3px) scale(1.008)" },
              { transform: "translateY(0) scale(1)" },
            ]
          : [
              { transform: "scale(1.006)" },
              { transform: "scale(1)" },
            ],
        {
          duration: layoutMode ? 320 : 220,
          delay: index * 35,
          easing: "cubic-bezier(.2,.8,.2,1)",
        }
      );
    });
  }
}

function hideAlignmentChrome() {
  for (const node of dom.projectSections.querySelectorAll(".layout-guide, .layout-snap-badge")) {
    node.classList.remove("visible");
  }
}

function showAlignmentChrome(snap, rect) {
  const vertical = dom.projectSections.querySelector(".layout-guide-v");
  const horizontal = dom.projectSections.querySelector(".layout-guide-h");
  const badge = dom.projectSections.querySelector(".layout-snap-badge");
  if (vertical) {
    vertical.classList.toggle("visible", snap.vertical != null);
    if (snap.vertical != null) vertical.style.left = `${snap.vertical}px`;
  }
  if (horizontal) {
    horizontal.classList.toggle("visible", snap.horizontal != null);
    if (snap.horizontal != null) horizontal.style.top = `${snap.horizontal}px`;
  }
  if (badge) {
    const visible = snap.vertical != null || snap.horizontal != null;
    badge.classList.toggle("visible", visible);
    if (visible) {
      badge.style.left = `${clamp(rect.left + 10, 4, Math.max(4, projectCanvasWidth - 74))}px`;
      badge.style.top = `${Math.max(4, rect.top - 27)}px`;
    }
  }
}

function closestSnap(sources, guides) {
  let best = null;
  for (const source of sources) {
    for (const guide of guides) {
      const delta = guide - source;
      if (Math.abs(delta) <= SNAP_DISTANCE && (!best || Math.abs(delta) < Math.abs(best.delta))) {
        best = { delta, guide };
      }
    }
  }
  return best;
}

function snapTileRect(rect, tile, edge) {
  const verticalGuides = [0, projectViewportWidth / 2, projectViewportWidth];
  const horizontalGuides = [0];
  for (const other of projectTiles()) {
    if (other === tile) continue;
    const candidate = rectOfTile(other);
    verticalGuides.push(candidate.left, candidate.left + candidate.width / 2, candidate.left + candidate.width);
    horizontalGuides.push(candidate.top, candidate.top + candidate.height / 2, candidate.top + candidate.height);
  }
  const moving = edge === "move";
  const xSources = moving
    ? [rect.left, rect.left + rect.width / 2, rect.left + rect.width]
    : edge.includes("w") ? [rect.left] : edge.includes("e") ? [rect.left + rect.width] : [];
  const ySources = moving
    ? [rect.top, rect.top + rect.height / 2, rect.top + rect.height]
    : edge.includes("n") ? [rect.top] : edge.includes("s") ? [rect.top + rect.height] : [];
  const xSnap = closestSnap(xSources, verticalGuides);
  const ySnap = closestSnap(ySources, horizontalGuides);
  const snapped = { ...rect };
  if (xSnap) {
    if (moving) snapped.left += xSnap.delta;
    else if (edge.includes("w")) {
      snapped.left += xSnap.delta;
      snapped.width -= xSnap.delta;
    } else if (edge.includes("e")) snapped.width += xSnap.delta;
  }
  if (ySnap) {
    if (moving) snapped.top += ySnap.delta;
    else if (edge.includes("n")) {
      snapped.top += ySnap.delta;
      snapped.height -= ySnap.delta;
    } else if (edge.includes("s")) snapped.height += ySnap.delta;
  }
  return {
    rect: constrainTileRect(snapped, edge),
    vertical: xSnap ? xSnap.guide : null,
    horizontal: ySnap ? ySnap.guide : null,
  };
}

function constrainTileRect(rect, edge = "move") {
  let left = num(rect.left);
  let top = num(rect.top);
  let width = clamp(num(rect.width, MIN_TILE_WIDTH), MIN_TILE_WIDTH, MAX_TILE_WIDTH);
  let height = clamp(num(rect.height, MIN_TILE_HEIGHT), MIN_TILE_HEIGHT, MAX_TILE_HEIGHT);
  if (left < 0) {
    if (edge.includes("w")) width = Math.max(MIN_TILE_WIDTH, width + left);
    left = 0;
  }
  if (top < 0) {
    if (edge.includes("n")) height = Math.max(MIN_TILE_HEIGHT, height + top);
    top = 0;
  }
  return {
    left: clamp(left, 0, MAX_WORKSPACE_POSITION),
    top: clamp(top, 0, MAX_WORKSPACE_POSITION),
    width,
    height,
  };
}

function interactionRect(event) {
  const active = activeTileInteraction;
  const dx = event.clientX - active.startX + dom.stage.scrollLeft - active.startScrollLeft;
  const dy = event.clientY - active.startY + dom.stage.scrollTop - active.startScrollTop;
  const rect = { ...active.startRect };
  if (active.edge === "move") {
    rect.left += dx;
    rect.top += dy;
    return constrainTileRect(rect, active.edge);
  }
  if (active.edge.includes("e")) rect.width += dx;
  if (active.edge.includes("s")) rect.height += dy;
  if (active.edge.includes("w")) {
    rect.left += dx;
    rect.width -= dx;
    if (rect.width < MIN_TILE_WIDTH) {
      rect.left = active.startRect.left + active.startRect.width - MIN_TILE_WIDTH;
      rect.width = MIN_TILE_WIDTH;
    }
  }
  if (active.edge.includes("n")) {
    rect.top += dy;
    rect.height -= dy;
    if (rect.height < MIN_TILE_HEIGHT) {
      rect.top = active.startRect.top + active.startRect.height - MIN_TILE_HEIGHT;
      rect.height = MIN_TILE_HEIGHT;
    }
  }
  return constrainTileRect(rect, active.edge);
}

function queueInteractionFrame(rect, snap) {
  activeTileInteraction.pendingRect = rect;
  activeTileInteraction.pendingSnap = snap;
  if (layoutFrame) return;
  layoutFrame = window.requestAnimationFrame(() => {
    layoutFrame = 0;
    if (!activeTileInteraction) return;
    setTileRect(activeTileInteraction.tile, activeTileInteraction.pendingRect);
    showAlignmentChrome(activeTileInteraction.pendingSnap, activeTileInteraction.pendingRect);
    updateProjectCanvasSize(activeTileInteraction.pendingRect, false);
  });
}

function autoScrollVelocity(position, start, end) {
  if (position < start + AUTO_SCROLL_MARGIN) {
    const pressure = clamp((start + AUTO_SCROLL_MARGIN - position) / AUTO_SCROLL_MARGIN, 0, 1);
    return -Math.ceil(AUTO_SCROLL_MAX * pressure);
  }
  if (position > end - AUTO_SCROLL_MARGIN) {
    const pressure = clamp((position - (end - AUTO_SCROLL_MARGIN)) / AUTO_SCROLL_MARGIN, 0, 1);
    return Math.ceil(AUTO_SCROLL_MAX * pressure);
  }
  return 0;
}

function runInteractionAutoScroll() {
  interactionScrollFrame = 0;
  const active = activeTileInteraction;
  if (!active || (!active.scrollVelocityX && !active.scrollVelocityY)) return;

  if (active.scrollVelocityX > 0) {
    const room = dom.stage.scrollLeft + dom.stage.clientWidth + active.scrollVelocityX + CANVAS_PADDING;
    if (room > projectCanvasWidth) {
      projectCanvasWidth = room;
      dom.projectSections.style.width = `${projectCanvasWidth}px`;
    }
  }
  if (active.scrollVelocityY > 0) {
    const currentHeight = Number.parseFloat(dom.projectSections.style.height) || 260;
    dom.projectSections.style.height = `${currentHeight + active.scrollVelocityY}px`;
  }

  const beforeLeft = dom.stage.scrollLeft;
  const beforeTop = dom.stage.scrollTop;
  dom.stage.scrollLeft += active.scrollVelocityX;
  dom.stage.scrollTop += active.scrollVelocityY;
  if (dom.stage.scrollLeft !== beforeLeft || dom.stage.scrollTop !== beforeTop) {
    const raw = interactionRect({ clientX: active.pointerX, clientY: active.pointerY });
    const snap = snapTileRect(raw, active.tile, active.edge);
    active.pendingRect = snap.rect;
    active.pendingSnap = snap;
    setTileRect(active.tile, snap.rect);
    showAlignmentChrome(snap, snap.rect);
    updateProjectCanvasSize(snap.rect, false);
  }
  interactionScrollFrame = window.requestAnimationFrame(runInteractionAutoScroll);
}

function updateInteractionAutoScroll(event) {
  const active = activeTileInteraction;
  if (!active) return;
  const bounds = dom.stage.getBoundingClientRect();
  active.pointerX = event.clientX;
  active.pointerY = event.clientY;
  active.scrollVelocityX = autoScrollVelocity(event.clientX, bounds.left, bounds.right);
  active.scrollVelocityY = autoScrollVelocity(event.clientY, bounds.top, bounds.bottom);
  if (!interactionScrollFrame && (active.scrollVelocityX || active.scrollVelocityY)) {
    interactionScrollFrame = window.requestAnimationFrame(runInteractionAutoScroll);
  }
}

function beginTileInteraction(event) {
  if (!layoutMode || activeTileInteraction || activeCanvasPan || event.button !== 0) return;
  const tile = event.target.closest(".proj");
  if (!tile) return;
  if (event.target.closest(".tile-edit-toolbar")) return;
  const handle = event.target.closest(".tile-handle");
  event.preventDefault();
  const highest = Math.max(0, ...projectTiles().map((node) => num(node.style.zIndex)));
  tile.style.zIndex = String(highest + 1);
  tile.classList.add("interacting");
  activeTileInteraction = {
    tile,
    pointerId: event.pointerId,
    edge: handle ? handle.dataset.edge : "move",
    startX: event.clientX,
    startY: event.clientY,
    startScrollLeft: dom.stage.scrollLeft,
    startScrollTop: dom.stage.scrollTop,
    pointerX: event.clientX,
    pointerY: event.clientY,
    scrollVelocityX: 0,
    scrollVelocityY: 0,
    startRect: rectOfTile(tile),
    pendingRect: rectOfTile(tile),
    pendingSnap: { vertical: null, horizontal: null },
  };
  try {
    if (tile.setPointerCapture) tile.setPointerCapture(event.pointerId);
  } catch (error) {
    // Synthetic diagnostics and a pointer released during a WebView hand-off
    // may no longer be capturable; the window listeners still finish safely.
  }
}

function moveTileInteraction(event) {
  if (!activeTileInteraction || event.pointerId !== activeTileInteraction.pointerId) return;
  updateInteractionAutoScroll(event);
  const raw = interactionRect(event);
  const snap = snapTileRect(raw, activeTileInteraction.tile, activeTileInteraction.edge);
  queueInteractionFrame(snap.rect, snap);
}

function finishTileInteraction(event) {
  if (!activeTileInteraction || event.pointerId !== activeTileInteraction.pointerId) return;
  if (layoutFrame) {
    window.cancelAnimationFrame(layoutFrame);
    layoutFrame = 0;
  }
  if (interactionScrollFrame) {
    window.cancelAnimationFrame(interactionScrollFrame);
    interactionScrollFrame = 0;
  }
  const active = activeTileInteraction;
  setTileRect(active.tile, active.pendingRect);
  active.tile.classList.remove("interacting");
  active.tile.classList.add("settling");
  if (typeof active.tile.animate === "function") {
    active.tile.animate(
      [
        { transform: "scale(.995)" },
        { transform: "scale(1.008)" },
        { transform: "scale(1)" },
      ],
      { duration: 300, easing: "cubic-bezier(.2,.9,.25,1)" }
    );
  }
  window.setTimeout(() => active.tile.classList.remove("settling"), 320);
  activeTileInteraction = null;
  hideAlignmentChrome();
  updateProjectCanvasSize(null, true);
  persistTileLayout();
}

function queueProjectResizeLayout() {
  if (projectResizeFrame) return;
  projectResizeFrame = window.requestAnimationFrame(() => {
    projectResizeFrame = 0;
    if (!activeTileInteraction) applyProjectLayout(false);
  });
}

function observeProjectCanvas() {
  if (projectResizeObserver || typeof ResizeObserver === "undefined") return;
  projectResizeObserver = new ResizeObserver(() => {
    if (!activeTileInteraction) queueProjectResizeLayout();
  });
  projectResizeObserver.observe(dom.stage);
}

function markWindowResizing() {
  dom.body.classList.add("window-resizing");
  queueProjectResizeLayout();
  if (windowResizeTimer) window.clearTimeout(windowResizeTimer);
  windowResizeTimer = window.setTimeout(() => {
    windowResizeTimer = 0;
    dom.body.classList.remove("window-resizing");
    if (layoutMode) return;
    for (const tile of projectTiles()) {
      playRenderAnimation(
        tile,
        [{ transform: "scale(.997)" }, { transform: "scale(1)" }],
        { duration: 180, easing: "cubic-bezier(.2,.8,.2,1)" },
      );
    }
  }, 140);
}

/* Middle-button canvas panning is intentionally separate from tile editing:
   left drag moves/resizes a tile in layout mode, while middle drag always
   moves the viewport. Pointer events are sampled once per animation frame so
   high-frequency WebView2 input never forces multiple layouts per paint. */
function applyCanvasPanFrame() {
  canvasPanFrame = 0;
  const active = activeCanvasPan;
  if (!active) return;
  dom.stage.scrollLeft = active.pendingScrollLeft;
  dom.stage.scrollTop = active.pendingScrollTop;
}

function queueCanvasPanFrame() {
  if (canvasPanFrame) return;
  canvasPanFrame = window.requestAnimationFrame(applyCanvasPanFrame);
}

function beginCanvasPan(event) {
  if (
    event.button !== MIDDLE_MOUSE_BUTTON
    || activeCanvasPan
    || activeTileInteraction
  ) return;
  event.preventDefault();
  activeCanvasPan = {
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    startScrollLeft: dom.stage.scrollLeft,
    startScrollTop: dom.stage.scrollTop,
    pendingScrollLeft: dom.stage.scrollLeft,
    pendingScrollTop: dom.stage.scrollTop,
  };
  dom.body.classList.add("canvas-panning");
  try {
    if (dom.stage.setPointerCapture) dom.stage.setPointerCapture(event.pointerId);
  } catch (error) {
    // Window-level listeners still keep the gesture usable if capture is lost
    // during a WebView/native window hand-off.
  }
}

function moveCanvasPan(event) {
  const active = activeCanvasPan;
  if (!active || event.pointerId !== active.pointerId) return;
  if ((event.buttons & MIDDLE_MOUSE_BUTTONS_MASK) === 0) {
    finishCanvasPan(event);
    return;
  }
  event.preventDefault();
  active.pendingScrollLeft = Math.max(
    0,
    active.startScrollLeft - (event.clientX - active.startX),
  );
  active.pendingScrollTop = Math.max(
    0,
    active.startScrollTop - (event.clientY - active.startY),
  );
  queueCanvasPanFrame();
}

function finishCanvasPan(event) {
  const active = activeCanvasPan;
  if (!active || event.pointerId !== active.pointerId) return;
  if (canvasPanFrame) {
    window.cancelAnimationFrame(canvasPanFrame);
    canvasPanFrame = 0;
  }
  dom.stage.scrollLeft = active.pendingScrollLeft;
  dom.stage.scrollTop = active.pendingScrollTop;
  activeCanvasPan = null;
  dom.body.classList.remove("canvas-panning");
  try {
    if (dom.stage.hasPointerCapture && dom.stage.hasPointerCapture(active.pointerId)) {
      dom.stage.releasePointerCapture(active.pointerId);
    }
  } catch (error) {
    // Capture may already have been released by Chromium.
  }
}

function preventMiddleAuxClick(event) {
  if (event.button === MIDDLE_MOUSE_BUTTON) event.preventDefault();
}

function bindCanvasPanning() {
  dom.stage.addEventListener("pointerdown", beginCanvasPan);
  dom.stage.addEventListener("auxclick", preventMiddleAuxClick);
  dom.stage.addEventListener("lostpointercapture", finishCanvasPan);
  window.addEventListener("pointermove", moveCanvasPan);
  window.addEventListener("pointerup", finishCanvasPan);
  window.addEventListener("pointercancel", finishCanvasPan);
}

function bindTileEditing() {
  dom.projectSections.addEventListener("click", (event) => {
    const button = event.target.closest("[data-tile-preset]");
    if (!button) return;
    const tile = button.closest(".proj");
    if (!tile) return;
    event.preventDefault();
    event.stopPropagation();
    applyTileSizePreset(tile, button.dataset.tilePreset);
  });
  dom.projectSections.addEventListener("pointerdown", (event) => {
    beginTileInteraction(event);
  });
  window.addEventListener("pointermove", moveTileInteraction);
  window.addEventListener("pointerup", finishTileInteraction);
  window.addEventListener("pointercancel", finishTileInteraction);
  observeProjectCanvas();
}

function selectView(name) {
  const target = ["overview", "activity", "extensions"].includes(name) ? name : "overview";
  dom.body.dataset.view = target;
  for (const button of document.querySelectorAll(".view-tab")) {
    button.classList.toggle("active", button.dataset.view === target);
  }
  for (const panel of document.querySelectorAll(".view-panel")) {
    const active = panel.dataset.panel === target;
    panel.hidden = !active;
    panel.classList.toggle("active", active);
  }
  el("stage").scrollTop = 0;
  el("stage").scrollLeft = 0;
}

function updateClock() {
  if (!dom.viewClock) return;
  dom.viewClock.textContent = new Date().toLocaleTimeString("zh-CN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
  });
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

let repairInFlight = false;

function disarmRepair() {
  repairInFlight = false;
  dom.btnRepair.disabled = false;
  dom.btnRepair.title = "一键修复全部服务";
  dom.btnRepairOffline.disabled = false;
  dom.btnRepairOffline.textContent = "一键修复全部服务";
}

async function requestRepair(button) {
  const bridge = api();
  if (!bridge || !bridge.repair_fleet || repairInFlight) return;
  repairInFlight = true;
  dom.btnRepair.disabled = true;
  dom.btnRepairOffline.disabled = true;
  dom.btnRepair.title = "修复请求处理中";
  if (button === dom.btnRepairOffline) button.textContent = "正在提交修复";
  let result = null;
  try {
    result = await Promise.race([
      bridge.repair_fleet(),
      new Promise((resolve) => {
        window.setTimeout(
          () => resolve({ ok: false, message: "修复请求超时，请稍后查看服务状态" }),
          8000,
        );
      }),
    ]);
  } catch (error) {
    result = { ok: false, message: "修复请求失败" };
  }
  const message = result && result.message ? result.message : "修复请求已发送";
  dom.repairHint.textContent = message;
  dom.sbState.textContent = message;
  window.setTimeout(() => {
    disarmRepair();
    pull(true);
  }, 3000);
}

/* ---------- controls ---------- */

function windowResizeEdgeAt(event) {
  const zone = event.target && event.target.closest
    ? event.target.closest("[data-window-edge]")
    : null;
  if (zone) return zone.dataset.windowEdge;

  const corner = 18;
  const edge = 10;
  const west = event.clientX <= corner;
  const east = event.clientX >= window.innerWidth - corner;
  const north = event.clientY <= corner;
  const south = event.clientY >= window.innerHeight - corner;
  if (north && west) return "nw";
  if (north && east) return "ne";
  if (south && west) return "sw";
  if (south && east) return "se";
  if (event.clientY <= edge) return "n";
  if (event.clientY >= window.innerHeight - edge) return "s";
  if (event.clientX <= edge) return "w";
  if (event.clientX >= window.innerWidth - edge) return "e";
  return null;
}

function requestWindowResize(event) {
  if (event.button !== 0 || dom.body.classList.contains("desktop-mode")) return;
  const edge = windowResizeEdgeAt(event);
  if (!edge) return;
  const bridge = api();
  if (!bridge || !bridge.begin_window_resize) return;
  event.preventDefault();
  event.stopPropagation();
  bridge.begin_window_resize(edge).catch(() => {});
}

function bindControls() {
  bindTileEditing();
  bindCanvasPanning();
  document.addEventListener("pointerdown", requestWindowResize, true);
  for (const button of document.querySelectorAll(".view-tab")) {
    button.addEventListener("click", () => selectView(button.dataset.view));
  }
  el("btnRefresh").addEventListener("click", async () => {
    dom.btnRefresh.classList.add("spinning");
    await pull(true);
    dom.btnRefresh.classList.remove("spinning");
  });
  el("btnRetry").addEventListener("click", () => pull(true));
  dom.btnCapture.addEventListener("click", async () => {
    const bridge = api();
    if (!bridge || !bridge.capture) return;
    const result = await bridge.capture();
    dom.sbState.textContent = result && result.message ? result.message : "截图失败";
  });
  dom.btnLayout.addEventListener("click", async () => {
    layoutMode = !layoutMode;
    configureTileEditing();
    if (!layoutMode) {
      await persistTileLayout();
      lastDataKey = "";
      await pull(false);
    }
  });
  dom.btnLayoutReset.addEventListener("click", () => resetTileLayout());
  dom.btnWidgetReset.addEventListener("click", () => resetTileLayout());
  dom.btnRepair.addEventListener("click", () => requestRepair(dom.btnRepair));
  dom.btnRepairOffline.addEventListener("click", () => requestRepair(dom.btnRepairOffline));
  el("btnMin").addEventListener("click", () => api() && api().minimize());
  el("btnClose").addEventListener("click", () => api() && api().hide_to_tray());
  dom.btnWidgetEdit.addEventListener("click", async () => {
    layoutMode = !layoutMode;
    configureTileEditing();
    if (!layoutMode) {
      await persistTileLayout();
      lastDataKey = "";
      await pull(false);
    }
  });
  dom.btnWidgetHide.addEventListener("click", () => api() && api().hide_to_tray());
  dom.btnWidgetQuit.addEventListener("click", () => api() && api().quit());
  dom.btnDesktop.addEventListener("click", async () => {
    const next = dom.btnDesktop.getAttribute("aria-pressed") !== "true";
    const bridge = api();
    if (!bridge || !bridge.set_desktop_mode) return;
    if (next && layoutMode) {
      layoutMode = false;
      configureTileEditing();
      await persistTileLayout();
    }
    render(await bridge.set_desktop_mode(next));
  });
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
  updateClock();
  pull(false);
  timer = window.setInterval(() => pull(false), POLL_MS);
  window.setInterval(updateClock, 30000);
}

window.addEventListener("pywebviewready", start);
if (api()) start();
window.addEventListener("resize", markWindowResizing);
window.addEventListener("beforeunload", () => {
  if (timer) window.clearInterval(timer);
  if (windowResizeTimer) window.clearTimeout(windowResizeTimer);
  if (projectResizeFrame) window.cancelAnimationFrame(projectResizeFrame);
  if (interactionScrollFrame) window.cancelAnimationFrame(interactionScrollFrame);
  if (canvasPanFrame) window.cancelAnimationFrame(canvasPanFrame);
});
