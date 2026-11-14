/* The Phaser scene: camera, NPC sprites, graves, speech bubbles. (The ground
 * is painted by ground.js and the things standing on it by objects.js.) */
import { AGGRO_COLOR, BUBBLE_MIN_MS, BUBBLE_TICKS, GRAVE_TICKS, MAX_GRAVES, NPC_SCALE, REPLY_DELAY_MS, TILE,
         overlay, params } from "./config.js";
import { clearCull, cull } from "./cull.js";
import { clearDrops, renderDrops } from "./drops.js";
import { anim, newFx, playFx, poseSprite } from "./fx.js";
import { animateGround, buildGround, clearGround } from "./ground.js";
import { terrainAt } from "./land.js";
import { menuOpen } from "./menu.js";
import { drawMinimap } from "./minimap.js";
import { clearObjects, flickerFires, renderObjects } from "./objects.js";
import { select } from "./panel.js";
import { groundClick, openInteract, playing } from "./play.js";
import { playFrame } from "./sound.js";
import { state } from "./state.js";
import { NPC_H, NPC_W, makeTextures, npcTexture } from "./textures.js";

// --- Phaser scene -----------------------------------------------------------
export class WorldScene extends Phaser.Scene {
  constructor() { super("world"); }
  create() {
    makeTextures(this);
    this.ring = this.add.image(0, 0, "ring").setDepth(9000).setVisible(false);
    this.homeMark = this.add.image(0, 0, "homemark").setOrigin(0).setTint(0x9fd3ff).setDepth(8999).setVisible(false);
    this.flicker = this.mapped = 0;
    const cam = this.cameras.main;
    cam.setZoom(Phaser.Math.Clamp(Number(params.get("zoom")) || START_ZOOM, MIN_ZOOM, MAX_ZOOM));  // ?zoom=2.5

    // Drag to pan (a short press without movement counts as a click).
    let drag = null;
    this.input.on("pointerdown", (p) => { drag = { x: p.x, y: p.y, moved: false }; });
    this.input.on("pointermove", (p) => {
      if (!drag || !p.isDown) return;
      const dx = p.x - p.prevPosition.x, dy = p.y - p.prevPosition.y;
      if (Math.abs(p.x - drag.x) + Math.abs(p.y - drag.y) > 5) drag.moved = true;
      if (drag.moved) {
        cam.stopFollow();
        cam.scrollX -= dx / cam.zoom;
        cam.scrollY -= dy / cam.zoom;
      }
    });
    // A click on a villager: select them (or, while playing someone, open what you
    // can do with them). A click on the ground: while playing, walk there.
    this.input.on("gameobjectup", (p, obj) => {
      if (!drag || drag.moved || !obj.npcId) return;
      if (playing() && obj.npcId !== state.possessed) {
        const c = this.game.canvas.getBoundingClientRect(), s = document.getElementById("stage").getBoundingClientRect();
        openInteract(obj.npcId, p.x * (c.width / this.scale.width) + c.left - s.left,
                     p.y * (c.height / this.scale.height) + c.top - s.top);
      } else select(obj.npcId);
    });
    this.input.on("pointerup", (p, over) => {
      if (drag && !drag.moved && !(over && over.length) && p.button === 0) {
        const at = cam.getWorldPoint(p.x, p.y);
        groundClick(this, at.x, at.y);
      }
      setTimeout(() => { drag = null; }, 0);
    });

    // Scroll to zoom toward the cursor.
    this.input.on("wheel", (p, _objs, _dx, dy) => {
      const before = cam.getWorldPoint(p.x, p.y);
      cam.setZoom(Phaser.Math.Clamp(cam.zoom * (dy > 0 ? 0.88 : 1.14), MIN_ZOOM, MAX_ZOOM));
      cam.preRender();
      const after = cam.getWorldPoint(p.x, p.y);
      cam.scrollX += before.x - after.x;
      cam.scrollY += before.y - after.y;
    });

    // enableCapture=false: don't swallow keystrokes meant for the seed input
    this.keys = this.input.keyboard.addKeys("W,A,S,D,UP,DOWN,LEFT,RIGHT", false);
    if (state.frame) render(state.frame);
  }
  update(t, dt) {
    if (t - this.flicker > 110) {  // campfires flicker
      this.flicker = t;
      flickerFires(t);
    }
    animateGround(t);
    cull(this);
    if (t - this.mapped > 120) {  // (the minimap's view box follows the camera between ticks)
      this.mapped = t;
      drawMinimap();
    }
    const k = this.keys, cam = this.cameras.main;
    const typing = document.activeElement && ["INPUT", "SELECT"].includes(document.activeElement.tagName);
    if (typing || menuOpen() || playing()) return placeOverlays(this);  // (while playing, the keys walk)
    const v = (0.6 * dt) / cam.zoom;
    const dx = (k.D.isDown || k.RIGHT.isDown) - (k.A.isDown || k.LEFT.isDown);
    const dy = (k.S.isDown || k.DOWN.isDown) - (k.W.isDown || k.UP.isDown);
    if (dx || dy) { cam.stopFollow(); cam.scrollX += dx * v; cam.scrollY += dy * v; }
    placeOverlays(this);
  }
}

