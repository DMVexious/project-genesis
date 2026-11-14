/* Things standing on the ground: trees, rocks, bushes, huts, farms, fires.
 * Each is its own sprite, placed by its foot and sorted by it, so people walk
 * in front of and behind them. The server says only "a tree"; which tree is
 * decided here, from the ground it stands on and where it is. */
import { TILE } from "./config.js";
import { track, untrack } from "./cull.js";
import { objectOf, terrainOf } from "./land.js";
import { state } from "./state.js";
import { tileHash } from "./textures.js";

function treeFor(ground, h) {
  if (ground === "sand") return "tree_palm";
  if (ground === "snow") return "tree_pine_snow";
  if (ground === "stone") return "tree_pine";
  if (ground === "forest") return h % 3 === 0 ? "tree_pine" : `tree_oak${h % 2}`;
  return h % 4 === 0 ? "tree_birch" : `tree_oak${(h >>> 2) % 2}`;
}

// How each kind of thing is drawn. tex/spent: its picture standing and after it
// has been harvested; sway: how far it may sit off-centre; flat: lies on the ground.
const LOOK = {
  rock: { tex: (ground, h) => (ground === "snow" ? "rock_snow" : `rock${h % 3}`), sway: 2,
          tint: (ground) => (ground === "sand" ? 0xeedcb4 : ground === "dirt" ? 0xdccbb4 : 0xffffff) },
  tree: { tex: treeFor, spent: () => "stump", sway: 3, sizes: true },
  bush: { tex: () => "bush", spent: () => "bush_empty", sway: 2 },
  fruit: { tex: () => "tree_apple", spent: () => "tree_apple_bare", sway: 2, sizes: true },
  cactus: { tex: () => "cactus", spent: () => "cactus_bare", sway: 3 },
  mushroom: { tex: () => "mushroom", spent: () => "mushroom_bare", sway: 2 },
  hut: { tex: (_ground, h) => `hut${(h >>> 3) % 4}` },
  farm: { tex: () => "farm", spent: () => "farm_growing", flat: true },
  fire: { tex: () => "fire0", fire: true },
  sapling: { tex: () => "sapling", sway: 3 },
  house: { tex: () => "house" },
  storehouse: { tex: () => "storehouse" },
  well: { tex: () => "well" },
  workshop: { tex: () => "workshop" },
  shrine: { tex: () => "shrine" },
  site: { tex: () => "site", site: true },
};
const UNKNOWN = { tex: () => "drop_other" };

const objs = new Map();   // tile index -> { img, ch, key, x, y, home, glow, bar, label }
let sites = new Set();    // building sites with a progress bar showing
const fires = new Set();
let shownKey = "", shownRows = null;

const picture = (look, o, ground, h) => (o.spent && look.spent ? look.spent(ground, h) : look.tex(ground, h));

export function clearObjects() {
  for (const rec of objs.values()) {
    dropSiteBar(rec);
    untrack(rec.img);
    rec.img.destroy();
    if (rec.glow) { untrack(rec.glow); rec.glow.destroy(); }
  }
  objs.clear();
  fires.clear();
  sites = new Set();
  shownKey = "";
  shownRows = null;
}

function take(sc, i, rec, live) {
  objs.delete(i);
  dropSiteBar(rec);
  fires.delete(rec);
  untrack(rec.img);
  if (rec.glow) { untrack(rec.glow); rec.glow.destroy(); }
  if (live) sc.tweens.add({ targets: rec.img, alpha: 0, duration: 300, onComplete: () => rec.img.destroy() });
  else rec.img.destroy();
}

// A felled tree: its crown tips over and fades while the stump takes its place.
function fell(sc, img, h) {
  const ghost = sc.add.image(img.x, img.y, img.texture.key).setOrigin(0.5, 1).setDepth(img.depth + 1)
    .setScale(img.scaleX, img.scaleY);
  sc.tweens.add({ targets: ghost, angle: h % 2 ? 82 : -82, alpha: { from: 1, to: 0 }, duration: 560, ease: "Quad.in",
    onComplete: () => ghost.destroy() });
}

