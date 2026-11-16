/* Project Genesis — Phaser front end (entry point).
 *
 * Phaser draws the world (tiles + NPC sprites); emojis and speech bubbles are
 * HTML elements in #overlay that track sprite positions every animation frame.
 * Talks to the Python orchestrator over a WebSocket. This file wires the
 * page's controls; the work is in the modules next to it.
 */
import { $ } from "./config.js";
import { initGodbar } from "./god.js";
import { initTabs } from "./tabs.js";
import { initMenu, menuOpen, openMenu } from "./menu.js";
import { initMinimap, toggleMinimap } from "./minimap.js";
import { connect, send } from "./net.js";
import { focus, initGod, renderTalks, select } from "./panel.js";
import { initModes } from "./modes.js";
import { initPlay, playing } from "./play.js";
import { initFp } from "./fp/fp.js";
import { initVoice } from "./voice.js";
import { goLive, rp, seek, setDir, step } from "./replay.js";
import { setSound, soundOn } from "./sound.js";
import { state } from "./state.js";

$("tl-scrub").oninput = (e) => {
  const idx = Number(e.target.value);
  if (state.replay) setDir(0);
  seek(idx);
};
$("tl-scrub").onchange = (e) => { if (Number(e.target.value) >= state.history.length - 1) goLive(); };
$("tl-play").onclick = () => setDir(rp() && rp().dir === 1 ? 0 : 1);
$("tl-rev").onclick = () => setDir(rp() && rp().dir === -1 ? 0 : -1);
$("tl-back").onclick = () => { setDir(0); step(-1); };
$("tl-fwd").onclick = () => { if (!rp()) return; setDir(0); step(1); };
$("tl-live").onclick = goLive;
$("tl-speed").onchange = (e) => { if (rp()) rp().speed = Number(e.target.value) || 1; };
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT" || menuOpen()) return;
  if (playing() && e.key === "Escape") return;  // (Esc leaves the body: see play.js)
  if (e.key === "m" || e.key === "M") toggleMinimap();
  else if ((e.key === "p" || e.key === "P") && !e.ctrlKey && !e.metaKey) togglePanel();
  else if (e.key === "," || e.key === "<") {
    e.preventDefault();
    if (e.shiftKey || e.key === "<") $("tl-rev").click(); else $("tl-back").click();
  } else if (e.key === ".") { e.preventDefault(); $("tl-fwd").click(); }
  else if (e.key === "Enter" && rp()) { e.preventDefault(); $("tl-play").click(); }
  else if (e.key === "Escape" && rp()) { e.preventDefault(); goLive(); }
});

const showSound = () => {
  $("btn-sound").textContent = soundOn() ? "🔊" : "🔇";
  $("btn-sound").classList.toggle("off", !soundOn());
};
$("btn-sound").onclick = () => { setSound(!soundOn()); showSound(); };
showSound();

$("btn-pause").onclick =() => send({ cmd: state.paused ? "resume" : "pause" });
$("btn-step").onclick = () => send({ cmd: "step" });
$("speed").oninput = (e) => send({ cmd: "speed", tick_ms: 1050 - Number(e.target.value) });
$("btn-reset").onclick = () => openMenu(false);
$("btn-map").onclick = toggleMinimap;

// The side panel folds away for a full-screen world (the ‹ › tab or P); remembered per browser.
function togglePanel(off = !document.body.classList.contains("panel-off")) {
  document.body.classList.toggle("panel-off", off);
  $("panel-toggle").textContent = off ? "‹" : "›";
  $("panel-toggle").title = off ? "Show the side panel (P)" : "Hide the side panel for a full-screen view (P)";
  try { localStorage.setItem("genesis.panelOff", off ? "1" : ""); } catch (e) { /* storage off */ }
  window.dispatchEvent(new Event("resize"));  // Phaser and the chart refit to the wider stage now
}
$("panel-toggle").onclick = () => togglePanel();
try { if (localStorage.getItem("genesis.panelOff") === "1") togglePanel(true); } catch (e) { /* storage off */ }
$("npc-list").onclick = (e) => {
  const li = e.target.closest("li[data-id]");
  if (li) select(Number(li.dataset.id));
};
$("llm-only").onchange = () => {
  try { localStorage.setItem("genesis.llmOnly", $("llm-only").checked ? "1" : ""); } catch (e) { /* storage off */ }
  renderTalks();
};
try { $("llm-only").checked = localStorage.getItem("genesis.llmOnly") === "1"; } catch (e) { /* storage off */ }
// clicking a name in a conversation follows that NPC
for (const box of [$("talks"), $("detail")]) box.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-id]");
  if (a) select(Number(a.dataset.id));
});
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) {  // quick save
    e.preventDefault();
    send({ cmd: "save", name: "quicksave" });
    return;
  }
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT" || menuOpen()) return;
  if (e.code === "Space") {
    e.preventDefault();
    send({ cmd: state.paused ? "resume" : "pause" });
  } else if (playing()) {
    return;  // (the keys below would also walk the body)
  } else if (e.key === "+" || e.key === "=") {
    send({ cmd: "speed", tick_ms: Math.max(50, state.tickMs - 50) });
  } else if (e.key === "-" || e.key === "_") {
    send({ cmd: "speed", tick_ms: Math.min(1000, state.tickMs + 50) });
  } else if ((e.key === "f" || e.key === "F") && state.selected != null) {
    focus(state.selected);
  }
});

// Saving: the 💾 button opens a box to name the save (a name already used is overwritten).
const defaultName = () => {
  const f = state.frame, v = f && f.villages && f.villages.find((x) => !x.abandoned);
  return `${v ? v.name : "World"} day ${(f && f.clock && f.clock.day) || 1}`;
};
$("btn-save").onclick = () => {
  $("save-form").hidden = !$("save-form").hidden;
  if (!$("save-form").hidden) { $("save-name").value = defaultName(); $("save-name").select(); }
};
$("save-cancel").onclick = () => { $("save-form").hidden = true; };
$("save-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const name = $("save-name").value.trim();
  if (name) send({ cmd: "save", name });
  $("save-form").hidden = true;
});

initMinimap();
initMenu();
initPlay();
initFp();
initVoice();
initGod();
initGodbar();
initTabs();
initModes();
connect();