export const MIN_ZOOM = 0.25, MAX_ZOOM = 4, START_ZOOM = 1.5;
export const game = new Phaser.Game({
  type: Phaser.AUTO,
  parent: "game",
  backgroundColor: "#17130f",
  pixelArt: true,
  scale: { mode: Phaser.Scale.RESIZE, width: "100%", height: "100%" },
  scene: WorldScene,
});
export const scene = () => game.scene.getScene("world");

// --- rendering -----------------------------------------------------------------
// A different world from the one on show (or the first): start afresh, let the
// camera roam all of it, and look at where its founders are.
function syncWorld(sc, f) {
  const key = `${f.world}:${f.width}x${f.height}`;
  if (state.worldKey === key) return;
  state.worldKey = key;
  clearObjects();
  clearGround(sc);
  clearCull();
  const cam = sc.cameras.main;
  cam.stopFollow();
  cam.setBounds(0, 0, f.width * TILE, f.height * TILE);
  const center = (params.get("center") || "").split(",").map(Number);  // ?center=<tileX>,<tileY>
  const alive = f.npcs.filter((n) => n.alive);
  if (center.length === 2 && center.every(Number.isFinite)) {
    cam.centerOn(center[0] * TILE, center[1] * TILE);
  } else if (alive.length) {
    const cx = alive.reduce((a, n) => a + n.x, 0) / alive.length;
    const cy = alive.reduce((a, n) => a + n.y, 0) / alive.length;
    cam.centerOn((cx + 0.5) * TILE, (cy + 0.5) * TILE);
  } else if (f.start) {
    cam.centerOn(f.start[0] * TILE, f.start[1] * TILE);
  }
}

export function npcPos(n) {
  const jitter = ((n.id % 3) - 1) * 4;
  return { x: n.x * TILE + TILE / 2 + jitter, y: n.y * TILE + TILE - 2 };
}

export const stageOf = (n) => n.stage || (n.age < 16 ? "child" : "adult");
export const scaleFor = (stage) => (stage === "child" ? NPC_SCALE * 0.7 : NPC_SCALE);

export function addGrave(sc, n, tick, instant = false) {
  const img = sc.add.image(n.x * TILE + TILE / 2, n.y * TILE + TILE, "grave")
    .setOrigin(0.5, 1).setDepth(n.y * TILE + TILE - 3).setAlpha(instant ? 1 : 0);
  if (!instant) sc.tweens.add({ targets: img, alpha: 1, delay: 900, duration: 600 });
  state.graves.push({ img, tick });
  while (state.graves.length > MAX_GRAVES) state.graves.shift().img.destroy();
}

