/* Blocky models for the first-person view, built from boxes in code (no files).
 *
 * One tile is one unit. Every box is textured with the same 16x16 noise at a
 * fixed size (16 texels a tile), so everything has the same chunky pixel grain,
 * and is coloured by its vertices. Boxes marked glow are unlit (fire, lit windows).
 * Models are merged into one geometry each and cached by name. */
import * as THREE from "../../vendor/three.module.min.js";
import { CLOTH, DEFAULT_LOOK, HAIR, SKIN, rand, shade } from "../textures.js";

// --- the shared grain and materials -------------------------------------------------
function grain(lo = 205) {
  const c = document.createElement("canvas");
  c.width = c.height = 16;
  const g = c.getContext("2d"), r = rand(91);
  for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) {
    const v = Math.round(lo + r() * (255 - lo));
    g.fillStyle = `rgb(${v},${v},${v})`;
    g.fillRect(x, y, 1, 1);
  }
  const t = new THREE.CanvasTexture(c);
  t.magFilter = THREE.NearestFilter;
  t.minFilter = THREE.NearestMipmapLinearFilter;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}
export const GRAIN = grain();
export const SOLID = new THREE.MeshLambertMaterial({ map: GRAIN, vertexColors: true });
export const GLOW = new THREE.MeshBasicMaterial({ map: GRAIN, vertexColors: true });
export const MATS = [SOLID, GLOW];

