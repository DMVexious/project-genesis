/* The ground. It is painted once per world into a few big pictures (one per
 * block of tiles) rather than tile by tile every frame: each tile's own
 * texture, then whatever higher ground spills over its edges, then a flower
 * or a pebble here and there. Water is left as holes, through which shows a
 * sea that lies under everything and moves. Mountains stand on top as sprites,
 * so that people can walk behind them. */
import { TILE } from "./config.js";
import { CHUNK, track, untrack } from "./cull.js";
import { terrainOf } from "./land.js";
import { DETAILS, EDGE_VARIANTS, VARIANTS, tileHash } from "./textures.js";

const SIDES = [["n", 0, -1], ["e", 1, 0], ["s", 0, 1], ["w", -1, 0]];
const CORNERS = [["nw", -1, -1], ["ne", 1, -1], ["sw", -1, 1], ["se", 1, 1]];
const g = { key: "", images: [], textures: [], water: null, light: null, winter: false, tint: 0xffffff };
// The ground through the year (multiplied over it): fresh in spring, sun-bleached
// in summer, russet in autumn, cold and grey under the frost of winter.
const SEASON_TINT = { spring: 0xffffff, summer: 0xfff4d8, autumn: 0xf4dcb4, winter: 0xdfe7ef };
const NO_FROST = new Set(["snow", "peak", "ash"]);

export function clearGround(sc) {
  for (const img of g.images) { untrack(img); img.destroy(); }
  for (const key of g.textures) if (sc.textures.exists(key)) sc.textures.remove(key);
  g.images = [];
  g.textures = [];
  g.key = g.base = "";
  g.rows = null;
}

function paintTile(ctx, src, at, x, y, px, py) {
  const t = at(x, y);
  if (!t) return;
  const h = tileHash(x, y);
  if (!t.water) ctx.drawImage(src(`t_${t.key}${h % (VARIANTS[t.key] || 1)}`), px, py);
  else if (t.key === "shallow") ctx.drawImage(src("t_shallow0"), px, py);
  // what spills over from higher ground on each side (the highest last, on top)
  const over = [];
  for (const [side, dx, dy] of SIDES) {
    const n = at(x + dx, y + dy);
    if (n && n.layer > t.layer) over.push([n.layer, n, side]);
  }
  over.sort((a, b) => a[0] - b[0]);
  for (const [, n, side] of over) {
    const surf = t.water && !n.water;  // land meeting water: a line of surf along it
    ctx.drawImage(src(`${surf ? "ew" : "e"}_${n.key}_${side}${(h >>> 5) % EDGE_VARIANTS}`), px, py);
  }
  for (const [corner, dx, dy] of CORNERS) {
    const n = at(x + dx, y + dy);
    if (!n || n.layer <= t.layer) continue;
    const a = at(x + dx, y), b = at(x, y + dy);
    if ((a && a.layer >= n.layer) || (b && b.layer >= n.layer)) continue;  // the sides already cover that corner
    ctx.drawImage(src(`c_${n.key}_${corner}`), px, py);
  }
  // a flower, a pebble, a lily pad... now and then
  const kinds = DETAILS[t.key];
  if (kinds && (h >>> 8) % (t.key === "grass" || t.key === "forest" ? 4 : 6) === 0 && !(g.winter && !t.water))
    ctx.drawImage(src(`d_${t.key}${(h >>> 12) % kinds}`), px + 2 + ((h >>> 16) % 9), py + 2 + ((h >>> 20) % 9));
  if (g.winter && !t.water && !NO_FROST.has(t.key)) ctx.drawImage(src(`frost${(h >>> 3) % 3}`), px, py);
}

function repaint(keys) {
  const W = g.width, H = g.height;
  for (const k of keys) {
    const [x0, y0] = k.split(":").map(Number);
    const tex = g.chunks.get(k);
    if (!tex) continue;
    const ctx = tex.getContext();
    ctx.clearRect(0, 0, tex.width, tex.height);
    const x1 = Math.min(W, x0 + CHUNK), y1 = Math.min(H, y0 + CHUNK);
    for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) paintTile(ctx, g.src, g.at, x, y, (x - x0) * TILE, (y - y0) * TILE);
    tex.refresh();
  }
}

