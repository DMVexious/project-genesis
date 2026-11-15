/* The side panel's tabs (World, Villages, People, Chronicle, Settings), the
 * charts of how the world has gone over time, the settings, and the little
 * tooltip that names whatever the pointer rests on in the world. */
import { $, TILE } from "./config.js";
import { objectOf, terrainAt } from "./land.js";
import { setVolume } from "./sound.js";
import { state } from "./state.js";

const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// --- tabs ---------------------------------------------------------------------------------
let tab = "world";
export const currentTab = () => tab;
export function showTab(name) {
  tab = name;
  for (const b of $("tabs").children) b.classList.toggle("on", b.dataset.tab === name);
  for (const s of document.querySelectorAll("#panel .tab")) s.hidden = s.dataset.tab !== name;
  if (name === "world") drawChart();
  try { localStorage.setItem("genesis.tab", name); } catch (_e) { /* storage off */ }
}

// --- the world over time -----------------------------------------------------------------
// One sample every SAMPLE ticks, from the live frames: people, food, believers.
const SAMPLE = 20, KEEP = 900;
const hist = { world: "", ticks: [], pop: [], food: [], faith: [] };
const SERIES = [["pop", "people", "#f0cf5c"], ["food", "food", "#8fc45a"], ["faith", "believers", "#c49aff"]];

function foodOf(f) {
  let n = 0;
  for (const p of f.npcs) if (p.alive && p.inv) n += (p.inv.food || 0) + (p.inv.fish || 0);
  for (const v of f.villages || []) if (v.stock) n += (v.stock.food || 0) + (v.stock.fish || 0);
  return n;
}

export function recordStats(f) {
  const world = `${f.world}`;
  if (world !== hist.world) { hist.world = world; for (const k of ["ticks", "pop", "food", "faith"]) hist[k] = []; }
  const last = hist.ticks[hist.ticks.length - 1];
  if (last != null && f.tick < last + SAMPLE) return;  // (also skips the past, while replaying)
  hist.ticks.push(f.tick);
  hist.pop.push(f.stats.population);
  hist.food.push(foodOf(f));
  hist.faith.push((f.god && f.god.believers) || 0);
  if (hist.ticks.length > KEEP) for (const k of ["ticks", "pop", "food", "faith"]) hist[k].shift();
  if (tab === "world") drawChart();
}

