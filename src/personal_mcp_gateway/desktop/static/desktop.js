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
const CARD_ID = document.body.dataset.cardId || "";

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
  btnDesktop: el("btnDesktop"),
  btnRefresh: el("btnRefresh"),
  btnRepairOffline: el("btnRepairOffline"),
  cardManagerSummary: el("cardManagerSummary"),
  cardSwitches: el("cardSwitches"),
  btnShowAllCards: el("btnShowAllCards"),
  btnHideAllCards: el("btnHideAllCards"),
  managerLocalState: el("managerLocalState"),
  managerLocalHint: el("managerLocalHint"),
  managerBusinessState: el("managerBusinessState"),
  managerBusinessHint: el("managerBusinessHint"),
  viewClock: el("viewClock"),
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
    eyebrow: "训练与恢复",
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
    eyebrow: "本地专注监督",
    tagline: "监督锁机 · 本地维护",
    groups: [],
  },
  personal: {
    flavor: "gateway",
    accent: "#63D8FF",
    display: "服务中心",
    eyebrow: "本机连接与自动恢复",
    tagline: "连接状态 · 自动恢复",
    groups: [],
  },
};
const SECTION_ORDER = ["foxlink", "watch", "journal", "personal", "bzsjk"];
const CARD_WIDGET_IDS = {
  personal: new Set(["personal_system", "personal_sync"]),
  foxlink: new Set(["focus_current", "focus_today"]),
  watch: new Set(["watch_workouts", "watch_sleep", "watch_current_plan", "watch_status"]),
  journal: new Set(["journal_recent", "journal_count"]),
  bzsjk: new Set(["bzsjk_project"]),
};
const VOLATILE_RENDER_FIELDS = new Set([
  "generatedAt",
  "fetchedAt",
  "sampledAt",
  "checkedAt",
  "latencyMs",
  "uptimeSeconds",
  "probeDurationMs",
  "lastSuccessfulPushAt",
]);
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
const CARD_BASE_SIZES = {
  foxlink: { width: 320, height: 270 },
  watch: { width: 430, height: 310 },
  journal: { width: 360, height: 310 },
  personal: { width: 650, height: 410 },
  bzsjk: { width: 360, height: 330 },
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
let desktopRegionFrame = 0;
let desktopRegionTimer = 0;

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
  if (days) return `${days}天${hours}小时`;
  if (hours) return `${hours}小时${minutes}分`;
  if (minutes) return `${minutes}分钟`;
  return `${s}秒`;
}

function clockOf(value) {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

function dateTimeOf(value) {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) return "时间未知";
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function plainState(value) {
  return ({
    online: "正常",
    degraded: "需注意",
    offline: "离线",
    disconnected: "未连接",
    local: "仅本机",
    unknown: "未知",
  })[String(value || "unknown")] || String(value || "未知");
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
let clockTimer = null;
let pollGeneration = 0;
let pullInFlight = null;
let forcePullQueued = false;
const BRIDGE_POLL_TIMEOUT_MS = 8000;

function api() {
  return window.pywebview && window.pywebview.api ? window.pywebview.api : null;
}

function bridgePollWithTimeout(operation) {
  let timeoutId = null;
  const timeout = new Promise((_, reject) => {
    timeoutId = window.setTimeout(
      () => reject(new Error("desktop bridge poll timed out")),
      BRIDGE_POLL_TIMEOUT_MS,
    );
  });
  return Promise.race([Promise.resolve().then(operation), timeout])
    .finally(() => window.clearTimeout(timeoutId));
}

function pull(force = false) {
  const bridge = api();
  if (!bridge) return Promise.resolve();
  if (force) {
    forcePullQueued = true;
    if (dom.btnRefresh) {
      dom.btnRefresh.disabled = true;
      dom.btnRefresh.classList.add("spinning");
      dom.btnRefresh.textContent = "刷新中";
    }
  }
  if (pullInFlight) return pullInFlight;

  pullInFlight = (async () => {
    try {
      do {
        const shouldForce = forcePullQueued;
        forcePullQueued = false;
        try {
          const payload = await bridgePollWithTimeout(
            () => shouldForce ? bridge.refresh() : bridge.snapshot(),
          );
          if (payload) render(payload);
        } catch (error) {
          console.warn("snapshot failed", error);
        }
      } while (forcePullQueued);
    } finally {
      pullInFlight = null;
      if (dom.btnRefresh) {
        dom.btnRefresh.disabled = false;
        dom.btnRefresh.classList.remove("spinning");
        dom.btnRefresh.textContent = "刷新";
      }
    }
  })();
  return pullInFlight;
}

/* ---------- rendering ---------- */

let lastDataKey = "";
let cardFreshness = { stale: false, label: "" };

function syncCardFreshness(payload, data) {
  const stale = Boolean(payload && payload.stale);
  const sampledAt = data && data.generatedAt ? data.generatedAt : payload && payload.fetchedAt;
  cardFreshness = {
    stale,
    label: stale ? `旧数据 · 更新于 ${dateTimeOf(sampledAt)}` : "",
  };
  dom.body.classList.toggle("stale-data", stale);
  for (const note of document.querySelectorAll(".card-stale-note")) {
    note.hidden = !stale;
    setRenderText(note, cardFreshness.label);
  }
}

function structuralRenderValue(value) {
  if (Array.isArray(value)) return value.map(structuralRenderValue);
  if (!value || typeof value !== "object") return value;
  const result = {};
  for (const [key, child] of Object.entries(value)) {
    if (VOLATILE_RENDER_FIELDS.has(key)) continue;
    result[key] = structuralRenderValue(child);
  }
  return result;
}

function cardRenderProjection(data, cardId) {
  const targets = Array.isArray(data.targets) ? data.targets : [];
  const widgets = Array.isArray(data.widgets) ? data.widgets : [];
  const target = targets.find((item) => item.id === cardId) || null;
  if (cardId === "personal") {
    const gateway = data.gateway || {};
    const ids = CARD_WIDGET_IDS.personal;
    return {
      target,
      targets,
      widgets: widgets.filter((widget) => ids.has(widget.id)),
      summary: data.summary,
      gateway: { state: gateway.state, version: gateway.version },
      fleet: data.fleet,
      activity: data.activity,
      events: data.events,
      errors: data.errors,
    };
  }
  const ids = CARD_WIDGET_IDS[cardId] || new Set();
  return {
    target,
    widgets: widgets.filter((widget) => ids.has(widget.id)),
  };
}

function renderDataKey(payload, data) {
  const projection = CARD_ID
    ? cardRenderProjection(data, CARD_ID)
    : {
      targets: data.targets,
      widgets: data.widgets,
      summary: data.summary,
      gateway: data.gateway ? { state: data.gateway.state, version: data.gateway.version } : null,
      events: data.events,
      errors: data.errors,
      activity: data.activity,
      fleet: data.fleet,
    };
  return JSON.stringify(structuralRenderValue({
    status: payload.status,
    stale: payload.stale,
    projection,
  }));
}

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
    setRenderText(dom.sbSync, `上次尝试 ${clockOf(payload.fetchedAt)}`);
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
    setRenderText(dom.offlineTitle, "暂时无法获取新数据");
    const failures = Math.min(FAILURE_DISPLAY_CAP, Math.max(0, num(payload.consecutiveFailures)));
    const failureText = failures >= FAILURE_DISPLAY_CAP ? `${FAILURE_DISPLAY_CAP}+` : failures;
    setRenderText(dom.offlineHint, `正在显示上次的数据 · 已自动重试 ${failureText} 次`);
  }

  const data = payload.data;
  syncCardFreshness(payload, data);
  const summary = data.summary || {};
  const gateway = data.gateway || {};
  const online = num(summary.online);
  const total = num(summary.total);
  const widgets = Array.isArray(data.widgets) ? data.widgets : [];
  const businessAttention = businessAttentionItems(data, widgets);
  renderManagementScopes(data, widgets, businessAttention);
  const visibleStatus = payload.status === "online" && businessAttention.length
    ? "degraded"
    : payload.status;

  setStatusChrome(
    visibleStatus,
    `${online}/${total}`,
    recoveryVisible
      ? "正在恢复连接"
      : businessAttention[0] || (payload.statusLabel || ""),
  );
  if (recoveryVisible) setRenderText(dom.sbState, "正在恢复连接");
  setRenderText(dom.sbSync, `更新于 ${clockOf(data.generatedAt || payload.fetchedAt)}`);
  setRenderText(dom.sbProbe, `检查耗时 ${num(data.probeDurationMs)} 毫秒`);
  renderGuard(data.fleet || null);

  // Skip the DOM rebuild when nothing but timestamps changed — the board polls
  // every 4s and most passes carry identical data.
  const key = renderDataKey(payload, data);
  if (key === lastDataKey) return;
  lastDataKey = key;

  if (!layoutMode) renderSections(data);
  if (CARD_ID) return;
  renderStrip(payload, online, total, gateway, summary, data, businessAttention);
  renderChart(data.activity && Array.isArray(data.activity.hourly) ? data.activity.hourly : []);
  renderEvents(data);
}

