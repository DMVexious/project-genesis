/* First person: see the world through the eyes of the villager you're playing.
 *
 * A three.js view laid over the stage while you're in a body (V, or the 🎥
 * button on the play bar). The server's frames are drawn here as blocks (see
 * blocks.js, things.js); the top-down Phaser view sleeps meanwhile and wakes up
 * as it was when you switch back.
 *   - click the view to take the mouse; move it to look around (Esc frees it)
 *   - WASD walk, relative to where you look (the body still steps tile by tile)
 *   - E: use what you're looking at (chop, gather, mine, build), or talk to someone
 *   - 1-9: the buttons on the play bar (eat, sleep, build...)
 */
import * as THREE from "../../vendor/three.module.min.js";
import { $, params } from "../config.js";
import { menuOpen } from "../menu.js";
import { send } from "../net.js";
import { openInteract, playing } from "../play.js";
import { state } from "../state.js";
import { game, layBubbles } from "../world.js";
import { atmosFrame, initAtmos, resetAtmos, updateAtmos } from "./atmos.js";
import { buildBlocks, clearBlocks, groundMeshes, waterTime } from "./blocks.js";
import { clearThings, initThings, objectMeshes, peopleMeshes, personAt, personHeight, poseThings, renderBeasts,
         renderObjects3d, renderPeople, renderPiles } from "./things.js";

const fp = {
  on: false, wanted: false, renderer: null, scene: null, cam: null, raf: 0, last: 0,
  yaw: 0, pitch: -0.08, held: new Set(), locked: false, unlockedAt: 0, world: null,
  hover: null, hoverAt: 0, boxOpen: false, eye: null,
};
try { fp.wanted = localStorage.getItem("genesis.fp") === "1"; } catch (e) { /* storage off */ }
export const fpOn = () => fp.on;
// (true just after Esc freed the mouse: that Esc must not also leave the body)
export const fpJustUnlocked = () => performance.now() - fp.unlockedAt < 350;

// --- setting up ----------------------------------------------------------------------------
function setup() {
  if (fp.renderer) return;
  const holder = $("fp");
  fp.renderer = new THREE.WebGLRenderer({ antialias: false, powerPreference: "high-performance" });
  fp.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  holder.appendChild(fp.renderer.domElement);
  fp.scene = new THREE.Scene();
  fp.cam = new THREE.PerspectiveCamera(72, 1, 0.05, 90);
  fp.cam.rotation.order = "YXZ";
  initThings(fp.scene);
  initAtmos(fp.scene);
  const canvas = fp.renderer.domElement;
  canvas.addEventListener("click", () => {
    if (fp.on && !fp.locked && canvas.requestPointerLock) canvas.requestPointerLock();
  });
  document.addEventListener("pointerlockchange", () => {
    const was = fp.locked;
    fp.locked = document.pointerLockElement === canvas;
    if (was && !fp.locked) fp.unlockedAt = performance.now();
    document.body.classList.toggle("fp-locked", fp.locked);
  });
  document.addEventListener("mousemove", (e) => {
    if (!fp.locked) return;
    fp.yaw -= e.movementX * 0.0024;
    fp.pitch = Math.max(-1.45, Math.min(1.45, fp.pitch - e.movementY * 0.0024));
  });
  const look = (params.get("fplook") || "").split(",").map(Number);  // ?fplook=yaw,pitch (tests: no mouse lock headless)
  if (look.length === 2 && look.every(Number.isFinite)) [fp.yaw, fp.pitch] = look;
}

function enter() {
  if (fp.on || !playing() || menuOpen()) return;
  setup();
  fp.on = true;
  document.body.classList.add("fp");
  if (game.loop) game.loop.sleep();
  resetAtmos();
  if (state.frame) draw(state.frame, { snap: true });
  const me = personAt(state.possessed);
  if (me && !params.has("fplook")) fp.yaw = me.p.face + Math.PI;  // look the way the body faces
  fp.last = performance.now();
  fp.raf = requestAnimationFrame(loop);
  showButton();
}

function exit() {
  if (!fp.on) return;
  fp.on = false;
  cancelAnimationFrame(fp.raf);
  if (document.pointerLockElement) document.exitPointerLock();
  document.body.classList.remove("fp", "fp-locked");
  fp.held.clear();
  if (game.loop) game.loop.wake();
  showButton();
}

