/* The side panel: stats, population list, inspector, conversations, chronicle. */
import { $, AGGRO_COLOR, MAX_TALKS, TILE, hex } from "./config.js";
import { send } from "./net.js";
import { playSelect } from "./sound.js";
import { villageColor } from "./villages.js";
import { state } from "./state.js";
import { portrait } from "./textures.js";
import { scene, stageOf } from "./world.js";

// --- side panel ------------------------------------------------------------------
export const BAR_COLORS = { health: "#d9534f", hunger: "#e0a458", energy: "#6aa6d8", social: "#e07aa8" };
export const SOURCE_LABEL = { llm: "  🧠 LLM", reflex: "  ⚡ reflex", plan: "  ↻ continuing", player: "  🎮 you" };
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
    const role = roleOf(n);
    setHtml(li, `
      <span class="swatch" style="background:${hex(AGGRO_COLOR[n.traits.aggression])}"></span>
      <span class="name">${esc(n.name)} <small>g${n.generation}</small> ${role ? `<span title="${esc(role.role)}">${role.emoji}</span>` : ""}${STAGE_ICON[stageOf(n)]}${n.home ? "🏠" : ""} ${n.status}</span>
      <span class="minibars">${["health", "hunger", "energy"].map((k) =>
        `<i style="width:${n[k]}%;background:${BAR_COLORS[k]}"></i>`).join("")}</span>
      <span>${n.emoji}</span>`);
    const after = prev ? prev.nextSibling : list.firstChild;
    if (after !== li) list.insertBefore(li, after);
    prev = li;
  }
  for (const [id, li] of rows) if (!seen.has(id)) { li.remove(); rows.delete(id); }
}

// What an NPC carries, as a row of slots: one per kind of item, with a count.
// (How much fits depends on the NPC: the strong carry more.)
const INV_SLOTS = 6;
function invSlots(inv, caps) {
  const items = (state.defs && state.defs.items) || {};
  const keys = [...new Set([...Object.keys(items), ...Object.keys(inv || {})])];
  const slots = keys.map((k) => {
    const d = items[k] || { label: k, emoji: "?", cap: 0 };
    const n = (inv && inv[k]) || 0;
    const cap = (caps && caps[k]) || d.cap;
    const full = cap && n >= cap;
    return `<span class="slot ${n ? "" : "empty"} ${full ? "full" : ""}" title="${esc(d.label)}: ${n}${cap ? ` of ${cap}` : ""}">` +
      `<span class="ico">${d.emoji}</span>${n ? `<b>${n}</b>` : ""}</span>`;
  });
  while (slots.length < INV_SLOTS) slots.push('<span class="slot empty"></span>');
  return `<div class="inv">${slots.join("")}</div>`;
}

// What an NPC is known for (its best skill, once good enough), or null.
function roleOf(n) {
  const skills = (state.defs && state.defs.skills) || {};
  return (n.role && skills[n.role]) || null;
}

// --- villages ----------------------------------------------------------------------------
function villageOf(n) {
  const v = state.frame && (state.frame.villages || []).find((x) => x.id === n.village);
  return v ? `of ${esc(v.name)} · ` : "";
}

const FEELING = (r) => (r >= 5 ? ["😊", "friendly"] : r <= -3 ? ["😠", "hostile"] : ["😐", "wary"]);
function villagesHtml(f) {
  const vs = f.villages || [];
  if (!vs.length) return `<div class="hint">No villages yet.</div>`;
  const objs = (state.defs && state.defs.objects) || {}, items = (state.defs && state.defs.items) || {};
  const nameOf = (id) => (vs.find((v) => String(v.id) === String(id)) || { name: "?" }).name;
  return vs.map((v) => {
    const color = "#" + villageColor(v.id).toString(16).padStart(6, "0");
    const built = Object.entries(v.buildings).map(([k, n]) =>
      `<span title="${esc((objs[k] && objs[k].label) || k)}">${(objs[k] && objs[k].emoji) || "🏠"}${n > 1 ? n : ""}</span>`).join(" ");
    const store = Object.entries(v.stock || {}).filter(([, n]) => n > 0).map(([k, n]) =>
      `${(items[k] && items[k].emoji) || ""}${n}`).join(" ");
    const rel = Object.entries(v.relations || {}).map(([id, r]) => {
      const [face, word] = FEELING(r);
      return `<span title="${word}">${face} ${esc(nameOf(id))}</span>`;
    }).join(" · ");
    const full = v.people >= v.room;
    return `<div class="village ${v.abandoned ? "gone" : ""}" data-x="${v.x}" data-y="${v.y}">
      <div class="vh"><span class="dot" style="background:${color}"></span><b>${esc(v.name)}</b>
        <span class="hint">${v.abandoned ? "abandoned" : `${v.people} people · homes for ${v.room}${full ? " (full)" : ""}`}</span></div>
      ${v.abandoned ? "" : `<div class="vb">${built || '<span class="hint">nothing built yet</span>'}</div>
      ${store ? `<div class="vs">📦 ${store}</div>` : ""}
      ${rel ? `<div class="vr">${rel}</div>` : ""}`}</div>`;
  }).join("");
}

