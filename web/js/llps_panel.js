import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const EXTENSION_NAME = "ComfyUI.LLPS.Panel";
const PANEL_ID = "llps-manager-panel";
const TOGGLE_ID = "llps-manager-toggle";

const SAMPLER_TYPES = new Set([
  "KSampler",
  "KSamplerAdvanced",
  "SamplerCustom",
  "SamplerCustomAdvanced",
  "LLPSKSampler",
  "UltimateSDUpscale",
  "UltimateSDUpscaleNoUpscale",
  "UltimateSDUpscaleCustomSample",
]);

const SAMPLER_WIDGET_NAMES = new Set([
  "sampler_name",
  "scheduler",
  "steps",
  "cfg",
  "denoise",
]);

const LLPS_NODE_TYPES = new Set(["LLPSController", "LLPSConfig"]);

let panel;
let list;
let summary;
let filters;
let previews;
let tabs;
let nodesSection;
let toggleButton;
let lastScan = [];
let activeStatusFilter = "all";
let activeManagerTab = "streams";
let patchedDrawNode = false;
const previewStreams = new Map();

function injectStyles() {
  if (document.getElementById("llps-manager-style")) {
    return;
  }

  const style = document.createElement("style");
  style.id = "llps-manager-style";
  style.textContent = `
    #${TOGGLE_ID} {
      position: fixed;
      right: 16px;
      bottom: 18px;
      z-index: 999;
      height: 34px;
      min-width: 58px;
      border: 1px solid rgba(156, 163, 175, 0.55);
      border-radius: 7px;
      background: rgba(24, 27, 34, 0.94);
      color: #f6f7fb;
      font: 600 12px/1 system-ui, sans-serif;
      cursor: pointer;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.28);
    }

    #${PANEL_ID} {
      position: fixed;
      right: 16px;
      bottom: 60px;
      z-index: 999;
      width: min(560px, calc(100vw - 32px));
      min-width: 360px;
      min-height: 320px;
      height: min(620px, calc(100vh - 90px));
      max-height: min(620px, calc(100vh - 90px));
      display: none;
      flex-direction: column;
      border: 1px solid rgba(156, 163, 175, 0.45);
      border-radius: 8px;
      background: rgba(24, 27, 34, 0.97);
      color: #f6f7fb;
      box-shadow: 0 16px 44px rgba(0, 0, 0, 0.38);
      font: 12px/1.4 system-ui, sans-serif;
      overflow: hidden;
      resize: both;
    }

    #${PANEL_ID}.llps-open {
      display: flex;
    }

    #${PANEL_ID} [hidden] {
      display: none !important;
    }

    .llps-panel-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 10px 12px;
      border-bottom: 1px solid rgba(156, 163, 175, 0.26);
      background: rgba(36, 41, 51, 0.92);
      cursor: move;
      user-select: none;
    }

    .llps-panel-title {
      font-weight: 700;
      font-size: 13px;
    }

    .llps-panel-actions {
      display: flex;
      gap: 6px;
    }

    .llps-panel-actions {
      cursor: default;
    }

    .llps-panel-tabs {
      display: flex;
      gap: 6px;
      padding: 8px 12px;
      border-bottom: 1px solid rgba(156, 163, 175, 0.18);
      background: rgba(18, 21, 28, 0.7);
    }

    .llps-tab {
      height: 28px;
      border: 1px solid rgba(156, 163, 175, 0.32);
      border-radius: 6px;
      background: rgba(255, 255, 255, 0.06);
      color: #f6f7fb;
      padding: 0 10px;
      cursor: pointer;
      font: 700 12px/1 system-ui, sans-serif;
    }

    .llps-tab.llps-active {
      background: #f6f7fb;
      border-color: #f6f7fb;
      color: #111827;
    }

    .llps-section {
      min-height: 0;
      flex: 1;
      overflow: auto;
    }

    .llps-section[hidden] {
      display: none;
    }

    .llps-panel-button {
      height: 28px;
      border: 1px solid rgba(156, 163, 175, 0.38);
      border-radius: 6px;
      background: rgba(255, 255, 255, 0.08);
      color: #f6f7fb;
      padding: 0 9px;
      cursor: pointer;
      font: 600 12px/1 system-ui, sans-serif;
    }

    .llps-panel-button:hover,
    #${TOGGLE_ID}:hover {
      background: rgba(255, 255, 255, 0.14);
    }

    .llps-panel-summary {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 8px;
      padding: 10px 12px;
      border-bottom: 1px solid rgba(156, 163, 175, 0.18);
    }

    .llps-stat {
      min-width: 0;
      border-radius: 6px;
      background: rgba(255, 255, 255, 0.07);
      padding: 7px 8px;
    }

    .llps-stat strong {
      display: block;
      font-size: 16px;
      line-height: 1;
    }

    .llps-stat span {
      display: block;
      margin-top: 4px;
      color: rgba(246, 247, 251, 0.72);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .llps-panel-filters {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      padding: 8px 12px;
      border-bottom: 1px solid rgba(156, 163, 175, 0.18);
    }

    .llps-preview-streams {
      display: grid;
      gap: 8px;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      padding: 8px;
      min-height: 0;
      flex: 1;
      overflow: auto;
    }

    .llps-preview-card {
      border: 1px solid rgba(156, 163, 175, 0.24);
      border-radius: 7px;
      padding: 8px;
      background: rgba(255, 255, 255, 0.055);
    }

    .llps-preview-card-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 7px;
    }

    .llps-preview-title {
      font-weight: 700;
      min-width: 0;
      overflow-wrap: anywhere;
    }

    .llps-preview-image {
      width: 100%;
      aspect-ratio: 1 / 1;
      object-fit: contain;
      background: rgba(0, 0, 0, 0.28);
      border-radius: 5px;
      display: block;
    }

    .llps-preview-empty {
      display: grid;
      place-items: center;
      width: 100%;
      aspect-ratio: 1 / 1;
      border-radius: 5px;
      background: rgba(0, 0, 0, 0.24);
      color: rgba(246, 247, 251, 0.64);
    }

    .llps-progress-row {
      display: grid;
      grid-template-columns: 1fr auto;
      align-items: center;
      gap: 8px;
      margin-top: 7px;
      color: rgba(246, 247, 251, 0.78);
    }

    .llps-progress-track {
      height: 5px;
      border-radius: 999px;
      overflow: hidden;
      background: rgba(156, 163, 175, 0.22);
    }

    .llps-progress-fill {
      height: 100%;
      width: 0%;
      background: #60a5fa;
    }

    .llps-preview-meta {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 4px 10px;
      margin-top: 7px;
      color: rgba(246, 247, 251, 0.72);
      overflow-wrap: anywhere;
    }

    .llps-filter {
      height: 26px;
      border: 1px solid rgba(156, 163, 175, 0.32);
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.06);
      color: #f6f7fb;
      padding: 0 9px;
      cursor: pointer;
      font: 700 11px/1 system-ui, sans-serif;
    }

    .llps-filter.llps-active {
      background: #f6f7fb;
      color: #111827;
      border-color: #f6f7fb;
    }

    .llps-node-list {
      overflow: auto;
      padding: 8px;
    }

    .llps-node-row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      align-items: center;
      min-height: 54px;
      border: 1px solid rgba(156, 163, 175, 0.24);
      border-left-width: 4px;
      border-radius: 7px;
      padding: 8px;
      margin-bottom: 8px;
      background: rgba(255, 255, 255, 0.055);
    }

    .llps-node-row[data-status="controlled"] {
      border-left-color: #34d399;
    }

    .llps-node-row[data-status="uncontrolled"] {
      border-left-color: #fb7185;
    }

    .llps-node-row[data-status="candidate"] {
      border-left-color: #fbbf24;
    }

    .llps-node-row[data-status="llps"] {
      border-left-color: #60a5fa;
    }

    .llps-node-row[data-status="ignored"] {
      border-left-color: #94a3b8;
      opacity: 0.9;
    }

    .llps-node-row[data-status="legacy"] {
      border-left-color: #a78bfa;
      opacity: 0.88;
    }

    .llps-node-title {
      font-weight: 700;
      overflow-wrap: anywhere;
    }

    .llps-node-meta {
      color: rgba(246, 247, 251, 0.68);
      margin-top: 3px;
      overflow-wrap: anywhere;
    }

    .llps-pill {
      display: inline-flex;
      align-items: center;
      height: 22px;
      border-radius: 999px;
      padding: 0 8px;
      color: #111827;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0;
      font-size: 10px;
      white-space: nowrap;
    }

    .llps-pill[data-status="controlled"] {
      background: #34d399;
    }

    .llps-pill[data-status="uncontrolled"] {
      background: #fb7185;
    }

    .llps-pill[data-status="candidate"] {
      background: #fbbf24;
    }

    .llps-pill[data-status="llps"] {
      background: #60a5fa;
    }

    .llps-pill[data-status="ignored"] {
      background: #94a3b8;
    }

    .llps-pill[data-status="legacy"] {
      background: #a78bfa;
    }

    .llps-empty {
      padding: 22px 12px;
      color: rgba(246, 247, 251, 0.72);
      text-align: center;
    }
  `;
  document.head.appendChild(style);
}

