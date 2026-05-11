import { app } from "../../scripts/app.js";

const EXTENSION_NAME = "ComfyUI.LLPS.Panel";
const PANEL_ID = "llps-manager-panel";
const TOGGLE_ID = "llps-manager-toggle";

const SAMPLER_TYPES = new Set([
  "KSampler",
  "KSamplerAdvanced",
  "SamplerCustom",
  "SamplerCustomAdvanced",
  "LLPSKSampler",
]);

const SAMPLER_WIDGET_NAMES = new Set([
  "sampler_name",
  "scheduler",
  "steps",
  "cfg",
  "denoise",
]);

const LLPS_NODE_TYPES = new Set(["LLPSConfig", "LLPSKSampler"]);

let panel;
let list;
let summary;
let toggleButton;
let lastScan = [];
let patchedDrawNode = false;

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
      width: min(380px, calc(100vw - 32px));
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
    }

    #${PANEL_ID}.llps-open {
      display: flex;
    }

    .llps-panel-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 10px 12px;
      border-bottom: 1px solid rgba(156, 163, 175, 0.26);
      background: rgba(36, 41, 51, 0.92);
    }

    .llps-panel-title {
      font-weight: 700;
      font-size: 13px;
    }

    .llps-panel-actions {
      display: flex;
      gap: 6px;
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
      grid-template-columns: repeat(3, 1fr);
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

function hasSamplerInputs(node) {
  const names = getInputs(node).map((input) => input?.name).filter(Boolean);
  return ["positive", "negative", "latent_image"].every((name) => names.includes(name));
}

function isSamplerLike(node) {
  if (!node) {
    return false;
  }
  if (SAMPLER_TYPES.has(node.type)) {
    return true;
  }
  return hasSamplerWidgets(node) && hasSamplerInputs(node);
}

function isLLPSConfig(node) {
  return node?.type === "LLPSConfig";
}

function isLLPSSampler(node) {
  return node?.type === "LLPSKSampler";
}

function hasLLPSConfigInput(node) {
  return getInputs(node).some((input) => {
    const type = Array.isArray(input?.type) ? input.type.join(",") : String(input?.type || "");
    return input?.name === "llps_config" || type.includes("LLPS_CONFIG");
  });
}

function classifyNode(node) {
  if (isLLPSConfig(node)) {
    return "llps";
  }
  if (isLLPSSampler(node)) {
    return "controlled";
  }
  if (isSamplerLike(node)) {
    return hasLLPSConfigInput(node) ? "candidate" : "uncontrolled";
  }
  if (LLPS_NODE_TYPES.has(node?.type)) {
    return "llps";
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
    default:
      return "unknown";
  }
}

function scanWorkflow() {
  const nodes = Array.isArray(app.graph?._nodes) ? app.graph._nodes : [];
  lastScan = nodes
    .map((node) => {
      const status = classifyNode(node);
      if (!status) {
        delete node.__llpsStatus;
        return null;
      }
      node.__llpsStatus = status;
      return {
        id: node.id,
        type: node.type,
        title: nodeTitle(node),
        status,
        node,
      };
    })
    .filter(Boolean)
    .sort((a, b) => {
      const order = { controlled: 0, uncontrolled: 1, candidate: 2, llps: 3 };
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

function createStat(label, value) {
  const item = document.createElement("div");
  item.className = "llps-stat";
  item.innerHTML = `<strong>${value}</strong><span>${label}</span>`;
  return item;
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
    { controlled: 0, uncontrolled: 0, candidate: 0, llps: 0 }
  );

  summary.replaceChildren(
    createStat("controlled", counts.controlled),
    createStat("uncontrolled", counts.uncontrolled),
    createStat("LLPS nodes", counts.llps)
  );

  list.replaceChildren();
  if (!lastScan.length) {
    const empty = document.createElement("div");
    empty.className = "llps-empty";
    empty.textContent = "No LLPS or sampler-like nodes found.";
    list.appendChild(empty);
    return;
  }

  for (const item of lastScan) {
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

    text.append(title, meta);

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

  const close = document.createElement("button");
  close.className = "llps-panel-button";
  close.type = "button";
  close.textContent = "Close";
  close.title = "Close LLPS manager";
  close.addEventListener("click", () => panel.classList.remove("llps-open"));

  actions.append(refresh, close);
  header.append(title, actions);

  summary = document.createElement("div");
  summary.className = "llps-panel-summary";

  list = document.createElement("div");
  list.className = "llps-node-list";

  panel.append(header, summary, list);
  document.body.append(panel, toggleButton);
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