// --- boxes into geometry -----------------------------------------------------------------
// [corner offsets of the 4 vertices, normal, which two axes the texture runs along]
const FACES = [
  { n: [1, 0, 0], c: [[1, 0, 1], [1, 0, 0], [1, 1, 0], [1, 1, 1]], uv: [2, 1] },
  { n: [-1, 0, 0], c: [[0, 0, 0], [0, 0, 1], [0, 1, 1], [0, 1, 0]], uv: [2, 1] },
  { n: [0, 1, 0], c: [[0, 1, 1], [1, 1, 1], [1, 1, 0], [0, 1, 0]], uv: [0, 2] },
  { n: [0, -1, 0], c: [[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]], uv: [0, 2] },
  { n: [0, 0, 1], c: [[0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], uv: [0, 1] },
  { n: [0, 0, -1], c: [[1, 0, 0], [0, 0, 0], [0, 1, 0], [1, 1, 0]], uv: [0, 1] },
];
const col = new THREE.Color();

// A list of boxes, added to with box(); geometry() merges them.
export class Builder {
  constructor(seed = 1) { this.solid = []; this.glow = []; this.r = rand(seed); }
  // x0..x1 (east), y0..y1 (up), z0..z1 (south), in tiles; color 0xRRGGBB; opts: glow, jitter (shade spread)
  box(x0, y0, z0, x1, y1, z1, color, opts = {}) {
    const j = opts.jitter ?? 0.06;
    const c = j ? shade(color, 1 - j + this.r() * 2 * j) : color;
    (opts.glow ? this.glow : this.solid).push([x0, y0, z0, x1, y1, z1, c]);
    return this;
  }
  // a box of size w x h x d centred on x, z, standing on y
  block(x, y, z, w, h, d, color, opts) { return this.box(x - w / 2, y, z - d / 2, x + w / 2, y + h, z + d / 2, color, opts); }
  geometry() {
    const all = [...this.solid, ...this.glow];
    const n = all.length * 36;
    const pos = new Float32Array(n * 3), nor = new Float32Array(n * 3), uv = new Float32Array(n * 2), cl = new Float32Array(n * 3);
    let v = 0;
    for (const [x0, y0, z0, x1, y1, z1, c] of all) {
      col.setHex(c);
      const lo = [x0, y0, z0], size = [x1 - x0, y1 - y0, z1 - z0];
      const off = ((x0 * 7.31 + z0 * 3.17 + y0 * 5.3) % 1) * 16;  // (so neighbouring boxes don't line up their grain)
      for (const f of FACES) {
        const quad = f.c.map((k) => [lo[0] + k[0] * size[0], lo[1] + k[1] * size[1], lo[2] + k[2] * size[2]]);
        for (const i of [0, 1, 2, 0, 2, 3]) {
          const p = quad[i];
          pos.set(p, v * 3);
          nor.set(f.n, v * 3);
          uv[v * 2] = p[f.uv[0]] + off / 16;
          uv[v * 2 + 1] = p[f.uv[1]];
          cl[v * 3] = col.r; cl[v * 3 + 1] = col.g; cl[v * 3 + 2] = col.b;
          v++;
        }
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("normal", new THREE.BufferAttribute(nor, 3));
    g.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
    g.setAttribute("color", new THREE.BufferAttribute(cl, 3));
    g.addGroup(0, this.solid.length * 36, 0);
    if (this.glow.length) g.addGroup(this.solid.length * 36, this.glow.length * 36, 1);
    g.computeBoundingSphere();
    return g;
  }
}

const cache = new Map();
const cached = (key, make) => {
  let g = cache.get(key);
  if (!g) { g = make(); cache.set(key, g); }
  return g;
};

// --- colours -------------------------------------------------------------------------
const BARK = 0x6b4a2c, BIRCH = 0xe6e0d0, PINE = 0x2f5a34, OAK = 0x4f8a37, OAK2 = 0x5a9640, PALM = 0x5f9a3a;
const LEAVES = {
  spring: [0x5fa043, 0x6aad4a], summer: [OAK, OAK2],
  autumn: [0xc98a2a, 0xb4532a], winter: null,
};
const STONE = 0x8a8578, PLANK = 0x9a7048, THATCH = 0xc9a25a, ROOF = 0x8a3d2a, WALL = 0xd8c9a6;

// --- the land's things -----------------------------------------------------------------
// key: the object's key; spent: harvested; ground: terrain key; h: tile hash; season
export function objectGeometry(key, spent, ground, h, season, extra = 0) {
  const variant = (h >>> 3) % 4;
  const name = `${key}:${spent ? 1 : 0}:${key === "tree" || key === "rock" ? ground : ""}:${variant}:${season}:${extra}`;
  return cached(name, () => {
    const b = new Builder(h % 997 + 1);
    (MAKE[key] || MAKE.unknown)(b, { spent, ground, h, variant, season, extra });
    return b.geometry();
  });
}

function leafBall(b, cx, y, cz, w, h, colors, step = 0.25) {
  // a crown of leaf cubes: a rough ball, a little ragged at the edges
  for (let yy = 0; yy < h; yy += step) for (let xx = -w / 2; xx < w / 2 - 1e-6; xx += step) for (let zz = -w / 2; zz < w / 2 - 1e-6; zz += step) {
    const dx = (xx + step / 2) / (w / 2), dz = (zz + step / 2) / (w / 2), dy = (yy + step / 2 - h / 2) / (h / 2);
    const d = dx * dx + dy * dy + dz * dz;
    if (d > 1.05 || (d > 0.75 && b.r() < 0.35)) continue;
    if (d < 0.45) continue;  // (hollow inside: never seen)
    b.box(cx + xx, y + yy, cz + zz, cx + xx + step, y + yy + step, cz + zz + step, colors[b.r() < 0.5 ? 0 : 1], { jitter: 0.1 });
  }
}

const MAKE = {
  tree(b, { spent, ground, h, season }) {
    const s = 0.9 + ((h >>> 9) % 5) * 0.05;
    if (spent) {  // a stump
      b.block(0, 0, 0, 0.3, 0.22, 0.3, BARK).block(0, 0.22, 0, 0.26, 0.02, 0.26, 0xc8a070);
      return;
    }
    if (ground === "sand") {  // a palm: a leaning trunk and fronds
      for (let i = 0; i < 8; i++) b.block(i * 0.03, i * 0.28, 0, 0.16, 0.28, 0.16, i % 2 ? 0x8a6a40 : 0x9c7a4a);
      const top = 8 * 0.28;
      for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1], [0.7, 0.7], [-0.7, -0.7], [0.7, -0.7], [-0.7, 0.7]]) {
        for (let k = 1; k <= 3; k++) b.block(0.21 + dx * k * 0.22, top - k * k * 0.035, dz * k * 0.22, 0.24, 0.07, 0.24, PALM);
      }
      b.block(0.21, top - 0.05, 0, 0.3, 0.18, 0.3, PALM);
      return;
    }
    const pine = ground === "snow" || ground === "stone" || (ground === "forest" && h % 3 === 0);
    const trunk = (ht, w, c) => b.block(0, 0, 0, w, ht, w, c);
    if (pine) {
      trunk(0.7 * s, 0.22, BARK);
      const snowy = ground === "snow" || season === "winter";
      for (let i = 0; i < 4; i++) {
        const w = (1.3 - i * 0.28) * s, y = (0.5 + i * 0.45) * s;
        b.block(0, y, 0, w, 0.45 * s, w, PINE, { jitter: 0.08 });
        if (snowy) b.block(0, y + 0.45 * s, 0, w * 0.8, 0.06, w * 0.8, 0xeef3f6, { jitter: 0.02 });
      }
      b.block(0, (0.5 + 4 * 0.45) * s, 0, 0.18, 0.25, 0.18, PINE);
      return;
    }
    const birch = h % 4 === 0 && ground !== "forest";
    trunk(1.2 * s, 0.24, birch ? BIRCH : BARK);
    if (birch) for (let i = 0; i < 4; i++) b.block(0, 0.15 + i * 0.27, 0, 0.25, 0.04, 0.25, 0x2a2622, { jitter: 0 });
    const leaves = LEAVES[season] || LEAVES.summer;
    if (!leaves) {  // winter: bare branches
      b.block(0.2, 0.9 * s, 0, 0.36, 0.08, 0.08, BARK).block(-0.2, 1.05 * s, 0, 0.36, 0.08, 0.08, BARK)
        .block(0, 1.2 * s, 0.18, 0.08, 0.08, 0.3, BARK).block(0, 1.2 * s, 0, 0.12, 0.4, 0.12, BARK);
      return;
    }
    const cols = birch ? [shade(leaves[0], 1.12), shade(leaves[1], 1.08)] : leaves;
    leafBall(b, 0, 0.85 * s, 0, 1.5 * s, 1.35 * s, cols);
  },
  sapling(b) {
    b.block(0, 0, 0, 0.06, 0.35, 0.06, BARK).block(0, 0.3, 0, 0.22, 0.18, 0.22, OAK2).block(0, 0.45, 0, 0.12, 0.1, 0.12, OAK2);
  },
  fruit(b, { spent, season }) {
    b.block(0, 0, 0, 0.24, 1.0, 0.24, BARK);
    if (season === "winter") { b.block(0, 0.95, 0, 0.12, 0.4, 0.12, BARK).block(0.18, 0.85, 0, 0.3, 0.07, 0.07, BARK); return; }
    leafBall(b, 0, 0.75, 0, 1.3, 1.1, [0x4f8a37, 0x5d9a40]);
    if (!spent) for (let i = 0; i < 9; i++) {
      const a = i * 2.4, r = 0.55;
      b.block(Math.cos(a) * r, 0.9 + (i % 3) * 0.25, Math.sin(a) * r, 0.11, 0.11, 0.11, 0xd23a2a, { jitter: 0.05 });
    }
  },
  bush(b, { spent }) {
    leafBall(b, 0, 0, 0, 0.8, 0.6, [0x3f7a33, 0x4c8a3a], 0.2);
    if (!spent) for (let i = 0; i < 8; i++) {
      const a = i * 2.1, r = 0.36;
      b.block(Math.cos(a) * r, 0.12 + (i % 3) * 0.15, Math.sin(a) * r, 0.08, 0.08, 0.08, 0x7a2a8a, { jitter: 0.08 });
    }
  },
  cactus(b, { spent }) {
    const g = 0x4f8a4a;
    b.block(0, 0, 0, 0.26, 0.9, 0.26, g).block(0.22, 0.35, 0, 0.18, 0.12, 0.14, g).block(0.27, 0.45, 0, 0.12, 0.3, 0.12, g)
      .block(-0.2, 0.5, 0, 0.16, 0.1, 0.12, g).block(-0.25, 0.58, 0, 0.1, 0.22, 0.1, g);
    if (!spent) b.block(0, 0.9, 0, 0.12, 0.1, 0.12, 0xd9446a).block(0.27, 0.75, 0, 0.1, 0.08, 0.1, 0xd9446a);
  },
  mushroom(b, { spent }) {
    for (const [x, z, s] of [[0, 0, 1], [0.25, 0.18, 0.7], [-0.22, 0.2, 0.6], [0.1, -0.25, 0.55]]) {
      b.block(x, 0, z, 0.07 * s, 0.18 * s, 0.07 * s, 0xe8e0cc);
      if (!spent) b.block(x, 0.17 * s, z, 0.24 * s, 0.09 * s, 0.24 * s, 0xc0392b).block(x, 0.26 * s, z, 0.12 * s, 0.04, 0.12 * s, 0xc0392b);
    }
  },
  rock(b, { ground, variant }) {
    const c = ground === "snow" ? 0x9a978f : STONE;
    b.block(0, 0, 0, 0.75, 0.42, 0.65, c, { jitter: 0.08 }).block(0.12, 0.42, -0.05, 0.45, 0.2, 0.4, shade(c, 1.08))
      .block(-0.25 + variant * 0.1, 0, 0.25, 0.3, 0.22, 0.3, shade(c, 0.92));
    if (ground === "snow") b.block(0.12, 0.62, -0.05, 0.42, 0.05, 0.36, 0xeef3f6, { jitter: 0 });
  },
  farm(b, { spent }) {
    // furrows, with crops (tall and golden when ripe, short and green while growing)
    for (let i = 0; i < 4; i++) {
      const z = -0.36 + i * 0.24;
      b.box(-0.48, 0, z - 0.08, 0.48, 0.05, z + 0.08, 0x5a3e26, { jitter: 0.04 });
      for (let k = 0; k < 5; k++) {
        const x = -0.38 + k * 0.19;
        if (spent) b.block(x, 0.05, z, 0.07, 0.12, 0.07, 0x6aa045);
        else b.block(x, 0.05, z, 0.08, 0.32, 0.08, 0xd8b84a).block(x, 0.33, z, 0.1, 0.08, 0.1, 0xe6c860);
      }
    }
  },
  fire(b) {
    for (const [x, z] of [[-0.18, 0], [0.18, 0], [0, -0.18], [0, 0.18]]) b.block(x, 0, z, 0.14, 0.1, 0.14, 0x6f6a63);
    b.block(0, 0, 0, 0.36, 0.08, 0.08, BARK).block(0, 0.05, 0, 0.08, 0.08, 0.36, BARK);
    b.block(0, 0.08, 0, 0.18, 0.18, 0.18, 0xf08a2a, { glow: true }).block(0, 0.2, 0, 0.1, 0.16, 0.1, 0xf6d04a, { glow: true });
  },
  blaze(b) {
    for (let i = 0; i < 7; i++) {
      const a = i * 1.9, r = 0.18 + (i % 3) * 0.1;
      b.block(Math.cos(a) * r, 0, Math.sin(a) * r, 0.2, 0.4 + (i % 4) * 0.22, 0.2, i % 2 ? 0xf06a1a : 0xf6b02a, { glow: true });
    }
    b.block(0, 0, 0, 0.24, 1.2, 0.24, 0x2a2420);
  },
  hut(b, { variant }) {
    const wall = [0x9a7048, 0x8a6440, 0xa87a50, 0x93683f][variant];
    b.box(-0.4, 0, -0.38, 0.4, 0.72, 0.38, wall);
    b.box(-0.12, 0, 0.379, 0.12, 0.45, 0.4, 0x3a2a1a, { jitter: 0 });   // door (south)
    b.box(0.2, 0.36, 0.379, 0.32, 0.48, 0.4, 0xf6d77a, { glow: true, jitter: 0 });  // window
    for (let i = 0; i < 5; i++) {  // a stepped thatch roof
      const w = 0.98 - i * 0.2;
      b.box(-w / 2, 0.72 + i * 0.12, -0.46 + i * 0.09, w / 2, 0.84 + i * 0.12, 0.46 - i * 0.09, THATCH, { jitter: 0.07 });
    }
    b.block(0.24, 1.0, -0.15, 0.1, 0.32, 0.1, STONE);
  },
  house(b) {
    b.box(-0.45, 0, -0.42, 0.45, 0.85, 0.42, STONE, { jitter: 0.03 });
    for (let i = 0; i < 4; i++) b.box(-0.45, 0.2 * i + 0.1, 0.42, 0.45, 0.2 * i + 0.11, 0.425, shade(STONE, 0.8), { jitter: 0 });
    b.box(-0.12, 0, 0.42, 0.12, 0.5, 0.44, 0x4a3420, { jitter: 0 });
    b.box(-0.36, 0.45, 0.42, -0.2, 0.62, 0.44, 0xf6d77a, { glow: true, jitter: 0 });
    b.box(0.2, 0.45, 0.42, 0.36, 0.62, 0.44, 0xf6d77a, { glow: true, jitter: 0 });
    for (let i = 0; i < 5; i++) {
      const w = 1.04 - i * 0.22;
      b.box(-w / 2, 0.85 + i * 0.13, -0.48 + i * 0.1, w / 2, 0.98 + i * 0.13, 0.48 - i * 0.1, ROOF, { jitter: 0.05 });
    }
    b.block(0.28, 1.1, -0.2, 0.12, 0.4, 0.12, shade(STONE, 0.85));
  },
  storehouse(b) {
    b.box(-0.46, 0, -0.42, 0.46, 0.75, 0.42, PLANK);
    for (const x of [-0.46, 0.46]) for (const z of [-0.42, 0.42]) b.block(x, 0, z, 0.1, 0.8, 0.1, 0x5a3e26);
    b.box(-0.25, 0, 0.42, 0.25, 0.55, 0.44, 0x5a3e26, { jitter: 0 });
    b.box(-0.52, 0.75, -0.5, 0.52, 0.87, 0.5, 0x6a4a30);
    for (let i = 0; i < 3; i++) b.block(-0.62 + 0.12 * i, 0, 0.55, 0.1, 0.12 + i * 0.05, 0.1, 0xc88a3a);
  },
  well(b) {
    b.box(-0.32, 0, -0.32, 0.32, 0.36, 0.32, STONE).box(-0.24, 0.3, -0.24, 0.24, 0.37, 0.24, 0x2f6f9f, { jitter: 0 });
    b.block(-0.28, 0.36, 0, 0.06, 0.5, 0.06, 0x5a3e26).block(0.28, 0.36, 0, 0.06, 0.5, 0.06, 0x5a3e26);
    b.box(-0.4, 0.84, -0.3, 0.4, 0.94, 0.3, ROOF).block(0, 0.7, 0, 0.6, 0.04, 0.04, 0x5a3e26);
  },
  workshop(b) {
    for (const x of [-0.42, 0.42]) for (const z of [-0.4, 0.4]) b.block(x, 0, z, 0.08, 0.8, 0.08, 0x5a3e26);
    b.box(-0.5, 0.8, -0.48, 0.5, 0.9, 0.48, PLANK).box(-0.42, 0, -0.4, 0.42, 0.6, -0.36, PLANK);
    b.box(-0.3, 0, -0.05, 0.3, 0.32, 0.2, 0x6a4a30);       // the bench
    b.block(0.15, 0.32, 0.08, 0.2, 0.12, 0.12, 0x55555a);  // an anvil
    b.block(-0.3, 0, 0.32, 0.22, 0.25, 0.18, BARK);         // a log pile
  },
  shrine(b) {
    b.box(-0.4, 0, -0.4, 0.4, 0.1, 0.4, STONE);
    for (const x of [-0.3, 0.3]) b.block(x, 0.1, 0, 0.1, 1.0, 0.1, 0xb83a2a);
    b.box(-0.45, 1.05, -0.07, 0.45, 1.15, 0.07, 0xb83a2a).box(-0.36, 0.85, -0.05, 0.36, 0.92, 0.05, 0xb83a2a);
    b.block(0, 0.1, 0, 0.18, 0.2, 0.18, 0xf6d77a, { glow: true });
  },
  // a building site: posts, then walls rising, then a roof frame (extra: 0..3, how far along)
  site(b, { extra }) {
    for (const x of [-0.4, 0.4]) for (const z of [-0.38, 0.38]) b.block(x, 0, z, 0.08, 0.35 + extra * 0.2, 0.08, PLANK);
    b.box(-0.45, 0, -0.43, 0.45, 0.04, 0.43, 0x7a5a3a);
    if (extra >= 1) b.box(-0.4, 0, -0.38, 0.4, 0.2 * extra, -0.32, PLANK).box(-0.4, 0, -0.38, -0.34, 0.2 * extra, 0.38, PLANK);
    if (extra >= 2) b.box(0.34, 0, -0.38, 0.4, 0.4, 0.38, PLANK);
    if (extra >= 3) b.box(-0.45, 0.75, -0.05, 0.45, 0.82, 0.05, 0x5a3e26);
    b.block(0.3, 0, 0.3, 0.22, 0.12, 0.18, 0xb08a5a);  // a stack of planks
  },
  unknown(b) { b.block(0, 0, 0, 0.3, 0.3, 0.3, 0xb04ad0); },
};

