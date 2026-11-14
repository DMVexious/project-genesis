/* Procedural pixel art. Every texture is drawn in code at startup (characters
 * on demand, from each NPC's inherited looks), so there are no image files.
 *
 * Three families are made here:
 *   - the ground: a few variants of each kind of terrain ("t_grass0"...), the
 *     ragged strips with which one kind spills over the edge of another
 *     ("e_grass_n0"...), small details to scatter on it ("d_grass2"...) and
 *     the water that shows wherever the ground leaves a hole;
 *   - things that stand on the ground (trees, rocks, huts, mountains...), drawn
 *     on a clear background and taller than a tile, so that people can walk
 *     behind them;
 *   - odds and ends: graves, things dropped on the ground, the selection ring.
 */
import { TILE } from "./config.js";
import { state } from "./state.js";
import { scene } from "./world.js";

// A number from a tile's position: picks variants so the map does not look tiled.
export const tileHash = (x, y) => (Math.imul(x, 73856093) ^ Math.imul(y, 19349663)) >>> 0;
const hashStr = (s) => { let h = 2166136261; for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return h >>> 0; };

export function rand(seed) {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}

export const OUTLINE = 0x140e09;
export const shade = (c, f) => {
  const r = Math.min(255, Math.round(((c >> 16) & 255) * f));
  const g = Math.min(255, Math.round(((c >> 8) & 255) * f));
  const b = Math.min(255, Math.round((c & 255) * f));
  return (r << 16) | (g << 8) | b;
};

// The colours of each kind of ground: its body, and the darker and lighter flecks in it.
export const GROUND = {
  sand: { base: 0xd9c38a, dark: 0xc9b278, light: 0xe8d6a0 },
  dirt: { base: 0x7a5a3a, dark: 0x6b4e32, light: 0x8a6a46 },
  stone: { base: 0x8a8578, dark: 0x767165, light: 0x9d988b },
  grass: { base: 0x5f8f3c, dark: 0x527f34, light: 0x73a447 },
  forest: { base: 0x456a30, dark: 0x3a5a29, light: 0x55793a },
  snow: { base: 0xe8eef2, dark: 0xd0dbe5, light: 0xffffff },
  peak: { base: 0x6f6a63, dark: 0x5c5852, light: 0x827d75 },
  path: { base: 0xb0946a, dark: 0x9a7f58, light: 0xc4a87c },
  ash: { base: 0x4a4440, dark: 0x3a3532, light: 0x5e5650 },
};
export const VARIANTS = { sand: 3, dirt: 3, stone: 3, grass: 4, forest: 3, snow: 3, peak: 2, path: 3, ash: 3 };  // "t_<kind><n>"
export const DETAILS = { sand: 3, dirt: 3, stone: 3, grass: 5, forest: 4, snow: 3, shallow: 3 };  // "d_<kind><n>"
export const EDGE_VARIANTS = 2;
const WATER_DEEP = 0x2f6f9f, WATER_SHALLOW = 0x9fdcea, FOAM = 0xeef9fd;