// Strength, agility...: what they are made of. Partly inherited, built up by what they do.
function attrCells(n) {
  const defs = (state.defs && state.defs.attrs) || {};
  const max = (state.defs && state.defs.limits && state.defs.limits.attr) || 10;
  return `<div class="attrs">${Object.entries(defs).map(([k, d]) => {
    const v = (n.attrs && n.attrs[k]) || 0;
    return `<div class="attr" title="${esc(d.label)} ${v} of ${max}: ${esc(d.blurb)}">
      <span class="ico">${d.emoji}</span><b>${v}</b><small>${esc(d.label.slice(0, 3))}</small>
      <span class="track"><span class="fill" style="width:${(100 * v) / max}%"></span></span></div>`;
  }).join("")}</div>`;
}

// Skills they have practised: level, and how far along to the next one.
function skillRows(n) {
  const defs = (state.defs && state.defs.skills) || {};
  const max = (state.defs && state.defs.limits && state.defs.limits.skill) || 10;
  const rows = Object.entries(defs).filter(([k]) => (n.skills && n.skills[k]) || (n.xp && n.xp[k])).map(([k, d]) => {
    const level = (n.skills && n.skills[k]) || 0;
    const [have, need] = (n.xp && n.xp[k]) || [0, 1];
    const pct = level >= max ? 100 : Math.min(100, (100 * have) / need);
    return `<div class="bar skill ${n.role === k ? "best" : ""}" title="${esc(d.label)}: ${esc(d.blurb)}${level >= max ? "" : ` (${have} of ${need} to the next level)`}">
      <span>${d.emoji} ${esc(d.label)}</span>
      <span class="track"><span class="fill" style="display:block;width:${pct}%"></span></span>
      <span>${level >= max ? "max" : `Lv ${level}`}</span></div>`;
  });
  return rows.length ? rows.join("") : `<div class="hint">Nothing yet. They get better at whatever they do.</div>`;
}

export function renderPanel(f) {
  const s = f.stats;
  const built = Object.values((state.defs && state.defs.objects) || {}).filter((o) => o.stat && o.stat in s)
    .map((o) => [o.stat, s[o.stat]]);
  const cells = [["pop", s.population], ["births", s.births], ["deaths", s.deaths], ["gen", s.max_generation],
    ["tick", f.tick], ...built, ["fights", s.fights], ["trades", s.trades]];
  setHtml($("stats"), cells.map(([k, v]) => `<div class="stat"><b>${v}</b><span>${k}</span></div>`).join("") +
    `<div class="stack">brain: ${esc(f.brain)}${f.talker && f.talker !== f.brain ? ` · talks: ${esc(f.talker)}` : ""} · memory: ${esc(f.memory)} (${s.memories})</div>` +
    (s.decisions ? `<div class="stack">decisions: 🧠 ${s.decisions.llm} · ⚡ ${s.decisions.reflex} · ↻ ${s.decisions.plan}</div>` : ""));

  renderList([...f.npcs].sort((a, b) => a.id - b.id));
  setHtml($("villages"), villagesHtml(f));

  const sel = f.npcs.find((n) => n.id === state.selected);
  const det = $("detail");
  det.hidden = !sel;
  let detail = "", head = "";
  updateGod(sel);
  if (sel) {
    const t = sel.traits;
    const parents = sel.parents.map((p) => esc(state.names.get(p) || `#${p}`)).join(" & ") || "first settlers";
    const sp = state.sprites.get(sel.id);
    const img = sp ? portrait(sp.key) : "";
    const role = roleOf(sel);
    head = `
      <div class="who">
        ${img ? `<img class="portrait" src="${img}" alt="">` : ""}
        <div>
          <h3>${esc(sel.name)} ${sel.status} ${sel.emoji}</h3>
          <div class="role">${role ? `${role.emoji} ${esc(role.role)} · ` : ""}${villageOf(sel)}${sel.age} years old${stageOf(sel) === "adult" ? "" : ` (${esc(stageOf(sel))})`}</div>
          <div class="look">${esc(describeLook(sel.look))}</div>
        </div>
      </div>`;
    detail = `
      <div class="traits">${Object.entries(t).map(([slot, v]) =>
        `<span class="trait ${sel.mutated.includes(slot) ? "mut" : ""}" title="${slot}${sel.mutated.includes(slot) ? " (mutation)" : ""}">${v}</span>`).join("")}</div>
      ${sel.belief ? `<div class="persona">🌀 ${esc(sel.name)} ${esc(sel.belief)}.<br>🗣️ They ${esc(sel.voice.replace(/\byourself\b/g, "themself").replace(/\byour\b/g, "their"))}.</div>` : ""}
      ${["health", "hunger", "energy", "social"].map((k) => `
        <div class="bar"><span>${BAR_LABELS[k]}</span>
        <span class="track"><span class="fill" style="display:block;width:${sel[k]}%;background:${BAR_COLORS[k]}"></span></span>
        <span>${sel[k]}</span></div>`).join("")}
      <div class="sub">Attributes</div>
      ${attrCells(sel)}
      <div class="sub">Skills</div>
      ${skillRows(sel)}
      <div class="sub">Inventory</div>
      ${invSlots(sel.inv, sel.caps)}
      <div class="meta">generation ${sel.generation} · ${parents}</div>
      <div class="meta">${sel.home ? `🏠 home at (${sel.home[0]}, ${sel.home[1]})` : "no home yet"}</div>
      ${sel.expecting ? `<div class="bar"><span>🤰 Expecting</span>
        <span class="track"><span class="fill" style="display:block;width:${sel.expecting}%;background:#e07aa8"></span></span>
        <span>${sel.expecting}%</span></div>` : ""}
      <div class="intent">${sel.alive ? "▶ " + esc(sel.intent || "…") + (SOURCE_LABEL[sel.source] || "") : "died: " + esc(sel.cause)}</div>`;
  }

  renderFeeds(f);
  if (sel) {
    const mine = state.talks.filter((t) => t.a === sel.id || t.b === sel.id).slice(0, 4);
    detail += `<div class="sub">Recent conversations</div>` +
      (mine.length ? `<ol class="talks">${mine.map(talkHtml).join("")}</ol>` : `<div class="hint">Hasn't spoken to anyone yet.</div>`);
  }
  setHtml($("detail-head"), head);
  setHtml($("detail-body"), detail);

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
                            llm: !!d.llm, replyLlm: !!(r && r.llm), deal: d.deal || "", player: !!d.player });
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

