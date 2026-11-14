/* Animations: tweened offsets on sprites, particles, floating text, and the
 * per-event choreography (swings, hits, chops...). */
import { TILE } from "./config.js";
import { animalSprite, hurt } from "./animals.js";
import { shakeObject } from "./objects.js";
import { bolt } from "./sky.js";
import { state } from "./state.js";

// --- animations -----------------------------------------------------------------
// Each sprite stands at s.pos (tweened while walking). s.fx holds offsets that
// short animations tween; every tween goes {from: rest} and yoyos back, so
// several can stack (walk + lunge + recoil) and always settle at rest.
export const newFx = () => ({ x: 0, y: 0, angle: 0, sx: 1, sy: 1, swing: 0, walkY: 0, walkA: 0 });
export const reduceMotion = !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);

export function anim(sc, s, props, duration, opts = {}) {
  return sc.tweens.add({ targets: s.fx, ...props, duration, yoyo: !!opts.yoyo, repeat: opts.repeat || 0,
    delay: opts.delay || 0, ease: opts.ease || "Quad.out", onComplete: opts.onComplete });
}

// Applied every frame: position + animation offsets + idle breathing.
export function poseSprite(sc, s, now) {
  const fx = s.fx, sp = s.sprite;
  const breath = s.dying || reduceMotion ? 0
    : s.sleeping ? 0.045 * Math.sin(now / 650 + s.phase)   // slow, deep breaths while asleep
    : 0.018 * Math.sin(now / 420 + s.phase);
  sp.setPosition(s.pos.x + fx.x, s.pos.y + fx.y + fx.walkY)
    .setScale(s.base * fx.sx * (1 - breath * 0.4), s.base * fx.sy * (1 + breath))
    .setAngle(fx.angle + fx.walkA)
    .setFlipX(s.face < 0);
  if (s.sleeping && !reduceMotion && now > s.nextZ) {
    s.nextZ = now + 1100;
    if (sc.cameras.main.worldView.contains(sp.x, sp.y))
      floatText(sc, sp.x + 6 * s.face, sp.y - sp.displayHeight, "z", "#9fd3ff",
        { size: 9, rise: 16, dx: 7 * s.face, dur: 1500 });
  }
}

export function flash(sc, s, delay) {
  const restore = () => { if (s.sprite.active) s.dying ? s.sprite.setTint(0x777777) : s.sprite.clearTint(); };
  sc.time.delayedCall(delay, () => {
    if (!s.sprite.active) return;
    s.sprite.setTintFill(0xffffff);
    sc.time.delayedCall(70, () => {
      if (!s.sprite.active) return;
      s.sprite.clearTint().setTint(0xff7a6a);
      sc.time.delayedCall(120, restore);
    });
  });
}

export function floatText(sc, x, y, text, color, { size = 11, delay = 0, rise = 18, dx = 0, dur = 900, pop = false } = {}) {
  const t = sc.add.text(x, y, text, { fontFamily: "monospace", fontStyle: "bold", fontSize: `${size}px`, color,
    stroke: "#140e09", strokeThickness: 3 }).setOrigin(0.5, 1).setDepth(20000).setResolution(3).setVisible(false);
  sc.tweens.add({ targets: t, x: x + dx, y: y - rise, delay, duration: dur, ease: "Cubic.out",
    onStart: () => t.setVisible(true) });
  if (pop) sc.tweens.add({ targets: t, scale: { from: 1.8, to: 1 }, delay, duration: 220, ease: "Back.out" });
  sc.tweens.add({ targets: t, alpha: 0, delay: delay + dur * 0.6, duration: dur * 0.4, onComplete: () => t.destroy() });
}

