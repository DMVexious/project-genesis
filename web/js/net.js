/* The WebSocket to the orchestrator. */
import { $, WS_URL, params } from "./config.js";
import { closeMenu, menuOpen, onPreview, openMenu } from "./menu.js";
import { focus, renderFeeds, renderPanel, select } from "./panel.js";
import { helpOnFirstVisit, syncMode } from "./modes.js";
import { renderPlayer } from "./play.js";
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
    } else if (msg.type === "preview") {
      onPreview(msg);
    } else if (msg.type === "control") {
      state.control = msg;
      state.paused = msg.paused;
      state.tickMs = msg.tick_ms;
      state.seed = msg.seed;
      $("btn-pause").textContent = msg.paused ? "▶" : "⏸";
      $("speed").value = 1050 - msg.tick_ms;
      $("speed-val").textContent = `${msg.tick_ms} ms/tick`;
      const land = ((state.defs && state.defs.maps) || []).find((d) => d.key === msg.map);
      $("world-info").textContent = `${land ? land.label : msg.map} · seed ${msg.seed}`;
      // The server is waiting on its start screen: show ours. (A bare view, for
      // recordings, has no screen to show: it just tells the world to begin.)
      if (msg.menu && params.has("bare")) send({ cmd: "resume" });
      else if (msg.menu && !menuOpen()) openMenu(true);
      else if (!msg.menu && menuOpen() && $("menu-cancel").hidden) closeMenu();  // someone else pressed Start
      if (!msg.menu && !params.has("bare")) helpOnFirstVisit();  // (once the world is under way)
    } else if (msg.type === "frame") {
      // the map changes on few ticks, so the server leaves out the parts we already have
      for (const part of ["tiles", "terrain"]) {
        if (msg[part]) continue;
        if (!state.latest) return;
        msg[part] = state.latest[part];
      }
      if (state.latest && (msg.world !== state.latest.world || msg.tick < state.latest.tick)) resetView();  // new world
      record(msg);
      state.latest = msg;
      if (state.replay) {  // keep recording and feeding the panel; the world view stays in the past
        renderFeeds(msg);
        updateTimeline();
        return;
      }
      state.frame = msg;
      render(msg);
      const changed = renderPlayer(msg);
      if (changed) focus(state.possessed != null ? state.possessed : state.selected);  // the camera goes with the player
      syncMode();
      renderPanel(msg);
      updateTimeline();
      if (params.has("follow") && state.selected === null && !state.followed) {  // ?follow=<npc id>
        state.followed = true;
        select(Number(params.get("follow")));
      }
    }
  };
}