export const KIND_ICON = { talk: "💬", fight: "⚔️", trade: "🤝", give: "🎁", mate: "💞", voice: "☁️" };
export const KIND_VERB = { talk: "to", fight: "attacking", trade: "trading with", give: "giving to", mate: "courting",
                           voice: "speaks to" };

export function talkHtml(t) {
  const who = (id) => (id === -1 ? `<b class="sky">The Voice</b>` : `<a data-id="${id}">${esc(state.names.get(id) || "?")}</a>`);
  return `<li class="talk ${t.kind}">
    <div class="head"><b>${t.tick}</b> ${KIND_ICON[t.kind] || "💬"} ${who(t.a)} <span>${KIND_VERB[t.kind] || "to"}</span> ${who(t.b)}</div>
    ${t.deal ? `<div class="deal">${esc(t.deal.replace(/\bthem\b/g, state.names.get(t.b) || "them"))}</div>` : ""}
    <div class="line ${t.llm || t.player ? "llm" : ""}">${who(t.a)}: “${esc(t.said)}”${t.player ? " 🎮" : t.llm ? LLM_MARK : ""}</div>
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
  focus(state.selected === id && id !== state.possessed ? null : id);
}

// Select a villager (null: nobody) and keep the camera on them; while playing
// someone, the camera stays on the player's own body.
export function focus(id) {
  state.selected = id;
  if (id !== null) playSelect();  // (a click also lets the browser start audio)
  const sc = scene();
  const s = state.sprites.get(state.possessed != null ? state.possessed : id);
  if (sc && sc.cameras) {
    if (s) sc.cameras.main.startFollow(s.sprite, true, 0.08, 0.08);
    else sc.cameras.main.stopFollow();
  }
  if (state.frame) renderPanel({ ...state.frame, dialogue: [], log: [] });
}

// --- the god box: become them, heal, feed, smite, and speak ------------------------------
let godFor;
function updateGod(sel) {
  const box = $("god");
  box.hidden = !sel || !sel.alive;
  if (box.hidden) return;
  const me = state.possessed, mine = sel.id === me;
  const key = `${sel.id}:${me}`;
  if (godFor === key) return;
  godFor = key;
  $("btn-possess").textContent = mine ? "🚪 Leave their body" : me != null ? "🎮 Become them instead" : "🎮 Become them";
  $("say-form").hidden = mine;
  $("say-text").placeholder = me != null
    ? `Say something to ${sel.name} (as ${state.names.get(me) || "you"})…`
    : `Speak to ${sel.name} from the sky…`;
}

export function initGod() {
  $("villages").addEventListener("click", (e) => {  // click a village: look at it
    const v = e.target.closest(".village");
    const sc = scene();
    if (!v || !sc || !sc.cameras) return;
    sc.cameras.main.stopFollow();
    sc.cameras.main.pan((Number(v.dataset.x) + 0.5) * TILE, (Number(v.dataset.y) + 0.5) * TILE, 600, "Sine.easeInOut");
  });
  $("btn-possess").onclick = () => {
    const id = state.selected;
    if (id == null) return;
    send(id === state.possessed ? { cmd: "release" } : { cmd: "possess", npc: id });
  };
  for (const power of ["heal", "feed", "smite"])
    $(`btn-${power}`).onclick = () => { if (state.selected != null) send({ cmd: "god", power, npc: state.selected }); };
  $("say-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("say-text").value.trim(), id = state.selected;
    if (!text || id == null) return;
    if (state.possessed != null && id !== state.possessed)
      send({ cmd: "player", order: { kind: "interact", target: id, option: "talk", text } });  // walk over and say it
    else send({ cmd: "say", npc: id, text });
    $("say-text").value = "";
  });
}
