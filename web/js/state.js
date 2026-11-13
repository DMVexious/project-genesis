/* Everything the page knows right now. One mutable object, shared by all modules. */
export const state = {
  defs: null,         // the game's content registry, sent once by the server (objects, items, actions...)
  frame: null,
  tickMs: 400,
  paused: false,
  selected: null,
  names: new Map(),
  sprites: new Map(), // id -> { sprite, shadow, spear, tag, statusEl, actionEl, key, dying }
  bubbles: new Map(), // id -> { el, tick, born }
  talks: [],          // recent exchanges, newest first: { tick, a, b, kind, said, reply }
  graves: [],         // { img, tick }
  tiles: [],
  fires: [],          // campfire tile images (animated)
  portraits: new Map(),
  ws: null,
  latest: null,       // newest frame from the server (state.frame is the one on screen)
  history: [],        // recorded frames for replay, oldest first
  interned: new Map(),// npc id -> last copies of its unchanging fields (shared between frames)
  replay: null,       // { idx, dir: -1|0|1, speed, timer } while watching the past
};
