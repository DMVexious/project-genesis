/* The side panel: stats, population list, inspector, conversations, chronicle. */
import { $, AGGRO_COLOR, MAX_TALKS, hex } from "./config.js";
import { state } from "./state.js";
import { portrait } from "./textures.js";
import { scene, stageOf } from "./world.js";

// --- side panel ------------------------------------------------------------------
export const BAR_COLORS = { health: "#d9534f", hunger: "#e0a458", energy: "#6aa6d8", social: "#e07aa8" };
export const SOURCE_LABEL = { llm: "  🧠 LLM", reflex: "  ⚡ reflex", plan: "  ↻ continuing" };
export const BAR_LABELS = { health: "Health", hunger: "Fullness", energy: "Energy", social: "Social" };
export const STAGE_ICON = { child: "👶", elder: "🧓", adult: "" };
export const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

export function describeLook(l) {
  if (!l) return "";
  const hair = l.hairstyle === "bald" ? "bald" : `${l.hair} ${l.hairstyle} hair`;
  const hat = l.hat && l.hat !== "none" ? ` · ${l.hat}${l.hat === "hood" || l.hat === "headband" ? "" : " hat"}` : "";
  return `${l.skin} skin · ${hair} · ${l.color} ${l.clothes}${hat}`;
}

// The panel is redrawn every tick, but most of it is the same as the tick
// before. Only touching the DOM when the markup really changed keeps hover,
// focus and scroll position intact (and anything interactive usable).
const lastHtml = new WeakMap();
function setHtml(el, html) {
  if (lastHtml.get(el) === html) return false;
  lastHtml.set(el, html);
  el.innerHTML = html;
  return true;
}

// One <li> per NPC, kept between ticks and updated in place.
const rows = new Map();  // npc id -> <li>
function renderList(npcs) {
  const list = $("npc-list");
  const seen = new Set();
  let prev = null;
  for (const n of npcs) {
    seen.add(n.id);
    let li = rows.get(n.id);
    if (!li) {
      li = document.createElement("li");
      li.dataset.id = n.id;
      rows.set(n.id, li);
    }
    const cls = `${n.id === state.selected ? "sel" : ""} ${n.alive ? "" : "dead"}`.trim();
    if (li.className !== cls) li.className = cls;
    setHtml(li, `
      <span class="swatch" style="background:${hex(AGGRO_COLOR[n.traits.aggression])}"></span>
      <span class="name">${esc(n.name)} <small>g${n.generation}</small> ${STAGE_ICON[stageOf(n)]}${n.home ? "🏠" : ""} ${n.status}</span>
      <span class="minibars">${["health", "hunger", "energy"].map((k) =>
        `<i style="width:${n[k]}%;background:${BAR_COLORS[k]}"></i>`).join("")}</span>
      <span>${n.emoji}</span>`);
    const after = prev ? prev.nextSibling : list.firstChild;
    if (after !== li) list.insertBefore(li, after);
    prev = li;
  }
  for (const [id, li] of rows) if (!seen.has(id)) { li.remove(); rows.delete(id); }
}

// What an NPC carries, e.g. "🪵 12 wood · 🍒 3 food · 🗡️ spear".
function describeInv(inv) {
  const items = (state.defs && state.defs.items) || {};
  const parts = Object.entries(inv || {}).filter(([, n]) => n > 0).map(([k, n]) => {
    const d = items[k] || { label: k, emoji: "" };
    return `${d.emoji} ${n > 1 || !d.emoji ? n + " " : ""}${esc(d.label)}`.trim();
  });
  return parts.length ? parts.join(" · ") : "carrying nothing";
}