function nodeTitle(node) {
  if (!node) {
    return "";
  }
  if (typeof node.getTitle === "function") {
    return node.getTitle();
  }
  return node.title || node.type || "Node";
}

function getWidgets(node) {
  return Array.isArray(node?.widgets) ? node.widgets : [];
}

function getInputs(node) {
  return Array.isArray(node?.inputs) ? node.inputs : [];
}

function hasSamplerWidgets(node) {
  const names = getWidgets(node).map((widget) => widget?.name).filter(Boolean);
  return names.filter((name) => SAMPLER_WIDGET_NAMES.has(name)).length >= 3;
}

function hasLatentSamplerInputs(node) {
  const names = getInputs(node).map((input) => input?.name).filter(Boolean);
  return ["positive", "negative", "latent_image"].every((name) => names.includes(name));
}

function hasImg2ImgSamplerInputs(node) {
  const names = getInputs(node).map((input) => input?.name).filter(Boolean);
  const hasImageSource = names.some((name) => ["image", "upscaled_image", "pixels"].includes(name));
  return hasImageSource && ["model", "positive", "negative"].every((name) => names.includes(name));
}

function isSamplerLike(node) {
  if (!node) {
    return false;
  }
  if (SAMPLER_TYPES.has(node.type)) {
    return true;
  }
  return hasSamplerWidgets(node) && (hasLatentSamplerInputs(node) || hasImg2ImgSamplerInputs(node));
}