export function makeTextures(scene) {
  const g = scene.make.graphics({ x: 0, y: 0, add: false });
  const px = (x, y, w, h, c, a = 1) => { g.fillStyle(c, a); g.fillRect(x, y, w, h); };
  const tri = (x1, y1, x2, y2, x3, y3, c, a = 1) => { g.fillStyle(c, a); g.fillTriangle(x1, y1, x2, y2, x3, y3); };
  const save = (key, w = TILE, h = TILE) => { g.generateTexture(key, w, h); g.clear(); };
  // A sprite with a dark outline: each rect in `solid` gets a 1px border, drawn
  // under all the fills so it survives only around the silhouette. `detail`
  // rects ([x, y, w, h, colour, alpha]) go on top, unoutlined.
  const solids = (solid) => {
    for (const [x, y, w, h] of solid) px(x - 1, y - 1, w + 2, h + 2, OUTLINE);
    for (const [x, y, w, h, c] of solid) px(x, y, w, h, c);
  };
  const details = (detail) => { for (const [x, y, w, h, c, a] of detail) px(x, y, w, h, c, a ?? 1); };
  const sprite = (key, w, h, solid, detail = []) => { solids(solid); details(detail); save(key, w, h); };

  // --- the ground ---------------------------------------------------------------
  const flecks = (r, pal, n = 16) => {
    for (let i = 0; i < n; i++) px(Math.floor(r() * 23), Math.floor(r() * 23), 2, 2, r() < 0.5 ? pal.dark : pal.light);
  };
  const EXTRA = {
    grass: (r, pal) => {  // blades
      for (let i = 0; i < 9; i++) px(Math.floor(r() * 24), Math.floor(r() * 21), 1, 2 + Math.floor(r() * 2), r() < 0.6 ? pal.light : 0x4a7430);
    },
    forest: (r) => {  // leaf litter and moss
      for (let i = 0; i < 7; i++) px(Math.floor(r() * 23), Math.floor(r() * 23), 1 + Math.floor(r() * 2), 1, r() < 0.5 ? 0x6b5a2e : 0x7a4a26);
      for (let i = 0; i < 3; i++) px(Math.floor(r() * 21), Math.floor(r() * 22), 3, 2, 0x38562a);
    },
    sand: (r, pal) => {  // wind ripples
      for (let i = 0; i < 3; i++) { const x = Math.floor(r() * 14), y = 3 + Math.floor(r() * 18); px(x, y, 5 + Math.floor(r() * 5), 1, pal.dark); px(x + 2, y + 1, 4, 1, pal.light); }
    },
    dirt: (r, pal, v) => {
      if (v === 1) { px(5, 17, 1, 3, 0x6f8a3a); px(6, 16, 1, 4, 0x86a347); px(15, 6, 1, 3, 0x6f8a3a); }   // a tuft of grass
      if (v === 2) { px(17, 15, 2, 2, 0x9a8a78); px(6, 6, 2, 1, 0x9a8a78); }                              // pebbles
    },
    stone: (r, pal) => {  // cracks and loose stones
      for (let i = 0; i < 2; i++) { const x = 2 + Math.floor(r() * 16), y = 2 + Math.floor(r() * 16); px(x, y, 4, 1, 0x625e54); px(x + 3, y + 1, 1, 3, 0x625e54); }
      for (let i = 0; i < 3; i++) { const x = Math.floor(r() * 21), y = Math.floor(r() * 21); px(x, y, 3, 2, pal.light); px(x, y + 2, 3, 1, pal.dark); }
    },
    snow: (r) => {  // drifts and glints
      for (let i = 0; i < 4; i++) px(Math.floor(r() * 19), Math.floor(r() * 21), 4 + Math.floor(r() * 3), 2, 0xd6e1ea);
      for (let i = 0; i < 4; i++) px(Math.floor(r() * 24), Math.floor(r() * 24), 1, 1, 0xffffff);
    },
    peak: (r) => { for (let i = 0; i < 5; i++) px(Math.floor(r() * 20), Math.floor(r() * 21), 4, 3, 0x514d48); },
    ash: (r) => {  // charcoal, and the last embers
      for (let i = 0; i < 6; i++) px(Math.floor(r() * 21), Math.floor(r() * 22), 3, 1, 0x26221f);
      for (let i = 0; i < 2; i++) px(Math.floor(r() * 22), Math.floor(r() * 22), 1, 1, r() < 0.5 ? 0xc0582a : 0x8a3a1e);
      for (let i = 0; i < 3; i++) px(Math.floor(r() * 22), Math.floor(r() * 22), 2, 2, 0x6e6660);
    },
    path: (r, pal) => {  // packed earth: pebbles, and the odd footprint
      for (let i = 0; i < 5; i++) px(Math.floor(r() * 22), Math.floor(r() * 22), 2, 1, 0x8a7350);
      for (let i = 0; i < 2; i++) { const x = 3 + Math.floor(r() * 15), y = 3 + Math.floor(r() * 15); px(x, y, 2, 3, pal.dark); px(x + 4, y + 2, 2, 3, pal.dark); }
    },
  };
  for (const [kind, pal] of Object.entries(GROUND)) {
    for (let v = 0; v < VARIANTS[kind]; v++) {
      const r = rand(hashStr(kind) + v * 977);
      px(0, 0, TILE, TILE, pal.base);
      flecks(r, pal, kind === "snow" ? 6 : 16);
      if (EXTRA[kind]) EXTRA[kind](r, pal, v);
      save(`t_${kind}${v}`);
    }
  }

  // Where one kind of ground meets a lower one, it spills over the shared edge
  // in a ragged strip ("e_<kind>_<side><variant>": the strip lies along that
  // side of the lower tile), and pokes round outer corners ("c_<kind>_<corner>").
  // The "ew_" strips are for spilling onto water: they carry a line of surf.
  const strip = (side, i, d, c, a = 1) => {  // one pixel-wide slice, d deep, i along the side
    if (side === "n") px(i, 0, 1, d, c, a);
    else if (side === "s") px(i, TILE - d, 1, d, c, a);
    else if (side === "w") px(0, i, d, 1, c, a);
    else px(TILE - d, i, d, 1, c, a);
  };
  const dot = (side, i, d, c, a = 1) => {  // the single pixel d in from the side, i along it
    if (side === "n") px(i, d, 1, 1, c, a);
    else if (side === "s") px(i, TILE - 1 - d, 1, 1, c, a);
    else if (side === "w") px(d, i, 1, 1, c, a);
    else px(TILE - 1 - d, i, 1, 1, c, a);
  };
  const profile = (r) => {  // how deep the strip is at each point along the side
    const out = [];
    let d = 3 + Math.floor(r() * 3);
    for (let i = 0; i < TILE; i++) {
      if (r() < 0.5) d += r() < 0.5 ? -1 : 1;
      d = Math.max(2, Math.min(6, d));
      out.push(d);
    }
    return out;
  };
  const edges = (kind, pal, alpha = 1) => {
    for (let v = 0; v < EDGE_VARIANTS; v++) {
      const depth = profile(rand(hashStr(kind) * 31 + v * 131));
      for (const side of ["n", "e", "s", "w"]) {
        for (const surf of alpha < 1 ? [false] : [false, true]) {
          const r = rand(hashStr(kind + side) + v * 17);
          for (let i = 0; i < TILE; i++) {
            strip(side, i, depth[i] + (alpha < 1 ? 3 : 0), pal.base, alpha);
            if (alpha < 1) strip(side, i, depth[i] + 9, pal.base, alpha * 0.45);  // (washes fade out rather than stop)
            if (alpha === 1) {
              dot(side, i, depth[i] - 1, pal.dark);                       // a darker lip, so the edge reads
              if (r() < 0.3) dot(side, i, Math.floor(r() * (depth[i] - 1)), r() < 0.5 ? pal.light : pal.dark);
            }
            if (surf) {
              dot(side, i, depth[i], FOAM, 0.85);
              if (r() < 0.55) dot(side, i, depth[i] + 1, FOAM, 0.45);
              if (r() < 0.2) dot(side, i, depth[i] + 3, FOAM, 0.3);
            }
          }
          save(`${surf ? "ew" : "e"}_${kind}_${side}${v}`);
        }
      }
    }
    // outer corners: a small rounded blob in the corner of the lower tile
    for (const [corner, fx, fy] of [["nw", 0, 0], ["ne", 1, 0], ["sw", 0, 1], ["se", 1, 1]]) {
      for (let y = 0; y < 5; y++) for (let x = 0; x < 5; x++) {
        const dd = x * x + y * y;
        if (dd > 17) continue;
        px(fx ? TILE - 1 - x : x, fy ? TILE - 1 - y : y, 1, 1, alpha === 1 && dd > 9 ? pal.dark : pal.base, alpha);
      }
      save(`c_${kind}_${corner}`);
    }
  };
  for (const [kind, pal] of Object.entries(GROUND)) edges(kind, pal);

  // Water. The sea is one big repeating texture lying under everything (the
  // ground has holes where there is water); shallows are a pale wash over it.
  px(0, 0, 64, 64, WATER_DEEP);
  {
    const r = rand(77);
    for (let i = 0; i < 26; i++) px(Math.floor(r() * 56), Math.floor(r() * 62), 5 + Math.floor(r() * 6), 1, 0x2a6491);
    for (let i = 0; i < 14; i++) px(Math.floor(r() * 58), Math.floor(r() * 62), 3 + Math.floor(r() * 4), 1, 0x3a7fb0);
  }
  save("water", 64, 64);
  {  // glints that drift across it
    const r = rand(78);
    for (let i = 0; i < 9; i++) { const x = Math.floor(r() * 54), y = Math.floor(r() * 62); px(x, y, 6, 1, 0xbfe6f5, 0.5); px(x + 2, y + 1, 3, 1, 0xdff3fb, 0.4); }
  }
  save("water_light", 64, 64);
  px(0, 0, TILE, TILE, WATER_SHALLOW, 0.42);  // (plain: the sea moving underneath gives it its texture)
  save("t_shallow0");
  edges("shallow", { base: WATER_SHALLOW }, 0.42);

  // Small things scattered over the ground: flowers, pebbles, shells... ("d_<kind><n>", 12x12)
  const D = 12;
  const detail = (key, rects) => { details(rects); save(key, D, D); };
  detail("d_grass0", [[2, 6, 1, 4, 0x3f6f2c], [5, 4, 1, 6, 0x3f6f2c], [8, 6, 1, 4, 0x3f6f2c], [1, 4, 3, 3, 0xffffff], [2, 5, 1, 1, 0xf6c04a],
    [4, 2, 3, 3, 0xffffff], [5, 3, 1, 1, 0xf6c04a], [7, 4, 3, 3, 0xffffff], [8, 5, 1, 1, 0xf6c04a]]);        // daisies
  detail("d_grass1", [[3, 6, 1, 4, 0x3f6f2c], [7, 5, 1, 5, 0x3f6f2c], [2, 4, 3, 3, 0xf6c04a], [3, 5, 1, 1, 0xe8841e],
    [6, 3, 3, 3, 0xf6c04a], [7, 4, 1, 1, 0xe8841e]]);                                                       // buttercups
  detail("d_grass2", [[2, 5, 1, 6, 0x4a7a30], [4, 2, 1, 9, 0x7fb04e], [6, 4, 1, 7, 0x4a7a30], [8, 1, 1, 10, 0x6a9a40],
    [10, 5, 1, 6, 0x7fb04e]]);                                                                              // tall grass
  detail("d_grass3", [[3, 6, 1, 4, 0x3f6f2c], [8, 6, 1, 4, 0x3f6f2c], [2, 4, 3, 3, 0xe07aa8], [3, 5, 1, 1, 0xffe08a],
    [7, 4, 3, 3, 0xb48ae0], [8, 5, 1, 1, 0xffe08a]]);                                                       // pink and violet
  detail("d_grass4", [[1, 4, 9, 5, 0x4f7f33], [3, 3, 5, 7, 0x4f7f33], [2, 5, 2, 2, 0x6a9a40], [6, 6, 2, 2, 0x6a9a40],
    [5, 4, 1, 1, 0x86b354]]);                                                                               // a patch of clover
  detail("d_dirt0", [[2, 6, 3, 2, 0x9a8a78], [2, 8, 3, 1, 0x5b4630], [7, 3, 2, 2, 0xa89a88], [7, 5, 2, 1, 0x5b4630], [8, 8, 2, 1, 0x9a8a78]]);
  detail("d_dirt1", [[3, 5, 1, 5, 0x6f8a3a], [5, 3, 1, 7, 0x86a347], [7, 5, 1, 5, 0x6f8a3a], [9, 6, 1, 4, 0x86a347]]);
  detail("d_dirt2", [[1, 5, 4, 1, 0x5b4630], [4, 6, 3, 1, 0x5b4630], [6, 7, 1, 3, 0x5b4630], [7, 4, 3, 1, 0x5b4630]]);
  detail("d_sand0", [[3, 4, 5, 4, 0xf4ead2], [4, 3, 3, 1, 0xf4ead2], [4, 5, 1, 3, 0xe0b9a0], [6, 5, 1, 3, 0xe0b9a0], [3, 8, 5, 1, 0xb8a06a]]);  // a shell
  detail("d_sand1", [[1, 3, 8, 1, 0xc4ad72], [3, 6, 8, 1, 0xc4ad72], [1, 9, 7, 1, 0xc4ad72], [2, 4, 5, 1, 0xeeddb0], [4, 7, 5, 1, 0xeeddb0]]);
  detail("d_sand2", [[2, 6, 2, 2, 0xa89a80], [6, 3, 3, 2, 0xb8a98e], [6, 5, 3, 1, 0x9a8a6a], [8, 8, 2, 1, 0xa89a80]]);
  detail("d_forest0", [[2, 3, 2, 1, 0xc0642a], [6, 2, 2, 1, 0xd9a13a], [4, 6, 2, 1, 0x8a3b1e], [8, 7, 2, 1, 0xc0642a], [2, 8, 2, 1, 0xd9a13a]]);  // fallen leaves
  detail("d_forest1", [[5, 3, 1, 8, 0x2f5c2a], [2, 4, 3, 1, 0x3f7a35], [6, 4, 3, 1, 0x3f7a35], [1, 6, 4, 1, 0x3f7a35], [6, 6, 4, 1, 0x3f7a35],
    [2, 8, 3, 1, 0x3f7a35], [6, 8, 3, 1, 0x3f7a35]]);                                                       // a fern
  detail("d_forest2", [[1, 6, 9, 1, 0x5b3a1e], [3, 5, 1, 1, 0x5b3a1e], [7, 7, 1, 2, 0x5b3a1e], [9, 5, 2, 1, 0x6d4825]]);  // a fallen twig
  detail("d_forest3", [[3, 6, 1, 3, 0x2f5c2a], [7, 5, 1, 4, 0x2f5c2a], [2, 4, 3, 2, 0xdfe8ff], [6, 3, 3, 2, 0xdfe8ff]]);
  detail("d_stone0", [[1, 4, 4, 1, 0x5c584f], [4, 5, 1, 3, 0x5c584f], [5, 7, 4, 1, 0x5c584f], [8, 8, 1, 2, 0x5c584f]]);
  detail("d_stone1", [[2, 4, 7, 4, 0x5f7f3c], [3, 3, 4, 6, 0x5f7f3c], [4, 5, 2, 2, 0x7a9a4a]]);                  // moss
  detail("d_stone2", [[2, 6, 2, 2, 0xa8a396], [5, 3, 3, 2, 0xb0ab9e], [5, 5, 3, 1, 0x6a665c], [8, 7, 2, 2, 0xa8a396], [8, 9, 2, 1, 0x6a665c]]);
  detail("d_snow0", [[2, 4, 7, 3, 0xcfdbe6], [3, 3, 5, 1, 0xcfdbe6], [3, 7, 5, 1, 0xdbe5ee]]);
  detail("d_snow1", [[4, 4, 1, 5, 0x8a7a52], [6, 3, 1, 6, 0x9a8a5e], [8, 5, 1, 4, 0x8a7a52], [3, 9, 7, 1, 0xcfdbe6]]);  // dry grass poking through
  detail("d_snow2", [[2, 3, 1, 1, 0xffffff], [8, 2, 1, 1, 0xffffff], [5, 6, 1, 1, 0xffffff], [9, 8, 1, 1, 0xffffff], [3, 9, 1, 1, 0xbfd0de]]);
  detail("d_shallow0", [[2, 4, 7, 4, 0x4f9a4a], [3, 3, 5, 6, 0x4f9a4a], [6, 4, 2, 2, 0x2f6f9f, 0.6], [3, 5, 3, 1, 0x6ab85e],
    [7, 6, 2, 2, 0xf4b6d0], [8, 6, 1, 1, 0xffe08a]]);                                                       // a lily pad
  detail("d_shallow1", [[3, 1, 1, 10, 0x5f8a3a], [5, 3, 1, 8, 0x76a34a], [7, 0, 1, 11, 0x5f8a3a], [9, 4, 1, 7, 0x76a34a],
    [3, 0, 1, 3, 0x7a5230], [7, 0, 1, 2, 0x7a5230]]);                                                       // reeds
  detail("d_shallow2", [[2, 5, 4, 1, FOAM, 0.6], [5, 7, 5, 1, FOAM, 0.5], [3, 9, 3, 1, FOAM, 0.4]]);        // ripples

  // --- things that stand on the ground --------------------------------------------
  // (Each is drawn on a clear background with its foot at the bottom centre, and
  // most are taller than a tile: they are placed by their foot and sorted by it,
  // so someone standing behind a tree is hidden by its crown.)
  const BARK = 0x5b3a1e, BARK_HI = 0x6d4825;
  const oak = (key, c, apples) => {
    sprite(key, 30, 42,
      [[13, 28, 4, 12, BARK], [11, 38, 8, 2, BARK],
       [5, 8, 20, 16, c.mid], [2, 12, 26, 9, c.mid], [9, 4, 12, 5, c.mid], [8, 23, 14, 4, c.dark]],
      [[14, 28, 1, 11, BARK_HI],
       [10, 5, 7, 3, c.light], [6, 9, 9, 6, c.light], [8, 10, 4, 3, c.bright], [3, 13, 5, 4, c.light], [20, 9, 3, 3, c.light],
       [17, 15, 9, 7, c.dark], [12, 20, 10, 5, c.deep],
       ...(apples ? [[7, 12], [12, 8], [18, 10], [22, 15], [9, 18], [15, 16], [19, 21], [5, 16]].flatMap(([x, y]) =>
         [[x, y, 2, 2, 0xd23a3a], [x, y, 1, 1, 0xff9a9a]]) : [])]);
  };
  const LEAF = { mid: 0x2f6b2f, light: 0x3d8a3a, bright: 0x56a84f, dark: 0x2a5e2a, deep: 0x26552a };
  const ORCHARD = { mid: 0x3f8f3a, light: 0x55a84a, bright: 0x74c060, dark: 0x347a32, deep: 0x2f6f2c };
  oak("tree_oak0", LEAF);
  sprite("tree_oak1", 30, 42,
    [[13, 29, 4, 11, BARK], [12, 38, 6, 2, BARK],
     [7, 5, 16, 20, LEAF.mid], [4, 10, 22, 10, LEAF.mid], [10, 2, 10, 4, LEAF.mid], [9, 25, 12, 3, LEAF.dark]],
    [[14, 29, 1, 10, BARK_HI],
     [9, 6, 7, 5, LEAF.light], [11, 3, 5, 2, LEAF.bright], [5, 11, 4, 4, LEAF.light], [10, 8, 3, 2, LEAF.bright],
     [16, 14, 9, 9, LEAF.dark], [11, 22, 10, 5, LEAF.deep]]);
  oak("tree_apple", ORCHARD, true);
  oak("tree_apple_bare", ORCHARD, false);
  sprite("tree_birch", 26, 42,
    [[12, 20, 3, 20, 0xe8e4da],
     [6, 6, 14, 13, 0x6b9a35], [4, 10, 18, 7, 0x6b9a35], [9, 3, 8, 4, 0x6b9a35]],
    [[12, 24, 2, 1, 0x333333], [13, 29, 2, 1, 0x333333], [12, 34, 2, 1, 0x333333], [14, 20, 1, 20, 0xc9c5bb],
     [7, 7, 6, 4, 0x82b347], [10, 4, 4, 2, 0xa6cf5f], [5, 11, 3, 3, 0x82b347],
     [14, 12, 7, 6, 0x5c8a2c], [8, 17, 10, 2, 0x527d27]]);
  const pine = (key, snow) => {
    const tiers = [[11, 2, 4, 5], [9, 6, 8, 6], [7, 11, 12, 7], [5, 17, 16, 8], [3, 24, 20, 8], [2, 31, 22, 5]];
    sprite(key, 26, 44,
      [[11, 36, 4, 7, BARK], ...tiers.map(([x, y, w, h]) => [x, y, w, h, 0x1f5532])],
      [[12, 36, 1, 7, BARK_HI],
       [11, 3, 2, 3, 0x2f7a46], [9, 7, 3, 3, 0x2f7a46], [7, 12, 4, 4, 0x2f7a46], [5, 18, 5, 5, 0x2f7a46],
       [3, 25, 6, 5, 0x2f7a46], [2, 31, 7, 3, 0x2f7a46],
       [14, 8, 3, 4, 0x184427], [15, 13, 4, 5, 0x184427], [16, 19, 5, 6, 0x184427], [17, 26, 6, 6, 0x184427],
       [18, 32, 6, 4, 0x184427],
       ...(snow ? [[11, 2, 4, 2], [9, 6, 6, 2], [7, 11, 8, 2], [5, 17, 10, 2], [3, 24, 12, 2], [2, 31, 9, 1],
                   [14, 13, 4, 1], [17, 19, 3, 1]].map(([x, y, w, h]) => [x, y, w, h, 0xf4f8fb]) : [])]);
  };
  // autumn: the same trees, turned gold and red
  const AUTUMN = { mid: 0xc8742a, light: 0xe0952f, bright: 0xf2b84a, dark: 0xa85a22, deep: 0x8a4a1e };
  const AUTUMN_RED = { mid: 0xb0442a, light: 0xd0602f, bright: 0xe8904a, dark: 0x8e3422, deep: 0x74301e };
  oak("tree_oak0_autumn", AUTUMN);
  oak("tree_oak1_autumn", AUTUMN_RED);
  sprite("tree_birch_autumn", 26, 42,
    [[12, 20, 3, 20, 0xe8e4da],
     [6, 6, 14, 13, 0xd8b030], [4, 10, 18, 7, 0xd8b030], [9, 3, 8, 4, 0xd8b030]],
    [[12, 24, 2, 1, 0x333333], [13, 29, 2, 1, 0x333333], [12, 34, 2, 1, 0x333333], [14, 20, 1, 20, 0xc9c5bb],
     [7, 7, 6, 4, 0xf0cc4a], [10, 4, 4, 2, 0xffe27a], [5, 11, 3, 3, 0xf0cc4a],
     [14, 12, 7, 6, 0xb88a22], [8, 17, 10, 2, 0xa07a1e]]);
  // winter: bare branches with snow along the tops
  const bare = (key, w, trunk, snowy) => {
    const B = trunk;
    sprite(key, w, 42,
      [[w / 2 - 2, 20, 4, 20, B], [w / 2 - 4, 38, 8, 2, B],
       [w / 2 - 9, 12, 2, 10, B], [w / 2 - 7, 20, 5, 2, B], [w / 2 + 6, 9, 2, 12, B], [w / 2 + 2, 18, 5, 2, B],
       [w / 2 - 1, 5, 2, 16, B], [w / 2 - 12, 8, 2, 5, B], [w / 2 + 9, 6, 2, 4, B]],
      [[w / 2 - 1, 20, 1, 18, 0x7a5a3a],
       ...(snowy ? [[w / 2 - 9, 12, 2, 1], [w / 2 + 6, 9, 2, 1], [w / 2 - 1, 5, 2, 1], [w / 2 - 7, 20, 5, 1],
                    [w / 2 + 2, 18, 5, 1], [w / 2 - 12, 8, 2, 1], [w / 2 + 9, 6, 2, 1]]
                    .map(([x, y, ww, h]) => [x, y, ww, h, 0xf4f8fb]) : [])]);
  };
  bare("tree_bare0", 30, BARK, true);
  bare("tree_bare1", 30, 0x4a3420, true);
  bare("tree_birch_bare", 26, 0xd8d4ca, true);
  pine("tree_pine", false);
  pine("tree_pine_snow", true);
  sprite("tree_palm", 30, 44,
    [[14, 22, 3, 21, 0x8a6a3c], [15, 14, 3, 9, 0x8a6a3c],
     [13, 2, 5, 5, 0x3f8f3a], [15, 8, 3, 7, 0x3f8f3a],
     [4, 10, 12, 3, 0x3f8f3a], [2, 13, 6, 3, 0x3f8f3a], [17, 9, 11, 3, 0x3f8f3a], [24, 12, 5, 3, 0x3f8f3a],
     [7, 5, 9, 3, 0x3f8f3a], [17, 4, 8, 3, 0x3f8f3a]],
    [[14, 26, 3, 1, 0x6d5230], [14, 30, 3, 1, 0x6d5230], [14, 34, 3, 1, 0x6d5230], [14, 38, 3, 1, 0x6d5230],
     [15, 18, 3, 1, 0x6d5230], [16, 22, 1, 20, 0xa8854e],
     [5, 10, 10, 1, 0x5fb04e], [18, 9, 9, 1, 0x5fb04e], [8, 5, 7, 1, 0x5fb04e], [18, 4, 6, 1, 0x5fb04e],
     [14, 2, 3, 1, 0x5fb04e], [3, 13, 4, 1, 0x5fb04e], [25, 12, 3, 1, 0x5fb04e],
     [13, 14, 2, 2, 0x5b3a1e], [18, 15, 2, 2, 0x5b3a1e]]);
  sprite("stump", 14, 11, [[3, 4, 8, 6, 0x6d4825]],
    [[3, 3, 8, 2, 0xc89a62], [5, 3, 4, 1, 0xdfb880], [6, 4, 2, 1, 0x9c7445], [3, 9, 8, 1, 0x4a3018]]);

  const BERRIES = [[6, 8], [12, 6], [16, 10], [9, 12], [13, 11]].flatMap(([x, y]) =>
    [[x, y, 3, 3, 0xd23a4f], [x, y, 1, 1, 0xff8a9a]]);
  const bush = (key, berries) => sprite(key, 24, 18,
    [[3, 6, 18, 10, 0x3f7a35], [6, 3, 12, 4, 0x4c8f40]],
    [[4, 14, 16, 2, 0x2d5a27], [7, 4, 6, 2, 0x5fa652], [4, 8, 4, 3, 0x4c8f40], [15, 7, 5, 6, 0x356a2d], ...(berries ? BERRIES : [])]);
  bush("bush", true);
  bush("bush_empty", false);

  sprite("rock0", 24, 18, [[3, 7, 17, 9, 0x66666e], [6, 3, 10, 5, 0x7d7d86]],
    [[8, 4, 5, 2, 0x9a9aa3], [3, 14, 17, 2, 0x4a4a52], [14, 8, 5, 5, 0x5a5a62]]);
  sprite("rock1", 24, 18, [[2, 9, 9, 7, 0x6a6a72], [3, 7, 6, 3, 0x7d7d86], [11, 5, 10, 11, 0x5e5e66], [12, 4, 7, 2, 0x8a8a93]],
    [[2, 14, 19, 2, 0x45454c], [13, 5, 4, 2, 0xa0a0a8], [4, 8, 3, 1, 0x9a9aa3]]);
  sprite("rock2", 22, 14, [[3, 5, 15, 7, 0x70707a], [6, 3, 9, 3, 0x84848e]],
    [[3, 10, 15, 2, 0x4c4c54], [7, 4, 4, 1, 0xa2a2ac]]);
  sprite("rock_snow", 24, 18, [[3, 7, 17, 9, 0x66666e], [6, 3, 10, 5, 0x7d7d86]],
    [[3, 14, 17, 2, 0x4a4a52], [14, 9, 5, 4, 0x5a5a62], [6, 3, 10, 2, 0xf4f8fb], [4, 7, 9, 2, 0xf4f8fb], [16, 7, 3, 1, 0xf4f8fb]]);

  const cactus = (key, fruit) => sprite(key, 18, 27,
    [[7, 4, 4, 22, 0x4f9a4a], [2, 11, 3, 7, 0x4f9a4a], [2, 15, 6, 3, 0x4f9a4a], [13, 8, 3, 8, 0x4f9a4a], [10, 13, 6, 3, 0x4f9a4a],
     ...(fruit ? [[8, 2, 2, 2, 0xe0559a], [2, 9, 2, 2, 0xe0559a], [14, 6, 2, 2, 0xe0559a]] : [])],
    [[8, 5, 1, 20, 0x6ab85e], [3, 12, 1, 5, 0x6ab85e], [14, 9, 1, 6, 0x6ab85e], [10, 6, 1, 19, 0x3c7a3a],
     ...(fruit ? [[8, 2, 1, 1, 0xffa0cc], [2, 9, 1, 1, 0xffa0cc], [14, 6, 1, 1, 0xffa0cc]] : [])]);
  cactus("cactus", true);
  cactus("cactus_bare", false);

  sprite("mushroom", 20, 14, [[3, 5, 6, 3, 0xc0392b], [9, 3, 6, 4, 0xc0392b], [14, 8, 4, 2, 0xc0392b]],
    [[5, 8, 2, 5, 0xe8dcc0], [11, 7, 2, 6, 0xe8dcc0], [15, 10, 2, 3, 0xe8dcc0],
     [4, 5, 1, 1, 0xffffff], [7, 6, 1, 1, 0xffffff], [10, 4, 1, 1, 0xffffff], [13, 5, 1, 1, 0xffffff], [15, 8, 1, 1, 0xffffff]]);
  sprite("mushroom_bare", 20, 14, [],
    [[5, 10, 2, 3, 0xd8ccb0], [11, 10, 2, 3, 0xd8ccb0], [4, 9, 4, 1, 0x8a5a3a], [10, 9, 4, 1, 0x8a5a3a]]);

  // Huts: four styles (thatch, red tile, slate, log cabin).
  const hut = (key, roof, roofHi, wall, wallLine) => {
    solids([[5, 17, 20, 15, wall]]);
    if (wallLine !== null) for (let y = 20; y < 31; y += 3) px(5, y, 20, 1, wallLine);
    tri(0, 19, 15, 0, 30, 19, OUTLINE);
    tri(2, 18, 15, 3, 28, 18, roof);
    tri(7, 16, 15, 6, 23, 16, roofHi);
    px(5, 18, 20, 1, 0x3d2614);
    px(13, 22, 5, 10, 0x3d2614); px(16, 27, 1, 1, 0xc89a62);                         // door + handle
    px(7, 21, 4, 4, 0xf3d27a); px(7, 23, 4, 1, 0x6b4a2c); px(9, 21, 1, 4, 0x6b4a2c);   // lit window
    px(20, 22, 3, 3, 0xf3d27a);
    px(5, 31, 20, 1, 0x4a3220);
    save(key, 30, 34);
  };
  hut("hut0", 0xc9a14a, 0xdcb862, 0xa0784c, null);
  hut("hut1", 0x8c3b26, 0xa34a31, 0xb08a5c, null);
  hut("hut2", 0x4f5d75, 0x63728c, 0x9a8f80, 0x857a6c);
  hut("hut3", 0x3f6b3a, 0x4d8247, 0x8a5a32, 0x6b4424);

  // Farms lie flat on the ground: tilled rows; growing = sprouts, ripe = golden wheat.
  const farmBase = () => {
    px(1, 1, TILE - 2, TILE - 2, 0x6a4a2c);
    for (let y = 3; y < TILE - 1; y += 6) px(2, y, 20, 3, 0x553a22);
    px(1, 1, TILE - 2, 1, 0x8a6a46); px(1, 1, 1, TILE - 2, 0x8a6a46); px(22, 1, 1, TILE - 2, 0x4a3220); px(1, 22, TILE - 2, 1, 0x4a3220);
  };
  farmBase();
  for (let y = 3; y < TILE - 1; y += 6) for (let x = 3; x < 21; x += 4) { px(x, y, 1, 2, 0x5fa040); px(x + 1, y - 1, 1, 1, 0x86c95a); }
  save("farm_growing");
  farmBase();
  for (let y = 4; y < TILE - 1; y += 6) for (let x = 3; x < 21; x += 3) { px(x, y - 2, 1, 4, 0xc9a13a); px(x, y - 3, 2, 2, 0xf0cf5c); }
  save("farm");

  // Campfire: two flame frames, swapped every few hundred ms, and a glow to lay under it.
  const fire = (key, flames) => {
    g.fillStyle(0x000000, 0.2); g.fillEllipse(12, 19, 20, 8);
    solids([[4, 17, 3, 2, 0x8a8a90], [8, 20, 3, 2, 0x8a8a90], [13, 20, 3, 2, 0x8a8a90], [17, 17, 3, 2, 0x8a8a90],
            [6, 14, 2, 2, 0x8a8a90], [16, 14, 2, 2, 0x8a8a90]]);
    details([[7, 17, 10, 2, BARK], [9, 15, 2, 5, BARK_HI], [13, 15, 2, 5, BARK_HI], ...flames]);
    save(key);
  };
  fire("fire0", [[9, 9, 6, 7, 0xe8641e], [10, 6, 4, 4, 0xf08a2a], [11, 4, 2, 3, 0xf6c04a], [10, 11, 4, 5, 0xf6c04a]]);
  fire("fire1", [[8, 10, 7, 6, 0xe8641e], [11, 5, 4, 6, 0xf08a2a], [12, 3, 2, 3, 0xf6c04a], [10, 12, 4, 4, 0xf6c04a], [8, 7, 1, 2, 0xf08a2a]]);
  for (let r = 30; r >= 4; r -= 2) { g.fillStyle(0xffb04a, 0.04); g.fillCircle(32, 32, r); }
  save("glow", 64, 64);

  // Mountains: one per tile of mountain terrain, each much wider and taller than
  // its tile, so a stretch of them reads as a range.
  const peak = (key, ax, ay, cap) => {
    tri(0, 48, ax, ay - 3, 44, 48, 0x2a2622);
    tri(2, 48, ax, ay, 42, 48, 0x7a756c);
    tri(2, 48, ax, ay, ax - 2, 48, 0x918b80);                                   // the lit face
    tri(ax + 5, 48, ax, ay, 42, 48, 0x5e5a54);                                  // the face in shadow
    tri(ax - cap * 0.5, ay + cap, ax, ay, ax + cap * 0.5, ay + cap, 0xf4f8fb);  // snow on top
    tri(ax - cap * 0.5, ay + cap, ax - cap * 0.2, ay + cap + 4, ax, ay + cap, 0xf4f8fb);
    tri(ax, ay + cap, ax + cap * 0.25, ay + cap + 5, ax + cap * 0.5, ay + cap, 0xdfe8ef);
    tri(ax, ay, ax + cap * 0.5, ay + cap, ax + 1, ay + cap, 0xdfe8ef);
    details([[ax - 9, ay + 22, 5, 1, 0x6a655d], [ax - 12, ay + 30, 7, 1, 0x6a655d], [ax + 6, ay + 26, 6, 1, 0x4c4843],
             [ax + 9, ay + 34, 7, 1, 0x4c4843], [ax - 4, ay + 36, 6, 1, 0x6a655d]]);
    save(key, 44, 48);
  };
  peak("peak0", 22, 5, 13);
  peak("peak1", 18, 10, 10);
  peak("peak2", 26, 8, 11);

  // --- what villages build ------------------------------------------------------------
  sprite("sapling", 14, 18, [[6, 9, 2, 8, 0x6d4825], [3, 3, 8, 6, 0x4c9a40]],
    [[4, 3, 3, 2, 0x6ab85e], [8, 6, 2, 2, 0x3c7a32], [5, 16, 4, 1, 0x4a3018]]);

  const STONE = 0x9a958a, STONE_DK = 0x77736a, STONE_HI = 0xb4afa3, WOOD = 0x8a5a32, WOOD_DK = 0x6b4424;
  // a stone house: grey walls, a shingle roof, a chimney, warm windows
  solids([[3, 18, 26, 16, STONE], [21, 3, 4, 10, STONE_DK]]);
  for (let y = 20; y < 33; y += 4) for (let x = 3 + ((y / 4) % 2) * 3; x < 28; x += 6) px(x, y, 5, 3, STONE_HI);
  tri(0, 20, 16, 4, 32, 20, OUTLINE); tri(2, 19, 16, 6, 30, 19, 0x5a3a26); tri(7, 17, 16, 9, 25, 17, 0x6d4a32);
  px(21, 3, 4, 2, 0x4c4843);
  px(13, 24, 6, 10, 0x3d2614); px(17, 29, 1, 1, 0xc89a62);
  px(5, 23, 5, 5, 0xf3d27a); px(7, 23, 1, 5, 0x4a3220); px(22, 23, 5, 5, 0xf3d27a); px(24, 23, 1, 5, 0x4a3220);
  px(3, 33, 26, 1, 0x4a4640);
  save("house", 32, 36);
  // a storehouse: a broad wooden barn with crates and sacks by the door
  solids([[2, 14, 28, 15, WOOD], [24, 22, 6, 7, 0xa8763e]]);
  for (let x = 4; x < 30; x += 4) px(x, 14, 1, 15, WOOD_DK);
  tri(0, 16, 16, 3, 32, 16, OUTLINE); tri(2, 15, 16, 5, 30, 15, 0x8c3b26); tri(8, 13, 16, 8, 24, 13, 0xa34a31);
  px(10, 18, 9, 11, 0x3d2614); px(14, 18, 1, 11, 0x2a1a0e);
  px(25, 23, 4, 1, 0x6b4424); px(25, 26, 4, 1, 0x6b4424);
  solids([[2, 24, 5, 5, 0xd8c8a0]]); px(3, 24, 3, 1, 0xb8a880);
  save("storehouse", 32, 30);
  // a well: a stone ring under a little roof, a bucket on a rope
  solids([[3, 15, 16, 9, STONE], [4, 3, 2, 13, WOOD], [16, 3, 2, 13, WOOD]]);
  px(5, 16, 12, 3, 0x2f6f9f); px(3, 19, 16, 1, STONE_DK); px(6, 21, 3, 2, STONE_HI); px(12, 21, 3, 2, STONE_HI);
  tri(1, 5, 11, 0, 21, 5, 0x6d4825); px(10, 5, 1, 7, 0xcfc4a8); solids([[8, 11, 5, 4, 0x8a5a32]]);
  save("well", 22, 26);
  // a workshop: an open-sided shed, a workbench, an anvil, tools on the wall
  solids([[2, 12, 3, 16, WOOD_DK], [27, 12, 3, 16, WOOD_DK], [6, 20, 12, 3, WOOD], [20, 21, 6, 4, 0x55555c]]);
  tri(0, 13, 16, 2, 32, 13, OUTLINE); tri(1, 12, 16, 4, 31, 12, 0x6b4a2c); tri(8, 11, 16, 6, 24, 11, 0x7a5a36);
  px(7, 23, 2, 5, WOOD_DK); px(15, 23, 2, 5, WOOD_DK); px(21, 25, 4, 3, 0x45454c); px(19, 21, 8, 1, 0x7a7a82);
  px(9, 17, 1, 3, 0x7a5230); px(8, 16, 3, 1, 0xa8a8b0); px(13, 16, 1, 4, 0x7a5230); px(12, 15, 3, 2, 0x8a8a92);
  px(2, 27, 28, 1, 0x4a3220);
  save("workshop", 32, 30);
  // a shrine: two stone posts and a lintel, with a glowing offering between
  solids([[3, 10, 4, 22, STONE], [17, 10, 4, 22, STONE], [1, 6, 22, 4, STONE_HI]]);
  px(1, 9, 22, 1, STONE_DK); px(4, 12, 1, 18, STONE_HI); px(18, 12, 1, 18, STONE_HI);
  for (let r = 6; r >= 2; r--) { g.fillStyle(0xffe08a, 0.12); g.fillCircle(12, 25, r); }
  solids([[9, 26, 6, 4, 0x8a8a90]]); px(11, 23, 2, 3, 0xffe08a); px(11, 22, 2, 1, 0xfff4c8);
  save("shrine", 24, 34);
  // a building site: a wooden frame going up, planks and stones waiting
  solids([[4, 6, 2, 22, 0xb08850], [22, 6, 2, 22, 0xb08850], [13, 2, 2, 26, 0xb08850]]);
  px(4, 10, 20, 2, 0xc89a62); px(4, 18, 20, 2, 0xc89a62); px(6, 12, 1, 6, 0x9a7444); px(20, 12, 1, 6, 0x9a7444);
  solids([[1, 25, 9, 3, 0xd9b47a], [17, 24, 4, 4, STONE], [22, 25, 4, 3, STONE_DK]]);
  px(1, 26, 9, 1, 0xb08a52);
  save("site", 28, 30);

  // --- odds and ends --------------------------------------------------------------
  // Grave marker.
  px(5, 19, 14, 3, 0x5b4128); px(6, 18, 12, 1, 0x6d4f31);
  px(8, 7, 8, 12, 0x8e8e96); px(9, 6, 6, 1, 0x8e8e96); px(9, 7, 6, 1, 0xa6a6ae);
  px(11, 9, 2, 7, 0x5e5e66); px(9, 11, 6, 2, 0x5e5e66);
  save("grave");

  g.fillStyle(0xffffff, 1); g.fillEllipse(9, 3, 18, 6);
  save("shadow", 18, 6);

  px(3, 4, 1, 16, 0x7a5230); px(2, 0, 3, 1, 0xcfcfd6); px(2, 1, 3, 3, 0xa8a8b0); px(3, 0, 1, 1, 0xffffff);
  px(2, 12, 3, 1, 0x3d2614);
  save("spear", 7, 20);

  g.lineStyle(2, 0xffe08a, 1); g.strokeEllipse(12, 5, 20, 8);
  save("ring", 24, 10);
  g.lineStyle(2, 0xffffff, 1); g.strokeRect(1, 1, TILE - 2, TILE - 2);
  save("homemark");

  // Things lying on the ground (a felled tree's wood, what the dead carried...).
  solids([[2, 8, 10, 4, 0x8a5a32], [5, 3, 10, 4, 0x9c6b3c]]);   // a small stack of logs
  px(2, 9, 10, 1, 0x6d4825); px(5, 4, 10, 1, 0x7a5230);
  px(2, 8, 2, 4, 0xd9b47a); px(13, 3, 2, 4, 0xd9b47a); px(2, 9, 1, 2, 0xb08a52); px(14, 4, 1, 2, 0xb08a52);
  save("drop_wood", 16, 13);
  solids([[2, 7, 10, 3, 0x4c8f40], [3, 3, 4, 4, 0xd23a4f], [8, 4, 4, 4, 0xd23a4f], [5, 1, 4, 4, 0xe0485e]]);   // berries on a leaf
  px(3, 3, 1, 1, 0xff8a9a); px(8, 4, 1, 1, 0xff8a9a); px(5, 1, 1, 1, 0xffa3b0);
  save("drop_food", 14, 11);
  solids([[1, 3, 13, 1, 0x7a5230], [12, 2, 4, 3, 0xa8a8b0]]);   // a spear lying flat
  px(14, 3, 2, 1, 0xe6e6ee);
  save("drop_spear", 17, 6);
  solids([[2, 2, 9, 8, 0xb08a5c]]);                             // anything else: a tied bundle
  px(2, 5, 9, 1, 0x6b4a2c); px(6, 2, 1, 8, 0x6b4a2c);
  save("drop_other", 13, 12);
  solids([[2, 5, 6, 5, 0x8a8a90], [7, 3, 6, 6, 0x9a9aa3]]);   // a few stones
  px(8, 3, 3, 1, 0xc0c0c8); px(3, 5, 2, 1, 0xb0b0b8);
  save("drop_stone", 15, 12);
  solids([[2, 4, 10, 4, 0x6aa6d8], [11, 3, 3, 6, 0x5a96c8]]);   // a fish
  px(3, 4, 8, 1, 0x9fd3ff); px(4, 5, 1, 1, OUTLINE);
  save("drop_fish", 16, 11);

  // --- the living world (M3) -------------------------------------------------------
  // A fire spreading through the trees: a charred trunk in tall flames (two frames).
  const blaze = (key, flames) => {
    g.fillStyle(0x000000, 0.25); g.fillEllipse(12, 28, 20, 6);
    solids([[10, 16, 4, 12, 0x2a1e16], [6, 24, 12, 4, 0x2a1e16]]);
    details(flames);
    save(key, 24, 30);
  };
  blaze("blaze0", [[5, 12, 14, 14, 0xe8641e], [7, 6, 10, 10, 0xf08a2a], [9, 2, 6, 7, 0xf6c04a], [10, 14, 4, 10, 0xfff0a0],
                   [3, 18, 4, 6, 0xd04a18], [17, 16, 4, 8, 0xd04a18]]);
  blaze("blaze1", [[6, 10, 13, 16, 0xe8641e], [6, 5, 9, 9, 0xf08a2a], [10, 0, 5, 7, 0xf6c04a], [9, 13, 5, 11, 0xfff0a0],
                   [4, 16, 3, 8, 0xd04a18], [18, 13, 3, 9, 0xd04a18], [15, 4, 2, 4, 0xf08a2a]]);
  for (let r = 4; r >= 1; r--) { g.fillStyle(0x8a8580, 0.18); g.fillCircle(5, 5, r); }
  save("smoke", 10, 10);
  // weather
  px(0, 0, 1, 7, 0xbfe0ff, 0.9); px(1, 1, 1, 5, 0xffffff, 0.5);
  save("raindrop", 2, 8);
  px(1, 0, 1, 3, 0xffffff); px(0, 1, 3, 1, 0xffffff);
  save("snowflake", 3, 3);
  for (let i = 0; i < 12; i++) {  // a soft, wide puff of fog
    g.fillStyle(0xe8ecef, 0.07); g.fillEllipse(128, 64, 240 - i * 18, 110 - i * 8);
  }
  save("fogpuff", 256, 128);
  for (let r = 64; r >= 2; r -= 2) { g.fillStyle(0xffffff, 0.06 + (64 - r) / 64 * 0.05); g.fillCircle(64, 64, r); }
  save("lightmask", 128, 128);   // cut out of the dark around a light
  for (let i = 0; i < 40; i++) {  // winter: snow lying on the ground (tiled over the map)
    const x = (i * 37) % 64, y = (i * 23 + (i >> 2) * 7) % 64;
    px(x, y, 2 + (i % 3), 1, 0xffffff, 0.9);
  }
  save("snowdust", 64, 64);
  for (let v = 0; v < 3; v++) {  // frost and a skin of snow over the land in winter (one per tile)
    const r = rand(9173 + v * 311);
    for (let i = 0; i < 5; i++) {
      const x = Math.floor(r() * 18), y = Math.floor(r() * 20), w = 3 + Math.floor(r() * 5);
      px(x, y, w, 2, 0xf4f8fb, 0.85); px(x + 1, y - 1, w - 2, 1, 0xffffff, 0.7);
    }
    for (let i = 0; i < 26; i++) px(Math.floor(r() * 24), Math.floor(r() * 24), 1, 1, 0xffffff, 0.75);
    save(`frost${v}`);
  }
  // a comet: a bright head and a long fading tail
  for (let i = 0; i < 60; i++) px(i, 7 - Math.round(i / 30), 2, 2 + Math.round(i / 20), 0xcfe8ff, (i / 60) * 0.6);
  g.fillStyle(0xffffff, 1); g.fillCircle(62, 7, 3);
  save("comet", 66, 14);

  // Wild animals. Each faces right; the sprite is flipped to face left.
  sprite("deer", 22, 20,
    [[4, 8, 13, 6, 0x9a6a3c], [15, 4, 4, 5, 0x9a6a3c], [17, 2, 3, 3, 0xa8784a],
     [5, 14, 2, 5, 0x7a5230], [8, 14, 2, 5, 0x7a5230], [13, 14, 2, 5, 0x7a5230], [15, 14, 2, 5, 0x7a5230]],
    [[5, 8, 11, 2, 0xb8885a], [4, 9, 2, 2, 0xf0e6d6], [19, 3, 1, 1, OUTLINE], [20, 4, 1, 1, 0x3a2a1e],
     [16, 0, 1, 2, 0x5a3a20], [18, 0, 1, 2, 0x5a3a20], [15, 0, 1, 1, 0x5a3a20], [19, 0, 1, 1, 0x5a3a20],
     [7, 11, 3, 1, 0xe0d0b8], [11, 10, 2, 1, 0xe0d0b8]]);
  sprite("rabbit", 13, 11,
    [[2, 5, 8, 5, 0xa89a88], [8, 3, 4, 4, 0xa89a88], [9, 0, 1, 4, 0xa89a88], [11, 0, 1, 4, 0xa89a88]],
    [[1, 6, 2, 2, 0xf4f0ea], [10, 4, 1, 1, OUTLINE], [9, 1, 1, 2, 0xe0b0b0], [3, 9, 6, 1, 0x8a7c6c]]);
  sprite("wolf", 24, 17,
    [[4, 6, 14, 6, 0x6e6e78], [16, 3, 6, 5, 0x6e6e78], [21, 5, 3, 2, 0x6e6e78], [17, 1, 2, 3, 0x5a5a64],
     [20, 1, 1, 2, 0x5a5a64], [1, 5, 4, 2, 0x5a5a64],
     [5, 12, 2, 4, 0x55555e], [8, 12, 2, 4, 0x55555e], [14, 12, 2, 4, 0x55555e], [16, 12, 2, 4, 0x55555e]],
    [[5, 6, 12, 2, 0x8a8a94], [6, 10, 10, 2, 0x5a5a64], [19, 4, 1, 1, 0xf6d24a], [23, 5, 1, 1, OUTLINE],
     [17, 7, 4, 1, 0xd8d8de]]);
  g.fillStyle(0xfff07a, 1); g.fillRect(0, 0, 2, 1); g.fillRect(4, 0, 2, 1);
  save("eyes", 6, 1);   // a wolf's eyes, shining in the dark
  g.destroy();
}

