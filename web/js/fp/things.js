/* Everything standing or moving on the land in first person, kept in step with
 * the server's frames: the objects on the tiles (diffed row by row, as in
 * objects.js), the villagers, the animals, what lies on the ground and graves.
 * Movers glide from tile to tile over as long as the step takes, as in 2D. */
import * as THREE from "../../vendor/three.module.min.js";
import { GRAVE_TICKS, MAX_GRAVES } from "../config.js";
import { objectOf, terrainAt, terrainOf } from "../land.js";
import { state } from "../state.js";
import { tileHash } from "../textures.js";
import { groundTop } from "./blocks.js";
import { MATS, SOLID, animalGeometry, dropGeometry, graveGeometry, loadGeometry, objectGeometry,
         personGeometry, toolGeometry } from "./models.js";

const objs = new Map();     // tile index -> { mesh, sig, key, x, y }
const people = new Map();   // npc id -> person record (see makePerson)
const beasts = new Map();   // animal id -> { mesh, mover, kind, hp, face }
const piles = new Map();    // "x,y" -> { mesh, item }
let graves = [];            // { mesh, tick }
const anims = [];           // one-off animations: { t0, ms, step(k), done() }
let shown = { key: "", rows: null, season: "" };
let root = null;            // the group everything here hangs from

export function initThings(scene) {
  if (root) return;
  root = new THREE.Group();
  scene.add(root);
}

const lerp = (a, b, t) => a + (b - a) * t;
const now = () => performance.now();
const animate = (ms, step, done) => anims.push({ t0: now(), ms, step, done });

// --- movers: where something is between two tiles -------------------------------------------
const mover = (x, z) => ({ fx: x, fz: z, tx: x, tz: z, t0: 0, ms: 1 });
function glide(mv, x, z, ms) {
  const [cx, cz] = moverAt(mv);
  Object.assign(mv, { fx: cx, fz: cz, tx: x, tz: z, t0: now(), ms: Math.max(1, ms) });
}
export function moverAt(mv, t = now()) {
  const k = Math.min(1, (t - mv.t0) / mv.ms);
  return [lerp(mv.fx, mv.tx, k), lerp(mv.fz, mv.tz, k), k];
}

// --- the objects on the tiles --------------------------------------------------------------
const NATURAL = new Set(["tree", "sapling", "bush", "fruit", "cactus", "mushroom", "rock"]);
const LIGHTS = { fire: 1.6, blaze: 2.2, hut: 0.6, house: 0.7, shrine: 0.5 };
const siteStage = (p) => Math.min(3, Math.floor((p || 0) / 25));

function placeObject(f, x, y, i, ch, sites, season, live) {
  const have = objs.get(i);
  const o = ch ? objectOf(ch) : null;
  const ground = (terrainOf(f.terrain[y][x]) || {}).key;
  const extra = o && o.key === "site" ? siteStage(sites.get(i)) : 0;
  const sig = o ? `${o.key}:${o.spent ? 1 : 0}:${extra}` : "";
  if (have && have.sig === sig) return;
  if (have) {
    if (live && have.key === "tree" && o && o.key === "tree" && o.spent) fell(have.mesh);
    else if (live) fadeOut(have.mesh); else root.remove(have.mesh);
    objs.delete(i);
  }
  if (!o) return;
  const h = tileHash(x, y);
  const mesh = new THREE.Mesh(objectGeometry(o.key, o.spent, ground, h, season, extra), MATS);
  const sway = NATURAL.has(o.key) ? 0.12 : 0;
  mesh.position.set(x + 0.5 + (((h >>> 6) % 5) - 2) * sway / 2, groundTop(x, y), y + 0.5 + (((h >>> 11) % 5) - 2) * sway / 2);
  mesh.rotation.y = NATURAL.has(o.key) ? ((h >>> 13) % 360) * Math.PI / 180 : 0;
  mesh.userData = { tile: [x, y], key: o.key, spent: o.spent, label: o.def && (o.def.place || o.def.label) };
  root.add(mesh);
  objs.set(i, { mesh, sig, key: o.key, x, y });
  if (live && !(have && have.key === o.key)) {  // newly built or grown: it rises into place
    mesh.scale.set(0.6, 0.3, 0.6);
    animate(380, (k) => { const s = 1 + (k - 1) * (1 - k) * -0.6; mesh.scale.set(lerp(0.6, 1, k) * s, lerp(0.3, 1, k), lerp(0.6, 1, k) * s); });
  }
}