function put(sc, f, x, y, i, ch, have, live) {
  const o = ch ? objectOf(ch) : null;
  const look = o && (LOOK[o.key] || UNKNOWN);
  const ground = (terrainOf(f.terrain[y][x]) || {}).key;
  const h = tileHash(x, y);
  if (have && o && have.key === o.key) {
    // the same thing in another state (ripe or picked, tree or stump): keep the sprite, change its picture
    if (live && o.key === "tree" && o.spent) fell(sc, have.img, h);
    have.img.setTexture(picture(look, o, ground, h));
    have.ch = ch;
    if (live) sc.tweens.add({ targets: have.img, alpha: { from: 0.4, to: 1 }, duration: 260 });
    return;
  }
  if (have) take(sc, i, have, live);
  if (!o) return;
  let img;
  if (look.flat) {
    img = sc.add.image(x * TILE, y * TILE, picture(look, o, ground, h)).setOrigin(0).setDepth(-2);
  } else {
    const sway = look.sway ? ((h >>> 6) % (2 * look.sway + 1)) - look.sway : 0;
    img = sc.add.image(x * TILE + TILE / 2 + sway, (y + 1) * TILE - 1, picture(look, o, ground, h))
      .setOrigin(0.5, 1).setDepth((y + 1) * TILE - 4);
    if (look.sizes) img.setScale(0.92 + ((h >>> 9) % 5) * 0.04);
  }
  const tint = look.tint ? look.tint(ground) : 0xffffff;
  if (tint !== 0xffffff) img.setTint(tint);
  track(img, x, y);
  const rec = { img, ch, key: o.key, x, y, home: img.x, glow: null };
  if (look.fire) {
    rec.glow = track(sc.add.image(x * TILE + TILE / 2, y * TILE + TILE / 2 + 3, "glow")
      .setBlendMode(Phaser.BlendModes.ADD).setDepth(-1).setScale(1.7), x, y);
    fires.add(rec);
  }
  objs.set(i, rec);
  if (live) {  // newly built or grown: it rises into place
    const sx = img.scaleX, sy = img.scaleY;
    sc.tweens.add({ targets: img, scaleX: { from: sx * 0.6, to: sx }, scaleY: { from: sy * 0.4, to: sy },
      alpha: { from: 0.3, to: 1 }, duration: 380, ease: "Back.out" });
  }
}

// Bring the sprites in line with a frame's tiles. Only tiles that changed are touched.
export function renderObjects(sc, f, opts = {}) {
  const key = `${f.world}:${f.trev}:${f.width}x${f.height}`;
  const fresh = key !== shownKey;
  if (fresh) { clearObjects(); shownKey = key; }
  if (f.tiles === shownRows) return;
  const live = !fresh && !opts.snap && !opts.reverse;
  const W = f.width;
  for (let y = 0; y < f.height; y++) {
    const row = f.tiles[y];
    if (shownRows && shownRows[y] === row) continue;  // nothing on this row changed
    const ground = f.terrain[y];
    for (let x = 0; x < W; x++) {
      const ch = row[x] !== ground[x] ? row[x] : "";
      const have = objs.get(y * W + x);
      if ((have ? have.ch : "") !== ch) put(sc, f, x, y, y * W + x, ch, have, live);
    }
  }
  shownRows = f.tiles;
  updateSites(sc, f);
}

// Building sites show how far along they are, and what they will be.
function updateSites(sc, f) {
  const W = f.width, live = new Set();
  for (const [x, y, what, progress] of f.sites || []) {
    const rec = objs.get(y * W + x);
    if (!rec || rec.key !== "site") continue;
    live.add(rec);
    if (!rec.bar) {
      rec.bar = sc.add.graphics().setDepth(rec.img.depth + 1);
      const place = (state.defs && state.defs.objects[what] && state.defs.objects[what].place) || what;
      rec.label = sc.add.text(x * TILE + TILE / 2, y * TILE - 22, place, { fontFamily: "monospace", fontSize: "8px",
        color: "#ffe9b8", stroke: "#140e09", strokeThickness: 3 }).setOrigin(0.5, 1).setResolution(3).setDepth(rec.img.depth + 1);
      track(rec.bar, x, y);
      track(rec.label, x, y);
    }
    if (rec.progress !== progress) {
      rec.progress = progress;
      const bx = x * TILE + 1, by = y * TILE - 20;
      rec.bar.clear().fillStyle(0x140e09, 0.85).fillRect(bx, by, TILE - 2, 5)
        .fillStyle(0xf0cf5c, 1).fillRect(bx + 1, by + 1, Math.round((TILE - 4) * progress / 100), 3);
    }
  }
  for (const rec of sites) if (!live.has(rec)) dropSiteBar(rec);
  sites = live;
}

function dropSiteBar(rec) {
  if (!rec.bar) return;
  untrack(rec.bar); untrack(rec.label);
  rec.bar.destroy(); rec.label.destroy();
  rec.bar = rec.label = null;
}

// Campfires flicker and their light breathes.
export function flickerFires(t) {
  const frame = Math.floor(t / 220) % 2 ? "fire1" : "fire0";
  for (const rec of fires) {
    rec.img.setTexture(frame);
    if (rec.glow) rec.glow.setAlpha(0.75 + 0.2 * Math.sin(t / 180 + rec.x));
  }
}

// A thing shudders when it is chopped at or picked from.
export function shakeObject(sc, x, y, width) {
  const rec = objs.get(y * width + x);
  if (!rec || rec.shaking) return;
  rec.shaking = true;
  sc.tweens.add({ targets: rec.img, x: { from: rec.home - 1.5, to: rec.home + 1.5 }, duration: 45, yoyo: true, repeat: 2,
    onComplete: () => { rec.img.x = rec.home; rec.shaking = false; } });
}
