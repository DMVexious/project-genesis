/* The lie of the land: what the tile characters the server sends stand for.
 * (The server describes its terrain and objects once, in the "defs" message;
 * these lookups are built from that.) */
import { state } from "./state.js";

let cache = null;
function tables() {
  if (cache && cache.defs === state.defs) return cache;
  const defs = state.defs || {};
  const terrain = new Map();  // glyph -> { key, layer, color, water, passable, label }
  for (const [key, t] of Object.entries(defs.terrain || {})) terrain.set(t.glyph, { key, ...t });
  const objects = new Map();  // glyph -> { key, spent, def }
  for (const [key, o] of Object.entries(defs.objects || {})) {
    objects.set(o.glyph, { key, spent: false, def: o });
    if (o.spent_glyph) objects.set(o.spent_glyph, { key, spent: true, def: o });
  }
  return (cache = { defs: state.defs, terrain, objects });
}

export const terrainOf = (glyph) => tables().terrain.get(glyph) || null;
export const objectOf = (glyph) => tables().objects.get(glyph) || null;

// The ground at a tile of a frame (null off the map).
export function terrainAt(f, x, y) {
  const row = f && f.terrain && f.terrain[y];
  return row ? terrainOf(row[x]) : null;
}