export function ageGraves(sc, tick) {
  state.graves = state.graves.filter((gr) => {
    if (tick < gr.tick + GRAVE_TICKS) return true;
    sc.tweens.add({ targets: gr.img, alpha: 0, duration: 1500, onComplete: () => gr.img.destroy() });
    return false;
  });
}

// The time bar over a villager who is busy with something that takes a while.
// It fills smoothly: each tick it is told where it should be by the next one.
const BAR_KINDS = new Set(["chop", "forage", "build", "craft", "eat", "sleep", "mate"]);
function updateWorkBar(s, n, tick, opts) {
  const el = s.workEl, fill = el.firstChild;
  const show = n.alive && BAR_KINDS.has(n.doing) && n.t1 > n.t0 && tick < n.t1;
  if (el.hidden === show) el.hidden = !show;
  if (!show) { s.workT0 = -1; return; }
  const span = n.t1 - n.t0;
  const live = !opts.snap && !opts.reverse && !state.paused;
  if (s.workT0 !== n.t0 || s.workKind !== n.doing) {  // a new job: start from wherever it stands now
    s.workT0 = n.t0; s.workKind = n.doing;
    el.dataset.kind = n.doing;
    fill.style.transition = "none";
    fill.style.width = `${Math.max(0, Math.min(100, (100 * (tick - n.t0)) / span))}%`;
    void fill.offsetWidth;  // apply it before the transition below starts
  }
  fill.style.transition = live ? `width ${opts.tickMs || state.tickMs}ms linear` : "none";
  fill.style.width = `${Math.max(0, Math.min(100, (100 * (tick + (live ? 1 : 0) - n.t0)) / span))}%`;
}