export function renderPanel(f) {
  const s = f.stats;
  const built = Object.values((state.defs && state.defs.objects) || {}).filter((o) => o.stat && o.stat in s)
    .map((o) => [o.stat, s[o.stat]]);
  const cells = [["pop", s.population], ["births", s.births], ["deaths", s.deaths], ["gen", s.max_generation],
    ["tick", f.tick], ...built, ["fights", s.fights], ["trades", s.trades]];
  setHtml($("stats"), cells.map(([k, v]) => `<div class="stat"><b>${v}</b><span>${k}</span></div>`).join("") +
    `<div class="stack">brain: ${esc(f.brain)} · memory: ${esc(f.memory)} (${s.memories})</div>` +
    (s.decisions ? `<div class="stack">decisions: 🧠 ${s.decisions.llm} · ⚡ ${s.decisions.reflex} · ↻ ${s.decisions.plan}</div>` : ""));

  renderList([...f.npcs].sort((a, b) => a.id - b.id));

  const sel = f.npcs.find((n) => n.id === state.selected);
  const det = $("detail");
  det.hidden = !sel;
  let detail = "";
  if (sel) {
    const t = sel.traits;
    const parents = sel.parents.map((p) => esc(state.names.get(p) || `#${p}`)).join(" & ") || "first settlers";
    const sp = state.sprites.get(sel.id);
    const img = sp ? portrait(sp.key) : "";
    detail = `
      <div class="who">
        ${img ? `<img class="portrait" src="${img}" alt="">` : ""}
        <div>
          <h3>${esc(sel.name)} ${sel.status} ${sel.emoji}</h3>
          <div class="look">${esc(stageOf(sel))} · ${esc(describeLook(sel.look))}</div>
        </div>
      </div>
      <div class="traits">${Object.entries(t).map(([slot, v]) =>
        `<span class="trait ${sel.mutated.includes(slot) ? "mut" : ""}" title="${slot}${sel.mutated.includes(slot) ? " (mutation)" : ""}">${v}</span>`).join("")}</div>
      ${sel.belief ? `<div class="persona">🌀 ${esc(sel.name)} ${esc(sel.belief)}.<br>🗣️ They ${esc(sel.voice.replace(/\byourself\b/g, "themself").replace(/\byour\b/g, "their"))}.</div>` : ""}
      ${["health", "hunger", "energy", "social"].map((k) => `
        <div class="bar"><span>${BAR_LABELS[k]}</span>
        <span class="track"><span class="fill" style="display:block;width:${sel[k]}%;background:${BAR_COLORS[k]}"></span></span>
        <span>${sel[k]}</span></div>`).join("")}
      <div class="meta">${describeInv(sel.inv)}</div>
      <div class="meta">age ${sel.age} · gen ${sel.generation} · ${parents}</div>
      <div class="meta">${sel.home ? `🏠 home at (${sel.home[0]}, ${sel.home[1]})` : "no home yet"}</div>
      <div class="intent">${sel.alive ? "▶ " + esc(sel.intent || "…") + (SOURCE_LABEL[sel.source] || "") : "died: " + esc(sel.cause)}</div>`;
  }

  renderFeeds(f);
  if (sel) {
    const mine = state.talks.filter((t) => t.a === sel.id || t.b === sel.id).slice(0, 4);
    detail += `<div class="sub">Recent conversations</div>` +
      (mine.length ? `<ol class="talks">${mine.map(talkHtml).join("")}</ol>` : `<div class="hint">Hasn't spoken to anyone yet.</div>`);
  }
  setHtml(det, detail);

  $("banner").hidden = s.population > 0;
  $("banner").textContent = "Extinction. Start a new world →";
}

// New lines for the Conversations feed and the Chronicle (kept live during replay too).
export function renderFeeds(f) {
  if (f.dialogue.length) {
    for (const d of f.dialogue) {
      if (d.reply) continue;
      const r = f.dialogue.find((x) => x.reply && x.npc === d.target && x.target === d.npc);
      state.talks.unshift({ tick: f.tick, a: d.npc, b: d.target, kind: d.kind, said: d.text, reply: r ? r.text : null,
                            llm: !!d.llm, replyLlm: !!(r && r.llm) });
    }
    state.talks.length = Math.min(state.talks.length, MAX_TALKS);
    renderTalks();
  }
  const log = $("log");
  const items = [];
  for (const line of f.log.filter((l) => !l.includes(" talks to "))) items.push(`<li><b>${f.tick}</b>${esc(line)}</li>`);
  if (items.length) {
    log.insertAdjacentHTML("afterbegin", items.reverse().join(""));
    while (log.children.length > 150) log.lastChild.remove();
  }
}

export const KIND_ICON = { talk: "💬", fight: "⚔️", trade: "🤝", mate: "💞" };
export const KIND_VERB = { talk: "to", fight: "attacking", trade: "trading with", mate: "courting" };

export function talkHtml(t) {
  const who = (id) => `<a data-id="${id}">${esc(state.names.get(id) || "?")}</a>`;
  return `<li class="talk ${t.kind}">
    <div class="head"><b>${t.tick}</b> ${KIND_ICON[t.kind] || "💬"} ${who(t.a)} <span>${KIND_VERB[t.kind] || "to"}</span> ${who(t.b)}</div>
    <div class="line ${t.llm ? "llm" : ""}">${who(t.a)}: “${esc(t.said)}”${t.llm ? LLM_MARK : ""}</div>
    ${t.reply ? `<div class="line reply ${t.replyLlm ? "llm" : ""}">${who(t.b)}: “${esc(t.reply)}”${t.replyLlm ? LLM_MARK : ""}</div>` : ""}
  </li>`;
}

export const LLM_MARK = ` <span class="mark" title="written by the LLM (unmarked lines are pre-written)">🧠</span>`;

export function renderTalks() {
  const llmOnly = $("llm-only").checked;
  const shown = llmOnly ? state.talks.filter((t) => t.llm || t.replyLlm) : state.talks;
  $("talks").innerHTML = shown.length ? shown.map(talkHtml).join("")
    : `<li class="hint">${llmOnly ? "No LLM-written lines yet." : "Nobody has spoken yet. NPCs talk when they meet."}</li>`;
}

export function select(id) {
  state.selected = state.selected === id ? null : id;
  const sc = scene();
  const s = state.sprites.get(state.selected);
  if (sc && sc.cameras) {
    if (s) sc.cameras.main.startFollow(s.sprite, true, 0.08, 0.08);
    else sc.cameras.main.stopFollow();
  }
  if (state.frame) renderPanel({ ...state.frame, dialogue: [], log: [] });
}