function setStatusChrome(status, count, label) {
  dom.tbChipDot.dataset.status = status;
  dom.sbDot.dataset.status = status;
  if (dom.fsDot) dom.fsDot.dataset.status = status;
  setRenderText(dom.tbCount, count);
  setRenderText(dom.tbLabel, label);
  const stateLabel = status === "disconnected"
    ? "连接已断开"
    : status === "online" ? "连接正常" : "部分卡片需注意";
  setRenderText(dom.sbState, stateLabel);
}

function renderGuard(fleet) {
  const state = fleet && fleet.watchdog ? fleet.watchdog.state : null;
  if (state === "running") {
    setRenderText(dom.sbGuard, "自动修复正常");
    dom.sbGuard.dataset.state = "ok";
  } else if (state) {
    setRenderText(dom.sbGuard, state === "missing" ? "自动修复未安装" : "自动修复不可用");
    dom.sbGuard.dataset.state = "bad";
  } else {
    setRenderText(dom.sbGuard, "自动修复 —");
    dom.sbGuard.dataset.state = "";
  }
}

function renderStrip(payload, online, total, gateway, summary, data, businessAttention) {
  setRenderText(dom.heroValue, `${online}/${total}`);
  const titles = {
    online: "所有系统正常运行",
    degraded: "部分链路已降级",
    offline: "存在离线项目",
  };
  setRenderText(
    dom.heroTitle,
    payload.status === "online" && businessAttention.length
      ? businessAttention.length === 1
        ? `本机服务正常，${businessAttention[0]}`
        : `本机服务正常，${businessAttention.length} 项数据需注意`
      : (titles[payload.status] || "状态未知"),
  );
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
  let label = "当前没有异常";
  let title = "当前链路稳定";
  let detail = "看护服务持续巡检，无待处理异常";
  let time = clockOf(data.generatedAt);

  if (error) {
    status = "offline";
    label = "最近异常";
    title = String(error.code || error.error || "网关异常");
    detail = String(error.message || error.summary || error.module || "等待下一轮诊断");
    time = clockOf(error.createdAt || error.created_at);
  } else if (event) {
    status = event.toState === "online" ? "online" : event.toState === "degraded" ? "degraded" : "offline";
    label = status === "online" ? "最近已恢复" : "最近状态变化";
    title = `${event.name || event.target || "链路"} ${status === "online" ? "已恢复" : "状态变化"}`;
    detail = `${plainState(event.fromState)} → ${plainState(event.toState)}`;
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
      detail: `${plainState(item.fromState)} → ${plainState(item.toState)}`,
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
        detail: `本机服务 ${target.mcp && target.mcp.ok ? `${num(target.mcp.latencyMs)} 毫秒` : "不可用"} / 安全连接 ${target.tunnel && target.tunnel.ok ? `${num(target.tunnel.latencyMs)} 毫秒` : "不可用"}`,
        time: "当前",
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
    gatewayRouteNode("01", "项目连接", `${onlineRoutes}/${totalRoutes}`, "正常连接", fleetState),
    gatewayRouteLink(fleetState),
    gatewayRouteNode("02", "本机服务", coreState === "online" ? "正常" : "异常", `${num(target.mcp && target.mcp.latencyMs)} 毫秒`, coreState),
    gatewayRouteLink(coreState === "online" && relayState === "online" ? "online" : "offline"),
    gatewayRouteNode("03", "安全连接", relayState === "online" ? "已连接" : "未连接", `${num(target.tunnel && target.tunnel.latencyMs)} 毫秒`, relayState),
    gatewayRouteLink(relayState === "online" && guardState === "online" ? "online" : "offline"),
    gatewayRouteNode("04", "自动修复", guardState === "online" ? "正常" : "需检查", fleet.repairSupported ? "可自动处理" : "仅查看状态", guardState),
  );

  const metrics = make("div", "gw-metrics");
  const metricData = [
    ["24 小时调用", compact(summary.calls24h), `${compact(summary.failures24h)} 次失败`, compact(summary.calls24h)],
    ["成功率", `${num(summary.successRate, 100).toFixed(1)}%`, "过去 24 小时", `${Math.round(num(summary.successRate, 100))}%`],
    ["检查耗时", `${num(data.probeDurationMs)} 毫秒`, "全部连接", `${num(data.probeDurationMs)} 毫秒`],
    ["已运行", duration(gateway.uptimeSeconds), `版本 ${gateway.version || "未知"}`, briefDuration(gateway.uptimeSeconds)],
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
    make("span", null, "服务状态"),
    make("b", null, `${runningServices}/${totalServices} 个正常`),
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
      make("span", null, `本机 ${item.mcp && item.mcp.ok ? `${num(item.mcp.latencyMs)} 毫秒` : "不可用"}`),
      make("span", null, `连接 ${item.tunnel && item.tunnel.ok ? `${num(item.tunnel.latencyMs)} 毫秒` : "不可用"}`),
    );
    rows.append(row);
  });
  serviceMatrix.append(matrixHead, rows);

  const telemetry = make("section", "gw-telemetry");
  const traffic = make("div", "gw-traffic");
  const trafficHead = make("header");
  trafficHead.append(make("span", null, "24 小时调用"), make("b", null, `${compact(summary.calls24h)} 次`));
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
  reading.append(make("strong", null, hasScore ? String(Math.round(score)) : "—"), make("span", null, "睡眠评分"));
  dial.append(svg, reading);
  return dial;
}