// --- people ----------------------------------------------------------------------------
// A villager as a set of parts that move: { geo: {head, body, arm, leg}, h: height }.
const HAT = { straw: 0xd8b860, hood: 0x5a4a6a, headband: 0xc0392b, feather: 0x6a4a2a, flower: 0xe86a9a };
export function personGeometry(look, stage, winter) {
  const L = { ...DEFAULT_LOOK, ...(look || {}) };
  const key = `p:${JSON.stringify(L)}:${stage}:${winter ? 1 : 0}`;
  return cached(key, () => {
    const skin = SKIN[L.skin] || SKIN.fair, hair = HAIR[L.hair] || HAIR.brown, cloth = CLOTH[L.color] || CLOTH.blue;
    const k = stage === "child" ? 0.68 : stage === "elder" ? 0.95 : 1;
    const hairC = stage === "elder" ? 0xb8b4ac : hair;
    // the head (pivot at the neck); at rest everyone faces +z (south)
    const head = new Builder(3).box(-0.12, 0, -0.12, 0.12, 0.24, 0.12, skin, { jitter: 0 })
      .box(-0.07, 0.11, 0.12, -0.03, 0.15, 0.125, 0x1a1410, { jitter: 0 })
      .box(0.03, 0.11, 0.12, 0.07, 0.15, 0.125, 0x1a1410, { jitter: 0 });
    if (L.hairstyle !== "bald") {
      head.box(-0.13, 0.17, -0.13, 0.13, 0.26, 0.13, hairC, { jitter: 0.03 }).box(-0.13, 0.04, -0.13, 0.13, 0.2, -0.08, hairC, { jitter: 0.03 });
      if (L.hairstyle === "mohawk") head.box(-0.03, 0.26, -0.12, 0.03, 0.34, 0.1, hairC);
      if (L.hairstyle === "bun" || L.hairstyle === "ponytail") head.box(-0.05, 0.12, -0.2, 0.05, 0.22, -0.12, hairC);
    }
    if (L.hairstyle === "long" || L.hairstyle === "braids") head.box(-0.13, -0.1, -0.13, 0.13, 0.05, -0.06, hairC, { jitter: 0.03 });
    if (L.hat && L.hat !== "none" && HAT[L.hat]) head.box(-0.15, 0.24, -0.15, 0.15, 0.31, 0.15, HAT[L.hat]);
    if (winter) head.box(-0.13, -0.03, -0.13, 0.13, 0.03, 0.13, 0xb83a3a, { jitter: 0 });  // a scarf
    const body = new Builder(4).box(-0.13, 0, -0.08, 0.13, 0.32, 0.08, cloth)
      .box(-0.13, 0, -0.08, 0.13, 0.04, 0.08, shade(cloth, 0.7), { jitter: 0 });
    if (L.clothes === "furs") body.box(-0.14, 0.2, -0.09, 0.14, 0.33, 0.09, 0x8b6b4a);
    if (L.clothes === "robe") body.box(-0.14, -0.14, -0.09, 0.14, 0.02, 0.09, cloth);
    const arm = new Builder(5).box(-0.045, -0.3, -0.045, 0.045, 0, 0.045, cloth).box(-0.04, -0.34, -0.04, 0.04, -0.28, 0.04, skin, { jitter: 0 });
    const pants = L.clothes === "overalls" ? 0x3a4f7a : L.clothes === "robe" ? cloth : shade(cloth, 0.5);
    const leg = new Builder(6).box(-0.055, -0.3, -0.055, 0.055, 0, 0.055, pants).box(-0.06, -0.33, -0.06, 0.06, -0.28, 0.07, 0x2a1e14, { jitter: 0 });
    return { head: head.geometry(), body: body.geometry(), arm: arm.geometry(), leg: leg.geometry(), k };
  });
}