// The season shows on the ground: a tint over it all, and frost on the land in winter.
export function setGroundSeason(season) {
  const tint = SEASON_TINT[season] || 0xffffff, winter = season === "winter";
  if (tint !== g.tint) {
    g.tint = tint;
    for (const img of g.images) img.setTint(tint);
  }
  if (winter !== g.winter) {
    g.winter = winter;
    if (g.chunks) repaint(g.chunks.keys());
  }
}

// (Re)paint the ground if this frame's world or terrain is not the one on show.
// Repaint only the blocks whose ground changed (a path worn, a pond dug), plus
// their neighbours, whose edges may spill over into them.
function patchGround(sc, f) {
  const W = f.width, H = f.height, dirty = new Set();
  for (let y = 0; y < H; y++) {
    if (f.terrain[y] === g.rows[y]) continue;
    const row = f.terrain[y], was = g.rows[y] || "";
    for (let x = 0; x < W; x++) {
      if (row[x] === was[x]) continue;
      g.land[y][x] = terrainOf(row[x]);
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        const cx = Math.floor((x + dx) / CHUNK), cy = Math.floor((y + dy) / CHUNK);
        if (cx >= 0 && cy >= 0 && cx * CHUNK < W && cy * CHUNK < H) dirty.add(`${cx * CHUNK}:${cy * CHUNK}`);
      }
    }
  }
  g.rows = f.terrain;
  repaint(dirty);
}

export function buildGround(sc, f) {
  const base = `${f.world}:${f.width}x${f.height}`, key = `${base}:${f.trev}`;
  if (g.key === key) return false;
  if (g.base === base && g.rows) {  // the same world, a little changed
    g.key = key;
    patchGround(sc, f);
    return true;
  }
  clearGround(sc);
  g.key = key;
  g.base = base;
  g.rows = f.terrain;
  g.chunks = new Map();
  const W = g.width = f.width, H = g.height = f.height;
  const land = g.land = f.terrain.map((row) => [...row].map(terrainOf));
  const at = g.at = (x, y) => (x >= 0 && y >= 0 && x < W && y < H ? land[y][x] : null);
  const sources = new Map();
  const src = g.src = (name) => {
    let s = sources.get(name);
    if (!s) sources.set(name, (s = sc.textures.get(name).getSourceImage()));
    return s;
  };

  // the sea, under everything
  if (g.water) { g.water.destroy(); g.light.destroy(); }
  g.water = sc.add.tileSprite(0, 0, W * TILE, H * TILE, "water").setOrigin(0).setDepth(-10);
  g.light = sc.add.tileSprite(0, 0, W * TILE, H * TILE, "water_light").setOrigin(0).setDepth(-9);

  for (let y0 = 0; y0 < H; y0 += CHUNK) {
    for (let x0 = 0; x0 < W; x0 += CHUNK) {
      const x1 = Math.min(W, x0 + CHUNK), y1 = Math.min(H, y0 + CHUNK);
      const name = `ground:${key}:${x0}:${y0}`;
      const tex = sc.textures.createCanvas(name, (x1 - x0) * TILE, (y1 - y0) * TILE);
      const ctx = tex.getContext();
      for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) paintTile(ctx, src, at, x, y, (x - x0) * TILE, (y - y0) * TILE);
      tex.refresh();
      g.chunks.set(`${x0}:${y0}`, tex);
      g.textures.push(name);
      g.images.push(track(sc.add.image(x0 * TILE, y0 * TILE, name).setOrigin(0).setDepth(-5).setTint(g.tint), x0, y0));
    }
  }

  // mountains: one sprite per tile of mountain, sorted by its foot like everything that stands
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      if (land[y][x] && land[y][x].key === "peak") {
        const h = tileHash(x, y);
        const img = sc.add.image(x * TILE + TILE / 2 + ((h >>> 4) % 7) - 3, (y + 1) * TILE + 2, `peak${h % 3}`)
          .setOrigin(0.5, 1).setDepth((y + 1) * TILE - 4).setTint(g.tint);
        g.images.push(track(img, x, y));
      }
    }
  }
  return true;
}

// The sea drifts, and glints slide across it the other way.
export function animateGround(t) {
  if (!g.water) return;
  g.water.tilePositionX = Math.floor(t * 0.004);
  g.water.tilePositionY = Math.floor(t * 0.0016);
  g.light.tilePositionX = -Math.floor(t * 0.012);
  g.light.tilePositionY = Math.floor(t * 0.007);
  g.light.setAlpha(0.55 + 0.25 * Math.sin(t / 900));
}