// Little square particles thrown up and pulled down by gravity.
export function burst(sc, x, y, colors, n, { spread = 12, up = 12, size = 2, dur = 500, gravity = 36, delay = 0 } = {}) {
  for (let i = 0; i < n; i++) {
    const r = sc.add.rectangle(x, y, size, size, colors[i % colors.length]).setDepth(y + 40).setVisible(false);
    const vx = (Math.random() - 0.5) * spread * 2, vy = -(0.4 + Math.random() * 0.6) * up;
    const p = { t: 0 };
    sc.tweens.add({ targets: p, t: 1, delay, duration: dur * (0.7 + Math.random() * 0.6),
      onStart: () => r.setVisible(true),
      onUpdate: () => r.setPosition(x + vx * p.t, y + vy * p.t + gravity * p.t * p.t).setAlpha(1 - p.t * p.t),
      onComplete: () => r.destroy() });
  }
}

// A white arc that sweeps across the target.
export function slash(sc, x, y, face, delay) {
  const g = sc.add.graphics({ x, y }).setDepth(20000).setVisible(false);
  g.lineStyle(2, 0xffffff, 1); g.beginPath(); g.arc(0, 0, 10, -2.4, -0.3); g.strokePath();
  g.lineStyle(1, 0xffd27a, 1); g.beginPath(); g.arc(0, 0, 7, -2.2, -0.5); g.strokePath();
  g.setScale(face, 1);
  sc.tweens.add({ targets: g, angle: { from: -30 * face, to: 50 * face }, alpha: { from: 1, to: 0 }, delay,
    duration: 230, ease: "Quad.out", onStart: () => g.setVisible(true), onComplete: () => g.destroy() });
}

// The tree, bush or whatever stands on a tile shudders.
export function shakeTile(sc, x, y) {
  if (state.frame) shakeObject(sc, x, y, state.frame.width);
}

export const tileCenter = (x, y) => ({ x: x * TILE + TILE / 2, y: y * TILE + TILE / 2 });
export const faceToward = (s, x) => { if (Math.abs(x - s.tx) > 2) s.face = x < s.tx ? -1 : 1; };

