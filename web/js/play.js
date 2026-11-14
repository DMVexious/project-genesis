/* Playing a villager. Once the player has become someone (the "Become them"
 * button), that body does only what it is told:
 *   - click the ground to walk there (click a tree or a bush to go and use it),
 *   - WASD or the arrow keys to take a step,
 *   - the bar at the bottom for whatever they can do where they stand,
 *   - click another villager to talk to them (in your own words), hit them,
 *     trade, give or court; they always answer.
 * Esc leaves the body, which goes back to living its own life. */
import { $, TILE } from "./config.js";
import { send } from "./net.js";
import { state } from "./state.js";

const ICON = { sleep: "💤", eat: "🍖", chop: "🪓", forage: "🍒", build: "🔨", craft: "🛠️", collect: "🫳", interact: "💬" };
const OPTION_ICON = { talk: "💬", fight: "⚔️", trade: "🤝", give: "🎁", mate: "💞" };
const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const name = (id) => state.names.get(id) || "them";

export const playing = () => state.possessed != null;
const order = (o) => send({ cmd: "player", order: o });

// A short label for an action button ("🔨 Build hut"); the full description goes in its tooltip.
function shortLabel(a) {
  const what = a.action.what;
  const defs = state.defs || {};
  if (a.kind === "build") return `${ICON.build} Build ${(defs.objects && defs.objects[what] && defs.objects[what].place) || what}`;
  if (a.kind === "craft") return `${ICON.craft} Make ${(defs.items && defs.items[what] && defs.items[what].label) || what}`;
  if (a.kind === "chop") return `${ICON.chop} Chop`;
  if (a.kind === "forage") return `${ICON.forage} Gather`;
  if (a.kind === "collect") return `${ICON.collect} Pick up`;
  if (a.kind === "eat") return `${ICON.eat} Eat`;
  if (a.kind === "sleep") return `${ICON.sleep} Sleep`;
  return a.label;
}

// --- the bar at the bottom of the screen ----------------------------------------
let barSig = "";
export function renderPlayer(f) {
  const p = f.player;
  const was = state.possessed;
  state.possessed = p ? p.id : null;
  state.player = p || null;
  document.body.classList.toggle("playing", !!p);
  $("player-bar").hidden = !p;
  if (!p) {
    closeInteract();
    return was != null;  // (true: just left a body)
  }
  $("pb-name").textContent = p.name;
  renderHeard(p.incoming);
  renderChat(p.chat);
  $("pb-note").textContent = p.note || (p.order === "walk" ? "walking…" : p.order === "interact" ? "on the way…" : "");
  const acts = p.actions.filter((a) => a.kind !== "interact");
  const sig = acts.map((a) => `${a.kind}:${a.action.what || ""}`).join("|");
  if (sig !== barSig) {
    barSig = sig;
    $("pb-actions").innerHTML = acts.length
      ? acts.map((a, i) => `<button data-i="${i}" title="${esc(a.label)}">${esc(shortLabel(a))}</button>`).join("")
      : `<span class="hint">Nothing to do right here. Walk somewhere.</span>`;
    $("pb-actions").acts = acts;
  }
  if (interactFor != null) fillInteract();  // (what you can do with them changes as you get close)
  return was !== p.id;  // (true: just became someone)
}

// --- the conversation you're in ------------------------------------------------------------
let chatWith = null, chatSig = "";
function renderChat(c) {
  $("pb-chat").hidden = !c;
  if (!c) { chatWith = null; chatSig = ""; return; }
  if (chatWith !== c.with) {  // a new conversation: put the cursor in the box
    chatWith = c.with;
    $("pbc-text").value = "";
    setTimeout(() => $("pbc-text").focus(), 0);
  }
  $("pbc-name").textContent = c.name;
  $("pbc-wait").textContent = c.waiting ? `${c.name} is thinking…` : "";
  const sig = JSON.stringify(c.lines);
  if (sig !== chatSig) {
    chatSig = sig;
    $("pbc-log").innerHTML = c.lines.map((l) =>
      `<div class="pbc-line ${l.me ? "me" : ""}"><b>${esc(l.who)}:</b> ${esc(l.text)}</div>`).join("");
    $("pbc-log").scrollTop = $("pbc-log").scrollHeight;
  }
}

// --- someone is speaking to you --------------------------------------------------------
let heardKey = "";
function renderHeard(inc) {
  $("pb-heard").hidden = !inc;
  if (!inc) { heardKey = ""; return; }
  const key = `${inc.from}:${inc.said}`;
  if (key === heardKey) return;
  heardKey = key;
  $("pb-heard-line").innerHTML = `<b>${esc(inc.name)}</b>: “${esc(inc.said || "…")}”` +
    (inc.want ? ` <span class="hint">(${esc(inc.name)} wants ${esc(inc.want)})</span>` : "");
  $("pb-yes").hidden = $("pb-no").hidden = !inc.consent;
  $("pb-say").hidden = inc.consent;
  $("pb-answer-text").value = "";
  $("pb-answer-text").focus();
}