function fell(mesh) {
  const ghost = mesh;  // the old tree tips over and goes
  const dir = Math.random() < 0.5 ? 1 : -1;
  animate(620, (k) => { ghost.rotation.z = dir * k * k * 1.45; },
    () => fadeOut(ghost));
}
function fadeOut(mesh) {
  animate(300, (k) => mesh.scale.setScalar(Math.max(0.01, 1 - k)), () => root.remove(mesh));
}

export function renderObjects3d(f, season, opts = {}) {
  const key = `${f.world}:${f.trev}:${f.width}x${f.height}`;
  const fresh = key !== shown.key || season !== shown.season;
  if (fresh) {
    for (const r of objs.values()) root.remove(r.mesh);
    objs.clear();
    shown = { key, rows: null, season };
  }
  const sites = new Map((f.sites || []).map(([x, y, , p]) => [y * f.width + x, p]));
  const live = !fresh && !opts.snap && !opts.reverse;
  for (let y = 0; y < f.height; y++) {
    const row = f.tiles[y];
    const siteRow = [...sites.keys()].some((i) => Math.floor(i / f.width) === y);
    if (shown.rows && shown.rows[y] === row && !siteRow) continue;
    for (let x = 0; x < f.width; x++) {
      const ch = row[x];
      placeObject(f, x, y, y * f.width + x, objectOf(ch) ? ch : "", sites, season, live);
    }
  }
  shown.rows = f.tiles;
}

// Fires, burning trees and homes with a light: for the few point lights (see atmos.js).
export function lightSpots() {
  const out = [];
  for (const r of objs.values()) if (LIGHTS[r.key]) out.push({ x: r.x + 0.5, z: r.y + 0.5, power: LIGHTS[r.key], fire: r.key === "fire" || r.key === "blaze" });
  return out;
}

// --- villagers ---------------------------------------------------------------------------
const HELD = { chop: "axe", mine: "pick", fish: "rod", construct: "hammer", build: "hammer", craft: "hammer" };
const LOADS = [["wood", 4], ["stone", 3], ["fish", 2], ["food", 4]];
const loadOf = (inv) => { for (const [item, least] of LOADS) if (inv && (inv[item] || 0) >= least) return item; return ""; };
const stageOf = (n) => n.stage || (n.age < 16 ? "child" : "adult");
const spot = (n) => [n.x + 0.5 + ((n.id % 3) - 1) * 0.12, n.y + 0.5 + ((n.id % 5) - 2) * 0.05];

function tag(mesh, id) { mesh.userData.npcId = id; return mesh; }
function makePerson(n, winter) {
  const g = personGeometry(n.look, stageOf(n), winter);
  const group = new THREE.Group();
  const hips = 0.33;
  const body = tag(new THREE.Mesh(g.body, MATS), n.id); body.position.y = hips;
  const head = tag(new THREE.Mesh(g.head, MATS), n.id); head.position.y = hips + 0.33;
  const limb = (geo, x, y) => { const p = new THREE.Group(); p.position.set(x, y, 0); p.add(tag(new THREE.Mesh(geo, MATS), n.id)); group.add(p); return p; };
  const armL = limb(g.arm, -0.175, hips + 0.31), armR = limb(g.arm, 0.175, hips + 0.31);
  const legL = limb(g.leg, -0.065, hips), legR = limb(g.leg, 0.065, hips);
  const tool = new THREE.Mesh(toolGeometry("axe"), SOLID);
  tool.position.set(0, -0.3, 0.03); tool.rotation.x = Math.PI / 2; tool.visible = false;
  armR.add(tool);
  const pack = new THREE.Mesh(loadGeometry("wood"), SOLID);
  pack.position.set(0, hips + 0.08, -0.15); pack.visible = false;
  group.add(body, head, pack);
  group.scale.setScalar(g.k * 0.92);
  root.add(group);
  return { group, head, armL, armR, legL, legR, tool, pack, look: "", held: "", carry: "", face: 0, phase: n.id * 1.7,
           mv: mover(...spot(n)), dying: 0, sleeping: false, jab: 0, workStart: 0, name: n.name, stage: stageOf(n) };
}
function dropPerson(id) {
  const p = people.get(id);
  if (!p) return;
  root.remove(p.group);
  people.delete(id);
}