function isLLPSConfig(node) {
  return node?.type === "LLPSConfig";
}

function isLLPSController(node) {
  return node?.type === "LLPSController";
}

function isLLPSSampler(node) {
  return node?.type === "LLPSKSampler";
}

function widgetValue(node, name, fallback = undefined) {
  const widget = getWidgets(node).find((item) => item?.name === name);
  return widget ? widget.value : fallback;
}

function nodeSortValue(node) {
  const value = Number(node?.id);
  return Number.isFinite(value) ? value : Number.MAX_SAFE_INTEGER;
}

function isControllerEnabled(controller) {
  return widgetValue(controller, "enabled", true) === true;
}

function controllerCoversNode(controller, node) {
  if (!controller || !isControllerEnabled(controller)) {
    return false;
  }
  return isSamplerLike(node);
}

function allControllers() {
  const nodes = Array.isArray(app.graph?._nodes) ? app.graph._nodes : [];
  return nodes.filter(isLLPSController).sort((a, b) => nodeSortValue(a) - nodeSortValue(b));
}

function enabledControllers() {
  return allControllers().filter(isControllerEnabled);
}

function activeController() {
  return enabledControllers()[0] || null;
}

function ignoredControllers() {
  const active = activeController();
  return enabledControllers().filter((controller) => controller !== active);
}

