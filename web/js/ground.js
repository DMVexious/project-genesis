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
const g = { key: "", images: [], textures: [], water: null, light: null };

export function clearGround(sc) {
  for (const img of g.images) { untrack(img); img.destroy(); }
  for (const key of g.textures) if (sc.textures.exists(key)) sc.textures.remove(key);
  g.images = [];
  g.textures = [];
  g.key = "";
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
  if (kinds && (h >>> 8) % (t.key === "grass" || t.key === "forest" ? 4 : 6) === 0)
    ctx.drawImage(src(`d_${t.key}${(h >>> 12) % kinds}`), px + 2 + ((h >>> 16) % 9), py + 2 + ((h >>> 20) % 9));
}

// (Re)paint the ground if this frame's world or terrain is not the one on show.
export function buildGround(sc, f) {
  const key = `${f.world}:${f.trev}:${f.width}x${f.height}`;
  if (g.key === key) return false;
  clearGround(sc);
  g.key = key;
  const W = f.width, H = f.height;
  const land = f.terrain.map((row) => [...row].map(terrainOf));
  const at = (x, y) => (x >= 0 && y >= 0 && x < W && y < H ? land[y][x] : null);
  const sources = new Map();
  const src = (name) => {
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
      g.textures.push(name);
      g.images.push(track(sc.add.image(x0 * TILE, y0 * TILE, name).setOrigin(0).setDepth(-5), x0, y0));
    }
  }

  // mountains: one sprite per tile of mountain, sorted by its foot like everything that stands
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      if (land[y][x] && land[y][x].key === "peak") {
        const h = tileHash(x, y);
        const img = sc.add.image(x * TILE + TILE / 2 + ((h >>> 4) % 7) - 3, (y + 1) * TILE + 2, `peak${h % 3}`)
          .setOrigin(0.5, 1).setDepth((y + 1) * TILE - 4);
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