export function renderPeople(f, opts = {}) {
  const winter = !!(f.clock && f.clock.season === "winter");
  const seen = new Set(), ms = opts.tickMs || state.tickMs;
  for (const n of f.npcs) {
    if (opts.reverse && !n.alive) continue;
    seen.add(n.id);
    const lookSig = `${JSON.stringify(n.look)}:${stageOf(n)}:${winter}`;
    let p = people.get(n.id);
    if (p && p.look !== lookSig && !p.dying) { dropPerson(n.id); p = null; }  // grew up or old, or winter came
    const [x, z] = spot(n);
    if (!p) { p = makePerson(n, winter); p.look = lookSig; people.set(n.id, p); }
    else if (!p.dying && (p.mv.tx !== x || p.mv.tz !== z)) {
      const ticks = opts.reverse ? 1 : n.dur || 2;
      glide(p.mv, x, z, Math.max(opts.tickMs ? 30 : 120, ticks * ms * 0.95));
      p.face = Math.atan2(x - p.mv.fx, z - p.mv.fz);
    }
    p.name = n.name;
    p.stage = stageOf(n);
    p.wading = (terrainAt(f, n.x, n.y) || {}).key === "shallow";
    p.sleeping = n.alive && n.status === "💤";
    const held = n.alive ? HELD[n.doing] || "" : "";
    if (held !== p.held) {
      p.held = held;
      p.tool.visible = !!held;
      if (held) { p.tool.geometry = toolGeometry(held); p.workStart = now(); }
    }
    if (!held && n.alive && n.inv && n.inv.spear) { p.tool.visible = true; p.tool.geometry = toolGeometry("spear"); }
    else if (!held) p.tool.visible = false;
    const carry = n.alive ? loadOf(n.inv) : "";
    if (carry !== p.carry) { p.carry = carry; p.pack.visible = !!carry; if (carry) p.pack.geometry = loadGeometry(carry); }
    if (!n.alive && !p.dying) {
      p.dying = now();
      addGrave(n, f.tick);
    }
  }
  for (const fx of opts.snap || opts.reverse ? [] : f.fx || []) {
    const p = people.get(fx.npc);
    if (p && (fx.type === "attack" || fx.type === "hunt")) p.jab = now();
    if (fx.target != null && fx.type === "attack") {  // whoever was hit faces whoever hit them
      const a = people.get(fx.npc), b = people.get(fx.target);
      if (a && b) {
        a.face = Math.atan2(b.mv.tx - a.mv.tx, b.mv.tz - a.mv.tz);
        b.face = Math.atan2(a.mv.tx - b.mv.tx, a.mv.tz - b.mv.tz);
      }
    }
  }
  for (const id of [...people.keys()]) if (!seen.has(id) && (opts.reverse || !people.get(id).dying)) dropPerson(id);
  graves = graves.filter((g) => {
    if (f.tick < g.tick + GRAVE_TICKS && !(opts.reverse && g.tick > f.tick)) return true;
    root.remove(g.mesh);
    return false;
  });
}

function addGrave(n, tick) {
  const mesh = new THREE.Mesh(graveGeometry(), SOLID);
  mesh.position.set(n.x + 0.5, 0, n.y + 0.5);
  mesh.visible = false;
  setTimeout(() => { mesh.visible = true; }, 1600);
  root.add(mesh);
  graves.push({ mesh, tick });
  while (graves.length > MAX_GRAVES) root.remove(graves.shift().mesh);
}

// Where a villager is now (for the camera, and for name tags and bubbles).
export function personAt(id) {
  const p = people.get(id);
  if (!p) return null;
  const [x, z] = moverAt(p.mv);
  return { x, z, y: groundTop(Math.floor(x), Math.floor(z)), p };
}
export const personHeight = (p) => (p ? 0.9 * p.group.scale.y : 0.83);
export const peopleMeshes = () => [...people.values()].filter((p) => p.group.visible && !p.dying).map((p) => p.group);

// --- animals, things on the ground ---------------------------------------------------------
export function renderBeasts(f, opts = {}) {
  const seen = new Set(), ms = opts.tickMs || state.tickMs;
  for (const [id, kind, x, y, hp] of f.animals || []) {
    seen.add(id);
    const px = x + 0.5 + ((id % 5) - 2) * 0.06, pz = y + 0.5;
    let b = beasts.get(id);
    if (!b) {
      const mesh = new THREE.Mesh(animalGeometry(kind), MATS);
      root.add(mesh);
      b = { mesh, kind, hp, face: (id % 4) * Math.PI / 2, mv: mover(px, pz) };
      beasts.set(id, b);
    } else if (b.mv.tx !== px || b.mv.tz !== pz) {
      b.face = Math.atan2(px - b.mv.tx, pz - b.mv.tz);
      glide(b.mv, px, pz, Math.max(90, ms * 0.95));
    }
    b.hp = hp;
  }
  const killed = new Set((f.fx || []).filter((e) => e.type === "hunt" && e.killed).map((e) => e.animal));
  for (const [id, b] of beasts) {
    if (seen.has(id)) continue;
    beasts.delete(id);
    if (opts.snap || opts.reverse) { root.remove(b.mesh); continue; }
    if (killed.has(id)) animate(500, (k) => { b.mesh.rotation.z = k * Math.PI / 2; }, () => setTimeout(() => root.remove(b.mesh), 800));
    else fadeOut(b.mesh);
  }
}