export function toggleFp(want = !fp.on) {
  fp.wanted = want;
  try { localStorage.setItem("genesis.fp", want ? "1" : ""); } catch (e) { /* storage off */ }
  if (want) enter(); else exit();
}

function showButton() {
  const b = $("pb-fp");
  if (b) { b.textContent = fp.on ? "🗺️ Top-down" : "🎥 First person"; b.title = fp.on ? "Back to the map view (V)" : "See through their eyes (V)"; }
}

// --- each frame from the server --------------------------------------------------------------
// Called by render() in world.js with every frame shown (live or replayed).
export function syncFp(f, opts = {}) {
  if (fp.on && (!playing() || menuOpen())) exit();           // left the body, died, or back at the start screen
  else if (!fp.on && fp.wanted && playing() && !menuOpen()) enter();
  if (!fp.on) return;
  draw(f, opts);
  if (fp.held.size && !opts.snap && !opts.reverse) stepHeld();  // keep walking while a key is held
}

function draw(f, opts) {
  if (!f.terrain || !state.defs) return;
  if (fp.world !== f.world) { clearThings(); clearBlocks(fp.scene); fp.world = f.world; }
  const season = (f.clock && f.clock.season) || "summer";
  buildBlocks(fp.scene, f, season);
  renderObjects3d(f, season, opts);
  renderPeople(f, opts);
  renderBeasts(f, opts);
  renderPiles(f);
  atmosFrame(f, opts);
}

// --- walking -----------------------------------------------------------------------------------
const MOVE = { w: [0, 1], arrowup: [0, 1], s: [0, -1], arrowdown: [0, -1], a: [-1, 0], arrowleft: [-1, 0], d: [1, 0], arrowright: [1, 0] };
function stepHeld() {
  let fwd = 0, side = 0;
  for (const k of fp.held) { fwd += MOVE[k][1]; side += MOVE[k][0]; }
  if (!fwd && !side) return;
  // where that is on the map: forward is where the camera looks (-z turned by yaw)
  const sx = Math.sin(fp.yaw), cz = Math.cos(fp.yaw);
  const mx = -sx * fwd + cz * side, mz = -cz * fwd - sx * side;
  const [dx, dy] = Math.abs(mx) > Math.abs(mz) ? [Math.sign(mx), 0] : [0, Math.sign(mz)];
  send({ cmd: "player", order: { kind: "step", dx, dy } });
}

// --- what you're looking at ----------------------------------------------------------------------
const ray = new THREE.Raycaster(), center = new THREE.Vector2(0, 0);
ray.far = 6;
const USE = { tree: "chop the tree", bush: "pick berries", fruit: "pick apples", cactus: "pick the fruit",
              mushroom: "pick mushrooms", rock: "mine the rock", site: "help build", drop: "pick it up" };
function lookAt() {
  ray.setFromCamera(center, fp.cam);
  const hits = ray.intersectObjects([...peopleMeshes(), ...objectMeshes(), ...groundMeshes()], true);
  for (const h of hits) {
    const d = h.object.userData;
    if (d.npcId != null) return { npc: d.npcId, d: h.distance };
    if (d.tile) {
      const verb = d.spent && d.key !== "tree" && d.key !== "site" ? null : USE[d.key];
      const label = verb || (d.key === "tree" ? "go to the stump" : `go to the ${d.label || d.key}`);
      return { tile: d.tile, label, d: h.distance };
    }
    if (d.tiles && h.instanceId != null) {
      const t = d.tiles[h.instanceId];
      const water = h.object === groundMeshes()[1];
      return { tile: t, label: water ? "go to the water" : "walk here", d: h.distance, ground: true };
    }
  }
  return null;
}

function use() {
  const h = fp.hover;
  if (!h) return;
  if (h.npc != null) {
    if (h.npc === state.possessed) return;
    if (document.pointerLockElement) document.exitPointerLock();
    const r = $("stage").getBoundingClientRect();
    openInteract(h.npc, r.width / 2, r.height / 2 + 40);
  } else if (h.tile) {
    send({ cmd: "player", order: { kind: "walk", x: h.tile[0], y: h.tile[1] } });
  }
}