// --- characters: drawn on demand from each NPC's inherited looks ------------
export const SKIN = { porcelain: 0xffe6d5, fair: 0xf6d2b0, tan: 0xd9a676, olive: 0xb88a5a, brown: 0x8d5a3b, deep: 0x5c3a26 };
export const HAIR = { black: 0x1e1a18, brown: 0x5a3820, auburn: 0x8a3b1e, blonde: 0xe2c46a, copper: 0xc0642a, ash: 0x9a9488 };
export const CLOTH = { red: 0xb83a3a, blue: 0x3a64b8, green: 0x3f8a46, ochre: 0xc89a3a, purple: 0x7a4a9a,
                teal: 0x2f8f8a, grey: 0x7a7a80, cream: 0xe8dcc0 };
export const DEFAULT_LOOK = { skin: "fair", hair: "brown", hairstyle: "short", clothes: "tunic", color: "blue", hat: "none" };
export const NPC_W = 20, NPC_H = 26;
export const lookKey = (look, stage) =>
  `npc:${stage}:${look.skin}:${look.hair}:${look.hairstyle}:${look.clothes}:${look.color}:${look.hat}`;

export function npcTexture(scene, rawLook, stage) {
  const look = { ...DEFAULT_LOOK, ...(rawLook || {}) };
  const key = lookKey(look, stage);
  if (scene.textures.exists(key)) return key;

  // Parts are collected first so every outlined part gets its dark border
  // drawn underneath all fills (black survives at the silhouette edges only).
  const parts = [];
  const part = (x, y, w, h, c, outline = true) => parts.push({ x, y, w, h, c, outline });
  const skin = SKIN[look.skin] || SKIN.fair;
  const cloth = CLOTH[look.color] || CLOTH.blue;
  const hairC = stage === "elder" ? 0xdedede : (HAIR[look.hair] || HAIR.brown);
  const denim = 0x3a4f7a, cream = 0xe8dcc0, fur = 0x8b6b4a;
  const pants = look.clothes === "overalls" ? denim : shade(cloth, 0.5);

  // legs + feet
  if (look.clothes !== "robe") { part(6, 18, 3, 5, pants); part(11, 18, 3, 5, pants); }
  part(6, 23, 3, 1, 0x2a1d14); part(11, 23, 3, 1, 0x2a1d14);
  // arms (sleeves) + hands
  const sleeve = { vest: cream, furs: fur }[look.clothes] ?? cloth;
  part(3, 10, 2, 6, sleeve); part(15, 10, 2, 6, sleeve);
  part(3, 16, 2, 1, skin); part(15, 16, 2, 1, skin);
  // torso
  switch (look.clothes) {
    case "robe":
      part(5, 10, 10, 13, cloth); part(5, 14, 10, 1, shade(cloth, 0.7), false); part(9, 10, 2, 13, shade(cloth, 1.15), false);
      break;
    case "vest":
      part(5, 10, 10, 8, cream); part(5, 10, 3, 8, cloth, false); part(12, 10, 3, 8, cloth, false);
      part(5, 16, 10, 1, 0x4a3220, false);
      break;
    case "furs":
      part(5, 10, 10, 8, fur);
      [[6, 12], [9, 14], [12, 12], [7, 16], [11, 16], [13, 15]].forEach(([x, y]) => part(x, y, 1, 1, 0xb08d64, false));
      part(5, 10, 10, 2, 0xd8c8a8, false); part(5, 16, 10, 1, cloth, false);
      break;
    case "overalls":
      part(5, 10, 10, 8, cloth); part(7, 12, 6, 6, denim, false); part(7, 10, 1, 2, denim, false); part(12, 10, 1, 2, denim, false);
      part(8, 13, 1, 1, 0xcfcfd6, false); part(11, 13, 1, 1, 0xcfcfd6, false);
      break;
    default: // tunic
      part(5, 10, 10, 8, cloth); part(5, 15, 10, 1, 0x4a3220, false); part(9, 15, 2, 1, 0xd9b45a, false);
      part(8, 10, 4, 1, shade(cloth, 0.75), false);
  }
  // head + face
  part(7, 3, 6, 7, skin);
  part(8, 6, 1, 1, 0x222222, false); part(11, 6, 1, 1, 0x222222, false);
  if (stage === "child") part(8, 8, 4, 1, shade(skin, 0.85), false);
  // hair
  const top = () => { part(7, 3, 6, 2, hairC); };
  switch (look.hairstyle) {
    case "short": top(); part(7, 5, 1, 1, hairC, false); part(12, 5, 1, 1, hairC, false); break;
    case "long": top(); part(6, 4, 1, 8, hairC); part(13, 4, 1, 8, hairC); break;
    case "ponytail": top(); part(13, 4, 2, 6, hairC); break;
    case "mohawk": part(9, 0, 2, 5, hairC); break;
    case "bun": top(); part(8, 1, 4, 2, hairC); break;
    case "braids":
      top(); part(6, 5, 1, 8, hairC); part(13, 5, 1, 8, hairC);
      part(6, 12, 1, 1, 0xd23a3a, false); part(13, 12, 1, 1, 0xd23a3a, false);
      break;
    default: break; // bald
  }
  // hats
  const band = look.color === "red" ? 0xf2f2f2 : 0xd23a3a;
  switch (look.hat) {
    case "straw": part(4, 3, 12, 1, 0xe3c26a); part(7, 1, 6, 2, 0xd4b052); part(7, 2, 6, 1, 0x8a3b1e, false); break;
    case "hood": part(6, 2, 8, 2, shade(cloth, 0.8)); part(6, 4, 1, 6, shade(cloth, 0.8)); part(13, 4, 1, 6, shade(cloth, 0.8)); break;
    case "headband": part(7, 4, 6, 1, band, false); break;
    case "feather": part(7, 4, 6, 1, band, false); part(13, 0, 1, 4, 0xf2f2f2); part(13, 0, 1, 1, 0xd23a3a, false); break;
    case "flower": part(11, 2, 2, 2, 0xff7ab0); part(11, 2, 1, 1, 0xffe066, false); break;
    default: break;
  }
  // elders lean on a walking stick
  if (stage === "elder") { part(18, 12, 1, 12, 0x6b4a2c); part(17, 12, 2, 1, 0x6b4a2c); }

  const g = scene.make.graphics({ x: 0, y: 0, add: false });
  for (const p of parts) if (p.outline) { g.fillStyle(OUTLINE, 1); g.fillRect(p.x - 1, p.y - 1, p.w + 2, p.h + 2); }
  for (const p of parts) { g.fillStyle(p.c, 1); g.fillRect(p.x, p.y, p.w, p.h); }
  g.generateTexture(key, NPC_W, NPC_H);
  g.destroy();
  return key;
}

export function portrait(key) {
  if (state.portraits.has(key)) return state.portraits.get(key);
  let url = "";
  try { url = scene().textures.getBase64(key); } catch (_e) { /* texture not ready */ }
  if (url) state.portraits.set(key, url);
  return url;
}