export function renderPiles(f) {
  const seen = new Set();
  for (const [x, y, items] of f.drops || []) {
    const key = `${x},${y}`;
    seen.add(key);
    const top = Object.entries(items).sort((a, b) => b[1] - a[1])[0];
    const item = top ? top[0] : "";
    let p = piles.get(key);
    if (p && p.item !== item) { root.remove(p.mesh); p = null; }
    if (!p) {
      const mesh = new THREE.Mesh(dropGeometry(item), SOLID);
      mesh.position.set(x + 0.5, 0.1, y + 0.5);
      mesh.userData = { tile: [x, y], key: "drop", label: item };
      root.add(mesh);
      piles.set(key, { mesh, item, phase: tileHash(x, y) % 100 });
    }
  }
  for (const [key, p] of piles) if (!seen.has(key)) { root.remove(p.mesh); piles.delete(key); }
}

export const objectMeshes = () => [...[...objs.values()].map((r) => r.mesh), ...[...piles.values()].map((p) => p.mesh)];

// --- every animation frame ------------------------------------------------------------------
const STROKE = { axe: 950, pick: 1050, hammer: 620 };
export function poseThings(t, me, eye = null) {
  for (let i = anims.length - 1; i >= 0; i--) {
    const a = anims[i], k = Math.min(1, (t - a.t0) / a.ms);
    a.step(k);
    if (k >= 1) { anims.splice(i, 1); if (a.done) a.done(); }
  }
  for (const [id, p] of people) {
    const [x, z, k] = moverAt(p.mv, t);
    // (not yourself, nor anyone standing right where your eyes are: they'd fill the view)
    p.group.visible = id !== me && !(eye && (x - eye[0]) ** 2 + (z - eye[2]) ** 2 < 0.2);
    const walking = k < 1;
    p.group.position.set(x, groundTop(Math.floor(x), Math.floor(z)) - (p.wading ? 0.16 : 0), z);
    p.group.rotation.y = p.face;
    const swing = walking ? Math.sin(t / 95 + p.phase) * 0.7 : 0;
    p.legL.rotation.x = swing; p.legR.rotation.x = -swing;
    p.armL.rotation.x = -swing * 0.8;
    let arm = swing * 0.8;
    if (p.held && STROKE[p.held]) {  // a tool raised slowly and brought down fast
      const s = ((t - p.workStart) % STROKE[p.held]) / STROKE[p.held];
      arm = s < 0.72 ? -Math.sin((s / 0.72) * Math.PI / 2) * 2.4 : -2.4 + Math.sin(((s - 0.72) / 0.28) * Math.PI / 2) * 2.6;
    } else if (p.held === "rod") arm = -1.0 + Math.sin(t / 700 + p.phase) * 0.05;
    const jab = t - p.jab < 300 ? Math.sin(((t - p.jab) / 300) * Math.PI) : 0;
    p.armR.rotation.x = arm - jab * 1.6;
    p.head.rotation.y = walking ? 0 : Math.sin(t / 2300 + p.phase) * 0.35;  // looking about
    if (p.dying) {
      const d = Math.min(1, (t - p.dying) / 700);
      p.group.rotation.z = d * Math.PI / 2;
      p.group.position.y += d * 0.12;
      if (t - p.dying > 1600) p.group.visible = false;
    } else {
      p.group.rotation.z = p.sleeping ? Math.PI / 2 : 0;
      if (p.sleeping) p.group.position.y += 0.12;
    }
  }
  for (const b of beasts.values()) {
    const [x, z, k] = moverAt(b.mv, t);
    const hop = k < 1 ? Math.sin(k * Math.PI) * (b.kind === "rabbit" ? 0.18 : 0.06) : 0;
    b.mesh.position.set(x, hop, z);
    b.mesh.rotation.y = b.face;
  }
  for (const p of piles.values()) {
    p.mesh.position.y = 0.12 + Math.sin(t / 500 + p.phase) * 0.04;
    p.mesh.rotation.y = t / 1400 + p.phase;
  }
}

export function clearThings() {
  if (!root) return;
  for (const r of objs.values()) root.remove(r.mesh);
  for (const id of [...people.keys()]) dropPerson(id);
  for (const b of beasts.values()) root.remove(b.mesh);
  for (const p of piles.values()) root.remove(p.mesh);
  for (const g of graves) root.remove(g.mesh);
  objs.clear(); beasts.clear(); piles.clear(); graves = []; anims.length = 0;
  shown = { key: "", rows: null, season: "" };
}
