/* The small map in the corner: the whole land at a glance, where everyone is,
 * and what the camera is looking at. Click or drag on it to look elsewhere.
 * (The start screen paints its previews of each kind of land the same way.) */
import { $, TILE, params } from "./config.js";
import { objectOf, terrainOf } from "./land.js";
import { state } from "./state.js";
import { tileHash } from "./textures.js";
import { villageColor } from "./villages.js";
import { scene } from "./world.js";

const OBJECT_COLOR = { rock: "#7a7a82", tree: "#2c6a2e", bush: "#3f8a3a", fruit: "#c8463c", cactus: "#4f9a4a",
                       mushroom: "#b8483c", hut: "#f0d27a", farm: "#f0cf5c", fire: "#ff8a2a", house: "#e8e0d0",
                       storehouse: "#c8783a", well: "#7fd0f0", workshop: "#c09060", shrine: "#fff0b0",
                       site: "#d9b47a", sapling: "#4c9a40" };
const SPENT_COLOR = { tree: "#6d4825", farm: "#8a6a3c" };
const rgb = (hex) => [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)];

// A map as a picture, one pixel per tile.
export function paintMap(terrain, tiles) {
  const h = terrain.length, w = terrain[0].length;
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  const img = ctx.createImageData(w, h);
  const colors = new Map();
  const colorOf = (ch, ground) => {
    const k = ch + ground;
    if (colors.has(k)) return colors.get(k);
    let c = "#000000";
    if (ch === ground) c = (terrainOf(ch) || {}).color || c;
    else {
      const o = objectOf(ch);
      c = o ? (o.spent && SPENT_COLOR[o.key]) || (o.spent ? null : OBJECT_COLOR[o.key]) || (terrainOf(ground) || {}).color || c : c;
    }
    colors.set(k, (c = rgb(c)));
    return c;
  };
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const [r, g, b] = colorOf(tiles[y][x], terrain[y][x]);
      const shade = 0.94 + (tileHash(x, y) % 7) * 0.02;  // a little grain, so flat ground is not a flat colour
      const i = (y * w + x) * 4;
      img.data[i] = Math.min(255, r * shade);
      img.data[i + 1] = Math.min(255, g * shade);
      img.data[i + 2] = Math.min(255, b * shade);
      img.data[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return canvas;
}

const SCALE = 2;  // minimap pixels per tile
let base = null, baseKey = "";

export function drawMinimap() {
  const f = state.frame, box = $("minimap");
  if (!f || !f.terrain || box.hidden) return;
  const canvas = $("minimap-canvas");
  const key = `${f.world}:${f.trev}:${f.rev}:${f.width}x${f.height}`;
  if (key !== baseKey) {
    baseKey = key;
    base = paintMap(f.terrain, f.tiles);
    if (canvas.width !== f.width * SCALE) canvas.width = f.width * SCALE;
    if (canvas.height !== f.height * SCALE) canvas.height = f.height * SCALE;
  }
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(base, 0, 0, canvas.width, canvas.height);
  for (const v of f.villages || []) {  // each village's hearth
    if (v.abandoned) continue;
    ctx.fillStyle = "#140e09";
    ctx.fillRect(v.x * SCALE - 4, v.y * SCALE - 4, SCALE + 8, SCALE + 8);
    ctx.fillStyle = "#" + villageColor(v.id).toString(16).padStart(6, "0");
    ctx.fillRect(v.x * SCALE - 3, v.y * SCALE - 3, SCALE + 6, SCALE + 6);
  }
  for (const n of f.npcs) {
    if (!n.alive) continue;
    ctx.fillStyle = "#140e09";
    ctx.fillRect(n.x * SCALE - 2, n.y * SCALE - 2, SCALE + 4, SCALE + 4);
    ctx.fillStyle = n.id === state.selected ? "#ffd24a" : "#ffffff";
    ctx.fillRect(n.x * SCALE - 1, n.y * SCALE - 1, SCALE + 2, SCALE + 2);
  }
  const sc = scene();
  if (sc && sc.cameras) {
    const v = sc.cameras.main.worldView, k = SCALE / TILE;
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 1;
    ctx.strokeRect(Math.round(v.x * k) + 0.5, Math.round(v.y * k) + 0.5, Math.round(v.width * k), Math.round(v.height * k));
  }
}

function lookAt(e) {
  const f = state.frame, sc = scene(), canvas = $("minimap-canvas");
  if (!f || !sc || !sc.cameras) return;
  const r = canvas.getBoundingClientRect();
  const cam = sc.cameras.main;
  cam.stopFollow();
  cam.centerOn(((e.clientX - r.left) / r.width) * f.width * TILE, ((e.clientY - r.top) / r.height) * f.height * TILE);
  drawMinimap();
}

export function initMinimap() {
  const box = $("minimap"), canvas = $("minimap-canvas");
  if (params.has("bare")) box.hidden = true;
  try { if (localStorage.getItem("genesis.minimap") === "0") box.hidden = true; } catch (_e) { /* storage off */ }
  let dragging = false;
  canvas.addEventListener("pointerdown", (e) => { dragging = true; canvas.setPointerCapture(e.pointerId); lookAt(e); });
  canvas.addEventListener("pointermove", (e) => { if (dragging) lookAt(e); });
  canvas.addEventListener("pointerup", () => { dragging = false; });
}

export function toggleMinimap() {
  const box = $("minimap");
  box.hidden = !box.hidden;
  try { localStorage.setItem("genesis.minimap", box.hidden ? "0" : "1"); } catch (_e) { /* storage off */ }
  drawMinimap();
}