function coveringControllers(node, controllers) {
  return controllers.filter((controller) => controllerCoversNode(controller, node));
}

function samplerSubfolderInfo(node) {
  const samplerSubfolder = String(widgetValue(node, "llps_subfolder", "") || "").trim();
  if (samplerSubfolder) {
    return { value: samplerSubfolder, source: "sampler llps_subfolder" };
  }
  const active = activeController();
  const controllerSubfolder = String(widgetValue(active, "subfolder", "") || "").trim();
  if (controllerSubfolder) {
    return { value: controllerSubfolder, source: `Controller #${active.id} subfolder` };
  }
  return { value: `${node.type}_${node.id}`, source: "automatic sampler fallback" };
}

function analyzeNode(node, active, ignored) {
  if (isLLPSController(node)) {
    const enabled = isControllerEnabled(node);
    if (enabled && active && node !== active) {
      return {
        status: "ignored",
        detail: `ignored because LLPS Controller #${active.id} is active`,
      };
    }
    return {
      status: "llps",
      detail: enabled ? "active workflow controller / all sampler-like nodes" : "disabled workflow controller",
    };
  }
  if (isLLPSConfig(node)) {
    return { status: "legacy", detail: "deprecated v1.2 config compatibility node" };
  }
  if (isSamplerLike(node)) {
    const controllers = active ? [active] : [];
    const coveredBy = coveringControllers(node, controllers);
    const subfolder = samplerSubfolderInfo(node);
    if (coveredBy.length) {
      return {
        status: "controlled",
        detail: `covered by LLPS Controller #${coveredBy[0].id}; subfolder: ${subfolder.value} (${subfolder.source})${ignored.length ? `; ${ignored.length} Controller ignored` : ""}`,
        controlledBy: coveredBy.map((controller) => controller.id),
        subfolder,
      };
    }

    if (active || ignored.length) {
      return {
        status: "uncontrolled",
        detail: isLLPSSampler(node)
          ? "legacy LLPS KSampler outside active Controller coverage"
          : "sampler-like node outside active Controller coverage",
        subfolder,
      };
    }

    return {
      status: "candidate",
      detail: isLLPSSampler(node)
        ? "legacy LLPS KSampler; add/enable LLPS Controller for workflow control"
        : "sampler-like node; add/enable LLPS Controller for workflow control",
      subfolder,
    };
  }
  if (LLPS_NODE_TYPES.has(node?.type)) {
    return { status: "llps", detail: "LLPS node" };
  }
  return null;
}

function statusLabel(status) {
  switch (status) {
    case "controlled":
      return "controlled";
    case "uncontrolled":
      return "uncontrolled";
    case "candidate":
      return "candidate";
    case "llps":
      return "LLPS";
    case "ignored":
      return "ignored";
    case "legacy":
      return "legacy";
    default:
      return "unknown";
  }
}

function scanWorkflow() {
  const nodes = Array.isArray(app.graph?._nodes) ? app.graph._nodes : [];
  const active = activeController();
  const ignored = ignoredControllers();
  lastScan = nodes
    .map((node) => {
      const analysis = analyzeNode(node, active, ignored);
      if (!analysis) {
        delete node.__llpsStatus;
        return null;
      }
      const { status, detail, controlledBy, subfolder } = analysis;
      node.__llpsStatus = status;
      return {
        id: node.id,
        type: node.type,
        title: nodeTitle(node),
        status,
        detail,
        controlledBy,
        subfolder,
        node,
      };
    })
    .filter(Boolean)
    .sort((a, b) => {
      const order = { controlled: 0, uncontrolled: 1, candidate: 2, llps: 3, ignored: 4, legacy: 5 };
      return (order[a.status] ?? 9) - (order[b.status] ?? 9) || Number(a.id) - Number(b.id);
    });

  app.graph?.setDirtyCanvas?.(true, false);
  renderPanel();
  return lastScan;
}

