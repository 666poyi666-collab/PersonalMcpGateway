(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const icons = {
    gateway: '<svg viewBox="0 0 24 24"><path d="M5 7.5h14M5 16.5h14M8 4v7m8 2v7M5 12h14"/></svg>',
    watch: '<svg viewBox="0 0 24 24"><rect x="6.5" y="5" width="11" height="14" rx="4"/><path d="M9 5V2.5h6V5m-6 14v2.5h6V19m-5.5-7h5"/></svg>',
    link: '<svg viewBox="0 0 24 24"><path d="M9.5 14.5l5-5m-7.7 8.2-1 1a3.5 3.5 0 0 1-5-5l3-3a3.5 3.5 0 0 1 5 0m6.4 2.6a3.5 3.5 0 0 1 0-5l3-3a3.5 3.5 0 0 1 5 5l-1 1"/></svg>',
    journal: '<svg viewBox="0 0 24 24"><path d="M5 3.5h11a3 3 0 0 1 3 3v14H8a3 3 0 0 1-3-3v-14Z"/><path d="M8 20.5a3 3 0 0 1 0-6h11M9 8h6m-6 3h4"/></svg>',
    service: '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="6" rx="2"/><rect x="4" y="14" width="16" height="6" rx="2"/><path d="M8 7h.01M8 17h.01"/></svg>'
  };
  let refreshTimer;
  let toastTimer;
  let refreshInFlight = false;

  function text(node, value) { node.textContent = String(value); }
  function compact(value) { return new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(value || 0); }
  function uptime(seconds) {
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return days ? `${days}天 ${hours}时` : hours ? `${hours}时 ${minutes}分` : `${minutes}分钟`;
  }
  function relative(date) {
    if (!date) return "刚刚";
    const normalized = /(?:Z|[+-]\d\d:\d\d)$/.test(date) ? date : `${date}Z`;
    const seconds = Math.max(0, Math.round((Date.now() - new Date(normalized)) / 1000));
    if (seconds < 60) return `${seconds} 秒前`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
    return `${Math.floor(seconds / 86400)} 天前`;
  }
  function stateLabel(state) { return ({ online: "ONLINE", degraded: "ATTENTION", offline: "OFFLINE" })[state] || "UNKNOWN"; }
  function syncLabel(state) {
    return ({ fresh: "已验证", stale: "已过期", offline: "离线", blocked: "受阻", unknown: "未知" })[state] || "未知";
  }
  function syncBlocker(reason) {
    return ({
      authority_not_observed: "未取得权威验证",
      implementation_incomplete: "实现尚未完成",
      pc_off_acceptance_pending: "PC-off 验收未通过",
      pc_runtime_required: "需要本机运行",
      snapshot_incomplete: "存在未完成同步",
      local_mcp_unreachable: "本地服务不可达",
      cloud_push_failed: "云端写入失败",
      local_data_unavailable: "本地数据不可用",
      local_items_unavailable: "部分项目不可用",
      no_local_entries: "暂无可同步数据",
    })[reason] || "未提供阻断原因";
  }
  function verifiedLabel(value) { return value ? relative(value) : "未验证"; }
  function component(label, data) {
    const row = document.createElement("div");
    row.className = `component ${data && data.ok ? "ok" : ""}`;
    const dot = document.createElement("i");
    const name = document.createElement("span"); name.textContent = label;
    const detail = document.createElement("em");
    const service = data && data.service ? data.service : null;
    if (data && data.ok) {
      detail.textContent = data.latencyMs == null ? "READY" : `${data.latencyMs}ms`;
    } else if (service && service.state && service.state !== "running") {
      detail.textContent = service.state === "missing" ? "NOT INSTALLED" : "SVC STOPPED";
    } else {
      detail.textContent = "DOWN";
    }
    if (service && service.name) row.title = `${service.name} · ${service.state || "unknown"}`;
    row.append(dot, name, detail);
    return row;
  }
  const PROJECT_STYLE = {
    foxlink: { flavor: "instrument", accent: "#007A55", display: "FocusLink", tagline: "专注 · 时间仪器", groups: ["FocusLink"] },
    watch: { flavor: "sport", accent: "#B6FF39", display: "步序 · 间歇跑", tagline: "训练 · 睡眠 · 手表", groups: ["步序 · 间歇跑"] },
    journal: { flavor: "paper", accent: "#A85F27", display: "拾光 · 日记复盘", tagline: "记录 · 回看 · 复盘", groups: ["拾光日记"] },
    personal: { flavor: "neutral", accent: "#7c6cff", display: "Personal Gateway", tagline: "总机房 · 隧道与看护", groups: [] },
  };
  const SECTION_ORDER = ["foxlink", "watch", "journal", "personal"];
  const CLAIMED_GROUPS = new Set(Object.values(PROJECT_STYLE).flatMap((s) => s.groups));
  let lastSectionsKey = "";

  function probeChip(label, probe) {
    const chip = document.createElement("div"); chip.className = "pv-chip";
    const dot = document.createElement("i"); dot.className = "dot";
    dot.dataset.status = probe && probe.ok ? "online" : "offline";
    const service = probe && probe.service ? probe.service : null;
    let detail = "不可用";
    if (probe && probe.ok) detail = probe.latencyMs == null ? "就绪" : `${probe.latencyMs}ms`;
    else if (service && service.state && service.state !== "running") detail = service.state === "missing" ? "服务未安装" : "服务已停止";
    if (service && service.name) chip.title = `${service.name} · ${service.state || "unknown"}`;
    const name = document.createElement("span"); name.textContent = label;
    const em = document.createElement("em"); em.textContent = detail;
    chip.append(dot, name, em);
    return chip;
  }
  function syncChip(sync) {
    const truth = sync && sync.truth ? sync.truth : { state: "unknown", lastVerifiedAt: null };
    const chip = document.createElement("div"); chip.className = "pv-chip sync-chip";
    chip.dataset.syncState = truth.state || "unknown";
    const dot = document.createElement("i"); dot.className = "dot";
    dot.dataset.status = truth.state === "fresh" ? "online" : truth.state === "stale" ? "degraded" : "offline";
    const name = document.createElement("span"); name.textContent = "同步";
    const detail = document.createElement("em"); detail.textContent = syncLabel(truth.state);
    chip.title = truth.blockerReason ? syncBlocker(truth.blockerReason) : `最后验证 ${verifiedLabel(truth.lastVerifiedAt)}`;
    chip.append(dot, name, detail);
    return chip;
  }
  function syncFact(label, value, priority = "secondary") {
    const card = document.createElement("article"); card.className = "pd-stat sync-fact"; card.dataset.priority = priority;
    const strong = document.createElement("strong"); strong.textContent = value;
    const span = document.createElement("span"); span.textContent = label;
    card.append(strong, span);
    return card;
  }
  function renderProjects(targets, widgets, data) {
    const grid = $("projectGrid");
    const key = JSON.stringify([targets, widgets, data.events, data.gateway, data.fleet]);
    if (key === lastSectionsKey) return;
    lastSectionsKey = key;
    grid.replaceChildren();
    const byId = new Map(targets.map((t) => [t.id, t]));
    const ordered = [];
    SECTION_ORDER.forEach((id) => { if (byId.has(id)) ordered.push(byId.get(id)); });
    targets.forEach((t) => { if (!SECTION_ORDER.includes(t.id)) ordered.push(t); });
    ordered.forEach((target) => {
      const style = PROJECT_STYLE[target.id] || { flavor: "neutral", accent: target.accent || "#8878ff", display: target.name, tagline: target.description || "", groups: [] };
      const section = document.createElement("section");
      section.className = `proj proj-${style.flavor}`;
      section.style.setProperty("--p-accent", style.accent);
      section.dataset.state = target.state || "offline";

      const head = document.createElement("header"); head.className = "proj-head";
      const naming = document.createElement("div");
      const h2 = document.createElement("h2"); h2.textContent = style.display;
      const tagline = document.createElement("p"); tagline.className = "proj-tagline";
      tagline.textContent = `${style.tagline}${target.version ? ` · v${String(target.version).replace(/^v/, "")}` : ""}`;
      naming.append(h2, tagline);
      const state = document.createElement("div"); state.className = "proj-state";
      const sdot = document.createElement("i"); sdot.className = "dot"; sdot.dataset.status = target.state || "offline";
      const slabel = document.createElement("b"); slabel.textContent = stateLabel(target.state);
      state.append(sdot, slabel);
      head.append(naming, state);

      const vitals = document.createElement("div"); vitals.className = "proj-vitals";
      vitals.append(probeChip("MCP", target.mcp));
      if (target.tunnel) vitals.append(probeChip("隧道", target.tunnel));
      if (target.sync) vitals.append(syncChip(target.sync));

      const dataZone = document.createElement("div"); dataZone.className = "proj-data";
      if (target.sync) {
        const truth = target.sync.truth || {};
        dataZone.append(
          syncFact("同步状态", syncLabel(truth.state), "primary"),
          syncFact("最后验证", verifiedLabel(truth.lastVerifiedAt)),
          syncFact("待同步", truth.pendingCount == null ? "—" : String(truth.pendingCount)),
          syncFact("阻断原因", truth.blockerReason ? syncBlocker(truth.blockerReason) : "无"),
        );
      }
      if (target.id === "personal") {
        const gw = data.gateway || {};
        const fleet = data.fleet || {};
        [["本次在线", uptime(gw.uptimeSeconds || 0)], ["累计调用", compact(gw.callsTotal)], ["调用失败", compact(gw.callsFailed)], ["看护服务", fleet.watchdog && fleet.watchdog.state === "running" ? "在线" : "异常"]].forEach(([label, value], index) => {
          const card = document.createElement("article"); card.className = "pd-stat";
          card.dataset.priority = index < 2 ? "primary" : "secondary";
          const strong = document.createElement("strong"); strong.textContent = String(value);
          const span = document.createElement("span"); span.textContent = label;
          card.append(strong, span); dataZone.append(card);
        });
      } else {
        const mine = (widgets || []).filter((w) => style.groups.includes(w.group || ""));
        mine.forEach((widget, index) => {
          const card = document.createElement("article"); card.className = `pd-widget${widget.ok ? "" : " error"}`;
          card.dataset.priority = index < 2 ? "primary" : "secondary";
          const whead = document.createElement("div"); whead.className = "pd-widget-head";
          const wtitle = document.createElement("strong"); wtitle.textContent = widget.title || widget.id; whead.append(wtitle);
          if (widget.subtitle) { const ws = document.createElement("small"); ws.textContent = widget.subtitle; whead.append(ws); }
          card.append(whead, widgetBody(widget)); dataZone.append(card);
        });
        if (!mine.length) {
          const empty = document.createElement("p"); empty.className = "pd-empty";
          empty.textContent = "尚未配置数据卡 — 编辑 board-widgets.yaml 即可点亮";
          dataZone.append(empty);
        }
      }

      section.append(head, vitals, dataZone);
      grid.append(section);
    });
  }
  function renderProjectsLegacy(targets) {
    const grid = $("projectGrid"); grid.replaceChildren();
    targets.forEach((target) => {
      const card = document.createElement("article");
      card.className = `project-card ${target.state}`;
      card.style.setProperty("--accent", target.accent || "#8878ff");
      const top = document.createElement("div"); top.className = "project-top";
      const icon = document.createElement("span"); icon.className = "project-icon"; icon.innerHTML = icons[target.icon] || icons.service;
      const pill = document.createElement("span"); pill.className = "state-pill"; pill.textContent = stateLabel(target.state);
      top.append(icon, pill);
      const title = document.createElement("div"); title.className = "project-name";
      const name = document.createElement("strong"); name.textContent = target.name;
      const version = document.createElement("small"); version.textContent = target.version ? `v${target.version}` : "";
      title.append(name, version);
      const description = document.createElement("p"); description.className = "project-desc"; description.textContent = target.description;
      const components = document.createElement("div"); components.className = "component-list";
      components.append(component("MCP Server", target.mcp));
      if (target.tunnel) components.append(component("Secure Tunnel", target.tunnel));
      card.append(top, title, description, components); grid.append(card);
    });
  }
  function renderChart(hourly) {
    const chart = $("activityChart"); chart.replaceChildren();
    const max = Math.max(1, ...hourly.map((item) => Number(item.calls)));
    hourly.forEach((item) => {
      const group = document.createElement("div"); group.className = "bar-group";
      const hour = new Date(item.bucket).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
      group.dataset.tip = `${hour} · ${item.calls} 次 / ${item.failures} 失败`;
      const calls = document.createElement("span"); calls.className = "bar"; calls.style.height = `${Math.max(2, item.calls / max * 100)}%`;
      group.append(calls);
      if (item.failures) { const failures = document.createElement("span"); failures.className = "bar fail"; failures.style.height = `${Math.max(4, item.failures / max * 100)}%`; group.append(failures); }
      chart.append(group);
    });
  }
  function renderActivity(items) {
    const list = $("activityList"); list.replaceChildren();
    if (!items.length) { const empty = document.createElement("div"); empty.className = "empty-state"; empty.textContent = "暂无调用记录"; list.append(empty); return; }
    items.forEach((item) => {
      const row = document.createElement("div"); row.className = "activity-row";
      const mark = document.createElement("span"); mark.className = "activity-mark"; mark.textContent = "↗";
      const info = document.createElement("div"); info.className = "activity-info";
      const tool = document.createElement("strong"); tool.textContent = item.tool;
      const module = document.createElement("small"); module.textContent = `${item.module} · ${relative(item.createdAt)}`;
      info.append(tool, module);
      const meta = document.createElement("div"); meta.className = "activity-meta";
      const result = document.createElement("b"); result.textContent = item.result === "success" ? "SUCCESS" : item.result; if (item.result !== "success") result.className = "failed";
      const duration = document.createElement("span"); duration.textContent = `${item.durationMs}ms`;
      meta.append(result, duration); row.append(mark, info, meta); list.append(row);
    });
  }
  function renderEvents(errors, events) {
    const list = $("eventList"); list.replaceChildren(); text($("errorCount"), errors.length + events.length);
    if (!errors.length && !events.length) { const empty = document.createElement("div"); empty.className = "empty-state good"; empty.textContent = "当前没有异常"; list.append(empty); return; }
    events.forEach((item) => {
      const row = document.createElement("div");
      row.className = `event-row ${item.toState === "online" ? "recovered" : "changed"}`;
      const dot = document.createElement("i");
      const copy = document.createElement("div"); copy.className = "event-copy";
      const title = document.createElement("strong"); title.textContent = item.toState === "online" ? `${item.name} 已恢复` : `${item.name} 状态变化`;
      const body = document.createElement("p"); body.textContent = `${stateLabel(item.fromState)} → ${stateLabel(item.toState)}`;
      copy.append(title, body);
      const time = document.createElement("time"); time.textContent = relative(item.occurredAt);
      row.append(dot, copy, time); list.append(row);
    });
    errors.forEach((item) => {
      const row = document.createElement("div"); row.className = "event-row";
      const dot = document.createElement("i");
      const copy = document.createElement("div"); copy.className = "event-copy";
      const title = document.createElement("strong"); title.textContent = `${item.module_id} · ${item.code}`;
      const body = document.createElement("p"); body.textContent = item.message;
      copy.append(title, body);
      const time = document.createElement("time"); time.textContent = relative(item.created_at);
      row.append(dot, copy, time); list.append(row);
    });
  }
  function widgetBody(widget) {
    const body = document.createElement("div"); body.className = "widget-body";
    if (!widget.ok) {
      const err = document.createElement("p"); err.className = "widget-error"; err.textContent = widget.error || "模块出错";
      body.append(err); return body;
    }
    const data = widget.data || {};
    if (widget.kind === "stat") {
      const wrap = document.createElement("div"); wrap.className = "widget-stat";
      const value = document.createElement("strong"); value.textContent = data.value ?? "—";
      const label = document.createElement("span"); label.textContent = data.label || "";
      wrap.append(value, label);
      if (data.note) { const note = document.createElement("small"); note.textContent = data.note; wrap.append(note); }
      body.append(wrap);
    } else if (widget.kind === "keyvalue") {
      const rows = document.createElement("div"); rows.className = "widget-kv";
      (data.pairs || []).forEach((pair) => {
        const row = document.createElement("div");
        const label = document.createElement("span"); label.textContent = pair.label;
        const value = document.createElement("b"); value.textContent = pair.value;
        row.append(label, value); rows.append(row);
      });
      body.append(rows);
    } else if (widget.kind === "list") {
      const items = data.items || [];
      if (!items.length) {
        const empty = document.createElement("div"); empty.className = "widget-empty"; empty.textContent = data.empty || "暂无内容";
        body.append(empty); return body;
      }
      const rows = document.createElement("div"); rows.className = "widget-rows";
      items.forEach((item) => {
        const row = document.createElement("div"); row.className = "widget-row";
        if (item.state) { const dot = document.createElement("i"); dot.className = `w-state ${item.state}`; row.append(dot); }
        const info = document.createElement("div"); info.className = "widget-row-info";
        const title = document.createElement("strong"); title.textContent = item.title || ""; info.append(title);
        if (item.subtitle) { const sub = document.createElement("small"); sub.textContent = item.subtitle; info.append(sub); }
        row.append(info);
        if (item.value != null) { const value = document.createElement("em"); value.textContent = item.value; row.append(value); }
        rows.append(row);
      });
      body.append(rows);
    } else {
      const text = document.createElement("div"); text.className = "widget-text";
      String(data.body || "").split("\n").filter(Boolean).forEach((line) => {
        const paragraph = document.createElement("p"); paragraph.textContent = line; text.append(paragraph);
      });
      body.append(text);
    }
    return body;
  }
  function renderWidgets(allWidgets) {
    const widgets = (allWidgets || []).filter((w) => !CLAIMED_GROUPS.has(w.group || ""));
    const grid = $("widgetGrid"); grid.replaceChildren();
    let currentGroup = null;
    widgets.forEach((widget) => {
      const group = widget.group || null;
      if (group !== currentGroup) {
        currentGroup = group;
        if (group) {
          const head = document.createElement("div"); head.className = "widget-group-head";
          if (widget.accent) head.style.setProperty("--w-accent", widget.accent);
          const dot = document.createElement("i");
          const label = document.createElement("span"); label.textContent = group;
          head.append(dot, label); grid.append(head);
        }
      }
      const card = document.createElement("article"); card.className = `widget-card flavor-${widget.flavor || "neutral"}${widget.ok ? "" : " error"}`;
      if (widget.accent) card.style.setProperty("--w-accent", widget.accent);
      const head = document.createElement("div"); head.className = "widget-head";
      const heading = document.createElement("div");
      const title = document.createElement("strong"); title.textContent = widget.title; heading.append(title);
      if (widget.subtitle) { const sub = document.createElement("small"); sub.textContent = widget.subtitle; heading.append(sub); }
      const chip = document.createElement("span"); chip.className = "widget-chip"; chip.textContent = widget.type;
      head.append(heading, chip);
      card.append(head, widgetBody(widget)); grid.append(card);
    });
  }
  function showToast(message) {
    const toast = $("toast"); toast.textContent = message; toast.classList.add("show");
    clearTimeout(toastTimer); toastTimer = setTimeout(() => toast.classList.remove("show"), 5000);
  }
  function applySnapshot(data) {
    const summary = data.summary;
    text($("onlineCount"), `${summary.online}/${summary.total}`);
    text($("onlineSub"), summary.offline ? `${summary.offline} 个项目离线` : summary.degraded ? `${summary.degraded} 个项目需要注意` : "所有独立项目运行正常");
    $("onlineTrack").style.width = `${summary.total ? summary.online / summary.total * 100 : 0}%`;
    text($("callsCount"), compact(summary.calls24h));
    text($("successRate"), `${Number(summary.successRate).toFixed(1)}%`);
    text($("failureSub"), `失败请求 ${summary.failures24h}`);
    text($("uptime"), uptime(data.gateway.uptimeSeconds));
    text($("versionLabel"), `v${data.gateway.version}`);
    text($("lastUpdated"), `探测 ${data.probeDurationMs}ms · ${new Date(data.generatedAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`);
    text($("chartTotal"), compact(summary.calls24h));
    renderProjects(data.targets, data.widgets || [], data); renderChart(data.activity.hourly); renderActivity(data.activity.recent); renderEvents(data.errors, data.events || []); renderWidgets(data.widgets || []);
    $("syncState").classList.remove("offline"); $("syncState").querySelector("b").textContent = "实时连接";
    text($("footerState"), "CONNECTED");
    const guard = data.fleet && data.fleet.watchdog ? data.fleet.watchdog.state : null;
    const guardNode = $("footerGuard");
    if (guardNode) {
      guardNode.textContent = guard === "running" ? "看护在线"
        : guard === "missing" ? "看护未安装"
        : guard ? "看护离线" : "看护 —";
      guardNode.className = guard === "running" ? "guard-ok" : guard ? "guard-bad" : "";
    }
    if (data.configWarning) showToast(data.configWarning);
  }
  async function refresh(force = false) {
    if (refreshInFlight) return;
    refreshInFlight = true;
    $("refreshNow").classList.add("loading");
    try {
      const response = await fetch(`/admin/dashboard-data${force ? "?force=1" : ""}`, { headers: { Accept: "application/json" }, cache: "no-store" });
      if (!response.ok) throw new Error("dashboard unavailable");
      applySnapshot(await response.json());
    } catch (_) {
      $("syncState").classList.add("offline"); $("syncState").querySelector("b").textContent = "连接中断";
      text($("footerState"), "RECONNECTING");
    } finally {
      refreshInFlight = false;
      $("refreshNow").classList.remove("loading");
      clearTimeout(refreshTimer); refreshTimer = setTimeout(refresh, document.hidden ? 15000 : 4000);
    }
  }
  function updateClock() {
    const now = new Date();
    text($("clockTime"), now.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
    text($("clockDate"), now.toLocaleDateString("zh-CN", { month: "short", day: "numeric", weekday: "short" }));
  }
  function setupTheme() {
    // Light unless the operator chose dark; anything else (unset, legacy values)
    // lands on the light default.
    const stored = localStorage.getItem("poyi-dashboard-theme");
    if (stored !== "dark") document.body.classList.add("light");
    $("themeToggle").addEventListener("click", () => {
      document.body.classList.toggle("light");
      localStorage.setItem("poyi-dashboard-theme", document.body.classList.contains("light") ? "light" : "dark");
    });
  }
  function setupDensity() {
    const allowed = ["full", "compact", "minimal"];
    const stored = localStorage.getItem("poyi-dashboard-density");
    const apply = (value) => {
      const density = allowed.includes(value) ? value : "full";
      document.body.dataset.density = density;
      localStorage.setItem("poyi-dashboard-density", density);
      document.querySelectorAll("[data-density-mode]").forEach((button) => {
        button.setAttribute("aria-pressed", String(button.dataset.densityMode === density));
      });
    };
    document.querySelectorAll("[data-density-mode]").forEach((button) => {
      button.addEventListener("click", () => apply(button.dataset.densityMode));
    });
    apply(stored);
  }
  $("refreshNow").addEventListener("click", () => refresh(true));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  setupTheme(); setupDensity(); updateClock(); setInterval(updateClock, 1000); refresh();
})();
