/* The start screen: choose the kind of land, its size, how many founders and
 * the seed, see what each land would look like, and begin. It opens by itself
 * when the server is waiting for a world to be chosen, and from the "New
 * world" button at any time. */
import { $ } from "./config.js";
import { toast } from "./god.js";
import { paintMap } from "./minimap.js";
import { send } from "./net.js";
import { state } from "./state.js";

const SIZES = [["Small", 60, 42], ["Medium", 80, 56], ["Large", 120, 84], ["Huge", 160, 112]];
const m = { open: false, first: false, built: false, map: "valley", size: 1, ask: 0, timer: 0 };

export const menuOpen = () => m.open;
const options = () => {
  const [, width, height] = SIZES[m.size];
  const mode = (document.querySelector('input[name="menu-mode"]:checked') || {}).value || "sandbox";
  return { map: m.map, seed: Math.max(0, Math.floor(Number($("menu-seed").value) || 0)), width, height,
           population: Math.max(1, Math.min(24, Math.floor(Number($("menu-pop").value) || 3))), mode };
};

function build() {
  const maps = (state.defs && state.defs.maps) || [];
  $("menu-maps").innerHTML = maps.map((d) => `
    <button class="map-card" data-map="${d.key}" title="${d.blurb}">
      <canvas width="80" height="56"></canvas><b>${d.label}</b><span>${d.blurb}</span>
    </button>`).join("");
  $("menu-size").innerHTML = SIZES.map(([name, w, h], i) => `<option value="${i}">${name} (${w} × ${h})</option>`).join("");
  m.built = maps.length > 0;
}

function mark() {
  for (const card of $("menu-maps").children) card.classList.toggle("sel", card.dataset.map === m.map);
}

// Ask the server what each kind of land would look like with these choices.
function preview() {
  clearTimeout(m.timer);
  m.timer = setTimeout(() => {
    const o = options();
    m.ask += 1;
    for (const card of $("menu-maps").children) {
      card.classList.add("loading");
      send({ cmd: "preview", ...o, map: card.dataset.map, id: m.ask });
    }
  }, 200);
}

export function onPreview(msg) {
  if (!m.open || msg.id !== m.ask) return;  // an answer to choices since changed
  const card = [...$("menu-maps").children].find((c) => c.dataset.map === msg.map);
  if (!card) return;
  const canvas = card.querySelector("canvas");
  const map = paintMap(msg.terrain, msg.tiles);
  canvas.width = msg.width * 2;
  canvas.height = msg.height * 2;
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(map, 0, 0, canvas.width, canvas.height);
  if (msg.start) {  // where the founders would settle
    const [x, y] = msg.start;
    ctx.strokeStyle = "#140e09"; ctx.lineWidth = 4; ctx.strokeRect(x * 2 - 5, y * 2 - 5, 12, 12);
    ctx.strokeStyle = "#ffd24a"; ctx.lineWidth = 2; ctx.strokeRect(x * 2 - 5, y * 2 - 5, 12, 12);
  }
  card.classList.remove("loading");
}

export function openMenu(first = false) {
  if (!m.built) build();
  if (!m.built) return;  // (the server has not said what maps there are yet)
  const c = state.control || {};
  if (!m.open) {
    m.map = c.map || m.map;
    const size = SIZES.findIndex(([, w, h]) => w === c.width && h === c.height);
    m.size = size >= 0 ? size : m.size;
    $("menu-size").value = m.size;
    $("menu-pop").value = c.population || 3;
    $("menu-seed").value = first ? c.seed ?? 7 : Math.floor(Math.random() * 1e6);
    const mode = document.querySelector(`input[name="menu-mode"][value="${c.god_mode || "sandbox"}"]`);
    if (mode) mode.checked = true;
  }
  send({ cmd: "saves" });
  m.open = true;
  m.first = first;
  send({ cmd: "models" });  // (re-read what Ollama has: a model may have been pulled since)
  showModels();
  $("menu-cancel").hidden = first;
  $("menu").hidden = false;
  mark();
  preview();
}

