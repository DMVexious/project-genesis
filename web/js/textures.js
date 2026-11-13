/* Procedural pixel art. Every texture is drawn in code at startup (characters
 * on demand, from each NPC's inherited looks), so there are no image files. */
import { TILE } from "./config.js";
import { state } from "./state.js";
import { scene } from "./world.js";

// Pick a texture for a tile. Position-hashed variants keep the map from
// looking tiled and give each hut its own style.
export const tileHash = (x, y) => (Math.imul(x, 73856093) ^ Math.imul(y, 19349663)) >>> 0;
export function texFor(ch, x, y) {
  const h = tileHash(x, y);
  switch (ch) {
    case ".": return `dirt${h % 3}`;
    case "#": return `rock${h % 2}`;
    case "T": return `tree${h % 3}`;
    case "s": return "stump";
    case "B": return "bush";
    case "b": return "bush_empty";
    case "H": return `hut${(h >>> 3) % 4}`;
    case "F": return "farm";
    case "f": return "farm_growing";
    case "C": return "fire0";
    default: return "dirt0";
  }
}

// --- procedural pixel-art textures ---------------------------------------
export function rand(seed) {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}

export function makeTextures(scene) {
  const g = scene.make.graphics({ x: 0, y: 0, add: false });
  const px = (x, y, w, h, c) => { g.fillStyle(c, 1); g.fillRect(x, y, w, h); };
  const save = (key, w = TILE, h = TILE) => { g.generateTexture(key, w, h); g.clear(); };

  const dirt = (seed) => {
    px(0, 0, TILE, TILE, 0x7a5a3a);
    const r = rand(seed);
    for (let i = 0; i < 18; i++) px(Math.floor(r() * TILE), Math.floor(r() * TILE), 2, 2, r() < 0.5 ? 0x6b4e32 : 0x8a6a46);
  };
  for (let i = 0; i < 3; i++) {
    dirt(1 + i * 11);
    if (i === 1) { px(5, 17, 1, 3, 0x6f8a3a); px(6, 16, 1, 4, 0x86a347); px(15, 6, 1, 3, 0x6f8a3a); } // grass tufts
    if (i === 2) { px(17, 15, 2, 2, 0x9a8a78); px(6, 6, 2, 1, 0x9a8a78); }                        // pebbles
    save(`dirt${i}`);
  }

  dirt(2);
  px(4, 9, 16, 11, 0x5f5f66); px(6, 6, 11, 5, 0x77777f); px(8, 7, 5, 2, 0x9a9aa3); px(4, 18, 16, 2, 0x45454c);
  save("rock0");
  dirt(12);
  px(3, 12, 9, 8, 0x66666e); px(4, 10, 6, 3, 0x7d7d86); px(12, 8, 9, 12, 0x5a5a62); px(13, 7, 6, 2, 0x8a8a93);
  px(3, 19, 18, 1, 0x45454c);
  save("rock1");

  // Trees: round oak, pine, and a lighter birch.
  dirt(3);
  px(10, 15, 4, 8, 0x5b3a1e);
  px(4, 4, 16, 12, 0x2f6b2f); px(6, 2, 12, 3, 0x3d8a3a); px(3, 7, 3, 6, 0x2a5e2a); px(18, 7, 3, 6, 0x2a5e2a);
  px(8, 5, 4, 3, 0x56a84f);
  save("tree0");
  dirt(13);
  px(11, 18, 3, 5, 0x5b3a1e);
  [[10, 1, 4, 3], [8, 4, 8, 4], [6, 8, 12, 4], [4, 12, 16, 6]].forEach(([x, y, w, h]) => px(x, y, w, h, 0x1f5532));
  px(11, 2, 2, 2, 0x2f7a46); px(9, 6, 3, 2, 0x2f7a46); px(7, 10, 3, 2, 0x2f7a46); px(5, 14, 4, 2, 0x2f7a46);
  save("tree1");
  dirt(14);
  px(11, 12, 2, 11, 0xe8e4da); px(11, 15, 2, 1, 0x333333); px(11, 19, 2, 1, 0x333333);
  px(5, 3, 14, 10, 0x6b9a35); px(7, 1, 10, 3, 0x82b347); px(4, 6, 3, 5, 0x5c8a2c); px(17, 6, 3, 5, 0x5c8a2c);
  px(9, 4, 3, 2, 0xa6cf5f);
  save("tree2");

  dirt(4);
  px(8, 13, 8, 6, 0x6d4825); px(9, 12, 6, 2, 0xc89a62); px(11, 13, 2, 1, 0x9c7445);
  save("stump");

  const bushBase = () => { px(4, 9, 16, 11, 0x3f7a35); px(6, 7, 12, 3, 0x4c8f40); px(5, 18, 14, 2, 0x2d5a27); };
  dirt(5); bushBase();
  [[7, 10], [13, 9], [16, 13], [9, 15], [12, 13]].forEach(([x, y]) => { px(x, y, 3, 3, 0xd23a4f); px(x, y, 1, 1, 0xff8a9a); });
  save("bush");
  dirt(6); bushBase(); save("bush_empty");

  // Huts: four styles (thatch, red tile, slate, log cabin).
  const hut = (roof, roofHi, wall, wallLine) => {
    dirt(7);
    g.fillStyle(roof, 1); g.fillTriangle(1, 11, 12, 1, 23, 11);
    g.fillStyle(roofHi, 1); g.fillTriangle(5, 9, 12, 3, 19, 9);
    px(4, 11, 16, 11, wall);
    if (wallLine !== null) for (let y = 13; y < 22; y += 3) px(4, y, 16, 1, wallLine);
    px(4, 11, 16, 1, 0x3d2614);
    px(10, 15, 5, 7, 0x3d2614); px(13, 18, 1, 1, 0xc89a62);  // door + handle
    px(5, 14, 3, 3, 0xf3d27a); px(5, 15, 3, 1, 0x6b4a2c);     // lit window
    px(3, 22, 18, 1, 0x4a3220);
  };
  hut(0xc9a14a, 0xdcb862, 0xa0784c, null); save("hut0");
  hut(0x8c3b26, 0xa34a31, 0xb08a5c, null); save("hut1");
  hut(0x4f5d75, 0x63728c, 0x9a8f80, 0x857a6c); save("hut2");
  hut(0x3f6b3a, 0x4d8247, 0x8a5a32, 0x6b4424); save("hut3");

  // Farms: tilled rows; growing = sprouts, ripe = golden wheat.
  const farmBase = () => {
    px(0, 0, TILE, TILE, 0x6a4a2c);
    for (let y = 3; y < TILE; y += 6) px(1, y, 22, 3, 0x553a22);
    px(0, 0, TILE, 1, 0x8a6a46); px(0, 0, 1, TILE, 0x8a6a46); px(23, 0, 1, TILE, 0x4a3220); px(0, 23, TILE, 1, 0x4a3220);
  };
  farmBase();
  for (let y = 3; y < TILE; y += 6) for (let x = 3; x < 22; x += 4) { px(x, y, 1, 2, 0x5fa040); px(x + 1, y - 1, 1, 1, 0x86c95a); }
  save("farm_growing");
  farmBase();
  for (let y = 3; y < TILE; y += 6) for (let x = 2; x < 22; x += 3) {
    px(x, y - 2, 1, 4, 0xc9a13a); px(x, y - 3, 2, 2, 0xf0cf5c);
  }
  save("farm");

  // Campfire: two flame frames, swapped every few hundred ms.
  const fire = (frame) => {
    dirt(8);
    g.fillStyle(0x000000, 0.18); g.fillEllipse(12, 18, 20, 8);
    [[5, 16], [8, 19], [12, 20], [16, 19], [19, 16], [7, 14], [17, 14]].forEach(([x, y]) => px(x, y, 3, 2, 0x8a8a90));
    px(7, 16, 10, 2, 0x5b3a1e); px(9, 14, 2, 5, 0x6d4825); px(13, 14, 2, 5, 0x6d4825);
    if (frame === 0) {
      px(9, 8, 6, 7, 0xe8641e); px(10, 5, 4, 4, 0xf08a2a); px(11, 3, 2, 3, 0xf6c04a); px(10, 10, 4, 5, 0xf6c04a);
    } else {
      px(8, 9, 7, 6, 0xe8641e); px(11, 4, 4, 6, 0xf08a2a); px(12, 2, 2, 3, 0xf6c04a); px(10, 11, 4, 4, 0xf6c04a);
      px(8, 6, 1, 2, 0xf08a2a);
    }
  };
  fire(0); save("fire0");
  fire(1); save("fire1");

  // Grave marker (drawn on top of the dirt where someone died).
  px(5, 19, 14, 3, 0x5b4128); px(6, 18, 12, 1, 0x6d4f31);
  px(8, 7, 8, 12, 0x8e8e96); px(9, 6, 6, 1, 0x8e8e96); px(9, 7, 6, 1, 0xa6a6ae);
  px(11, 9, 2, 7, 0x5e5e66); px(9, 11, 6, 2, 0x5e5e66);
  save("grave");

  g.fillStyle(0xffffff, 1); g.fillEllipse(9, 3, 18, 6);
  save("shadow", 18, 6);

  px(3, 4, 1, 16, 0x7a5230); px(2, 0, 3, 1, 0xcfcfd6); px(2, 1, 3, 3, 0xa8a8b0); px(3, 0, 1, 1, 0xffffff);
  px(2, 12, 3, 1, 0x3d2614);
  save("spear", 7, 20);

  g.lineStyle(2, 0xffe08a, 1); g.strokeEllipse(12, 5, 20, 8);
  save("ring", 24, 10);
  g.lineStyle(2, 0xffffff, 1); g.strokeRect(1, 1, TILE - 2, TILE - 2);
  save("homemark");
  g.destroy();
}