function focusNode(nodeId) {
  const node = app.graph?.getNodeById?.(nodeId);
  if (!node) {
    return;
  }
  app.canvas?.centerOnNode?.(node);
  app.canvas?.selectNode?.(node, false);
  app.graph?.setDirtyCanvas?.(true, true);
}

function selectNodesByStatus(status) {
  const matches = lastScan.filter((item) => item.status === status).map((item) => item.node).filter(Boolean);
  if (!matches.length || !app.canvas?.selectNode) {
    return;
  }
  matches.forEach((node, index) => app.canvas.selectNode(node, index !== 0));
  app.graph?.setDirtyCanvas?.(true, true);
}

function previewStreamKey(data) {
  return String(data?.sampler_node_id || data?.sampler_label || data?.run_id || "unknown");
}

function updatePreviewStream(data) {
  if (!data) {
    return;
  }
  const key = previewStreamKey(data);
  const previous = previewStreams.get(key) || {};
  previewStreams.set(key, {
    ...previous,
    ...data,
    image: data.image || previous.image,
    updatedAt: Date.now(),
  });
  renderPanel();
}

function clearPreviewStreams() {
  previewStreams.clear();
  renderPanel();
}

function createStat(label, value) {
  const item = document.createElement("div");
  item.className = "llps-stat";
  item.innerHTML = `<strong>${value}</strong><span>${label}</span>`;
  return item;
}

function createPreviewMeta(label, value) {
  const item = document.createElement("div");
  item.textContent = `${label}: ${value || "-"}`;
  return item;
}

function renderPreviewStreams() {
  if (!previews) {
    return;
  }

  previews.replaceChildren();
  const streams = [...previewStreams.values()].sort((a, b) => Number(a.sampler_node_id || 0) - Number(b.sampler_node_id || 0));
  previews.classList.toggle("llps-has-streams", streams.length > 0);
  if (!streams.length) {
    const empty = document.createElement("div");
    empty.className = "llps-empty";
    empty.textContent = "No LLPS preview streams yet.";
    previews.appendChild(empty);
    return;
  }

  for (const stream of streams) {
    const node = app.graph?.getNodeById?.(Number(stream.sampler_node_id));
    const card = document.createElement("article");
    card.className = "llps-preview-card";

    const head = document.createElement("div");
    head.className = "llps-preview-card-head";

    const title = document.createElement("div");
    title.className = "llps-preview-title";
    title.textContent = node
      ? `${nodeTitle(node)} #${stream.sampler_node_id}`
      : `${stream.sampler_label || stream.sampler_node_type || "Sampler"} #${stream.sampler_node_id || "-"}`;

    const focus = document.createElement("button");
    focus.className = "llps-panel-button";
    focus.type = "button";
    focus.textContent = "Focus";
    focus.title = "Focus this sampler node on the canvas";
    focus.addEventListener("click", () => focusNode(Number(stream.sampler_node_id)));

    head.append(title, focus);
    card.appendChild(head);

    if (stream.image) {
      const img = document.createElement("img");
      img.className = "llps-preview-image";
      img.alt = title.textContent;
      img.src = stream.image;
      card.appendChild(img);
    } else {
      const empty = document.createElement("div");
      empty.className = "llps-preview-empty";
      empty.textContent = "Waiting for preview";
      card.appendChild(empty);
    }

    const step = Number(stream.step || 0);
    const total = Number(stream.total_steps || 0);
    const percent = total > 0 ? Math.max(0, Math.min(100, Math.round((step / total) * 100))) : 0;

    const progress = document.createElement("div");
    progress.className = "llps-progress-row";

    const track = document.createElement("div");
    track.className = "llps-progress-track";
    const fill = document.createElement("div");
    fill.className = "llps-progress-fill";
    fill.style.width = `${percent}%`;
    track.appendChild(fill);

    const stepText = document.createElement("div");
    stepText.textContent = total > 0 ? `${step} / ${total}` : "-";

    progress.append(track, stepText);
    card.appendChild(progress);

    const meta = document.createElement("div");
    meta.className = "llps-preview-meta";
    meta.append(
      createPreviewMeta("method", stream.method || stream.previewer_class),
      createPreviewMeta("status", stream.filename_resolution_status || (stream.save_also ? "saving" : "preview")),
      createPreviewMeta("subfolder", stream.effective_subfolder),
      createPreviewMeta("source", stream.sampler_subfolder ? "sampler llps_subfolder" : (stream.controller_subfolder ? "controller subfolder" : "automatic fallback")),
      createPreviewMeta("last file", stream.last_saved_file)
    );
    card.appendChild(meta);

    previews.appendChild(card);
  }
}