function watchConsole(target, widgets) {
  const workouts = widgets.find((widget) => widget.id === "watch_workouts");
  const sleep = widgets.find((widget) => widget.id === "watch_sleep");
  const currentPlan = widgets.find((widget) => widget.id === "watch_current_plan");
  const status = widgets.find((widget) => widget.id === "watch_status");
  const workoutPairs = widgetPairs(workouts);
  const sleepPairs = widgetPairs(sleep);
  const planPairs = widgetPairs(currentPlan);
  const statusPairs = widgetPairs(status);
  const workoutReady = Boolean(workouts && workouts.ok && workoutPairs.size);
  const sleepReady = Boolean(sleep && sleep.ok && sleepPairs.size);
  const planReady = Boolean(currentPlan && currentPlan.ok && planPairs.size);
  const statusReady = Boolean(status && status.ok && statusPairs.size);
  const statusUnavailable = Boolean(status && (
    status.ok === false
    || status.availability === "unavailable"
    || (status.data && status.data.availability === "unavailable")
  ));
  const watchOnline = statusUnavailable
    ? false
    : statusReady
    ? Boolean(status.data && status.data.watchOnline)
    : target.state === "online";
  const readyCount = Number(workoutReady) + Number(sleepReady);
  const dataState = !watchOnline ? "offline" : readyCount === 2 ? "online" : "degraded";
  const stateLabel = !watchOnline ? "手机或手表未连接" : dataState === "online" ? "数据已更新" : "部分数据待更新";

  const console = make("div", "watch-console");
  console.dataset.dataState = dataState;
  console.setAttribute("aria-label", "步序训练与恢复数据");

  const head = make("header", "wi-console-head");
  const live = make("div", "wi-live-mark");
  const dot = make("i", "dot mini");
  dot.dataset.status = dataState;
  live.append(dot, make("span", null, "训练与恢复"));
  head.append(live, make("b", null, stateLabel));

  const run = make("section", "wi-run");
  const runPrimary = make("div", "wi-run-primary");
  runPrimary.append(
    make("span", null, "累计距离"),
    make("strong", null, workoutPairs.get("总距离") || "—"),
    make("small", null, `${workoutPairs.get("训练次数") || "—"} 次训练`),
  );
  const runMetrics = make("div", "wi-run-metrics");
  runMetrics.append(
    watchMetric("活动时长", workoutPairs.get("累计活动")),
    watchMetric("平均心率", workoutPairs.get("平均心率")),
  );
  const plan = make("div", "wi-plan");
  plan.append(
    make("span", null, "当前计划"),
    make("strong", null, planPairs.get("当前计划") || (planReady ? "暂无当前计划" : "等待当前计划")),
  );
  run.append(runPrimary, runMetrics, plan);

  const recovery = make("section", "wi-recovery");
  const recoveryHead = make("div", "wi-recovery-head");
  recoveryHead.append(make("span", null, "恢复情况"), make("b", null, sleepReady ? "睡眠数据已更新" : "等待设备数据"));
  recovery.append(
    recoveryHead,
    watchScoreDial(sleepPairs.get("睡眠评分") || "—"),
    watchMetric("睡眠时长", sleepPairs.get("睡眠时长"), "wi-sleep-duration"),
    watchMetric("心率区间", sleepPairs.get("心率区间"), "wi-heart-range"),
  );

  const issues = [workouts, sleep, currentPlan, status]
    .map((widget) => widget && widget.data && widget.data.body ? String(widget.data.body) : widget && widget.error ? String(widget.error) : "")
    .filter(Boolean);
  const bleState = statusPairs.get("BLE");
  const bleReason = statusPairs.get("BLE 原因");
  const lanFallback = statusPairs.get("LAN 回退");
  let statusNote = issues[0] || "";
  if (!statusNote && statusReady && bleState && bleState !== "CONNECTED") {
    const bleLabel = ({
      DISCONNECTED: "未连接",
      CONNECTING: "正在连接",
      TIMEOUT: "连接超时",
      ERROR: "连接异常",
    })[bleState] || bleState;
    statusNote = `蓝牙${bleLabel}${bleReason ? `（${bleReason}）` : ""}${lanFallback ? ` · 局域网备用连接${lanFallback}` : ""}${watchOnline ? " · 手表整体在线" : ""}`;
  }
  if (!statusNote && dataState !== "online") {
    const pending = [];
    if (!workoutReady) pending.push("训练");
    if (!sleepReady) pending.push("睡眠");
    if (!planReady) pending.push("计划");
    statusNote = `${pending.join(" / ")}数据等待恢复`;
  }
  if (statusNote) {
    const note = make("p", "wi-sync-note", statusNote);
    note.dataset.level = watchOnline ? "neutral" : "alert";
    recovery.append(note);
  }

  console.append(head, run, recovery);
  return console;
}

function focusConsole(widgets) {
  const current = widgets.find((item) => item.id === "focus_current");
  const today = widgets.find((item) => item.id === "focus_today");
  const ready = Boolean(current && current.ok && current.data && current.availability !== "unavailable");
  const todayReady = Boolean(today && today.ok && today.data);
  const value = ready && current.data.value ? String(current.data.value) : "当前专注待读取";
  const label = ready && current.data.label ? String(current.data.label) : "等待当前任务";
  const note = ready && current.data.note ? String(current.data.note) : "本机恢复后自动刷新";
  const state = ready ? String(current.data.sessionState || "unknown") : "offline";
  const console = make("div", "focus-console");
  console.dataset.dataState = ready ? "online" : "offline";

  const head = make("header", "fl-head");
  const stateLabel = { running: "正在专注", paused: "专注已暂停", idle: "当前空闲", stopped: "当前空闲" }[state] || "等待数据";
  head.append(make("span", null, "当前专注"), make("b", null, stateLabel));
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
  const todayValue = todayReady && today.data.value ? String(today.data.value) : "—";
  const todayLabel = todayReady && today.data.isToday === false ? `旧数据 ${today.data.sourceDate || ""}`.trim() : "今日累计";
  foot.append(make("span", null, todayLabel), make("b", null, todayValue));
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
  const todayWritten = Boolean(recentReady && recent.data.todayWritten);
  const console = make("div", "journal-console");
  console.dataset.dataState = recentReady ? "online" : "offline";

  const head = make("header", "jr-head");
  const headCopy = make("div");
  headCopy.append(make("span", null, "今日记录"), make("strong", null, todayWritten ? "今天已写" : "今天还没写"));
  const countBlock = make("div", "jr-count");
  countBlock.append(make("b", null, total), make("small", null, "日记总数"));
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
  const latestDate = recentReady && recent.data.latestEntryDate ? String(recent.data.latestEntryDate) : "";
  const note = count && count.ok && count.data && count.data.note
    ? String(count.data.note)
    : latestDate ? `最近一篇 ${latestDate}` : "等待同步状态";
  foot.append(make("span", null, "最近更新"), make("b", null, note));
  console.append(head, ledger, foot);
  return console;
}

