/* Only what is near the camera is drawn. Things that stay put (stretches of
 * ground, trees, rocks, mountains) are filed here by the block of tiles they
 * stand in; blocks out of view have everything in them hidden. */
import { TILE } from "./config.js";

export const CHUNK = 16;        // tiles along the side of a block
const members = new Map();      // block -> Set of game objects
let shown = null;               // the blocks now in view (null until the first look: everything shows)
let lastView = "";
const blockOf = (x, y) => (Math.floor(y / CHUNK) << 12) | Math.floor(x / CHUNK);

export function track(obj, x, y) {
  const k = blockOf(x, y);
  let set = members.get(k);
  if (!set) members.set(k, (set = new Set()));
  set.add(obj);
  obj.cullBlock = k;
  if (shown) obj.setVisible(shown.has(k));
  return obj;
}

export function untrack(obj) {
  const set = members.get(obj.cullBlock);
  if (set) set.delete(obj);
}

export function clearCull() {
  members.clear();
  shown = null;
  lastView = "";
}

// Called every frame; does nothing unless the view has moved to other blocks.
export function cull(sc) {
  const v = sc.cameras.main.worldView;
  const x0 = Math.floor(v.x / TILE / CHUNK) - 1, x1 = Math.floor(v.right / TILE / CHUNK) + 1;
  const y0 = Math.floor(v.y / TILE / CHUNK) - 1, y1 = Math.floor(v.bottom / TILE / CHUNK) + 2;  // (tall things reach up from below)
  const sig = `${x0},${y0},${x1},${y1}`;
  if (sig === lastView && shown) return;
  lastView = sig;
  const now = new Set();
  for (let cy = Math.max(0, y0); cy <= y1; cy++) for (let cx = Math.max(0, x0); cx <= x1; cx++) now.add((cy << 12) | cx);
  for (const [k, set] of members) {
    const on = now.has(k);
    if (shown && shown.has(k) === on) continue;
    for (const o of set) o.setVisible(on);
  }
  shown = now;
}