// --- saved games ----------------------------------------------------------------------------
const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function ago(t) {
  const s = Math.max(0, Date.now() / 1000 - t);
  return s < 90 ? "just now" : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 129600 ? `${Math.round(s / 3600)} h ago`
    : new Date(t * 1000).toLocaleDateString();
}

export function onSaves(list) {
  $("menu-saves").hidden = !list.length;
  const maps = (state.defs && state.defs.maps) || [];
  $("menu-saves-list").innerHTML = list.map((s) => {
    const land = (maps.find((d) => d.key === s.map) || {}).label || s.map;
    const where = (s.villages || []).slice(0, 2).join(", ");
    return `<li><b>${esc(s.name)}</b><span class="hint">${esc(land)}${where ? ` · ${esc(where)}` : ""} · day ${s.day ?? "?"}, ${esc(s.season || "")}
      · ${s.people} people${s.mode === "deity" ? " · 🙏" : ""} · ${ago(s.saved)}</span>
      <button data-load="${esc(s.name)}" class="primary">Load</button>
      <button data-del="${esc(s.name)}" title="Delete this save">🗑</button></li>`;
  }).join("");
}

// --- which LLMs villagers think and talk with -------------------------------------------
function modelOptions(sel, installed, current, extra) {
  const names = [...extra, ...installed];
  if (current && !names.includes(current)) names.push(current);
  sel.innerHTML = names.map((n) => {
    const [v, label] = Array.isArray(n) ? n : [n, n];
    return `<option value="${v}"${v === current ? " selected" : ""}>${label}</option>`;
  }).join("");
}

export function showModels() {
  const ms = state.control && state.control.models;
  if (!ms || !m.open) return;
  modelOptions($("menu-think"), ms.installed, ms.think, [["heuristic", "none (rules only)"]]);
  modelOptions($("menu-talk"), ms.installed, ms.talk, [["heuristic", "none (canned lines)"]]);
  $("menu-bestof").value = String(ms.best_of || 1);
  const busy = !!ms.switching;
  for (const id of ["menu-think", "menu-talk", "menu-bestof"]) $(id).disabled = busy;
  $("menu-models-note").textContent = busy ? "loading model…" :
    ms.installed.length ? "" : "Ollama isn't running: villagers use rules and canned lines.";
}

function changeModels(key, value) {
  send({ cmd: "models", [key]: key === "best_of" ? Number(value) : value });
}

export function closeMenu() {
  m.open = false;
  $("menu").hidden = true;
}

export function initMenu() {
  $("menu-maps").addEventListener("click", (e) => {
    const card = e.target.closest(".map-card");
    if (card) { m.map = card.dataset.map; mark(); }
  });
  $("menu-maps").addEventListener("dblclick", (e) => { if (e.target.closest(".map-card")) $("menu-start").click(); });
  $("menu-size").onchange = (e) => { m.size = Number(e.target.value); preview(); };
  $("menu-pop").onchange = preview;
  $("menu-seed").oninput = preview;
  $("menu-dice").onclick = () => { $("menu-seed").value = Math.floor(Math.random() * 1e6); preview(); };
  $("menu-cancel").onclick = closeMenu;
  $("menu-think").onchange = (e) => changeModels("think", e.target.value);
  $("menu-talk").onchange = (e) => changeModels("talk", e.target.value);
  $("menu-bestof").onchange = (e) => changeModels("best_of", e.target.value);
  $("menu-start").onclick = () => { send({ cmd: "reset", ...options() }); closeMenu(); };
  let armed = "";  // (a delete asks twice: the first click arms it)
  $("menu-saves-list").addEventListener("click", (e) => {
    const load = e.target.closest("button[data-load]"), del = e.target.closest("button[data-del]");
    if (load) { send({ cmd: "load", name: load.dataset.load }); closeMenu(); }
    else if (del && armed === del.dataset.del) { send({ cmd: "delete_save", name: del.dataset.del }); armed = ""; }
    else if (del) { armed = del.dataset.del; del.textContent = "Delete?"; toast(`Click again to delete "${armed}"`, false); }
  });
  document.addEventListener("keydown", (e) => {
    if (!m.open) return;
    if (e.key === "Escape" && !m.first) closeMenu();
    else if (e.key === "Enter") $("menu-start").click();
  });
}