function filterItems() {
  if (activeStatusFilter === "all") {
    return lastScan;
  }
  return lastScan.filter((item) => item.status === activeStatusFilter);
}

function renderFilters(counts) {
  if (!filters) {
    return;
  }

  const options = [
    ["all", `All ${lastScan.length}`],
    ["controlled", `Controlled ${counts.controlled}`],
    ["uncontrolled", `Uncontrolled ${counts.uncontrolled}`],
    ["candidate", `Candidate ${counts.candidate}`],
    ["llps", `LLPS ${counts.llps}`],
    ["ignored", `Ignored ${counts.ignored}`],
    ["legacy", `Legacy ${counts.legacy}`],
  ];

  filters.replaceChildren();
  for (const [status, label] of options) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `llps-filter${activeStatusFilter === status ? " llps-active" : ""}`;
    button.textContent = label;
    button.addEventListener("click", () => {
      activeStatusFilter = status;
      renderPanel();
    });
    filters.appendChild(button);
  }
}

function renderTabs() {
  if (!tabs) {
    return;
  }
  const streamCount = previewStreams.size;
  const options = [
    ["streams", `Streams ${streamCount}`],
    ["nodes", `Nodes ${lastScan.length}`],
  ];
  tabs.replaceChildren();
  for (const [tab, label] of options) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `llps-tab${activeManagerTab === tab ? " llps-active" : ""}`;
    button.textContent = label;
    button.addEventListener("click", () => {
      activeManagerTab = tab;
      renderPanel();
    });
    tabs.appendChild(button);
  }
}