export function playFx(sc, events) {
  if (reduceMotion) return;
  const get = (id) => { const s = state.sprites.get(id); return s && s.sprite.active ? s : null; };
  // several announcements over one head (two level-ups at once) take turns instead of piling up
  const said = new Map();
  const queue = (id) => { const k = said.get(id) || 0; said.set(id, k + 1); return k * 500; };
  const view = sc.cameras.main.worldView;
  for (const e of events) {
    const s = get(e.npc);
    if (!s) continue;
    if (!view.contains(s.tx, s.ty) && e.type !== "attack") continue;  // off screen: skip the show
    const head = { x: s.tx, y: s.ty - 14 * s.base };
    switch (e.type) {
      case "attack": {
        const b = get(e.target);
        if (!b) break;
        const delay = e.retaliation ? 320 : 0;
        let dx = b.tx - s.tx, dy = b.ty - s.ty;
        const len = Math.hypot(dx, dy);
        if (len < 1) { dx = s.face; dy = 0; } else { dx /= len; dy /= len; }
        faceToward(s, b.tx); faceToward(b, s.tx);
        // attacker: lean back, then lunge in with a swing
        anim(sc, s, { x: { from: 0, to: dx * 9 }, y: { from: 0, to: dy * 6 }, swing: { from: 0, to: 1 },
                      angle: { from: 0, to: 12 * s.face } }, 110, { yoyo: true, delay, ease: "Quad.in" });
        const at = delay + 110;
        const hx = b.tx, hy = b.ty - 12 * b.base;
        const side = dx >= 0 ? 1 : -1;
        if (e.hit) {
          const big = e.damage >= 20;  // spear hits
          flash(sc, b, at);
          anim(sc, b, { x: { from: 0, to: dx * (big ? 8 : 5) }, y: { from: 0, to: dy * 3 },
                        angle: { from: 0, to: 14 * side }, sy: { from: 1, to: 0.9 } }, 90, { yoyo: true, delay: at });
          slash(sc, hx, hy, side, at);
          burst(sc, hx, hy, [0xd9534f, 0xff8a7a, 0xffffff], big ? 12 : 7, { delay: at, spread: 14, up: 12 });
          floatText(sc, hx + dx * 4, hy - 8, `-${e.damage}`, big ? "#ffcf4a" : "#ff6a5a",
                    { size: big ? 14 : 11, delay: at, dx: dx * 8, pop: big });
          if (big && view.contains(hx, hy)) sc.time.delayedCall(at, () => sc.cameras.main.shake(140, 0.004));
        } else {
          // dodge: the target hops aside
          anim(sc, b, { y: { from: 0, to: -7 }, x: { from: 0, to: dx * 4 } }, 120, { yoyo: true, delay: at - 40 });
          floatText(sc, hx, hy - 8, "miss", "#c8c8d0", { size: 10, delay: at });
        }
        break;
      }
      case "chop": {
        const c = tileCenter(e.x, e.y);
        faceToward(s, c.x);
        for (let i = 0; i < 2; i++) {
          const d = i * 280;
          anim(sc, s, { swing: { from: 0, to: 1 }, angle: { from: 0, to: 13 * s.face }, x: { from: 0, to: 3 * s.face } },
               100, { yoyo: true, delay: d, ease: "Quad.in" });
          sc.time.delayedCall(d + 100, () => shakeTile(sc, e.x, e.y));
          burst(sc, c.x, c.y + 3, [0xc89a62, 0x6d4825, 0x3d8a3a], 5, { delay: d + 100, spread: 12, up: 10 });
        }
        break;
      }
      case "forage": {
        const c = tileCenter(e.x, e.y);
        faceToward(s, c.x);
        anim(sc, s, { sy: { from: 1, to: 0.82 }, sx: { from: 1, to: 1.1 } }, 140, { yoyo: true, repeat: 1 });
        sc.time.delayedCall(140, () => shakeTile(sc, e.x, e.y));
        burst(sc, c.x, c.y, [0xd23a4f, 0xff8a9a, 0x4c8f40], 6, { spread: 8, up: 14, delay: 140 });
        break;
      }
      case "build": {
        const c = tileCenter(e.x, e.y);
        faceToward(s, c.x);
        for (let i = 0; i < 3; i++) {
          const d = i * 200;
          anim(sc, s, { y: { from: 0, to: -3 }, swing: { from: 0, to: 0.7 }, angle: { from: 0, to: 9 * s.face } },
               90, { yoyo: true, delay: d });
          burst(sc, c.x, c.y + 8, [0x9a8a78, 0xc8b89a, 0xe3c26a], 4, { delay: d + 90, spread: 14, up: 6, gravity: 10 });
        }
        break;
      }
      case "eat":
        anim(sc, s, { sy: { from: 1, to: 0.93 }, y: { from: 0, to: 1 } }, 90, { yoyo: true, repeat: 3 });
        burst(sc, head.x + 3 * s.face, head.y + 4, e.cooked ? [0xe0a458, 0xf6c04a] : [0xd23a4f, 0xff8a9a], 4,
              { spread: 6, up: 4, delay: 90, size: 1 });
        break;
      case "craft":
        anim(sc, s, { angle: { from: 0, to: 360 * s.face } }, 380,
             { ease: "Cubic.inOut", onComplete: () => { s.fx.angle = 0; } });
        burst(sc, head.x, head.y - 6, [0xffffff, 0xf6e27a, 0xcfcfd6], 10, { spread: 14, up: 16, delay: 380, gravity: 8 });
        break;
      case "talk":
        anim(sc, s, { y: { from: 0, to: -3 } }, 110, { yoyo: true });
        break;
      case "trade": {
        const b = get(e.target);
        anim(sc, s, { y: { from: 0, to: -4 } }, 120, { yoyo: true });
        if (b) {
          anim(sc, b, { y: { from: 0, to: -4 } }, 120, { yoyo: true, delay: 140 });
          burst(sc, (s.tx + b.tx) / 2, head.y, [0xf6c04a, 0xffe08a], 6, { spread: 8, up: 14, delay: 120 });
        }
        break;
      }
      case "gift": {
        const b = get(e.target);
        anim(sc, s, { y: { from: 0, to: -4 } }, 120, { yoyo: true });
        if (b) {
          faceToward(s, b.tx); faceToward(b, s.tx);
          anim(sc, b, { y: { from: 0, to: -5 }, sy: { from: 1, to: 1.06 } }, 140, { yoyo: true, delay: 260 });
          floatText(sc, b.tx, b.ty - 24 * b.base, `+${e.n} ${e.item}`, "#b8e986", { size: 10, delay: 260 });
        }
        break;
      }
      case "pickup":
        anim(sc, s, { sy: { from: 1, to: 0.88 } }, 110, { yoyo: true });
        floatText(sc, s.tx, s.ty - 26 * s.base,
                  Object.entries(e.items || {}).map(([k, n]) => `+${n} ${k}`).join(" "), "#ffe08a", { size: 10, delay: 200 });
        break;
      case "birth": {
        anim(sc, s, { sx: { from: 0.1, to: 1 }, sy: { from: 0.1, to: 1 } }, 520, { ease: "Back.out" });
        for (const pid of e.parents || []) {
          const p = get(pid);
          if (!p) continue;
          for (let i = 0; i < 3; i++)
            floatText(sc, p.tx + (i - 1) * 5, p.ty - 22 * p.base, "♥", "#ff7ab0",
                      { size: 10, delay: i * 180, rise: 20, dx: (i - 1) * 4, dur: 1100 });
        }
        break;
      }
      case "work": {
        // one swing of the tool while a job is under way (the full flourish plays when it is done)
        if (e.x != null) faceToward(s, tileCenter(e.x, e.y).x);
        if (e.kind === "eat") {
          anim(sc, s, { sy: { from: 1, to: 0.94 }, y: { from: 0, to: 1 } }, 90, { yoyo: true, repeat: 1 });
        } else if (e.kind === "forage") {
          anim(sc, s, { sy: { from: 1, to: 0.86 }, sx: { from: 1, to: 1.08 } }, 140, { yoyo: true });
          if (e.x != null) sc.time.delayedCall(140, () => shakeTile(sc, e.x, e.y));
        } else {
          anim(sc, s, { swing: { from: 0, to: 1 }, angle: { from: 0, to: 12 * s.face }, x: { from: 0, to: 3 * s.face } },
               100, { yoyo: true, ease: "Quad.in" });
          if (e.x != null) {
            const c = tileCenter(e.x, e.y);
            if (e.kind === "chop") sc.time.delayedCall(100, () => shakeTile(sc, e.x, e.y));
            burst(sc, c.x, c.y + 4, e.kind === "chop" ? [0xc89a62, 0x6d4825] : [0x9a8a78, 0xc8b89a], 3,
                  { delay: 100, spread: 10, up: 8 });
          }
        }
        break;
      }
      case "conceive": {
        const b = get(e.target);
        for (const p of [s, b]) {
          if (!p) continue;
          anim(sc, p, { y: { from: 0, to: -4 } }, 160, { yoyo: true, repeat: 1 });
          for (let i = 0; i < 3; i++)
            floatText(sc, p.tx + (i - 1) * 5, p.ty - 22 * p.base, "♥", "#ff7ab0",
                      { size: 10, delay: i * 180, rise: 20, dx: (i - 1) * 4, dur: 1100 });
        }
        break;
      }
      case "mine": {
        const c = tileCenter(e.x, e.y);
        faceToward(s, c.x);
        anim(sc, s, { swing: { from: 0, to: 1 }, angle: { from: 0, to: 14 * s.face } }, 100, { yoyo: true, ease: "Quad.in" });
        sc.time.delayedCall(100, () => shakeTile(sc, e.x, e.y));
        burst(sc, c.x, c.y + 4, [0x9a9aa3, 0xc0c0c8, 0xffe08a], 7, { delay: 100, spread: 12, up: 12 });
        break;
      }
      case "fish": {
        const c = tileCenter(e.x, e.y);
        faceToward(s, c.x);
        burst(sc, c.x, c.y, [0xdff3fb, 0x9fd3ff], 8, { spread: 10, up: 14, gravity: 30 });
        if (e.caught) floatText(sc, s.tx, s.ty - 26 * s.base, `+${e.caught} fish`, "#9fe8ff", { size: 11, delay: 200 });
        break;
      }
      case "plant": {
        const c = tileCenter(e.x, e.y);
        burst(sc, c.x, c.y + 4, [0x6ab85e, 0x8a6a3c], 6, { spread: 8, up: 8 });
        break;
      }
      case "construct": {
        const c = tileCenter(e.x, e.y);
        anim(sc, s, { y: { from: 0, to: -3 }, swing: { from: 0, to: 0.7 } }, 90, { yoyo: true, repeat: 1 });
        burst(sc, c.x, c.y + 6, [0xc8a46a, 0x9a8a78], 5, { spread: 12, up: 8 });
        break;
      }
      case "built": {
        const c = tileCenter(e.x, e.y);
        burst(sc, c.x, c.y, [0xf0cf5c, 0xffffff, 0xc8a46a], 18, { spread: 22, up: 24, gravity: 16, dur: 900 });
        const label = (state.defs && state.defs.objects[e.what] && state.defs.objects[e.what].label) || e.what;
        floatText(sc, c.x, c.y - 20, `${label} built!`, "#ffe08a", { size: 11, rise: 24, dur: 1600, pop: true });
        break;
      }
      case "pray":
        floatText(sc, s.tx, s.ty - 28 * s.base, "✦", "#fff4c8", { size: 14, rise: 26, dur: 1600 });
        break;
      case "levelup": {
        // a new level of a skill: a little jump, a shower of sparks, and what it was
        const d = (state.defs && state.defs.skills && state.defs.skills[e.skill]) || { label: e.skill };
        const delay = queue(e.npc);
        anim(sc, s, { y: { from: 0, to: -6 }, sy: { from: 1, to: 1.12 } }, 170, { yoyo: true, delay });
        burst(sc, head.x, head.y - 4, [0xffe08a, 0xf6c04a, 0xffffff], 12,
              { spread: 16, up: 20, gravity: 14, dur: 800, delay });
        floatText(sc, s.tx, s.ty - 28 * s.base, `${d.label} ${e.level}!`, "#ffe08a",
                  { size: 12, rise: 26, dur: 1700, pop: true, delay });
        break;
      }
      case "attrup": {
        const d = (state.defs && state.defs.attrs && state.defs.attrs[e.attr]) || { label: e.attr };
        floatText(sc, s.tx, s.ty - 28 * s.base, `+1 ${d.label}`, "#9fe8ff",
                  { size: 11, rise: 24, dur: 1600, delay: queue(e.npc) });
        break;
      }
      case "death":
        if (e.cause === "lightning") bolt(sc, s.tx, s.ty - 10);  // a bolt from a clear sky
        // dust when the body lands (see the fall in renderNpcs)
        burst(sc, s.tx + 8 * s.face, s.ty - 2, [0x9a8a78, 0x7a6a58], 8, { delay: 1150, spread: 14, up: 5, gravity: 6, dur: 700 });
        break;
      // --- the living world ---
      case "hunt": {  // a spear or a stone at a deer, a rabbit, or the wolf at their throat
        const b = animalSprite(e.animal);
        const ax = b ? b.pos.x : tileCenter(e.x, e.y).x, ay = b ? b.pos.y - 8 : tileCenter(e.x, e.y).y;
        faceToward(s, ax);
        anim(sc, s, { x: { from: 0, to: 7 * s.face }, swing: { from: 0, to: 1 }, angle: { from: 0, to: 12 * s.face } },
             110, { yoyo: true, ease: "Quad.in" });
        if (e.hit) {
          if (b) { b.face = ax < s.tx ? 1 : -1; hurt(sc, b); }
          slash(sc, ax, ay, s.face, 110);
          burst(sc, ax, ay, [0xd9534f, 0xff8a7a], e.killed ? 10 : 5, { delay: 110, spread: 12, up: 10 });
          floatText(sc, ax, ay - 8, e.killed ? "got it!" : `-${e.damage}`, e.killed ? "#ffe08a" : "#ff6a5a",
                    { size: e.killed ? 12 : 10, delay: 110, pop: !!e.killed });
        } else {
          floatText(sc, ax, ay - 8, "missed", "#c8c8d0", { size: 10, delay: 110 });
        }
        break;
      }
      case "mauled": {  // a wolf leaps at them
        const b = animalSprite(e.animal);
        if (b) {
          const dx = s.tx - b.pos.x;
          b.face = dx < 0 ? -1 : 1;
          sc.tweens.add({ targets: b.pos, x: { from: b.pos.x, to: b.pos.x + Math.sign(dx || 1) * 8 }, hop: { from: 0, to: 1.5 },
                          duration: 110, yoyo: true, ease: "Quad.in" });
        }
        if (e.hit) {
          flash(sc, s, 110);
          anim(sc, s, { x: { from: 0, to: (b ? Math.sign(s.tx - b.pos.x) : 1) * 5 }, angle: { from: 0, to: 12 } }, 90,
               { yoyo: true, delay: 110 });
          slash(sc, s.tx, s.ty - 12 * s.base, b ? Math.sign(s.tx - b.pos.x) || 1 : 1, 110);
          burst(sc, s.tx, s.ty - 12 * s.base, [0xd9534f, 0xff8a7a, 0xffffff], 8, { delay: 110, spread: 14, up: 12 });
          floatText(sc, s.tx, s.ty - 30 * s.base, `-${e.damage}`, "#ff6a5a", { size: 11, delay: 110 });
        }
        break;
      }
      case "douse": {  // beating out a fire
        const c = tileCenter(e.x, e.y);
        faceToward(s, c.x);
        for (let i = 0; i < 3; i++)
          anim(sc, s, { swing: { from: 0, to: 1 }, angle: { from: 0, to: 14 * s.face } }, 90, { yoyo: true, delay: i * 200 });
        burst(sc, c.x, c.y, [0x8a8580, 0xb8b4ae, 0x9fd3ff], 14, { spread: 16, up: 20, gravity: -4, dur: 1000 });
        floatText(sc, c.x, c.y - 18, "put out!", "#9fe8ff", { size: 10, delay: 400 });
        break;
      }
      case "burn":
        flash(sc, s, 0);
        burst(sc, s.tx, s.ty - 8, [0xe8641e, 0xf6c04a], 6, { spread: 8, up: 14, gravity: -4 });
        floatText(sc, s.tx, s.ty - 28 * s.base, "ow, hot!", "#ffb04a", { size: 10, delay: 100 });
        break;
      case "sick":
        anim(sc, s, { sy: { from: 1, to: 0.9 }, angle: { from: 0, to: 6 } }, 300, { yoyo: true, repeat: 1 });
        floatText(sc, s.tx, s.ty - 28 * s.base, "fever", "#b8d86a", { size: 10, rise: 14, dur: 1500 });
        break;
      case "recover":
        anim(sc, s, { y: { from: 0, to: -5 }, sy: { from: 1, to: 1.1 } }, 160, { yoyo: true });
        floatText(sc, s.tx, s.ty - 28 * s.base, "better!", "#9fe8ff", { size: 10, rise: 18, dur: 1500 });
        break;
      case "cold":  // shivering
        anim(sc, s, { x: { from: -1, to: 1 } }, 45, { yoyo: true, repeat: 5 });
        floatText(sc, s.tx + 6 * s.face, s.ty - 26 * s.base, "brr", "#cfe8ff", { size: 9, rise: 12, dur: 1200 });
        break;
      default: break;
    }
  }
}