// --- every animation frame ----------------------------------------------------------------------
const v3 = new THREE.Vector3();
function loop(t) {
  if (!fp.on) return;
  fp.raf = requestAnimationFrame(loop);
  const dt = Math.min(100, t - fp.last);
  fp.last = t;
  const holder = $("fp"), w = holder.clientWidth, h = holder.clientHeight;
  const size = fp.renderer.getSize(new THREE.Vector2());
  if (size.x !== w || size.y !== h) {
    fp.renderer.setSize(w, h);
    fp.cam.aspect = w / Math.max(1, h);
    fp.cam.updateProjectionMatrix();
  }
  poseThings(t, state.possessed, fp.eye);
  const me = personAt(state.possessed);
  if (me) {
    const k = Math.min(1, (t - me.p.mv.t0) / me.p.mv.ms);
    const bob = k < 1 ? Math.abs(Math.sin(t / 95)) * 0.03 : 0;
    fp.eye = [me.x, me.y + personHeight(me.p) * 0.9 + bob - (me.p.wading ? 0.16 : 0), me.z];
  }
  if (fp.eye) fp.cam.position.set(...fp.eye);
  fp.cam.rotation.set(fp.pitch, fp.yaw, 0);
  updateAtmos(fp.scene, fp.cam, t, dt);
  waterTime(t);
  if (t - fp.hoverAt > 90) { fp.hoverAt = t; fp.hover = lookAt(); showHint(); }
  freeMouseForBoxes();
  fp.renderer.render(fp.scene, fp.cam);
  placeTags(w, h);
}

function showHint() {
  const h = fp.hover, el = $("fp-hint");
  let text = "";
  if (h && h.npc != null) text = `E · talk to ${state.names.get(h.npc) || "them"}`;
  else if (h && h.tile) text = `E · ${h.label}`;
  if (el.textContent !== text) el.textContent = text;
  $("fp-cross").classList.toggle("on", !!(h && (h.npc != null || !h.ground)));
}

// someone speaks to you, or a box to type in opens: give the mouse back
function freeMouseForBoxes() {
  const open = !$("interact").hidden || !$("pb-chat").hidden || !$("pb-heard").hidden || !$("help").hidden;
  if (open && !fp.boxOpen && document.pointerLockElement) document.exitPointerLock();
  fp.boxOpen = open;
}

// Name tags (status icons, work bars) and speech bubbles over heads in view.
const speech = [];
function placeTags(w, h) {
  for (const [id, s] of state.sprites) {
    const at = id === state.possessed ? null : personAt(id);
    let show = false, x = 0, y = 0;
    if (at) {
      v3.set(at.x, at.y + personHeight(at.p) + 0.12, at.z);
      const dist = v3.distanceTo(fp.cam.position);
      v3.project(fp.cam);
      x = (v3.x + 1) / 2 * w; y = (1 - v3.y) / 2 * h;
      show = v3.z < 1 && dist < 22 && x > -60 && x < w + 60 && y > -60 && y < h + 80;
    }
    s.tag.style.display = show ? "" : "none";
    if (show) { s.tag.style.left = `${x}px`; s.tag.style.top = `${y}px`; }
    const b = state.bubbles.get(id);
    if (b) {
      b.el.style.display = show ? "" : "none";
      if (show) speech.push({ b, id, x, y: y + 18 });
    }
  }
  layBubbles(speech, 2);
  speech.length = 0;
}

// --- keys ----------------------------------------------------------------------------------------
export function initFp() {
  showButton();
  $("pb-fp").onclick = () => toggleFp();
  const typing = (e) => ["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName);
  document.addEventListener("keydown", (e) => {
    if (typing(e) || menuOpen() || e.ctrlKey || e.metaKey || e.altKey) return;
    const k = e.key.toLowerCase();
    if (k === "v" && playing()) { e.preventDefault(); toggleFp(); return; }
    if (!fp.on) return;
    if (MOVE[k]) {
      e.preventDefault();
      if (!fp.held.has(k)) { fp.held.add(k); stepHeld(); }
    } else if (k === "e") { e.preventDefault(); use(); }
    else if (/^[1-9]$/.test(k)) {
      const b = $("pb-actions").querySelectorAll("button[data-i]")[Number(k) - 1];
      if (b) { e.preventDefault(); b.click(); }
    }
  });
  document.addEventListener("keyup", (e) => fp.held.delete(e.key.toLowerCase()));
  window.addEventListener("blur", () => fp.held.clear());
  window.genesisFp = fp;  // (for poking at in the console, and the headless tests)
}