function renderPanel() {
  if (!panel || !list || !summary) {
    return;
  }

  const counts = lastScan.reduce(
    (acc, item) => {
      acc[item.status] = (acc[item.status] || 0) + 1;
      return acc;
    },
    { controlled: 0, uncontrolled: 0, candidate: 0, llps: 0, ignored: 0, legacy: 0 }
  );

  summary.replaceChildren(
    createStat("controlled", counts.controlled),
    createStat("uncontrolled", counts.uncontrolled),
    createStat("candidate", counts.candidate),
    createStat("LLPS nodes", counts.llps + counts.ignored + counts.legacy)
  );
  renderTabs();
  renderFilters(counts);
  renderPreviewStreams();
  if (previews) {
    previews.hidden = activeManagerTab !== "streams";
  }
  if (nodesSection) {
    nodesSection.hidden = activeManagerTab !== "nodes";
  }

  list.replaceChildren();
  const visibleItems = filterItems();
  if (!visibleItems.length) {
    const empty = document.createElement("div");
    empty.className = "llps-empty";
    empty.textContent = lastScan.length ? "No nodes match the current filter." : "No LLPS or sampler-like nodes found.";
    list.appendChild(empty);
    return;
  }

  for (const item of visibleItems) {
    const row = document.createElement("div");
    row.className = "llps-node-row";
    row.dataset.status = item.status;

    const text = document.createElement("div");
    const title = document.createElement("div");
    title.className = "llps-node-title";
    title.textContent = item.title;

    const meta = document.createElement("div");
    meta.className = "llps-node-meta";
    meta.textContent = `#${item.id} / ${item.type}`;

    const detail = document.createElement("div");
    detail.className = "llps-node-meta";
    detail.textContent = item.detail || statusLabel(item.status);

    text.append(title, meta, detail);
    if (item.subfolder) {
      const subfolder = document.createElement("div");
      subfolder.className = "llps-node-meta";
      subfolder.textContent = `subfolder: ${item.subfolder.value} (${item.subfolder.source})`;
      text.appendChild(subfolder);
    }

    const actions = document.createElement("div");
    const pill = document.createElement("span");
    pill.className = "llps-pill";
    pill.dataset.status = item.status;
    pill.textContent = statusLabel(item.status);

    const focus = document.createElement("button");
    focus.className = "llps-panel-button";
    focus.type = "button";
    focus.textContent = "Focus";
    focus.title = "Focus this node on the canvas";
    focus.addEventListener("click", () => focusNode(item.id));

    actions.append(pill, focus);
    row.append(text, actions);
    list.appendChild(row);
  }
}

function enablePanelDrag(header) {
  let dragging = null;
  header.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest("button")) {
      return;
    }
    const rect = panel.getBoundingClientRect();
    dragging = {
      x: event.clientX - rect.left,
      y: event.clientY - rect.top,
    };
    panel.style.left = `${rect.left}px`;
    panel.style.top = `${rect.top}px`;
    panel.style.right = "auto";
    panel.style.bottom = "auto";
    header.setPointerCapture?.(event.pointerId);
  });

  header.addEventListener("pointermove", (event) => {
    if (!dragging) {
      return;
    }
    const width = panel.offsetWidth;
    const height = panel.offsetHeight;
    const left = Math.max(0, Math.min(window.innerWidth - width, event.clientX - dragging.x));
    const top = Math.max(0, Math.min(window.innerHeight - height, event.clientY - dragging.y));
    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
  });

  const stop = (event) => {
    if (!dragging) {
      return;
    }
    dragging = null;
    header.releasePointerCapture?.(event.pointerId);
  };
  header.addEventListener("pointerup", stop);
  header.addEventListener("pointercancel", stop);
}

