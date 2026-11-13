/* Project Genesis — Phaser front end (entry point).
 *
 * Phaser draws the world (tiles + NPC sprites); emojis and speech bubbles are
 * HTML elements in #overlay that track sprite positions every animation frame.
 * Talks to the Python orchestrator over a WebSocket. This file wires the
 * page's controls; the work is in the modules next to it.
 */
import { $ } from "./config.js";
import { connect, send } from "./net.js";
import { renderTalks, select } from "./panel.js";
import { goLive, rp, seek, setDir, step } from "./replay.js";
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
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  if (e.key === "," || e.key === "<") {
    e.preventDefault();
    if (e.shiftKey || e.key === "<") $("tl-rev").click(); else $("tl-back").click();
  } else if (e.key === ".") { e.preventDefault(); $("tl-fwd").click(); }
  else if (e.key === "Enter" && rp()) { e.preventDefault(); $("tl-play").click(); }
  else if (e.key === "Escape" && rp()) { e.preventDefault(); goLive(); }
});

$("btn-pause").onclick = () => send({ cmd: state.paused ? "resume" : "pause" });
$("btn-step").onclick = () => send({ cmd: "step" });
$("speed").oninput = (e) => send({ cmd: "speed", tick_ms: 1050 - Number(e.target.value) });
$("btn-reset").onclick = () => {
  const v = $("seed").value;
  const seed = v === "" || Number(v) === state.seed ? Math.floor(Math.random() * 1e6) : Number(v);
  send({ cmd: "reset", seed });
};
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
  if (e.code === "Space" && e.target.tagName !== "INPUT") {
    e.preventDefault();
    send({ cmd: state.paused ? "resume" : "pause" });
  }
});

connect();
