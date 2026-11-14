/* Wild animals: deer and rabbits grazing and bolting, wolves on the prowl.
 * The server sends each as [id, kind, x, y, hp]; they walk (or hop) from tile
 * to tile like the villagers do, and a wolf's eyes shine in the dark. */
import { TILE } from "./config.js";
import { reduceMotion } from "./fx.js";
import { state } from "./state.js";

const LOOK = {
  deer: { scale: 1.1, hop: 2, waddle: 3 },
  rabbit: { scale: 1, hop: 4, waddle: 0 },
  wolf: { scale: 1.1, hop: 1.5, waddle: 4 },
};
const beasts = new Map();  // id -> { img, shadow, eyes, kind, pos, hop, face, tx, ty, hp }
export const EYES_DEPTH = 15002;  // above the dark (see sky.js)

const spot = (x, y, id) => ({ x: x * TILE + TILE / 2 + ((id % 5) - 2) * 2, y: y * TILE + TILE - 3 });

export function animalSprite(id) { return beasts.get(id) || null; }

export function clearAnimals() {
  for (const b of beasts.values()) destroy(b);
  beasts.clear();
}

function destroy(b) {
  if (b.tween) b.tween.stop();
  b.img.destroy();
  b.shadow.destroy();
  if (b.eyes) b.eyes.destroy();
}

export function renderAnimals(sc, f, opts = {}) {
  const seen = new Set(), ms = opts.tickMs || state.tickMs;
  const killed = new Set((f.fx || []).filter((e) => e.type === "hunt" && e.killed).map((e) => e.animal));
  for (const [id, kind, x, y, hp] of f.animals || []) {
    seen.add(id);
    const look = LOOK[kind] || LOOK.deer, p = spot(x, y, id);
    let b = beasts.get(id);
    if (!b) {
      const img = sc.add.image(p.x, p.y, kind).setOrigin(0.5, 1).setScale(look.scale).setDepth(p.y);
      const shadow = sc.add.image(p.x, p.y, "shadow").setAlpha(0.45).setScale(kind === "rabbit" ? 0.6 : 0.9);
      const eyes = kind === "wolf" ? sc.add.image(p.x, p.y, "eyes").setBlendMode(Phaser.BlendModes.ADD)
        .setDepth(EYES_DEPTH).setAlpha(0) : null;
      b = { img, shadow, eyes, kind, look, pos: { x: p.x, y: p.y, hop: 0 }, face: (id % 2) ? 1 : -1, tx: p.x, ty: p.y, hp };
      beasts.set(id, b);
      if (!opts.snap) sc.tweens.add({ targets: [img], alpha: { from: 0, to: 1 }, duration: 500 });
    } else if (b.tx !== p.x || b.ty !== p.y) {
      if (b.tween) b.tween.stop();
      if (p.x !== b.tx) b.face = p.x < b.tx ? -1 : 1;
      const dur = Math.max(90, ms * 0.95);
      b.tween = sc.tweens.add({ targets: b.pos, x: p.x, y: p.y, duration: dur, ease: "Linear" });
      if (!reduceMotion) sc.tweens.add({ targets: b.pos, hop: { from: 0, to: 1 }, duration: dur / 2, yoyo: true,
        ease: "Sine.out" });
    }
    if (hp < b.hp && !opts.snap) hurt(sc, b);
    b.hp = hp; b.tx = p.x; b.ty = p.y;
  }
  for (const [id, b] of beasts) {
    if (seen.has(id)) continue;
    beasts.delete(id);
    b.killed = killed.has(id);
    if (opts.snap || opts.reverse) { destroy(b); continue; }
    // gone: hunted down (it falls over), or slipped away into the wild (it fades)
    if (b.tween) b.tween.stop();
    if (b.eyes) b.eyes.destroy();
    sc.tweens.add({ targets: [b.img, b.shadow], alpha: 0, delay: b.killed ? 500 : 0, duration: 700,
      onComplete: () => { b.img.destroy(); b.shadow.destroy(); } });
    if (b.killed) sc.tweens.add({ targets: b.img, angle: 90 * b.face, duration: 450, ease: "Bounce.out" });
  }
}

export function hurt(sc, b) {
  b.img.setTintFill(0xffffff);
  sc.time.delayedCall(80, () => { if (b.img.active) b.img.setTint(0xff7a6a); });
  sc.time.delayedCall(220, () => { if (b.img.active) b.img.clearTint(); });
}

// Every animation frame: place them, hop them, and light up the wolves' eyes in the dark.
export function poseAnimals(sc, dark, now) {
  for (const b of beasts.values()) {
    const L = b.look, wobble = reduceMotion ? 0 : Math.sin(now / 120 + b.tx) * L.waddle * b.pos.hop;
    b.img.setPosition(b.pos.x, b.pos.y - b.pos.hop * L.hop).setFlipX(b.face < 0).setAngle(wobble)
      .setDepth(b.pos.y);
    b.shadow.setPosition(b.pos.x, b.pos.y - 1).setDepth(b.pos.y - 0.6);
    if (b.eyes) {
      const blink = Math.floor(now / 140 + b.tx) % 31 === 0 ? 0 : 1;
      b.eyes.setPosition(b.img.x + 7 * b.face * L.scale, b.img.y - 13 * L.scale).setAlpha(Math.max(0, dark - 0.15) * 1.4 * blink);
    }
  }
}

export const isAnimal = (id) => beasts.has(id);
