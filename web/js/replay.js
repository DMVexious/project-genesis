/* Replay: scrub back through recorded frames while the live world keeps running. */
import { $, GRAVE_TICKS } from "./config.js";
import { renderPanel, renderTalks } from "./panel.js";
import { state } from "./state.js";
import { addGrave, clearWorld, render, scene } from "./world.js";

export function resetView() {
  stopReplay();
  state.history = [];
  state.interned.clear();
  clearWorld();
  state.names.clear();
  state.selected = null;
  state.talks = [];
  renderTalks();
  $("log").innerHTML = "";
}

// --- replay ------------------------------------------------------------------------
// Every frame is recorded here, so the viewer can scrub back through recent
// history while the live world keeps running. Frames repeat most of their data
// tick to tick, so unchanged parts are shared with the previous frame.
export const MAX_HISTORY = 4000;  // ~10 minutes at 150 ms/tick
export const SHARED = ["traits", "look", "parents", "mutated"];

export function record(f) {
  const prev = state.history[state.history.length - 1];
  if (prev && prev.tiles.length === f.tiles.length) {
    const rows = f.tiles.map((r, i) => (r === prev.tiles[i] ? prev.tiles[i] : r));
    f.tiles = rows.every((r, i) => r === prev.tiles[i]) ? prev.tiles : rows;
  }
  for (const n of f.npcs) {
    let c = state.interned.get(n.id);
    if (!c) state.interned.set(n.id, (c = {}));
    for (const k of SHARED) {
      const sig = JSON.stringify(n[k]);
      if (c[k + "$"] === sig) n[k] = c[k];
      else { c[k] = n[k]; c[k + "$"] = sig; }
    }
    for (const k of ["name", "belief", "voice", "intent", "cause"]) {
      if (c[k] === n[k]) n[k] = c[k]; else c[k] = n[k];
    }
  }
  state.history.push(f);
  if (state.history.length > MAX_HISTORY) {
    state.history.splice(0, state.history.length - MAX_HISTORY);
    if (state.replay && --state.replay.idx < 0) seek(0);  // the oldest frame we were on fell off
  }
}

export const rp = () => state.replay;

export function enterReplay() {
  if (state.replay || !state.history.length) return;
  state.replay = { idx: state.history.length - 1, dir: 0, speed: Number($("tl-speed").value) || 1, timer: 0 };
  document.body.classList.add("replaying");
}

export function stopReplay() {
  if (!state.replay) return;
  clearTimeout(state.replay.timer);
  state.replay = null;
  document.body.classList.remove("replaying");
}

export function refollow() {
  const sc = scene(), s = state.sprites.get(state.selected);
  if (sc && sc.cameras && s) sc.cameras.main.startFollow(s.sprite, true, 0.08, 0.08);
}

export function showFrame(f, opts) {
  state.frame = f;
  render(f, opts);
  renderPanel({ ...f, dialogue: [], log: [] });  // don't replay old lines into the feeds
  updateTimeline();
}

// Jump straight to a frame: rebuild the world as it stood then.
export function seek(idx) {
  const h = state.history;
  if (!h.length) return;
  enterReplay();
  idx = Math.max(0, Math.min(h.length - 1, idx));
  state.replay.idx = idx;
  const f = h[idx], sc = scene();
  clearWorld();
  if (sc && sc.ring) {  // graves of anyone who died in the minute before
    for (let i = idx - 1; i >= 0 && h[i].tick > f.tick - GRAVE_TICKS; i--)
      for (const n of h[i].npcs) if (!n.alive) addGrave(sc, n, h[i].tick, true);
  }
  showFrame(f, { snap: true });
  refollow();
}

// Move one frame, animating it like the live view would.
export function step(dir) {
  enterReplay();
  const r = state.replay, next = r.idx + dir;
  if (next >= state.history.length) return goLive();
  if (next < 0) return setDir(0);
  r.idx = next;
  showFrame(state.history[next], { reverse: dir < 0, tickMs: frameMs() });
}

export const frameMs = () => Math.max(16, state.tickMs / (rp() ? rp().speed : 1));

export function setDir(dir) {
  enterReplay();
  const r = state.replay;
  if (!r) return;
  clearTimeout(r.timer);
  r.dir = dir;
  const tick = () => {
    if (state.replay !== r || !r.dir) return;
    step(r.dir);
    if (state.replay === r && r.dir) r.timer = setTimeout(tick, frameMs());
  };
  if (dir) r.timer = setTimeout(tick, 0);
  updateTimeline();
}

export function goLive() {
  stopReplay();
  const f = state.latest;
  if (!f) return;
  clearWorld();
  state.frame = f;
  const sc = scene(), h = state.history;
  if (sc && sc.ring)
    for (let i = h.length - 2; i >= 0 && h[i].tick > f.tick - GRAVE_TICKS; i--)
      for (const n of h[i].npcs) if (!n.alive) addGrave(sc, n, h[i].tick, true);
  render(f, { snap: true });
  renderPanel({ ...f, dialogue: [], log: [] });
  refollow();
  updateTimeline();
}

export function updateTimeline() {
  const h = state.history, r = state.replay;
  const scrub = $("tl-scrub");
  scrub.max = Math.max(0, h.length - 1);
  if (document.activeElement !== scrub || !r) scrub.value = r ? r.idx : h.length - 1;
  const live = state.latest ? state.latest.tick : 0;
  const at = r && h[r.idx] ? h[r.idx].tick : live;
  $("tl-tick").textContent = r ? `tick ${at} · ${live - at} behind` : `tick ${live}`;
  $("tl-play").textContent = r && r.dir === 1 ? "⏸" : "▶";
  $("tl-rev").textContent = r && r.dir === -1 ? "⏸" : "◀◀";
}