// What they hold while working, and the load on their back.
export function toolGeometry(kind) {
  return cached(`tool:${kind}`, () => {
    const b = new Builder(7).box(-0.02, -0.04, -0.02, 0.02, 0.36, 0.02, 0x7a5a3a);
    if (kind === "axe") b.box(-0.02, 0.26, 0.02, 0.02, 0.36, 0.12, 0xb0b4b8);
    else if (kind === "pick") b.box(-0.02, 0.32, -0.12, 0.02, 0.36, 0.12, 0xb0b4b8);
    else if (kind === "hammer") b.box(-0.04, 0.3, -0.06, 0.04, 0.38, 0.06, 0x6a6a70);
    else if (kind === "rod") b.box(-0.01, 0.36, -0.01, 0.01, 0.7, 0.01, 0x7a5a3a);
    else if (kind === "spear") b.box(-0.015, 0.36, -0.015, 0.015, 0.62, 0.015, 0x7a5a3a).box(-0.03, 0.62, -0.03, 0.03, 0.72, 0.03, 0xc0c4c8);
    return b.geometry();
  });
}
export function loadGeometry(item) {
  const c = { wood: BARK, stone: STONE, fish: 0x8ab0c8, food: 0xc0392b }[item] || 0xb08a5a;
  return cached(`load:${item}`, () => new Builder(8).box(-0.11, 0, -0.07, 0.11, 0.22, 0.07, c).geometry());
}

