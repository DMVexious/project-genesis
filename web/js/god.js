/* The god's toolbar: powers over the land, the sky and the people, chosen from a
 * bar along the bottom of the world while in God mode.
 *
 * Pick a tool, then click the ground to use it (drag to paint the land tools
 * across many tiles; right-drag still moves the view). Sky powers (rain, sun...)
 * happen as soon as they are picked. In Deity mode every power costs Faith,
 * shown on the left of the bar. What a power is and costs comes from the
 * server (powers.py), sent with the game's defs. */
import { $, TILE } from "./config.js";
import { floatText } from "./fx.js";
import { send } from "./net.js";
import { state } from "./state.js";

const GROUPS = [["land", "🌳 Land"], ["gifts", "🎁 Gifts"], ["sky", "⛅ Sky"], ["build", "🏗️ Build"],
                ["life", "🦌 Life"], ["events", "📜 Events"]];
const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const g = { group: "land", tool: null, built: "", faith: null, deity: false, mark: null, lastTile: "", painting: false };

export const godTool = () => g.tool;
const powers = () => (state.defs && state.defs.powers) || [];
export const powerDef = (key) => powers().find((p) => p.key === key);

// --- the bar ------------------------------------------------------------------------------
function build() {
  const ps = powers();
  if (!ps.length) return false;
  $("gb-groups").innerHTML = GROUPS.map(([k, label]) => `<button data-group="${k}">${label}</button>`).join("");
  g.built = "x";
  showGroup();
  return true;
}

function showGroup() {
  for (const b of $("gb-groups").children) b.classList.toggle("on", b.dataset.group === g.group);
  $("gb-tools").innerHTML = powers().filter((p) => p.group === g.group).map((p) => `
    <button data-power="${p.key}" title="${esc(p.blurb)}${p.paint ? " (drag to paint)" : ""}">
      <span class="gt-emoji">${p.emoji}</span><span class="gt-label">${esc(p.label)}</span>
      <span class="gt-cost">${p.cost}</span></button>`).join("");
  markTools();
}

function markTools() {
  for (const b of $("gb-tools").children) {
    const p = powerDef(b.dataset.power);
    b.classList.toggle("on", !!g.tool && g.tool.key === p.key);
    b.classList.toggle("dear", g.deity && g.faith != null && p.cost > g.faith);
  }
  $("godbar").classList.toggle("deity", g.deity);
  const hint = g.tool
    ? `${g.tool.emoji} ${g.tool.blurb}: click the ground${g.tool.paint ? " (drag to paint)" : ""} · right-drag moves the view · Esc puts it down`
    : "Pick a power, then click the ground. Click a villager for heal, feed, bless, smite or a word from the sky.";
  $("gb-hint").textContent = hint;
}

export function pickTool(key) {
  const p = powerDef(key);
  if (!p) return;
  if (p.target === "sky") {  // the sky answers at once: nothing to aim
    send({ cmd: "power", power: key });
    return;
  }
  g.tool = g.tool && g.tool.key === key ? null : p;
  document.body.classList.toggle("god-tool", !!g.tool);
  markTools();
}

export function dropTool() {
  if (!g.tool) return false;
  g.tool = null;
  document.body.classList.remove("god-tool");
  if (g.mark) g.mark.setVisible(false);
  markTools();
  return true;
}

// Called with every frame: faith, and whether the bar shows at all.
export function renderGodbar(f) {
  if (!g.built && !build()) return;
  const god = f.god || {};
  const deity = god.mode === "deity";
  if (deity !== g.deity || god.faith !== g.faith) {
    g.deity = deity;
    g.faith = god.faith;
    $("gb-faith").hidden = !deity;
    $("gb-faith").innerHTML = `🙏 <b>${god.faith}</b> faith<span class="hint"> · ${god.believers || 0} believe</span>`;
    markTools();
  }
}

// --- using a tool on the land ---------------------------------------------------------------
// The tile under the pointer is outlined while a tool is in hand.
export function hoverTile(sc, wx, wy) {
  if (!g.tool) { if (g.mark) g.mark.setVisible(false); return; }
  if (!g.mark || g.mark.scene !== sc) {
    g.mark = sc.add.graphics().setDepth(16000);
  }
  const x = Math.floor(wx / TILE), y = Math.floor(wy / TILE);
  g.mark.clear().lineStyle(2, 0xf6d77a, 0.95).strokeRect(x * TILE + 1, y * TILE + 1, TILE - 2, TILE - 2)
    .fillStyle(0xf6d77a, 0.15).fillRect(x * TILE + 1, y * TILE + 1, TILE - 2, TILE - 2).setVisible(true);
}

// Pointer pressed (start=true) or dragged over a new tile with a tool in hand.
export function useTool(sc, wx, wy, start) {
  if (!g.tool) return false;
  const x = Math.floor(wx / TILE), y = Math.floor(wy / TILE), key = `${x},${y}`;
  if (start) { g.painting = true; g.lastTile = ""; }
  if (!g.painting || key === g.lastTile || (!start && !g.tool.paint)) return true;
  g.lastTile = key;
  const f = state.frame;
  if (!f || x < 0 || y < 0 || x >= f.width || y >= f.height) return true;
  send({ cmd: "power", power: g.tool.key, x, y });
  floatText(sc, (x + 0.5) * TILE, (y + 0.3) * TILE, g.tool.emoji, "#fff4c8", { size: 12, rise: 14, dur: 700 });
  return true;
}

export function endTool() { g.painting = false; }

// --- what the server says back ------------------------------------------------------------------
let toastTimer = 0;
export function toast(text, ok = true) {
  if (!text) return;
  const el = $("toast");
  el.textContent = text;
  el.classList.toggle("bad", !ok);
  el.hidden = false;
  el.classList.remove("show"); void el.offsetWidth; el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3200);
}

export function initGodbar() {
  $("gb-groups").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-group]");
    if (b) { g.group = b.dataset.group; showGroup(); }
  });
  $("gb-tools").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-power]");
    if (b) pickTool(b.dataset.power);
  });
}