function answer(accept) {
  send({ cmd: "answer", text: $("pb-answer-text").value.trim(), accept });
  $("pb-heard").hidden = true;
}

// --- the menu that opens on another villager ---------------------------------------
let interactFor = null;

function fillInteract() {
  const id = interactFor, p = state.player;
  const act = p && p.actions.find((a) => a.kind === "interact" && a.action.target === id);
  const near = !!act;
  $("ia-head").innerHTML = `<b>${esc(name(id))}</b> <span class="hint">${near ? "right next to you" : "you'll walk over first"}</span>`;
  const choices = near ? act.choices : [{ option: "fight", label: "attack them" }];
  const sig = JSON.stringify(choices) + id;
  if ($("ia-buttons").dataset.sig === sig) return;
  $("ia-buttons").dataset.sig = sig;
  $("ia-buttons").innerHTML = choices.filter((c) => c.option !== "talk").map((c) => {
    const key = c.option.split(":")[0];
    const text = key === "fight" ? "Hit them" : key === "mate" ? "Ask to start a family" : c.label;
    return `<button data-option="${esc(c.option)}" class="${key}">${OPTION_ICON[key] || ""} ${esc(text)}</button>`;
  }).join("") + (near ? "" : `<span class="hint">Trading and giving open up once you're beside them.</span>`);
}

export function openInteract(id, screenX, screenY) {
  if (!playing() || id === state.possessed) return;
  interactFor = id;
  const box = $("interact"), stage = $("stage").getBoundingClientRect();
  box.hidden = false;
  fillInteract();
  const w = box.offsetWidth, h = box.offsetHeight;
  box.style.left = `${Math.max(8, Math.min(stage.width - w - 8, screenX - w / 2))}px`;
  box.style.top = `${Math.max(8, Math.min(stage.height - h - 60, screenY - h - 24))}px`;
  $("ia-text").value = "";
  $("ia-text").focus();
}

export function closeInteract() {
  interactFor = null;
  $("interact").hidden = true;
}

function doInteract(option) {
  if (interactFor == null) return;
  order({ kind: "interact", target: interactFor, option, text: $("ia-text").value.trim() });
  closeInteract();
}

// --- clicks and keys -----------------------------------------------------------------
// The ground was clicked (world coordinates): walk there.
export function groundClick(sc, wx, wy) {
  if (!playing()) return false;
  closeInteract();
  const x = Math.floor(wx / TILE), y = Math.floor(wy / TILE);
  order({ kind: "walk", x, y });
  const mark = sc.add.image(x * TILE + TILE / 2, y * TILE + TILE - 3, "ring").setDepth(8998).setTint(0x9fe8ff);
  sc.tweens.add({ targets: mark, alpha: 0, scale: 1.6, duration: 700, onComplete: () => mark.destroy() });
  return true;
}

const KEYS = { w: [0, -1], arrowup: [0, -1], s: [0, 1], arrowdown: [0, 1], a: [-1, 0], arrowleft: [-1, 0], d: [1, 0], arrowright: [1, 0] };

export function initPlay() {
  $("pb-actions").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-i]");
    if (b) order({ kind: "act", action: $("pb-actions").acts[Number(b.dataset.i)].action });
  });
  $("pb-leave").onclick = () => send({ cmd: "release" });  // (the toolbar stays on 🎮 Play: click someone else to become them)
  $("ia-buttons").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-option]");
    if (b) doInteract(b.dataset.option);
  });
  $("ia-say").addEventListener("submit", (e) => {
    e.preventDefault();
    if ($("ia-text").value.trim()) doInteract("talk");
  });
  $("ia-close").onclick = closeInteract;
  $("pbc-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("pbc-text").value.trim();
    if (!text) return;
    send({ cmd: "chat", text });
    $("pbc-text").value = "";
  });
  $("pbc-end").onclick = () => send({ cmd: "chat_end" });
  $("pb-answer").addEventListener("submit", (e) => { e.preventDefault(); answer(false); });
  $("pb-yes").onclick = () => answer(true);
  $("pb-no").onclick = () => answer(false);
  document.addEventListener("keydown", (e) => {
    if (!playing()) return;
    const typing = ["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName);
    if (e.key === "Escape") {
      e.preventDefault();
      if (interactFor != null) closeInteract();
      else if (!typing) send({ cmd: "release" });
      return;
    }
    if (typing) return;
    const dir = KEYS[e.key.toLowerCase()];
    if (dir) {
      e.preventDefault();
      order({ kind: "step", dx: dir[0], dy: dir[1] });  // (held down, the key repeats: you keep walking)
    }
  });
}
