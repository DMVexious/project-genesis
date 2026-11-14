/* Things lying on the ground: a felled tree's wood, what the dead carried.
 * They bob gently where they fell and fly to whoever walks over them. */
import { TILE } from "./config.js";
import { state } from "./state.js";

const piles = new Map();  // "x,y" -> { img, label, bob, sig }
const TEXTURES = ["wood", "food", "spear", "stone", "fish"];

const total = (items) => Object.values(items).reduce((a, n) => a + n, 0);
// the picture shown is the item there is most of
function textureFor(items) {
  const top = Object.entries(items).sort((a, b) => b[1] - a[1])[0];
  return top && TEXTURES.includes(top[0]) ? `drop_${top[0]}` : "drop_other";
}

function remove(p) {
  if (p.bob) p.bob.stop();
  p.img.destroy();
  p.label.destroy();
}

export function clearDrops() {
  for (const p of piles.values()) remove(p);
  piles.clear();
}

export function renderDrops(sc, f, opts = {}) {
  const live = !opts.snap && !opts.reverse;
  const fell = new Set(), taken = new Map();
  if (live) {
    for (const e of f.fx || []) {
      if (e.type === "drop") fell.add(`${e.x},${e.y}`);
      else if (e.type === "pickup") taken.set(`${e.x},${e.y}`, e.npc);
    }
  }

  const seen = new Set();
  for (const [x, y, items] of f.drops || []) {
    const key = `${x},${y}`, sig = JSON.stringify(items);
    seen.add(key);
    let p = piles.get(key);
    if (p && p.sig === sig) continue;
    const cx = x * TILE + TILE / 2, cy = y * TILE + TILE - 7, count = total(items);
    if (!p) {
      const img = sc.add.image(cx, cy, textureFor(items)).setDepth(y * TILE + TILE - 6);
      const label = sc.add.text(cx + 7, cy + 3, "", { fontFamily: "monospace", fontStyle: "bold", fontSize: "8px",
        color: "#ffffff", stroke: "#140e09", strokeThickness: 3 }).setOrigin(0.5).setResolution(3)
        .setDepth(y * TILE + TILE - 5);
      p = { img, label, bob: null };
      piles.set(key, p);
      if (fell.has(key)) {  // it tumbles out of the falling tree
        img.setScale(0.2).setY(cy - 12);
        label.setAlpha(0);
        sc.tweens.add({ targets: img, y: cy, scale: 1, duration: 420, delay: 380, ease: "Bounce.out" });
        sc.tweens.add({ targets: label, alpha: 1, delay: 800, duration: 200 });
      }
      p.bob = sc.tweens.add({ targets: img, y: cy - 2, duration: 900, delay: 900 + Math.random() * 600,
        yoyo: true, repeat: -1, ease: "Sine.inOut" });
    }
    p.sig = sig;
    p.img.setTexture(textureFor(items));
    p.label.setText(count > 1 ? String(count) : "");
  }

  for (const [key, p] of [...piles]) {
    if (seen.has(key)) continue;
    piles.delete(key);
    const who = state.sprites.get(taken.get(key));
    if (!who) { remove(p); continue; }
    // picked up: it flies into their hands
    if (p.bob) p.bob.stop();
    p.label.destroy();
    p.img.setDepth(20000);
    sc.tweens.add({ targets: p.img, x: who.pos.x, y: who.pos.y - 16, scale: 0.3, duration: 260, ease: "Quad.in",
      onComplete: () => p.img.destroy() });
  }
}
