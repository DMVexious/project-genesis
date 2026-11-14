/* What a click on a villager does: the three modes, shown as a toolbar over the world.
 *   👁 Watch: follow and inspect them.
 *   ⚡ God:   a menu on them: smite, heal, feed, speak from the sky, or become them.
 *   🎮 Play:  become them; from then on you steer that body (see play.js).
 * Plus the controls sheet (the ❓ button, or H), shown once on a first visit. */
import { $ } from "./config.js";
import { send } from "./net.js";
import { focus } from "./panel.js";
import { state } from "./state.js";

const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const HINT = {
  watch: "Watch: click a villager to follow them and see their stats.",
  god: "God mode: click a villager to smite, heal, feed or speak to them.",
  play: "Play: click a villager to become them.",
};
let mode = "watch";
export const currentMode = () => mode;

export function setMode(m) {
  if (m !== "play" && state.possessed != null) send({ cmd: "release" });  // leaving play leaves the body
  if (m === "play" && mode !== "play") before = mode;
  mode = m;
  closeGodMenu();
  for (const b of document.querySelectorAll("#modes button[data-mode]")) b.classList.toggle("on", b.dataset.mode === m);
  document.body.dataset.mode = m;
  showHint();
}

function showHint() {
  const playingSomeone = state.possessed != null;
  $("mode-hint").textContent = playingSomeone ? "" : HINT[mode];
  $("mode-hint").hidden = playingSomeone;
}

// Called with every frame: keep the toolbar in step with the server (a body can be
// left with Esc, or die).
let before = "watch";   // the mode to go back to on leaving a body
let wasIn = null;
export function syncMode() {
  const inBody = state.possessed;
  if (inBody != null && wasIn == null) {          // just became someone: the god steps aside
    before = mode === "play" ? before : mode;
    mode = "play";
    closeGodMenu();
  } else if (inBody == null && wasIn != null) {   // just left the body: the god is back
    setMode(before === "play" ? "watch" : before);
  }
  wasIn = inBody;
  showHint();
}

// A villager was clicked (and the player is not already in someone's body).
// Returns true if this mode handled it.
export function villagerClick(id, screenX, screenY) {
  if (mode === "play") { send({ cmd: "possess", npc: id }); focus(id); return true; }
  if (mode === "god") { openGodMenu(id, screenX, screenY); return true; }
  return false;
}

// --- the god menu --------------------------------------------------------------------
let godFor = null;
function openGodMenu(id, x, y) {
  godFor = id;
  focus(id);
  const box = $("godmenu"), stage = $("stage").getBoundingClientRect();
  $("gm-head").innerHTML = `⚡ <b>${esc(state.names.get(id) || "?")}</b>`;
  box.hidden = false;
  const w = box.offsetWidth, h = box.offsetHeight;
  box.style.left = `${Math.max(8, Math.min(stage.width - w - 8, x - w / 2))}px`;
  box.style.top = `${Math.max(8, Math.min(stage.height - h - 60, y - h - 24))}px`;
  $("gm-text").value = "";
  $("gm-text").focus();
}

export function closeGodMenu() {
  godFor = null;
  $("godmenu").hidden = true;
}

// --- the controls sheet ------------------------------------------------------------------
export function toggleHelp(show) {
  const box = $("help");
  box.hidden = show === undefined ? !box.hidden : !show;
  if (box.hidden) try { localStorage.setItem("genesis.helpSeen", "1"); } catch (_e) { /* storage off */ }
}

export function helpOnFirstVisit() {
  let seen = false;
  try { seen = localStorage.getItem("genesis.helpSeen") === "1"; } catch (_e) { /* storage off */ }
  if (!seen) toggleHelp(true);
}

export function initModes() {
  $("modes").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-mode]");
    if (b) setMode(b.dataset.mode);
    else if (e.target.closest("#btn-help")) toggleHelp();
  });
  $("help-close").onclick = () => toggleHelp(false);
  $("gm-close").onclick = closeGodMenu;
  $("gm-buttons").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-power]");
    if (!b || godFor == null) return;
    const id = godFor;  // (switching mode closes this menu, and forgets who it was for)
    if (b.dataset.power === "become") { setMode("play"); send({ cmd: "possess", npc: id }); }
    else send({ cmd: "god", power: b.dataset.power, npc: id });
    closeGodMenu();
  });
  $("gm-say").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("gm-text").value.trim();
    if (text && godFor != null) send({ cmd: "say", npc: godFor, text });
    closeGodMenu();
  });
  document.addEventListener("keydown", (e) => {
    if (["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName)) {
      if (e.key === "Escape" && godFor != null) closeGodMenu();
      return;
    }
    if (e.key === "Escape" && godFor != null) { closeGodMenu(); return; }
    if (e.key === "Escape" && !$("help").hidden) { toggleHelp(false); return; }
    if (state.possessed != null) return;  // (while playing, the keys walk: see play.js)
    const pick = { 1: "watch", 2: "god", 3: "play" }[e.key];
    if (pick) setMode(pick);
    else if (e.key === "h" || e.key === "H" || e.key === "?") toggleHelp();
  });
  setMode("watch");
}
