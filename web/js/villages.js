/* Villages on the map: each one's name over its hearth, and the reach of its land. */
import { TILE } from "./config.js";

export const VILLAGE_COLORS = [0xf0cf5c, 0x9fd3ff, 0xe07aa8, 0x8fc45a, 0xc49aff, 0xff9a5a, 0x6ad8c8, 0xd8d0b0];
export const villageColor = (id) => VILLAGE_COLORS[(id - 1) % VILLAGE_COLORS.length];
const shown = new Map();  // village id -> { label, ring, sig }

export function clearVillages() {
  for (const v of shown.values()) { v.label.destroy(); v.ring.destroy(); }
  shown.clear();
}

export function renderVillages(sc, f) {
  const seen = new Set();
  for (const v of f.villages || []) {
    seen.add(v.id);
    let s = shown.get(v.id);
    if (!s) {
      const label = sc.add.text(0, 0, "", { fontFamily: "Georgia, serif", fontStyle: "bold", fontSize: "13px",
        color: "#ffffff", stroke: "#140e09", strokeThickness: 4 }).setOrigin(0.5, 1).setResolution(2).setDepth(15000);
      const ring = sc.add.graphics().setDepth(-1);
      shown.set(v.id, (s = { label, ring, sig: "" }));
    }
    const color = v.abandoned ? 0x8a8478 : villageColor(v.id);
    const sig = `${v.name}:${v.people}:${v.radius}:${v.abandoned}`;
    if (s.sig === sig) continue;
    s.sig = sig;
    const cx = v.x * TILE + TILE / 2, cy = v.y * TILE + TILE / 2;
    s.label.setText(v.abandoned ? `${v.name} (abandoned)` : `${v.name} · ${v.people}`)
      .setPosition(cx, cy - TILE * 1.6).setColor(`#${color.toString(16).padStart(6, "0")}`);
    // its land: a faint dashed ring
    const r = (v.radius + 0.5) * TILE;
    s.ring.clear().lineStyle(2, color, v.abandoned ? 0.12 : 0.28);
    for (let a = 0; a < Math.PI * 2; a += Math.PI / 24) {
      s.ring.beginPath();
      s.ring.arc(cx, cy, r, a, a + Math.PI / 48);
      s.ring.strokePath();
    }
  }
  for (const [id, s] of [...shown]) if (!seen.has(id)) { s.label.destroy(); s.ring.destroy(); shown.delete(id); }
}