function bzsjkTargetFromWidgets(widgets) {
  const dedicated = widgets.find((item) => item.id === "bzsjk_project");
  const legacy = widgets.find((item) => item.id === "projects");
  const items = [dedicated, legacy].flatMap((widget) => (
    widget && widget.ok && widget.data && Array.isArray(widget.data.items)
      ? widget.data.items
      : []
  ));
  const projectItem = items.find((item) => item.repoId === "bzsjk")
    || items.find((item) => String(item.title || "").trim() === "不做手机控");
  return {
    id: "bzsjk",
    name: "不做手机控",
    description: "本地监督与锁机维护项目",
    state: "local",
    version: null,
    mcp: null,
    tunnel: null,
    projectItem: projectItem || null,
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
  const rawStatus = String(item.value || "仅本机，数据待接入");
  const status = rawStatus === "工作区干净"
    ? "状态正常"
    : rawStatus.includes("未提交") ? "有待整理的更新" : rawStatus;
  const console = make("div", "bz-console");
  const head = make("header", "bz-head");
  head.append(make("span", null, "本地专注监督"), make("b", null, "仅在这台电脑运行"));
  const main = make("section", "bz-main");
  main.append(make("span", null, "当前状态"), make("strong", null, status), make("small", null, "电脑关机后暂停更新"));
  const metrics = make("div", "bz-metrics");
  const rows = [
    ["运行位置", "这台电脑"],
    ["数据更新", "电脑开机时"],
    ["专注数据", focusReady ? "可读取" : "等待恢复"],
  ];
  for (const [label, value] of rows) {
    const metric = make("div", "bz-metric");
    metric.append(make("span", null, label), make("strong", null, value));
    metrics.append(metric);
  }
  const foot = make("footer", "bz-foot");
  foot.append(make("span", null, "数据范围"), make("b", null, "只读取本机数据"));
  console.append(head, main, metrics, foot);
  return console;
}

function cardPrimary(label, value, note = "", state = "online") {
  const primary = make("section", "card-primary");
  primary.dataset.status = state;
  const displayValue = value == null || value === "" ? "状态未知" : value;
  primary.append(make("span", null, label), make("strong", null, displayValue));
  if (note) primary.append(make("small", null, note));
  return primary;
}

function cardMetric(label, value, note = "") {
  const metric = make("div", "card-metric");
  const displayValue = value == null || value === "" ? "暂无数据" : value;
  metric.append(make("span", null, label), make("strong", null, displayValue));
  if (note) metric.append(make("small", null, note));
  return metric;
}

function cardSupport(...metrics) {
  const support = make("section", "card-support");
  support.append(...metrics);
  return support;
}

function cardDetails(title, rows) {
  const details = make("section", "card-details");
  details.append(make("header", null, title));
  const list = make("div", "card-detail-list");
  for (const [label, value, state = ""] of rows) {
    const row = make("div", "card-detail-row");
    if (state) row.dataset.status = state;
    const displayValue = value == null || value === "" ? "暂无信息" : value;
    row.append(make("span", null, label), make("strong", null, displayValue));
    list.append(row);
  }
  details.append(list);
  return details;
}

function gatewayConsoleV3(target, data, widgets) {
  const summary = data.summary || {};
  const fleet = data.fleet || {};
  const targets = Array.isArray(data.targets) ? data.targets : [];
  const system = widgets.find((widget) => widget.id === "personal_system");
  const sync = widgets.find((widget) => widget.id === "personal_sync");
  const systemData = system && system.ok && system.data ? system.data : null;
  const syncData = sync && sync.ok && sync.data ? sync.data : null;
  const localServiceCount = targets.filter((item) => item.mcp && item.mcp.ok).length;
  const totalServices = targets.length;
  const state = totalServices === 0
    ? "offline"
    : localServiceCount === totalServices
    ? "online"
    : localServiceCount > 0 ? "degraded" : "offline";
  const stateText = totalServices === 0
    ? "尚未发现本机服务"
    : state === "online" ? "本机服务正常" : state === "degraded" ? "部分服务需注意" : "有服务未运行";
  const serviceSummary = totalServices === 0
    ? "尚未读取到服务清单"
    : `${localServiceCount}/${totalServices} 个服务运行正常`;
  const guardRunning = Boolean(fleet.watchdog && fleet.watchdog.state === "running");
  const console = make("div", "gateway-console card-console");
  console.dataset.dataState = state;
  console.setAttribute("aria-label", "本机服务状态");
  console.append(
    cardPrimary("本机服务", stateText, serviceSummary, state),
    cardSupport(
      cardMetric("自动恢复", guardRunning ? "正常" : "需检查", guardRunning ? "持续看护中" : "暂未运行"),
      cardMetric("24 小时使用", `${compact(summary.calls24h)} 次`, `${compact(summary.failures24h)} 次未成功`),
      cardMetric("成功率", `${num(summary.successRate, 100).toFixed(1)}%`, "过去 24 小时"),
    ),
  );
  const serviceRows = targets.map((item) => {
    const style = PROJECT_STYLE[item.id] || {};
    const serviceOk = Boolean(item.mcp && item.mcp.ok);
    return [style.display || item.name || item.id, serviceOk ? "运行正常" : "未运行", serviceOk ? "online" : "offline"];
  });
  serviceRows.push(["自动恢复", guardRunning ? "运行正常" : "需检查", guardRunning ? "online" : "offline"]);
  if (systemData) {
    serviceRows.push([
      "控制中心",
      systemData.gatewayState === "online" ? "运行正常" : "正在启动",
      systemData.gatewayState === "online" ? "online" : "degraded",
    ]);
  }
  if (syncData) {
    const productCount = num(syncData.productCount);
    const freshCount = num(syncData.freshCount);
    const syncCurrent = productCount > 0 && freshCount === productCount;
    serviceRows.push([
      "数据连接",
      productCount ? `${freshCount}/${productCount} 项已确认` : "暂无连接数据",
      syncCurrent ? "online" : "degraded",
    ]);
  }
  console.append(cardDetails("各项本机服务", serviceRows));
  return console;
}

function watchConsoleV3(target, widgets) {
  const workouts = widgets.find((widget) => widget.id === "watch_workouts");
  const sleep = widgets.find((widget) => widget.id === "watch_sleep");
  const currentPlan = widgets.find((widget) => widget.id === "watch_current_plan");
  const status = widgets.find((widget) => widget.id === "watch_status");
  const workoutPairs = widgetPairs(workouts);
  const sleepPairs = widgetPairs(sleep);
  const planPairs = widgetPairs(currentPlan);
  const statusUnavailable = Boolean(status && (
    status.ok === false
    || status.availability === "unavailable"
    || (status.data && status.data.availability === "unavailable")
  ));
  const watchOnline = statusUnavailable
    ? false
    : status && status.ok && status.data
      ? status.data.watchOnline !== false
      : target.state === "online";
  const plan = planPairs.get("当前计划") || "暂无训练计划";
  const primaryValue = watchOnline ? plan : "设备未连接";
  const primaryNote = watchOnline ? "当前训练安排" : "请检查手机或手表连接";
  const state = watchOnline ? "online" : "offline";
  const console = make("div", "watch-console card-console");
  console.dataset.dataState = state;
  console.setAttribute("aria-label", "步序运动与睡眠数据");
  console.append(cardPrimary(watchOnline ? "当前计划" : "连接状态", primaryValue, primaryNote, state));
  if (!watchOnline) {
    console.append(
      cardSupport(
        cardMetric("本机服务", target.mcp && target.mcp.ok ? "运行正常" : "需检查"),
        cardMetric("设备数据", "等待连接", "后台会自动重试"),
      ),
      cardDetails("恢复连接", [
        ["当前情况", "手机或手表未连接", "offline"],
        ["本机服务", target.mcp && target.mcp.ok ? "运行正常" : "需检查", target.mcp && target.mcp.ok ? "online" : "offline"],
        ["恢复方式", "打开手机端并检查手表连接"],
      ]),
    );
    return console;
  }
  console.append(
    cardSupport(
      cardMetric(
        "累计距离",
        workoutPairs.get("总距离") || "暂无训练记录",
        workoutPairs.get("训练次数") ? `${workoutPairs.get("训练次数")} 次训练` : "暂无训练次数",
      ),
      cardMetric("睡眠评分", sleepPairs.get("睡眠评分") || "等待睡眠数据", sleepPairs.get("睡眠时长") || "等待更新"),
    ),
    cardDetails("训练与恢复", [
      ["活动时长", workoutPairs.get("累计活动") || "暂无活动记录"],
      ["平均心率", workoutPairs.get("平均心率") || "暂无心率记录"],
      ["睡眠时长", sleepPairs.get("睡眠时长") || "等待睡眠数据"],
      ["心率区间", sleepPairs.get("心率区间") || "暂无心率区间"],
    ]),
  );
  return console;
}

function focusConsoleV3(widgets) {
  const current = widgets.find((item) => item.id === "focus_current");
  const today = widgets.find((item) => item.id === "focus_today");
  const ready = Boolean(current && current.ok && current.data && current.availability !== "unavailable");
  const todayReady = Boolean(today && today.ok && today.data);
  const state = ready ? String(current.data.sessionState || "idle") : "offline";
  const stateLabel = { running: "正在专注", paused: "专注已暂停", idle: "当前空闲", stopped: "当前空闲" }[state] || "等待数据";
  const value = ready && current.data.value ? String(current.data.value) : "暂时不可用";
  const task = ready && (current.data.taskTitle || current.data.label)
    ? String(current.data.taskTitle || current.data.label)
    : "等待当前任务";
  const activeMinutes = ready ? num(current.data.activeMinutes) : 0;
  const todayLabel = todayReady && today.data.isToday === false
    ? `截至 ${today.data.sourceDate || "旧数据"}`
    : "今日累计";
  const console = make("div", "focus-console card-console");
  console.dataset.dataState = ready ? "online" : "offline";
  console.setAttribute("aria-label", "FocusLink 专注状态");
  console.append(
    cardPrimary("当前专注", value, stateLabel, ready ? "online" : "offline"),
    cardSupport(
      cardMetric("本次已专注", activeMinutes ? `${activeMinutes} 分钟` : "0 分钟", task),
      cardMetric(todayLabel, todayReady && today.data.value ? String(today.data.value) : "今日数据等待更新"),
    ),
    cardDetails("本次专注", [
      ["状态", stateLabel],
      ["任务", task],
      ["提示", ready && current.data.note ? String(current.data.note) : "恢复后会自动更新"],
    ]),
  );
  return console;
}

function journalConsoleV3(widgets) {
  const recent = widgets.find((item) => item.id === "journal_recent");
  const count = widgets.find((item) => item.id === "journal_count");
  const ready = Boolean(recent && recent.ok && recent.data);
  const items = ready && Array.isArray(recent.data.items) ? recent.data.items.slice(0, 4) : [];
  const total = count && count.ok && count.data && count.data.value != null ? String(count.data.value) : "暂无统计";
  const todayWritten = Boolean(ready && recent.data.todayWritten);
  const latest = items[0];
  const console = make("div", "journal-console card-console");
  console.dataset.dataState = ready ? "online" : "offline";
  console.setAttribute("aria-label", "拾光日记状态");
  console.append(
    cardPrimary("今日日记", ready ? (todayWritten ? "已经写过" : "还没有写") : "暂时不可用", ready ? "今天的记录状态" : "恢复后会自动更新", ready ? "online" : "offline"),
    cardSupport(
      cardMetric("日记总数", total),
      cardMetric("最近一篇", latest ? String(latest.value || latest.subtitle || "已记录") : "暂无记录", latest ? String(latest.title || "未命名记录") : ""),
    ),
    cardDetails("最近记录", items.length
      ? items.map((item) => [String(item.value || "日期未记录"), String(item.title || "未命名记录")])
      : [["记录", ready ? "还没有日记" : "等待数据"]]),
  );
  return console;
}

function bzsjkConsoleV3(target) {
  const item = target.projectItem || {};
  const rawStatus = String(item.value || "等待本机数据");
  const status = rawStatus === "工作区干净"
    ? "状态正常"
    : rawStatus.includes("未提交") ? "有待整理的更新" : rawStatus;
  const console = make("div", "bz-console card-console");
  console.dataset.dataState = "local";
  console.setAttribute("aria-label", "不做手机控本地监督状态");
  console.append(
    cardPrimary("项目状态", status, "来自本机 Git 工作区", "local"),
    cardSupport(
      cardMetric("工作区", rawStatus),
      cardMetric("当前分支", String(item.branch || "未读取")),
    ),
    cardDetails("本地项目", [
      ["最近提交", item.lastCommitAt ? dateTimeOf(item.lastCommitAt) : "未读取"],
      ["数据来源", "本机 Git 只读"],
      ["提交说明", String(item.subtitle || "暂无提交信息")],
    ]),
  );
  return console;
}

function projectCore(target, widgets, data) {
  const core = make("div", "proj-core");
  let label = "当前状态";
  let value = target.state === "online" ? "正常" : target.state === "degraded" ? "需注意" : "离线";

  if (target.id === "personal") {
    const summary = data.summary || {};
    label = "正常连接";
    value = `${num(summary.online)}/${num(summary.total)}`;
  } else if (target.id === "foxlink") {
    const focus = widgets.find((widget) => widget.id === "focus_current");
    label = "当前专注";
    if (focus && focus.ok && focus.data && focus.data.value) value = String(focus.data.value);
  } else if (target.id === "watch") {
    const watchStatus = widgets.find((widget) => widget.id === "watch_status");
    const watchUnavailable = Boolean(watchStatus && (
      watchStatus.ok === false
      || watchStatus.availability === "unavailable"
      || (watchStatus.data && watchStatus.data.availability === "unavailable")
    ));
    const plan = widgets.find((widget) => widget.id === "watch_current_plan");
    const pairs = widgetPairs(plan);
    label = watchUnavailable ? "连接状态" : "当前计划";
    value = watchUnavailable ? "手机未连接" : (pairs.get("当前计划") || value);
  } else if (target.id === "journal") {
    const recent = widgets.find((widget) => widget.id === "journal_recent");
    label = "今日日记";
    if (recent && recent.ok && recent.data) value = recent.data.todayWritten ? "已写" : "未写";
  } else if (target.id === "bzsjk") {
    label = "本地项目";
    value = target.projectItem && target.projectItem.value ? String(target.projectItem.value) : "仅本机";
  }

  core.append(make("span", null, label), make("strong", null, value));
  return core;
}

function projectDisplayState(target, widgets) {
  if (target.id === "bzsjk") return "local";
  if (target.id === "personal") return target.mcp && target.mcp.ok ? "online" : "offline";
  if (target.id === "foxlink") {
    const current = widgets.find((widget) => widget.id === "focus_current");
    if (current && current.ok && current.availability !== "unavailable") return "online";
    return target.state === "offline" ? "offline" : "degraded";
  }
  if (target.id === "journal") {
    const recent = widgets.find((widget) => widget.id === "journal_recent");
    if (recent && recent.ok && recent.data) return "online";
    return target.state === "offline" ? "offline" : "degraded";
  }
  if (target.id !== "watch") return target.state || "offline";
  const status = widgets.find((widget) => widget.id === "watch_status");
  const unavailable = Boolean(status && (
    status.ok === false
    || status.availability === "unavailable"
    || (status.data && status.data.availability === "unavailable")
  ));
  if (unavailable || (status && status.ok && status.data && status.data.watchOnline === false)) {
    return "offline";
  }
  return target.state || "offline";
}

function sectionDataCards(target, widgets, data) {
  const style = PROJECT_STYLE[target.id];
  const cards = [];
  if (target.id === "personal") {
    return [gatewayConsoleV3(target, data, widgets)];
  }
  if (target.id === "watch") {
    return [watchConsoleV3(target, widgets)];
  }
  if (target.id === "foxlink") {
    return [focusConsoleV3(widgets)];
  }
  if (target.id === "journal") {
    return [journalConsoleV3(widgets)];
  }
  if (target.id === "bzsjk") {
    return [bzsjkConsoleV3(target)];
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
      make("span", null, event.toState === "online" ? "已恢复" : `${plainState(event.fromState)} → ${plainState(event.toState)}`),
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
    personal: new Set(["personal_system", "personal_sync"]),
    foxlink: new Set(["focus_current", "focus_today"]),
    watch: new Set(["watch_workouts", "watch_sleep", "watch_current_plan", "watch_status"]),
    journal: new Set(["journal_recent", "journal_count"]),
    bzsjk: new Set(["bzsjk_project"]),
  };
  const ids = widgetIds[target.id] || new Set();
  const relevantWidgets = widgets.filter(
    (widget) => ids.has(widget.id) || style.groups.includes(widget.group || ""),
  );
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
  return JSON.stringify(structuralRenderValue([
    index,
    target,
    relevantWidgets,
    targetEvents,
    targetActivity,
    gatewayData,
  ]));
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
  const displayState = projectDisplayState(target, widgets);
  section.dataset.state = displayState;
  section.dataset.projectId = target.id;

  const head = make("header", "proj-head");
  if (CARD_ID === target.id) head.classList.add("pywebview-drag-region");
  const naming = make("div", "proj-naming");
  if (style.eyebrow) naming.append(make("span", "proj-eyebrow", style.eyebrow));
  naming.append(make("h2", null, style.display));
  const sub = make("p", "proj-tagline");
  sub.textContent = `${style.tagline}${target.version ? ` · v${String(target.version).replace(/^v/, "")}` : ""}`;
  naming.append(sub);
  const state = make("div", "proj-state");
  const dot = make("i", "dot");
  dot.dataset.status = displayState;
  const stateText = target.id === "watch" && displayState === "offline"
    ? "未连接"
    : ({ online: "正常", degraded: "需注意", offline: "离线", local: "仅本机" }[displayState] || "未知");
  state.append(dot, make("b", null, stateText));
  const grip = make("span", "tile-grip");
  grip.setAttribute("aria-hidden", "true");
  head.append(naming, state, projectCore(target, widgets, data));
  if (target.id === "personal") {
    const identity = make("div", "gw-identity");
    identity.append(
      make("span", null, "本机服务  /  端口 8761"),
      make("span", null, `自动更新  /  ${num(data.refreshIntervalSeconds, 4)} 秒`),
      make("span", null, "异常时尝试自动恢复"),
    );
    head.append(identity);
  }
  head.append(make("span", "proj-index", String(index + 1).padStart(2, "0")), grip);

  const vitals = make("div", "proj-vitals");
  if (target.mcp) vitals.append(probeChip("本机服务", target.mcp));
  if (target.tunnel) vitals.append(probeChip("安全连接", target.tunnel));
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
  editToolbar.setAttribute("aria-label", "卡片快捷尺寸");
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
    n: "调整卡片上边缘",
    ne: "调整卡片右上角",
    e: "调整卡片右边缘",
    se: "调整卡片右下角",
    s: "调整卡片下边缘",
    sw: "调整卡片左下角",
    w: "调整卡片左边缘",
    nw: "调整卡片左上角",
  };
  for (const [edge, label] of Object.entries(handleLabels)) {
    const handle = renderKey(make("button", `tile-handle tile-handle-${edge}`), `handle:${edge}`);
    handle.type = "button";
    handle.dataset.edge = edge;
    handle.title = label;
    handle.setAttribute("aria-label", label);
    section.append(handle);
  }
  if (CARD_ID === target.id) {
    const staleNote = renderKey(make("div", "card-stale-note", cardFreshness.label), "card-stale-note");
    staleNote.hidden = !cardFreshness.stale;
    section.append(staleNote);
    const controls = renderKey(make("div", "card-window-controls"), "card-controls");
    controls.setAttribute("aria-label", "卡片窗口控制");
    const move = renderKey(
      make("span", "card-window-move pywebview-drag-region", "::"),
      "card-control:move",
    );
    move.title = "拖动这张卡片";
    move.setAttribute("role", "button");
    move.setAttribute("aria-label", "拖动这张卡片");
    controls.append(move);
    const manage = renderKey(make("button", "card-window-manage", "管理"), "card-control:manage");
    manage.type = "button";
    manage.dataset.cardAction = "manage";
    manage.title = "打开卡片管理面板";
    manage.setAttribute("aria-label", manage.title);
    controls.append(manage);
    for (const preset of ["small", "medium", "large"]) {
      const label = { small: "小", medium: "中", large: "大" }[preset];
      const button = renderKey(make("button", "card-window-size", label), `card-size:${preset}`);
      button.type = "button";
      button.dataset.cardSize = preset;
      button.title = `${label}尺寸`;
      button.setAttribute("aria-label", `切换为${label}尺寸`);
      controls.append(button);
    }
    const reset = renderKey(make("button", "card-window-reset", "\u21ba"), "card-control:reset");
    reset.type = "button";
    reset.dataset.cardAction = "reset";
    reset.title = "恢复这张卡片的默认位置与大小";
    reset.setAttribute("aria-label", reset.title);
    const hide = renderKey(make("button", "card-window-hide", "关闭"), "card-control:hide");
    hide.type = "button";
    hide.dataset.cardAction = "hide";
    hide.title = "只隐藏这张卡片，可从管理面板或托盘恢复";
    hide.setAttribute("aria-label", hide.title);
    controls.append(reset, hide);
    const resizeCorner = renderKey(make("span", "card-window-resize-corner"), "card-resize-corner");
    resizeCorner.setAttribute("aria-hidden", "true");
    section.append(controls, resizeCorner);
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
  if (CARD_ID) {
    const selected = targets.find((target) => target.id === CARD_ID)
      || OFFLINE_MATRIX_TARGETS.find((target) => target.id === CARD_ID);
    targets.splice(0, targets.length);
    if (selected) targets.push(selected);
  }
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

  if (!CARD_ID) installLayoutChrome();
  const existing = new Map(projectTiles().map((section) => [section.dataset.projectId, section]));
  const nextSections = [];
  const added = [];
  let structureChanged = existing.size !== ordered.length;
  for (let index = 0; index < ordered.length; index += 1) {
    const target = ordered[index];
    const cardOrder = SECTION_ORDER.indexOf(target.id);
    const displayIndex = CARD_ID && cardOrder >= 0 ? cardOrder : index;
    const signature = projectSectionSignature(target, displayIndex, widgets, data);
    let section = existing.get(target.id);
    if (!section) {
      section = buildProjectSection(target, displayIndex, widgets, data);
      section._renderSignature = signature;
      added.push(section);
      structureChanged = true;
    } else if (section._renderSignature !== signature) {
      patchProjectSection(section, buildProjectSection(target, displayIndex, widgets, data));
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
    copy.append(make("p", null, `${plainState(event.fromState)} → ${plainState(event.toState)}`));
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

function businessAttentionItems(data, widgets) {
  const issues = [];
  const targets = Array.isArray(data.targets) ? data.targets : [];
  const watchTarget = targets.find((target) => target.id === "watch");
  if (watchTarget && projectDisplayState(watchTarget, widgets) === "offline") {
    issues.push("步序设备未连接");
  }

  const currentFocus = widgets.find((widget) => widget.id === "focus_current");
  const todayFocus = widgets.find((widget) => widget.id === "focus_today");
  if (!currentFocus || !currentFocus.ok || currentFocus.availability === "unavailable") {
    issues.push("专注数据等待更新");
  } else if (!todayFocus || !todayFocus.ok) {
    issues.push("今日专注等待更新");
  } else if (todayFocus.data && todayFocus.data.isToday === false) {
    issues.push(`今日专注截至 ${todayFocus.data.sourceDate || "旧数据"}`);
  }

  const journal = widgets.find((widget) => widget.id === "journal_recent");
  if (!journal || !journal.ok || journal.availability === "unavailable") {
    issues.push("日记数据等待更新");
  }

  const personalSync = widgets.find((widget) => widget.id === "personal_sync");
  if (personalSync && (!personalSync.ok || personalSync.availability === "unavailable")) {
    issues.push("数据连接等待更新");
  } else if (personalSync && personalSync.data) {
    const productCount = num(personalSync.data.productCount);
    const freshCount = num(personalSync.data.freshCount);
    if (personalSync.data.freshness === "stale" || (productCount > 0 && freshCount < productCount)) {
      issues.push("数据连接有待确认");
    }
  }

  const localProject = widgets.find((widget) => widget.id === "bzsjk_project");
  if (localProject && !localProject.ok) issues.push("本地项目状态等待更新");
  return issues;
}

function renderManagementScopes(data, widgets, businessAttention = businessAttentionItems(data, widgets)) {
  if (!dom.managerLocalState || !dom.managerBusinessState) return;
  const summary = data.summary || {};
  const fleet = data.fleet || {};
  const targets = Array.isArray(data.targets) ? data.targets : [];
  const total = targets.length;
  const online = targets.filter((target) => target.mcp && target.mcp.ok).length;
  const localHealthy = total > 0 && online === total;
  const guardRunning = Boolean(fleet.watchdog && fleet.watchdog.state === "running");
  const localState = localHealthy && guardRunning ? "online" : localHealthy ? "degraded" : "offline";
  dom.managerLocalState.parentElement.dataset.status = localState;
  setRenderText(
    dom.managerLocalState,
    localState === "online" ? "本机服务正常" : localState === "degraded" ? "服务正常，自动恢复需检查" : "有本机服务需处理",
  );
  setRenderText(
    dom.managerLocalHint,
    `${online}/${total} 个项目服务可用 · 自动恢复${guardRunning ? "正常" : "需检查"}`,
  );

  const businessState = businessAttention.length ? "degraded" : "online";
  dom.managerBusinessState.parentElement.dataset.status = businessState;
  setRenderText(
    dom.managerBusinessState,
    businessAttention.length ? `${businessAttention.length} 项数据需注意` : "业务数据已更新",
  );
  setRenderText(
    dom.managerBusinessHint,
    businessAttention.length
      ? businessAttention.slice(0, 3).join(" · ")
      : "专注、设备、日记和本地项目均可读取",
  );
}

function renderCardVisibility(visibility, visibleCardCount) {
  const total = SECTION_ORDER.length;
  const visible = Math.max(0, Math.min(total, visibleCardCount));
  if (dom.cardManagerSummary) {
    setRenderText(
      dom.cardManagerSummary,
      visible === 0 ? "5 张卡片当前全部隐藏" : `5 张卡片中已显示 ${visible} 张`,
    );
  }
  if (dom.cardSwitches) {
    for (const button of dom.cardSwitches.querySelectorAll("[data-card-toggle]")) {
      const isVisible = visibility[button.dataset.cardToggle] === true;
      button.setAttribute("aria-pressed", String(isVisible));
      const state = button.querySelector("b");
      if (state) setRenderText(state, isVisible ? "显示中" : "已隐藏");
    }
  }
  if (dom.btnShowAllCards) dom.btnShowAllCards.disabled = visible === total;
  if (dom.btnHideAllCards) dom.btnHideAllCards.disabled = visible === 0;
}

function applyView(view) {
  if (view.theme) root.dataset.theme = view.theme;
  if (!layoutMode && view.projectLayout && typeof view.projectLayout === "object") {
    projectLayout = view.projectLayout;
    const incomingVersion = Number(view.projectLayoutVersion);
    projectLayoutVersion = Number.isFinite(incomingVersion)
      ? Math.max(1, incomingVersion)
      : Object.keys(projectLayout).length ? 1 : PROJECT_LAYOUT_VERSION;
  }
  const cardMode = CARD_ID ? true : Boolean(view.cardMode);
  const hiddenCards = Array.isArray(view.hiddenCards) ? view.hiddenCards : [];
  const visibility = view.cardVisibility && typeof view.cardVisibility === "object"
    ? view.cardVisibility
    : Object.fromEntries(SECTION_ORDER.map((id) => [id, !hiddenCards.includes(id)]));
  const declaredVisibleCount = Number(view.visibleCardCount);
  const visibleCardCount = Number.isFinite(declaredVisibleCount)
    ? Math.max(0, declaredVisibleCount)
    : Object.values(visibility).filter(Boolean).length;
  renderCardVisibility(visibility, visibleCardCount);
  if (cardMode && dom.body.dataset.view !== "overview") selectView("overview");
  dom.body.classList.remove("compact");
  dom.body.classList.toggle("desktop-mode", cardMode);
  if (dom.btnDesktop) {
    const anyVisible = visibleCardCount > 0;
    const actionLabel = anyVisible ? "隐藏桌面卡片" : "显示桌面卡片";
    dom.btnDesktop.dataset.visibleCardCount = String(visibleCardCount);
    dom.btnDesktop.textContent = actionLabel;
    dom.btnDesktop.title = anyVisible
      ? `当前显示 ${visibleCardCount} 张，点击全部隐藏`
      : "点击显示全部桌面卡片";
    dom.btnDesktop.setAttribute("aria-label", actionLabel);
    dom.btnDesktop.setAttribute("aria-pressed", String(anyVisible));
  }
  if (dom.titlebarDrag) {
    dom.titlebarDrag.classList.toggle("pywebview-drag-region", !cardMode);
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

function contentClassForTile(projectId, width, height) {
  const base = CARD_ID ? CARD_BASE_SIZES[projectId] : null;
  if (base) {
    // Native card presets are physical HWND pixels, while WebView layout sizes
    // are CSS pixels. Normalize only card windows so 150%/200% DPI does not
    // silently demote medium and large presets by one content tier.
    const deviceScale = Math.max(1, num(window.devicePixelRatio, 1));
    const scale = Math.min(
      width * deviceScale / base.width,
      height * deviceScale / base.height,
    );
    if (scale < 0.84) return "small";
    if (scale < 1.18) return "medium";
    return "large";
  }
  const area = width * height;
  if (width < 260 || height < 190 || area < 60000) return "small";
  if (width < 500 || height < 310 || area < 180000) return "medium";
  return "large";
}

function setTileDensity(tile, width, height) {
  const area = width * height;
  const contentClass = contentClassForTile(tile.dataset.projectId, width, height);
  const summaryOnly = contentClass === "small";
  const micro = (width < 145 && height < 125) || area < 15500;
  const widthClass = width < 280 ? "small" : width < 480 ? "medium" : "large";
  const heightClass = height < 150 ? "short" : height < 225 ? "medium" : "tall";
  const narrow = width < 390;
  const densityKey = `${contentClass}:${widthClass}:${heightClass}:${narrow ? 1 : 0}:${micro ? 1 : 0}`;
  const densityChanged = Boolean(tile.dataset.densityKey && tile.dataset.densityKey !== densityKey);
  tile.dataset.densityKey = densityKey;
  tile.dataset.widthClass = widthClass;
  tile.dataset.heightClass = heightClass;
  tile.dataset.contentClass = contentClass;
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
  if (CARD_ID) {
    const width = Math.max(1, document.documentElement.clientWidth);
    const height = Math.max(1, document.documentElement.clientHeight);
    projectViewportWidth = width;
    projectViewportHeight = height;
    projectCanvasWidth = width;
    dom.projectSections.style.width = `${width}px`;
    dom.projectSections.style.height = `${height}px`;
    return;
  }
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

function desktopRegionList() {
  const regions = [];
  for (const tile of projectTiles()) {
    const bounds = tile.getBoundingClientRect();
    if (bounds.width < 1 || bounds.height < 1) continue;
    const radius = Number.parseFloat(window.getComputedStyle(tile).borderTopLeftRadius) || 0;
    regions.push({ x: bounds.left, y: bounds.top, width: bounds.width, height: bounds.height, radius });
  }
  const controls = dom.body.classList.contains("desktop-mode") ? document.querySelector(".widget-controls") : null;
  if (controls) {
    const bounds = controls.getBoundingClientRect();
    if (bounds.width > 1 && bounds.height > 1) {
      const radius = Number.parseFloat(window.getComputedStyle(controls).borderTopLeftRadius) || 0;
      regions.push({ x: bounds.left, y: bounds.top, width: bounds.width, height: bounds.height, radius });
    }
  }
  return regions;
}

function queueDesktopRegionSync(delay = 0) {
  if (CARD_ID) return;
  if (!dom.body.classList.contains("desktop-mode")) return;
  if (desktopRegionTimer) window.clearTimeout(desktopRegionTimer);
  if (delay > 0) {
    desktopRegionTimer = window.setTimeout(() => {
      desktopRegionTimer = 0;
      queueDesktopRegionSync();
    }, delay);
    return;
  }
  if (desktopRegionFrame) return;
  desktopRegionFrame = window.requestAnimationFrame(() => {
    desktopRegionFrame = 0;
    const bridge = api();
    if (!bridge || !bridge.set_desktop_regions) return;
    const editing = layoutMode;
    const regions = editing ? [] : desktopRegionList();
    Promise.resolve(bridge.set_desktop_regions(regions, editing)).catch(() => {});
  });
}

function applyProjectLayout(animate = false) {
  if (CARD_ID) {
    const viewportWidth = Math.max(1, document.documentElement.clientWidth);
    const viewportHeight = Math.max(1, document.documentElement.clientHeight);
    projectViewportWidth = viewportWidth;
    projectViewportHeight = viewportHeight;
    projectCanvasWidth = viewportWidth;
    dom.projectSections.style.width = `${viewportWidth}px`;
    dom.projectSections.style.height = `${viewportHeight}px`;
    dom.projectSections.classList.add("free-layout", "card-layout");
    const tile = projectTiles().find((candidate) => candidate.dataset.projectId === CARD_ID);
    if (!tile) return;
    tile.style.zIndex = "1";
    setTileRect(tile, {
      left: 0,
      top: 0,
      width: viewportWidth,
      height: viewportHeight,
    });
    if (animate) {
      playRenderAnimation(
        tile,
        [
          { opacity: 0, transform: "translateY(7px) scale(.985)" },
          { opacity: 1, transform: "translateY(0) scale(1)" },
        ],
        { duration: 260, easing: "cubic-bezier(.2,.8,.2,1)", fill: "both" },
      );
    }
    return;
  }
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
  queueDesktopRegionSync();
  queueDesktopRegionSync(420);
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
  if (CARD_ID) {
    layoutMode = false;
    dom.body.classList.remove("layout-mode");
    return;
  }
  const changed = dom.body.classList.contains("layout-mode") !== layoutMode;
  dom.body.classList.toggle("layout-mode", layoutMode);
  const layoutButton = el("btnLayout");
  if (layoutButton) {
    layoutButton.setAttribute("aria-pressed", String(layoutMode));
    layoutButton.title = layoutMode ? "完成并保存卡片布局" : "编辑卡片布局";
    layoutButton.setAttribute("aria-label", layoutButton.title);
  }
  const widgetEditButton = el("btnWidgetEdit");
  if (widgetEditButton) {
    widgetEditButton.textContent = layoutMode ? "完成编辑" : "编辑卡片";
    widgetEditButton.title = layoutMode ? "完成并保存卡片布局" : "移动或缩放卡片";
    widgetEditButton.setAttribute("aria-pressed", String(layoutMode));
  }
  if (dom.matrixFitState) {
    dom.matrixFitState.textContent = layoutMode ? "拖动卡片 · 边角缩放 · 自动保存" : "自动排列";
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
  queueDesktopRegionSync();
  queueDesktopRegionSync(360);
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
    const cardControl = event.target.closest("[data-card-size], [data-card-action]");
    if (cardControl && CARD_ID) {
      event.preventDefault();
      event.stopPropagation();
      const bridge = api();
      if (!bridge) return;
      if (cardControl.dataset.cardSize && bridge.set_size) {
        bridge.set_size(cardControl.dataset.cardSize).catch(() => {});
      } else if (cardControl.dataset.cardAction === "manage" && bridge.open_management) {
        bridge.open_management().catch(() => {});
      } else if (cardControl.dataset.cardAction === "reset" && bridge.reset_geometry) {
        bridge.reset_geometry().catch(() => {});
      } else if (cardControl.dataset.cardAction === "hide" && bridge.hide_card) {
        bridge.hide_card().catch(() => {});
      }
      return;
    }
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
  if (dom.btnRepairOffline) {
    dom.btnRepairOffline.disabled = false;
    dom.btnRepairOffline.textContent = "尝试自动修复";
  }
}

async function requestRepair(button) {
  const bridge = api();
  if (!bridge || !bridge.repair_fleet || repairInFlight) return;
  repairInFlight = true;
  if (dom.btnRepairOffline) dom.btnRepairOffline.disabled = true;
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

async function setAllCardsVisible(visible, control) {
  const bridge = api();
  if (!bridge || !bridge.set_all_cards_visible) return;
  if (control) control.disabled = true;
  let payload = null;
  try {
    payload = await bridge.set_all_cards_visible(Boolean(visible));
  } catch (error) {
    setRenderText(dom.sbState, "卡片开关失败，请重试");
  } finally {
    if (control) control.disabled = false;
  }
  if (payload) render(payload);
}

function bindControls() {
  bindTileEditing();
  bindCanvasPanning();
  document.addEventListener("pointerdown", requestWindowResize, true);
  for (const button of document.querySelectorAll(".view-tab")) {
    button.addEventListener("click", () => selectView(button.dataset.view));
  }
  if (dom.btnRefresh) dom.btnRefresh.addEventListener("click", () => { void pull(true); });
  el("btnRetry").addEventListener("click", () => { void pull(true); });
  if (dom.btnRepairOffline) {
    dom.btnRepairOffline.addEventListener("click", () => requestRepair(dom.btnRepairOffline));
  }
  if (dom.cardSwitches) {
    dom.cardSwitches.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-card-toggle]");
      if (!button) return;
      const bridge = api();
      if (!bridge || !bridge.set_card_visible) return;
      const next = button.getAttribute("aria-pressed") !== "true";
      button.disabled = true;
      try {
        const payload = await bridge.set_card_visible(button.dataset.cardToggle, next);
        if (payload) render(payload);
      } catch (error) {
        setRenderText(dom.sbState, "这张卡片开关失败，请重试");
      } finally {
        button.disabled = false;
      }
    });
  }
  if (dom.btnShowAllCards) {
    dom.btnShowAllCards.addEventListener("click", () => {
      void setAllCardsVisible(true, dom.btnShowAllCards);
    });
  }
  if (dom.btnHideAllCards) {
    dom.btnHideAllCards.addEventListener("click", () => {
      void setAllCardsVisible(false, dom.btnHideAllCards);
    });
  }
  el("btnMin").addEventListener("click", () => api() && api().minimize());
  el("btnClose").addEventListener("click", () => api() && api().hide_to_tray());
  dom.btnDesktop.addEventListener("click", () => {
    const anyVisible = num(dom.btnDesktop.dataset.visibleCardCount) > 0;
    void setAllCardsVisible(!anyVisible, dom.btnDesktop);
  });
}

function schedulePoll(delay = POLL_MS) {
  const generation = ++pollGeneration;
  if (timer) window.clearTimeout(timer);
  timer = null;
  if (document.hidden) return;
  timer = window.setTimeout(async () => {
    timer = null;
    await pull(false);
    if (generation === pollGeneration && !document.hidden) schedulePoll(POLL_MS);
  }, delay);
}

function start() {
  if (bridgeReady) return;
  bridgeReady = true;
  bindControls();
  updateClock();
  schedulePoll(0);
  clockTimer = window.setInterval(updateClock, 30000);
}

window.addEventListener("pywebviewready", start);
if (api()) start();
window.addEventListener("resize", markWindowResizing);
document.addEventListener("visibilitychange", () => schedulePoll(0));
window.addEventListener("beforeunload", () => {
  pollGeneration += 1;
  if (timer) window.clearTimeout(timer);
  if (clockTimer) window.clearInterval(clockTimer);
  if (windowResizeTimer) window.clearTimeout(windowResizeTimer);
  if (projectResizeFrame) window.cancelAnimationFrame(projectResizeFrame);
  if (interactionScrollFrame) window.cancelAnimationFrame(interactionScrollFrame);
  if (canvasPanFrame) window.cancelAnimationFrame(canvasPanFrame);
});