// --- characters: drawn on demand from each NPC's inherited looks ------------
export const SKIN = { porcelain: 0xffe6d5, fair: 0xf6d2b0, tan: 0xd9a676, olive: 0xb88a5a, brown: 0x8d5a3b, deep: 0x5c3a26 };
export const HAIR = { black: 0x1e1a18, brown: 0x5a3820, auburn: 0x8a3b1e, blonde: 0xe2c46a, copper: 0xc0642a, ash: 0x9a9488 };
export const CLOTH = { red: 0xb83a3a, blue: 0x3a64b8, green: 0x3f8a46, ochre: 0xc89a3a, purple: 0x7a4a9a,
                teal: 0x2f8f8a, grey: 0x7a7a80, cream: 0xe8dcc0 };
export const DEFAULT_LOOK = { skin: "fair", hair: "brown", hairstyle: "short", clothes: "tunic", color: "blue", hat: "none" };
export const NPC_W = 20, NPC_H = 26;
export const OUTLINE = 0x140e09;
export const shade = (c, f) => {
  const r = Math.min(255, Math.round(((c >> 16) & 255) * f));
  const g = Math.min(255, Math.round(((c >> 8) & 255) * f));
  const b = Math.min(255, Math.round((c & 255) * f));
  return (r << 16) | (g << 8) | b;
};
export const lookKey = (look, stage) =>
  `npc:${stage}:${look.skin}:${look.hair}:${look.hairstyle}:${look.clothes}:${look.color}:${look.hat}`;