function drawChart() {
  const cv = $("chart");
  if (!cv || cv.offsetParent === null) return;
  const dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
  if (cv.width !== Math.round(W * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);
  const n = hist.ticks.length;
  const pad = { l: 4, r: 4, t: 8, b: 14 };
  g.strokeStyle = "rgba(255,255,255,.07)";
  g.lineWidth = 1;
  for (let i = 1; i < 4; i++) { const y = pad.t + ((H - pad.t - pad.b) * i) / 4; g.beginPath(); g.moveTo(pad.l, y); g.lineTo(W - pad.r, y); g.stroke(); }
  const deity = !!(state.frame && state.frame.god && state.frame.god.mode === "deity");
  const shown = SERIES.filter(([k]) => k !== "faith" || deity);
  if (n < 2) {
    g.fillStyle = "#9b8f80"; g.font = "11px sans-serif"; g.textAlign = "center";
    g.fillText("The story so far will be drawn here.", W / 2, H / 2);
  } else {
    // each line on its own scale (the key below gives the numbers)
    for (const [k, , color] of shown) {
      const vals = hist[k], top = Math.max(4, ...vals) * 1.1;
      g.strokeStyle = color; g.lineWidth = 1.6; g.lineJoin = "round";
      g.beginPath();
      vals.forEach((v, i) => {
        const x = pad.l + ((W - pad.l - pad.r) * i) / (n - 1), y = H - pad.b - ((H - pad.t - pad.b) * v) / top;
        if (i) g.lineTo(x, y); else g.moveTo(x, y);
      });
      g.stroke();
    }
    const days = (t) => `day ${Math.floor(t / ((state.defs && state.defs.clock && state.defs.clock.day) || 240)) + 1}`;
    g.fillStyle = "#9b8f80"; g.font = "10px sans-serif";
    g.textAlign = "left"; g.fillText(days(hist.ticks[0]), pad.l, H - 2);
    g.textAlign = "right"; g.fillText(days(hist.ticks[n - 1]), W - pad.r, H - 2);
  }
  const now = (k) => (n ? hist[k][n - 1] : 0), peak = (k) => (n ? Math.max(...hist[k]) : 0);
  $("chart-key").innerHTML = shown.map(([k, label, color]) =>
    `<span><i style="background:${color}"></i>${label} <b>${now(k)}</b> <small>(most ${peak(k)})</small></span>`).join("");
}

// --- family -------------------------------------------------------------------------------
// Parents above, brothers and sisters beside, children below (the dead by name, faded).
export function familyHtml(sel, f) {
  const alive = new Map(f.npcs.map((n) => [n.id, n]));
  const who = (id) => {
    const n = alive.get(id), name = state.names.get(id) || `#${id}`;
    return `<a class="kin ${n && n.alive ? "" : "gone"}" data-id="${id}" title="${n && n.alive ? `${n.age} years old` : "no longer living"}">${esc(name)}</a>`;
  };
  const kids = f.npcs.filter((n) => n.parents.includes(sel.id)).map((n) => n.id);
  const sibs = f.npcs.filter((n) => n.id !== sel.id && n.parents.length && n.parents.some((p) => sel.parents.includes(p))).map((n) => n.id);
  const row = (label, ids, none) => `<div class="fam-row"><small>${label}</small>${ids.length ? ids.map(who).join("") : `<span class="hint">${none}</span>`}</div>`;
  return `<div class="family">
    ${row("parents", sel.parents, "the first settlers")}
    <div class="fam-row me"><small>${sibs.length ? "with" : ""}</small><b>${esc(sel.name)}</b>${sibs.map(who).join("")}</div>
    ${row("children", kids, "none yet")}</div>`;
}

// --- settings -------------------------------------------------------------------------------
const DEFAULTS = { bubbles: "all", names: true, bars: true, volume: 55 };
export const settings = { ...DEFAULTS };
try { Object.assign(settings, JSON.parse(localStorage.getItem("genesis.settings") || "{}")); } catch (_e) { /* fresh */ }
const keep = () => { try { localStorage.setItem("genesis.settings", JSON.stringify(settings)); } catch (_e) { /* storage off */ } };

function applySettings() {
  document.body.classList.toggle("no-bars", !settings.bars);
  document.body.classList.toggle("no-names", !settings.names);
  document.body.dataset.bubbles = settings.bubbles;
  setVolume(settings.volume / 100);
}

function initSettings() {
  const box = $("settings");
  box.querySelector(`input[name="bubbles"][value="${settings.bubbles}"]`).checked = true;
  $("set-names").checked = settings.names;
  $("set-bars").checked = settings.bars;
  $("set-volume").value = settings.volume;
  box.addEventListener("input", () => {
    settings.bubbles = (box.querySelector('input[name="bubbles"]:checked') || {}).value || "all";
    settings.names = $("set-names").checked;
    settings.bars = $("set-bars").checked;
    settings.volume = Number($("set-volume").value);
    keep();
    applySettings();
  });
  applySettings();
}

// --- what is under the pointer ----------------------------------------------------------------
// Rest the pointer on something in the world for a moment and its name shows.
let tipTimer = 0, tipKey = "";
export function hoverTip(wx, wy, sx, sy) {
  const tip = $("tip");
  const f = state.frame;
  const x = Math.floor(wx / TILE), y = Math.floor(wy / TILE);
  const key = `${x},${y}`;
  if (key === tipKey) { place(tip, sx, sy); return; }
  tipKey = key;
  tip.hidden = true;
  clearTimeout(tipTimer);
  if (!f || !f.tiles || x < 0 || y < 0 || x >= f.width || y >= f.height) return;
  tipTimer = setTimeout(() => {
    const text = describeTile(f, x, y);
    if (!text) return;
    tip.innerHTML = text;
    tip.hidden = false;
    place(tip, sx, sy);
  }, 380);
}
export function hideTip() { clearTimeout(tipTimer); tipKey = ""; $("tip").hidden = true; }
function place(tip, sx, sy) { tip.style.left = `${sx + 14}px`; tip.style.top = `${sy + 16}px`; }

function describeTile(f, x, y) {
  const ground = terrainAt(f, x, y);
  const ch = f.tiles[y][x];
  const o = ch !== f.terrain[y][x] ? objectOf(ch) : null;
  const village = (f.villages || []).find((v) => v.x === x && v.y === y);
  const lines = [];
  if (o) {
    const label = o.def.label || o.key;
    const spent = o.spent ? { tree: " (a stump)", bush: " (picked bare)", farm: " (growing)", fruit: " (picked)" }[o.key] || " (used up)" : "";
    lines.push(`<b>${esc(label[0].toUpperCase() + label.slice(1))}</b>${spent}`);
    if (o.key === "site") {
      const s = (f.sites || []).find(([sx, sy]) => sx === x && sy === y);
      const what = s && state.defs.objects[s[2]];
      if (s) lines.push(`building ${esc((what && what.label) || s[2])}: ${s[3]}% done`);
    }
    const home = f.npcs.filter((n) => n.alive && n.home && n.home[0] === x && n.home[1] === y).map((n) => esc(n.name));
    if (home.length) lines.push(`home of ${home.join(", ")}`);
  }
  if (village) lines.push(`the hearth of <b>${esc(village.name)}</b>`);
  if (!lines.length && ground && ground.key !== "grass") lines.push(esc(ground.label || ground.key));
  if (ground && lines.length && o) lines.push(`<span class="hint">on ${esc(ground.label || ground.key)}</span>`);
  return lines.join("<br>");
}

export function initTabs() {
  $("tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-tab]");
    if (b) showTab(b.dataset.tab);
  });
  let first = "world";
  try { first = localStorage.getItem("genesis.tab") || "world"; } catch (_e) { /* storage off */ }
  showTab(document.querySelector(`#tabs button[data-tab="${first}"]`) ? first : "world");
  initSettings();
  window.addEventListener("resize", () => { if (tab === "world") drawChart(); });
}
