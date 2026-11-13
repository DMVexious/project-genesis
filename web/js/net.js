/* The WebSocket to the orchestrator. */
import { $, WS_URL, params } from "./config.js";
import { renderFeeds, renderPanel, select } from "./panel.js";
import { record, resetView, updateTimeline } from "./replay.js";
import { state } from "./state.js";
import { render } from "./world.js";

// --- networking ---------------------------------------------------------------------
export function send(msg) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) state.ws.send(JSON.stringify(msg));
}

export function connect() {
  const ws = new WebSocket(WS_URL);
  state.ws = ws;
  ws.onopen = () => { $("conn").textContent = "live"; $("conn").className = "pill ok"; };
  ws.onclose = () => {
    $("conn").textContent = "reconnecting…"; $("conn").className = "pill bad";
    setTimeout(connect, 1500);
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "defs") {
      state.defs = msg;
    } else if (msg.type === "control") {
      state.paused = msg.paused;
      state.tickMs = msg.tick_ms;
      state.seed = msg.seed;
      $("btn-pause").textContent = msg.paused ? "▶" : "⏸";
      $("speed").value = 1050 - msg.tick_ms;
      $("speed-val").textContent = `${msg.tick_ms} ms/tick`;
      if (document.activeElement !== $("seed")) $("seed").value = msg.seed;
    } else if (msg.type === "frame") {
      if (!msg.tiles) {  // the map did not change this tick, so the server left it out
        if (!state.latest) return;
        msg.tiles = state.latest.tiles;
      }
      if (state.latest && msg.tick < state.latest.tick) resetView();  // new world
      record(msg);
      state.latest = msg;
      if (state.replay) {  // keep recording and feeding the panel; the world view stays in the past
        renderFeeds(msg);
        updateTimeline();
        return;
      }
      state.frame = msg;
      render(msg);
      renderPanel(msg);
      updateTimeline();
      if (params.has("follow") && state.selected === null && !state.followed) {  // ?follow=<npc id>
        state.followed = true;
        select(Number(params.get("follow")));
      }
    }
  };
}