export function npcTexture(scene, rawLook, stage) {
  const look = { ...DEFAULT_LOOK, ...(rawLook || {}) };
  const key = lookKey(look, stage);
  if (scene.textures.exists(key)) return key;

  // Parts are collected first so every outlined part gets its dark border
  // drawn underneath all fills (black survives at the silhouette edges only).
  const parts = [];
  const part = (x, y, w, h, c, outline = true) => parts.push({ x, y, w, h, c, outline });
  const skin = SKIN[look.skin] || SKIN.fair;
  const cloth = CLOTH[look.color] || CLOTH.blue;
  const hairC = stage === "elder" ? 0xdedede : (HAIR[look.hair] || HAIR.brown);
  const denim = 0x3a4f7a, cream = 0xe8dcc0, fur = 0x8b6b4a;
  const pants = look.clothes === "overalls" ? denim : shade(cloth, 0.5);

  // legs + feet
  if (look.clothes !== "robe") { part(6, 18, 3, 5, pants); part(11, 18, 3, 5, pants); }
  part(6, 23, 3, 1, 0x2a1d14); part(11, 23, 3, 1, 0x2a1d14);
  // arms (sleeves) + hands
  const sleeve = { vest: cream, furs: fur }[look.clothes] ?? cloth;
  part(3, 10, 2, 6, sleeve); part(15, 10, 2, 6, sleeve);
  part(3, 16, 2, 1, skin); part(15, 16, 2, 1, skin);
  // torso
  switch (look.clothes) {
    case "robe":
      part(5, 10, 10, 13, cloth); part(5, 14, 10, 1, shade(cloth, 0.7), false); part(9, 10, 2, 13, shade(cloth, 1.15), false);
      break;
    case "vest":
      part(5, 10, 10, 8, cream); part(5, 10, 3, 8, cloth, false); part(12, 10, 3, 8, cloth, false);
      part(5, 16, 10, 1, 0x4a3220, false);
      break;
    case "furs":
      part(5, 10, 10, 8, fur);
      [[6, 12], [9, 14], [12, 12], [7, 16], [11, 16], [13, 15]].forEach(([x, y]) => part(x, y, 1, 1, 0xb08d64, false));
      part(5, 10, 10, 2, 0xd8c8a8, false); part(5, 16, 10, 1, cloth, false);
      break;
    case "overalls":
      part(5, 10, 10, 8, cloth); part(7, 12, 6, 6, denim, false); part(7, 10, 1, 2, denim, false); part(12, 10, 1, 2, denim, false);
      part(8, 13, 1, 1, 0xcfcfd6, false); part(11, 13, 1, 1, 0xcfcfd6, false);
      break;
    default: // tunic
      part(5, 10, 10, 8, cloth); part(5, 15, 10, 1, 0x4a3220, false); part(9, 15, 2, 1, 0xd9b45a, false);
      part(8, 10, 4, 1, shade(cloth, 0.75), false);
  }
  // head + face
  part(7, 3, 6, 7, skin);
  part(8, 6, 1, 1, 0x222222, false); part(11, 6, 1, 1, 0x222222, false);
  if (stage === "child") part(8, 8, 4, 1, shade(skin, 0.85), false);
  // hair
  const top = () => { part(7, 3, 6, 2, hairC); };
  switch (look.hairstyle) {
    case "short": top(); part(7, 5, 1, 1, hairC, false); part(12, 5, 1, 1, hairC, false); break;
    case "long": top(); part(6, 4, 1, 8, hairC); part(13, 4, 1, 8, hairC); break;
    case "ponytail": top(); part(13, 4, 2, 6, hairC); break;
    case "mohawk": part(9, 0, 2, 5, hairC); break;
    case "bun": top(); part(8, 1, 4, 2, hairC); break;
    case "braids":
      top(); part(6, 5, 1, 8, hairC); part(13, 5, 1, 8, hairC);
      part(6, 12, 1, 1, 0xd23a3a, false); part(13, 12, 1, 1, 0xd23a3a, false);
      break;
    default: break; // bald
  }
  // hats
  const band = look.color === "red" ? 0xf2f2f2 : 0xd23a3a;
  switch (look.hat) {
    case "straw": part(4, 3, 12, 1, 0xe3c26a); part(7, 1, 6, 2, 0xd4b052); part(7, 2, 6, 1, 0x8a3b1e, false); break;
    case "hood": part(6, 2, 8, 2, shade(cloth, 0.8)); part(6, 4, 1, 6, shade(cloth, 0.8)); part(13, 4, 1, 6, shade(cloth, 0.8)); break;
    case "headband": part(7, 4, 6, 1, band, false); break;
    case "feather": part(7, 4, 6, 1, band, false); part(13, 0, 1, 4, 0xf2f2f2); part(13, 0, 1, 1, 0xd23a3a, false); break;
    case "flower": part(11, 2, 2, 2, 0xff7ab0); part(11, 2, 1, 1, 0xffe066, false); break;
    default: break;
  }
  // elders lean on a walking stick
  if (stage === "elder") { part(18, 12, 1, 12, 0x6b4a2c); part(17, 12, 2, 1, 0x6b4a2c); }

  const g = scene.make.graphics({ x: 0, y: 0, add: false });
  for (const p of parts) if (p.outline) { g.fillStyle(OUTLINE, 1); g.fillRect(p.x - 1, p.y - 1, p.w + 2, p.h + 2); }
  for (const p of parts) { g.fillStyle(p.c, 1); g.fillRect(p.x, p.y, p.w, p.h); }
  g.generateTexture(key, NPC_W, NPC_H);
  g.destroy();
  return key;
}

export function portrait(key) {
  if (state.portraits.has(key)) return state.portraits.get(key);
  let url = "";
  try { url = scene().textures.getBase64(key); } catch (_e) { /* texture not ready */ }
  if (url) state.portraits.set(key, url);
  return url;
}