// --- animals and things on the ground -------------------------------------------------------
export function animalGeometry(kind) {
  return cached(`a:${kind}`, () => {
    const b = new Builder(9);
    if (kind === "rabbit") {
      const c = 0xb8a890;
      b.box(-0.08, 0.04, -0.12, 0.08, 0.18, 0.1, c).box(-0.06, 0.12, 0.08, 0.06, 0.24, 0.18, c)
        .box(-0.05, 0.24, 0.1, -0.02, 0.38, 0.13, c).box(0.02, 0.24, 0.1, 0.05, 0.38, 0.13, c)
        .box(-0.03, 0.1, -0.16, 0.03, 0.16, -0.11, 0xf0ece4, { jitter: 0 });
    } else if (kind === "wolf") {
      const c = 0x6a6a72;
      b.box(-0.1, 0.24, -0.3, 0.1, 0.44, 0.2, c).box(-0.09, 0.32, 0.18, 0.09, 0.5, 0.38, c).box(-0.05, 0.34, 0.38, 0.05, 0.42, 0.48, shade(c, 0.85))
        .box(-0.08, 0.5, 0.22, -0.03, 0.58, 0.27, c).box(0.03, 0.5, 0.22, 0.08, 0.58, 0.27, c)
        .box(-0.03, 0.3, -0.48, 0.03, 0.38, -0.3, c)
        .box(-0.06, 0.42, 0.37, -0.03, 0.45, 0.375, 0xf6e04a, { glow: true, jitter: 0 })
        .box(0.03, 0.42, 0.37, 0.06, 0.45, 0.375, 0xf6e04a, { glow: true, jitter: 0 });
      for (const [x, z] of [[-0.07, -0.24], [0.07, -0.24], [-0.07, 0.14], [0.07, 0.14]]) b.block(x, 0, z, 0.05, 0.25, 0.05, shade(c, 0.85));
    } else {  // deer
      const c = 0xa0703a;
      b.box(-0.1, 0.38, -0.28, 0.1, 0.6, 0.22, c).box(-0.06, 0.55, 0.16, 0.06, 0.8, 0.28, c).box(-0.06, 0.74, 0.24, 0.06, 0.84, 0.42, c)
        .box(-0.02, 0.5, -0.32, 0.02, 0.6, -0.27, 0xf0ece4, { jitter: 0 })
        .box(-0.1, 0.84, 0.26, -0.08, 1.0, 0.28, 0xd8c8a8).box(0.08, 0.84, 0.26, 0.1, 1.0, 0.28, 0xd8c8a8);
      for (const [x, z] of [[-0.07, -0.22], [0.07, -0.22], [-0.07, 0.16], [0.07, 0.16]]) b.block(x, 0, z, 0.05, 0.4, 0.05, shade(c, 0.8));
    }
    return b.geometry();
  });
}
const DROP_COLOR = { wood: BARK, food: 0xc0392b, stone: STONE, fish: 0x8ab0c8, spear: 0xc0c4c8 };
export function dropGeometry(item) {
  return cached(`d:${item}`, () => new Builder(10).block(0, 0, 0, 0.2, 0.2, 0.2, DROP_COLOR[item] || 0xd8b860).geometry());
}
export function graveGeometry() {
  return cached("grave", () => new Builder(11).block(0, 0, 0, 0.3, 0.06, 0.6, 0x6a4a30)
    .block(0, 0, -0.25, 0.26, 0.4, 0.08, 0x8a8578).geometry());
}
