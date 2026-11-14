/* Project Genesis — Phaser front end (entry point).
 *
 * Phaser draws the world (tiles + NPC sprites); emojis and speech bubbles are
 * HTML elements in #overlay that track sprite positions every animation frame.
 * Talks to the Python orchestrator over a WebSocket. This file wires the
 * page's controls; the work is in the modules next to it.
 */
import { $ } from "./config.js";
import { initMenu, menuOpen, openMenu } from "./menu.js";
import { initMinimap, toggleMinimap } from "./minimap.js";
import { connect, send } from "./net.js";
import { initGod, renderTalks, select } from "./panel.js";
import { initPlay, playing } from "./play.js";
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
  if (e.code === "Space" && e.target.tagName !== "INPUT" && !menuOpen()) {
    e.preventDefault();
    send({ cmd: state.paused ? "resume" : "pause" });
  }
});

initMinimap();
initMenu();
initPlay();
initGod();
connect();