function createPanel() {
  injectStyles();

  toggleButton = document.createElement("button");
  toggleButton.id = TOGGLE_ID;
  toggleButton.type = "button";
  toggleButton.textContent = "LLPS";
  toggleButton.title = "Toggle LLPS manager";
  toggleButton.addEventListener("click", () => {
    panel.classList.toggle("llps-open");
    scanWorkflow();
  });

  panel = document.createElement("section");
  panel.id = PANEL_ID;
  panel.setAttribute("aria-label", "LLPS manager");

  const header = document.createElement("div");
  header.className = "llps-panel-header";

  const title = document.createElement("div");
  title.className = "llps-panel-title";
  title.textContent = "LLPS Manager";

  const actions = document.createElement("div");
  actions.className = "llps-panel-actions";

  const refresh = document.createElement("button");
  refresh.className = "llps-panel-button";
  refresh.type = "button";
  refresh.textContent = "Refresh";
  refresh.title = "Scan the current workflow again";
  refresh.addEventListener("click", scanWorkflow);

  const selectUncontrolled = document.createElement("button");
  selectUncontrolled.className = "llps-panel-button";
  selectUncontrolled.type = "button";
  selectUncontrolled.textContent = "Select";
  selectUncontrolled.title = "Select all uncontrolled sampler-like nodes on the canvas";
  selectUncontrolled.addEventListener("click", () => selectNodesByStatus("uncontrolled"));

  const close = document.createElement("button");
  close.className = "llps-panel-button";
  close.type = "button";
  close.textContent = "Close";
  close.title = "Close LLPS manager";
  close.addEventListener("click", () => panel.classList.remove("llps-open"));

  actions.append(selectUncontrolled, refresh, close);
  header.append(title, actions);

  tabs = document.createElement("div");
  tabs.className = "llps-panel-tabs";

  nodesSection = document.createElement("div");
  nodesSection.className = "llps-section";

  summary = document.createElement("div");
  summary.className = "llps-panel-summary";

  filters = document.createElement("div");
  filters.className = "llps-panel-filters";

  previews = document.createElement("div");
  previews.className = "llps-preview-streams";

  list = document.createElement("div");
  list.className = "llps-node-list";

  nodesSection.append(summary, filters, list);
  panel.append(header, tabs, previews, nodesSection);
  document.body.append(panel, toggleButton);
  enablePanelDrag(header);
  scanWorkflow();
}

function drawBadge(ctx, node, status) {
  if (!ctx || !node || !status) {
    return;
  }

  const width = Array.isArray(node.size) ? node.size[0] : 160;
  const color =
    status === "controlled"
      ? "#34d399"
      : status === "uncontrolled"
        ? "#fb7185"
        : status === "candidate"
          ? "#fbbf24"
          : status === "ignored"
            ? "#94a3b8"
            : "#60a5fa";
  const label = statusLabel(status);

  ctx.save();
  ctx.lineWidth = 3;
  ctx.strokeStyle = color;
  ctx.strokeRect(1.5, 1.5, Math.max(1, width - 3), 22);
  ctx.fillStyle = color;
  ctx.font = "bold 10px sans-serif";
  const labelWidth = Math.min(ctx.measureText(label).width + 14, Math.max(48, width - 12));
  ctx.fillRect(Math.max(6, width - labelWidth - 6), 5, labelWidth, 17);
  ctx.fillStyle = "#111827";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(label, Math.max(6, width - labelWidth - 6) + labelWidth / 2, 13.5);
  ctx.restore();
}

function patchCanvasBadges() {
  if (patchedDrawNode || !window.LGraphCanvas?.prototype?.drawNode) {
    return;
  }
  patchedDrawNode = true;

  const originalDrawNode = window.LGraphCanvas.prototype.drawNode;
  window.LGraphCanvas.prototype.drawNode = function (node, ctx) {
    const result = originalDrawNode.apply(this, arguments);
    if (panel?.classList.contains("llps-open")) {
      drawBadge(ctx, node, node?.__llpsStatus);
    }
    return result;
  };
}

function registerMenuCommand() {
  app.registerExtension({
    name: EXTENSION_NAME,
    commands: [
      {
        id: "llps_toggle_manager",
        label: "Toggle LLPS Manager",
        function: () => {
          panel?.classList.toggle("llps-open");
          scanWorkflow();
        },
      },
      {
        id: "llps_refresh_manager",
        label: "Refresh LLPS Manager",
        function: scanWorkflow,
      },
    ],
    menuCommands: [
      {
        path: ["LLPS"],
        commands: ["llps_toggle_manager", "llps_refresh_manager"],
      },
    ],
    setup() {
      createPanel();
      patchCanvasBadges();
      api.addEventListener("llps_preview", ({ detail }) => updatePreviewStream(detail));
      api.addEventListener("execution_start", clearPreviewStreams);
    },
    nodeCreated() {
      window.setTimeout(scanWorkflow, 0);
    },
    afterConfigureGraph() {
      window.setTimeout(scanWorkflow, 0);
    },
  });
}

registerMenuCommand();