export function renderNpcs(sc, f, opts = {}) {
  const seen = new Set();
  for (const n of f.npcs) {
    if (opts.reverse && !n.alive) continue;  // played backwards, the dead simply vanish
    seen.add(n.id);
    state.names.set(n.id, n.name);
    let s = state.sprites.get(n.id);
    const p = npcPos(n);
    const stage = stageOf(n);
    const key = npcTexture(sc, n.look, stage);
    if (!s) {
      const sprite = sc.add.image(p.x, p.y, key).setOrigin(0.5, 1).setInteractive({ useHandCursor: true });
      sprite.setDepth(p.y);
      sprite.npcId = n.id;
      sprite.setScale(scaleFor(stage));
      const shadow = sc.add.image(p.x, p.y, "shadow").setTint(AGGRO_COLOR[n.traits.aggression] || 0xffffff).setAlpha(0.75);
      const spear = sc.add.image(p.x, p.y, "spear").setOrigin(0.5, 1).setAngle(16).setVisible(false);
      const tag = document.createElement("div");
      tag.className = "npc-tag";
      tag.innerHTML = '<div class="icons"><span class="status"></span><span class="action"></span></div>' +
                      '<div class="work" hidden><i></i></div>';
      overlay.appendChild(tag);
      s = { sprite, shadow, spear, tag, statusEl: tag.querySelector(".status"), actionEl: tag.querySelector(".action"),
            workEl: tag.querySelector(".work"), workT0: -1, dying: false, key, wading: false,
            pos: { x: p.x, y: p.y },  // where the NPC stands (tweened while walking)
            fx: newFx(),              // animation offsets layered on top (see anim section)
            base: scaleFor(stage), face: 1, phase: (n.id * 1.7) % (Math.PI * 2), nextZ: 0 };
      state.sprites.set(n.id, s);
    } else if (!s.dying && (s.tx !== p.x || s.ty !== p.y)) {
      // Walk at this NPC's own speed: the step lasts as long as its action does.
      if (s.tween) s.tween.stop();
      const ticks = opts.reverse ? 1 : n.dur || 2;
      const ms = Math.max(opts.tickMs ? 30 : 120, ticks * (opts.tickMs || state.tickMs) * 0.95);
      if (p.x !== s.tx) s.face = p.x < s.tx ? -1 : 1;
      s.tween = sc.tweens.add({ targets: s.pos, x: p.x, y: p.y, ease: "Linear", duration: ms });
      // hop and waddle so walkers don't glide
      const steps = Math.max(1, Math.round(ms / 220));
      sc.tweens.add({ targets: s.fx, walkY: { from: 0, to: -2.5 }, walkA: { from: -4, to: 4 }, ease: "Sine.inOut",
        duration: ms / steps / 2, yoyo: true, repeat: steps - 1,
        onComplete: () => { s.fx.walkY = 0; s.fx.walkA = 0; } });
    }
    if (s.key !== key && !s.dying) { // grew up, or grew old
      s.key = key;
      s.base = scaleFor(stage);
      s.sprite.setTexture(key);
      anim(sc, s, { sx: { from: 1.25, to: 1 }, sy: { from: 0.8, to: 1 } }, 420, { ease: "Back.out" });
    }
    s.sleeping = n.alive && n.status === "💤";
    s.tx = p.x; s.ty = p.y;
    const ground = terrainAt(f, n.x, n.y);
    const wading = !!ground && ground.key === "shallow";  // knee-deep in water: no feet, no shadow
    if (wading !== s.wading) {
      s.wading = wading;
      if (wading) s.sprite.setCrop(0, 0, NPC_W, NPC_H - 7); else s.sprite.setCrop();
      s.shadow.setVisible(!wading);
    }
    s.sprite.setDepth(p.y);
    s.spear.setVisible(!!(n.inv && n.inv.spear) && n.alive);
    s.statusEl.textContent = n.status;
    updateWorkBar(s, n, f.tick, opts);
    // Re-trigger the pop animation whenever an action emoji is shown.
    s.actionEl.textContent = "";
    if (n.emoji) {
      const el = s.actionEl.cloneNode();
      el.textContent = n.emoji;
      s.actionEl.replaceWith(el);
      s.actionEl = el;
    }
    if (!n.alive && !s.dying) {
      s.dying = true;
      s.sprite.setTint(0x777777).disableInteractive();
      s.spear.setVisible(false);
      addGrave(sc, n, f.tick);
      sc.tweens.add({ targets: [s.sprite, s.shadow], alpha: 0, delay: 600, duration: 900 });
      sc.tweens.add({ targets: s.fx, angle: 90 * s.face, delay: 600, duration: 700, ease: "Bounce.out",
        onComplete: () => { if (state.sprites.get(n.id) === s) removeNpc(n.id); } });
    }
  }
  for (const id of [...state.sprites.keys()]) {
    if (!seen.has(id) && (opts.reverse || !state.sprites.get(id).dying)) removeNpc(id);
  }
  ageGraves(sc, f.tick);
}

export function removeNpc(id) {
  const s = state.sprites.get(id);
  if (!s) return;
  s.sprite.destroy();
  s.shadow.destroy();
  s.spear.destroy();
  s.tag.remove();
  state.sprites.delete(id);
  const b = state.bubbles.get(id);
  if (b) { b.el.remove(); state.bubbles.delete(id); }
}

export function showBubble(d, tick) {
  const old = state.bubbles.get(d.npc);
  if (old) old.el.remove();
  const el = document.createElement("div");
  el.className = `bubble ${d.kind || ""} ${d.player ? "player" : ""}`;
  el.textContent = d.text;  // the server already caps lines at MAX_WORDS
  overlay.appendChild(el);
  state.bubbles.set(d.npc, { el, tick, born: performance.now() });
}

export function renderBubbles(f) {
  for (const d of f.dialogue) {
    // the answer pops up a beat after the line it answers, so it reads as an exchange
    if (d.reply) setTimeout(() => { if (state.sprites.has(d.npc)) showBubble(d, state.frame.tick); }, REPLY_DELAY_MS);
    else if (d.npc === -1) showBubble({ ...d, npc: d.target, kind: "sky" }, f.tick);  // the voice from the sky, over its listener
    else showBubble(d, f.tick);
  }
  const now = performance.now();
  for (const [id, b] of state.bubbles) {
    if (f.tick >= b.tick + BUBBLE_TICKS && now - b.born >= BUBBLE_MIN_MS && !b.el.classList.contains("fade")) {
      b.el.classList.add("fade");
      b.el.addEventListener("animationend", () => {
        b.el.remove();
        if (state.bubbles.get(id) === b) state.bubbles.delete(id);
      }, { once: true });
    }
  }
}

export function placeOverlays(sc) {
  const canvas = game.canvas;
  if (!canvas) return;
  const c = canvas.getBoundingClientRect();
  const o = overlay.getBoundingClientRect();
  const k = c.width / game.scale.width;
  const cam = sc.cameras.main;
  const view = cam.worldView;
  const toScreen = (x, y) => [c.left - o.left + (x - view.x) * cam.zoom * k,
                              c.top - o.top + (y - view.y) * cam.zoom * k];
  const now = sc.time.now;
  for (const [id, s] of state.sprites) {
    const sp = s.sprite;
    poseSprite(sc, s, now);
    s.shadow.setPosition(s.pos.x + s.fx.x, s.pos.y - 1).setScale(s.base * s.fx.sx).setDepth(sp.depth - 0.6);
    if (s.spear.visible) {
      const swing = s.fx.swing * 75;  // a jab/slash rotates the spear forward
      s.spear.setPosition(sp.x + 9 * s.base * s.face, sp.y - 1 - s.fx.swing * 3).setScale(s.base)
        .setFlipX(s.face < 0).setAngle((16 + swing) * s.face).setDepth(sp.depth + 0.1);
    }
    const [x, y] = toScreen(sp.x, sp.y - sp.displayHeight - 2);
    const visible = x > -40 && y > -40 && x < c.width + 40 && y < c.height + 80;
    s.tag.style.display = visible ? "" : "none";
    s.tag.style.left = `${x}px`;
    s.tag.style.top = `${y}px`;
    const b = state.bubbles.get(id);
    if (b) {
      b.el.style.display = visible ? "" : "none";
      b.el.style.left = `${x}px`;
      b.el.style.top = `${y}px`;
    }
  }
  const sel = state.sprites.get(state.selected);
  sc.ring.setVisible(!!sel);
  if (sel) sc.ring.setPosition(sel.sprite.x, sel.sprite.y).setDepth(sel.sprite.depth - 0.5);
  const selN = sel && state.frame && state.frame.npcs.find((n) => n.id === state.selected);
  const home = selN && selN.home;
  sc.homeMark.setVisible(!!home);
  if (home) sc.homeMark.setPosition(home[0] * TILE, home[1] * TILE);
}

// opts (replay only): snap = jumped here, draw without effects; reverse = playing
// backwards (no effects, speech or deaths); tickMs = how long each frame is shown.
export function render(f, opts = {}) {
  const sc = scene();
  if (!sc || !sc.sys.isActive() || !sc.ring || !f.terrain || !state.defs) return;
  syncWorld(sc, f);
  buildGround(sc, f);
  renderObjects(sc, f, opts);
  if (opts.reverse) {
    state.graves = state.graves.filter((gr) => gr.tick <= f.tick || (gr.img.destroy(), false));
    clearBubbles();
  }
  renderNpcs(sc, f, opts);
  renderDrops(sc, f, opts);
  if (!opts.reverse && !opts.snap) {
    playFx(sc, f.fx || []);
    playFrame(f);
    renderBubbles(f);
  }
  drawMinimap();
}

export function clearBubbles() {
  for (const b of state.bubbles.values()) b.el.remove();
  state.bubbles.clear();
}

// Sprites, speech and graves only (the panel feeds are left alone).
export function clearWorld() {
  for (const id of [...state.sprites.keys()]) removeNpc(id);
  clearBubbles();
  clearDrops();
  for (const gr of state.graves) gr.img.destroy();
  state.graves = [];
}
