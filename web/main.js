/* Project Genesis — Phaser front end.
 *
 * Phaser draws the world (tiles + NPC sprites); emojis and speech bubbles are
 * HTML elements in #overlay that track sprite positions every animation frame.
 * All textures are generated procedurally at startup (characters on demand,
 * from each NPC's inherited looks), so there are no image assets to ship.
 * Talks to the Python orchestrator over a WebSocket.
 */
(() => {
  "use strict";

  const TILE = 24;
  const NPC_SCALE = 1.25;
  // Speech bubbles fade this many ticks after they appear. Ticks are short now
  // (~150 ms; NPC actions span several), so this is ~3.5 s - long enough to read.
  const BUBBLE_TICKS = 24;
  const BUBBLE_MIN_MS = 6000;   // ...and never before 6 s, however fast the sim runs
  const REPLY_DELAY_MS = 1200;  // the listener's answer appears this long after the line
  const MAX_TALKS = 60;         // exchanges kept in the Conversations feed
  const GRAVE_TICKS = 400; // graves stay ~1 minute
  const MAX_GRAVES = 80;
  const AGGRO_COLOR = { Pacifist: 0x7bb661, Neutral: 0x6aa6d8, Hostile: 0xe0a458, Bloodthirsty: 0xd9534f };
  const hex = (n) => "#" + n.toString(16).padStart(6, "0");

  // Pick a texture for a tile. Position-hashed variants keep the map from
  // looking tiled and give each hut its own style.
  const tileHash = (x, y) => (Math.imul(x, 73856093) ^ Math.imul(y, 19349663)) >>> 0;
  function texFor(ch, x, y) {
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

  const params = new URLSearchParams(location.search);
  // Served by the orchestrator itself -> same host. Inside Tauri (or opened as
  // a file) the page has its own origin, so talk to the sidecar's fixed port.
  const servedByOrchestrator = /^https?:$/.test(location.protocol) && !/tauri\.localhost$/.test(location.hostname);
  const WS_URL = params.get("server") ||
    (servedByOrchestrator ? `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws` : "ws://127.0.0.1:8765/ws");

  const $ = (id) => document.getElementById(id);
  const overlay = $("overlay");
  if (params.has("bare")) $("panel").hidden = true;  // ?bare=1: world only (screenshots, recordings)

  const state = {
    frame: null,
    tickMs: 400,
    paused: false,
    selected: null,
    names: new Map(),
    sprites: new Map(), // id -> { sprite, shadow, spear, tag, statusEl, actionEl, key, dying }
    bubbles: new Map(), // id -> { el, tick, born }
    talks: [],          // recent exchanges, newest first: { tick, a, b, kind, said, reply }
    graves: [],         // { img, tick }
    tiles: [],
    fires: [],          // campfire tile images (animated)
    portraits: new Map(),
    ws: null,
  };

  // --- procedural pixel-art textures ---------------------------------------
  function rand(seed) {
    let s = seed >>> 0;
    return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
  }

  function makeTextures(scene) {
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
  const SKIN = { porcelain: 0xffe6d5, fair: 0xf6d2b0, tan: 0xd9a676, olive: 0xb88a5a, brown: 0x8d5a3b, deep: 0x5c3a26 };
  const HAIR = { black: 0x1e1a18, brown: 0x5a3820, auburn: 0x8a3b1e, blonde: 0xe2c46a, copper: 0xc0642a, ash: 0x9a9488 };
  const CLOTH = { red: 0xb83a3a, blue: 0x3a64b8, green: 0x3f8a46, ochre: 0xc89a3a, purple: 0x7a4a9a,
                  teal: 0x2f8f8a, grey: 0x7a7a80, cream: 0xe8dcc0 };
  const DEFAULT_LOOK = { skin: "fair", hair: "brown", hairstyle: "short", clothes: "tunic", color: "blue", hat: "none" };
  const NPC_W = 20, NPC_H = 26;
  const OUTLINE = 0x140e09;
  const shade = (c, f) => {
    const r = Math.min(255, Math.round(((c >> 16) & 255) * f));
    const g = Math.min(255, Math.round(((c >> 8) & 255) * f));
    const b = Math.min(255, Math.round((c & 255) * f));
    return (r << 16) | (g << 8) | b;
  };
  const lookKey = (look, stage) =>
    `npc:${stage}:${look.skin}:${look.hair}:${look.hairstyle}:${look.clothes}:${look.color}:${look.hat}`;

  function npcTexture(scene, rawLook, stage) {
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

  function portrait(key) {
    if (state.portraits.has(key)) return state.portraits.get(key);
    let url = "";
    try { url = scene().textures.getBase64(key); } catch (_e) { /* texture not ready */ }
    if (url) state.portraits.set(key, url);
    return url;
  }

  // --- Phaser scene -----------------------------------------------------------
  class WorldScene extends Phaser.Scene {
    constructor() { super("world"); }
    create() {
      makeTextures(this);
      this.ring = this.add.image(0, 0, "ring").setDepth(9000).setVisible(false);
      this.homeMark = this.add.image(0, 0, "homemark").setOrigin(0).setTint(0x9fd3ff).setDepth(8999).setVisible(false);
      this.flicker = 0;
      const cam = this.cameras.main;
      cam.setZoom(Phaser.Math.Clamp(Number(params.get("zoom")) || 1, 0.4, 4));  // ?zoom=2.5

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
      this.input.on("gameobjectup", (_p, obj) => { if (drag && !drag.moved && obj.npcId) select(obj.npcId); });
      this.input.on("pointerup", () => { setTimeout(() => { drag = null; }, 0); });

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
      // campfires flicker
      if (t - this.flicker > 220) {
        this.flicker = t;
        const frame = Math.floor(t / 220) % 2 ? "fire1" : "fire0";
        for (const img of state.fires) img.setTexture(frame);
      }
      const k = this.keys, cam = this.cameras.main;
      if (document.activeElement && document.activeElement.tagName === "INPUT") return placeOverlays(this);
      const v = (0.6 * dt) / cam.zoom;
      const dx = (k.D.isDown || k.RIGHT.isDown) - (k.A.isDown || k.LEFT.isDown);
      const dy = (k.S.isDown || k.DOWN.isDown) - (k.W.isDown || k.UP.isDown);
      if (dx || dy) { cam.stopFollow(); cam.scrollX += dx * v; cam.scrollY += dy * v; }
      placeOverlays(this);
    }
  }

  const MIN_ZOOM = 0.4, MAX_ZOOM = 4;
  const game = new Phaser.Game({
    type: Phaser.AUTO,
    parent: "game",
    backgroundColor: "#17130f",
    pixelArt: true,
    scale: { mode: Phaser.Scale.RESIZE, width: "100%", height: "100%" },
    scene: WorldScene,
  });
  const scene = () => game.scene.getScene("world");

  // --- rendering -----------------------------------------------------------------
  function renderTiles(sc, f) {
    const fires = [];
    if (state.tiles.length !== f.height || (state.tiles[0] || []).length !== f.width) {
      state.tiles.flat().forEach((img) => img.destroy());
      state.tiles = f.tiles.map((row, y) => [...row].map((ch, x) => {
        const img = sc.add.image(x * TILE, y * TILE, texFor(ch, x, y)).setOrigin(0).setData("ch", ch);
        if (ch === "C") fires.push(img);
        return img;
      }));
      state.fires = fires;
      // New world: let the camera roam it, starting over the settlers.
      const cam = sc.cameras.main;
      cam.setBounds(0, 0, f.width * TILE, f.height * TILE);
      const alive = f.npcs.filter((n) => n.alive);
      const center = (params.get("center") || "").split(",").map(Number);  // ?center=<tileX>,<tileY>
      if (center.length === 2 && center.every(Number.isFinite)) {
        cam.centerOn(center[0] * TILE, center[1] * TILE);
      } else if (alive.length) {
        const cx = alive.reduce((a, n) => a + n.x, 0) / alive.length;
        const cy = alive.reduce((a, n) => a + n.y, 0) / alive.length;
        cam.centerOn(cx * TILE, cy * TILE);
      }
      return;
    }
    f.tiles.forEach((row, y) => {
      for (let x = 0; x < row.length; x++) {
        const img = state.tiles[y][x];
        const ch = row[x];
        if (img.getData("ch") !== ch) {
          img.setTexture(texFor(ch, x, y)).setData("ch", ch);
          sc.tweens.add({ targets: img, alpha: { from: 0.4, to: 1 }, duration: 250 });
        }
        if (ch === "C") fires.push(img);
      }
    });
    state.fires = fires;
  }

  function npcPos(n) {
    const jitter = ((n.id % 3) - 1) * 4;
    return { x: n.x * TILE + TILE / 2 + jitter, y: n.y * TILE + TILE - 2 };
  }

  const stageOf = (n) => n.stage || (n.age < 60 ? "child" : "adult");
  const scaleFor = (stage) => (stage === "child" ? NPC_SCALE * 0.7 : NPC_SCALE);

  function addGrave(sc, n, tick) {
    const img = sc.add.image(n.x * TILE + TILE / 2, n.y * TILE + TILE, "grave")
      .setOrigin(0.5, 1).setDepth(n.y * TILE + TILE - 3).setAlpha(0);
    sc.tweens.add({ targets: img, alpha: 1, delay: 900, duration: 600 });
    state.graves.push({ img, tick });
    while (state.graves.length > MAX_GRAVES) state.graves.shift().img.destroy();
  }

  function ageGraves(sc, tick) {
    state.graves = state.graves.filter((gr) => {
      if (tick < gr.tick + GRAVE_TICKS) return true;
      sc.tweens.add({ targets: gr.img, alpha: 0, duration: 1500, onComplete: () => gr.img.destroy() });
      return false;
    });
  }

  function renderNpcs(sc, f) {
    const seen = new Set();
    for (const n of f.npcs) {
      seen.add(n.id);
      state.names.set(n.id, n.name);
      let s = state.sprites.get(n.id);
      const p = npcPos(n);
      const stage = stageOf(n);
      const key = npcTexture(sc, n.look, stage);
      if (!s) {
        const sprite = sc.add.image(p.x, p.y, key).setOrigin(0.5, 1).setInteractive({ useHandCursor: true });
        sprite.npcId = n.id;
        sprite.setScale(scaleFor(stage));
        const shadow = sc.add.image(p.x, p.y, "shadow").setTint(AGGRO_COLOR[n.traits.aggression] || 0xffffff).setAlpha(0.75);
        const spear = sc.add.image(p.x, p.y, "spear").setOrigin(0.5, 1).setAngle(16).setVisible(false);
        const tag = document.createElement("div");
        tag.className = "npc-tag";
        tag.innerHTML = '<span class="status"></span><span class="action"></span>';
        overlay.appendChild(tag);
        s = { sprite, shadow, spear, tag, statusEl: tag.children[0], actionEl: tag.children[1], dying: false, key };
        state.sprites.set(n.id, s);
      } else if (!s.dying && (s.tx !== p.x || s.ty !== p.y)) {
        // Walk at this NPC's own speed: the step lasts as long as its action does.
        if (s.tween) s.tween.stop();
        const ticks = n.dur || 2;
        s.tween = sc.tweens.add({ targets: s.sprite, x: p.x, y: p.y, ease: "Linear",
          duration: Math.max(120, ticks * state.tickMs * 0.95) });
        // a little bob so walkers don't glide
        sc.tweens.add({ targets: s.sprite, scaleY: { from: s.sprite.scaleX * 0.92, to: s.sprite.scaleX },
          duration: Math.max(120, ticks * state.tickMs * 0.45), yoyo: true });
      }
      if (s.key !== key && !s.dying) { // grew up, or grew old
        s.key = key;
        s.sprite.setTexture(key).setScale(scaleFor(stage));
      }
      s.tx = p.x; s.ty = p.y;
      s.sprite.setDepth(p.y);
      s.spear.setVisible(!!n.spear && n.alive);
      s.statusEl.textContent = n.status;
      // Re-trigger the pop animation whenever an action emoji is shown.
      s.actionEl.textContent = "";
      if (n.emoji) {
        const el = s.actionEl.cloneNode();
        el.textContent = n.emoji;
        s.tag.replaceChild(el, s.actionEl);
        s.actionEl = el;
      }
      if (!n.alive && !s.dying) {
        s.dying = true;
        s.sprite.setTint(0x777777).disableInteractive();
        s.spear.setVisible(false);
        addGrave(sc, n, f.tick);
        sc.tweens.add({ targets: [s.sprite, s.shadow], alpha: 0, delay: 600, duration: 900 });
        sc.tweens.add({ targets: s.sprite, angle: 90, delay: 600, duration: 900, onComplete: () => removeNpc(n.id) });
      }
    }
    for (const id of [...state.sprites.keys()]) {
      if (!seen.has(id) && !state.sprites.get(id).dying) removeNpc(id);
    }
    ageGraves(sc, f.tick);
  }

  function removeNpc(id) {
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

  function showBubble(d, tick) {
    const old = state.bubbles.get(d.npc);
    if (old) old.el.remove();
    const el = document.createElement("div");
    el.className = `bubble ${d.kind || ""}`;
    el.textContent = d.text;  // the server already caps lines at MAX_WORDS
    overlay.appendChild(el);
    state.bubbles.set(d.npc, { el, tick, born: performance.now() });
  }

  function renderBubbles(f) {
    for (const d of f.dialogue) {
      // the answer pops up a beat after the line it answers, so it reads as an exchange
      if (d.reply) setTimeout(() => { if (state.sprites.has(d.npc)) showBubble(d, state.frame.tick); }, REPLY_DELAY_MS);
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

  function placeOverlays(sc) {
    const canvas = game.canvas;
    if (!canvas) return;
    const c = canvas.getBoundingClientRect();
    const o = overlay.getBoundingClientRect();
    const k = c.width / game.scale.width;
    const cam = sc.cameras.main;
    const view = cam.worldView;
    const toScreen = (x, y) => [c.left - o.left + (x - view.x) * cam.zoom * k,
                                c.top - o.top + (y - view.y) * cam.zoom * k];
    for (const [id, s] of state.sprites) {
      const sp = s.sprite;
      s.shadow.setPosition(sp.x, sp.y - 1).setScale(sp.scaleX).setDepth(sp.depth - 0.6);
      if (s.spear.visible) s.spear.setPosition(sp.x + 9 * sp.scaleX, sp.y - 1).setScale(sp.scaleX).setDepth(sp.depth + 0.1);
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

  function render(f) {
    const sc = scene();
    if (!sc || !sc.sys.isActive() || !sc.ring) return;
    renderTiles(sc, f);
    renderNpcs(sc, f);
    renderBubbles(f);
  }

  // --- side panel ------------------------------------------------------------------
  const BAR_COLORS = { health: "#d9534f", hunger: "#e0a458", energy: "#6aa6d8", social: "#e07aa8" };
  const SOURCE_LABEL = { llm: "  🧠 LLM", reflex: "  ⚡ reflex", plan: "  ↻ continuing" };
  const BAR_LABELS = { health: "Health", hunger: "Fullness", energy: "Energy", social: "Social" };
  const STAGE_ICON = { child: "👶", elder: "🧓", adult: "" };
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function describeLook(l) {
    if (!l) return "";
    const hair = l.hairstyle === "bald" ? "bald" : `${l.hair} ${l.hairstyle} hair`;
    const hat = l.hat && l.hat !== "none" ? ` · ${l.hat}${l.hat === "hood" || l.hat === "headband" ? "" : " hat"}` : "";
    return `${l.skin} skin · ${hair} · ${l.color} ${l.clothes}${hat}`;
  }

  function renderPanel(f) {
    const s = f.stats;
    const cells = [["pop", s.population], ["births", s.births], ["deaths", s.deaths], ["gen", s.max_generation],
      ["tick", f.tick], ["huts", s.huts], ["farms", s.farms ?? 0], ["fires", s.fires ?? 0], ["fights", s.fights],
      ["trades", s.trades]];
    $("stats").innerHTML = cells.map(([k, v]) => `<div class="stat"><b>${v}</b><span>${k}</span></div>`).join("") +
      `<div class="stack">engine: ${esc(f.engine)} · brain: ${esc(f.brain)} · memory: ${esc(f.memory)} (${s.memories})</div>` +
      (s.decisions ? `<div class="stack">decisions: 🧠 ${s.decisions.llm} · ⚡ ${s.decisions.reflex} · ↻ ${s.decisions.plan}</div>` : "");

    const npcs = [...f.npcs].sort((a, b) => a.id - b.id);
    $("npc-list").innerHTML = npcs.map((n) => `
      <li data-id="${n.id}" class="${n.id === state.selected ? "sel" : ""} ${n.alive ? "" : "dead"}">
        <span class="swatch" style="background:${hex(AGGRO_COLOR[n.traits.aggression])}"></span>
        <span class="name">${esc(n.name)} <small>g${n.generation}</small> ${STAGE_ICON[stageOf(n)]}${n.home ? "🏠" : ""} ${n.status}</span>
        <span class="minibars">${["health", "hunger", "energy"].map((k) =>
          `<i style="width:${n[k]}%;background:${BAR_COLORS[k]}"></i>`).join("")}</span>
        <span>${n.emoji}</span>
      </li>`).join("");

    const sel = f.npcs.find((n) => n.id === state.selected);
    const det = $("detail");
    det.hidden = !sel;
    if (sel) {
      const t = sel.traits;
      const parents = sel.parents.map((p) => esc(state.names.get(p) || `#${p}`)).join(" & ") || "first settlers";
      const sp = state.sprites.get(sel.id);
      const img = sp ? portrait(sp.key) : "";
      det.innerHTML = `
        <div class="who">
          ${img ? `<img class="portrait" src="${img}" alt="">` : ""}
          <div>
            <h3>${esc(sel.name)} ${sel.status} ${sel.emoji}</h3>
            <div class="look">${esc(stageOf(sel))} · ${esc(describeLook(sel.look))}</div>
          </div>
        </div>
        <div class="traits">${Object.entries(t).map(([slot, v]) =>
          `<span class="trait ${sel.mutated.includes(slot) ? "mut" : ""}" title="${slot}${sel.mutated.includes(slot) ? " (mutation)" : ""}">${v}</span>`).join("")}</div>
        ${sel.belief ? `<div class="persona">🌀 ${esc(sel.name)} ${esc(sel.belief)}.<br>🗣️ They ${esc(sel.voice.replace(/\byourself\b/g, "themself").replace(/\byour\b/g, "their"))}.</div>` : ""}
        ${["health", "hunger", "energy", "social"].map((k) => `
          <div class="bar"><span>${BAR_LABELS[k]}</span>
          <span class="track"><span class="fill" style="display:block;width:${sel[k]}%;background:${BAR_COLORS[k]}"></span></span>
          <span>${sel[k]}</span></div>`).join("")}
        <div class="meta">🪵 ${sel.wood} wood · 🍒 ${sel.food} food ${sel.spear ? "· 🗡️ spear" : ""} · age ${sel.age} · gen ${sel.generation} · ${parents}</div>
        <div class="meta">${sel.home ? `🏠 home at (${sel.home[0]}, ${sel.home[1]})` : "no home yet"}</div>
        <div class="intent">${sel.alive ? "▶ " + esc(sel.intent || "…") + (SOURCE_LABEL[sel.source] || "") : "died: " + esc(sel.cause)}</div>`;
    }

    if (f.dialogue.length) {
      for (const d of f.dialogue) {
        if (d.reply) continue;
        const r = f.dialogue.find((x) => x.reply && x.npc === d.target && x.target === d.npc);
        state.talks.unshift({ tick: f.tick, a: d.npc, b: d.target, kind: d.kind, said: d.text, reply: r ? r.text : null,
                              llm: !!d.llm, replyLlm: !!(r && r.llm) });
      }
      state.talks.length = Math.min(state.talks.length, MAX_TALKS);
      renderTalks();
    }
    if (sel) {
      const mine = state.talks.filter((t) => t.a === sel.id || t.b === sel.id).slice(0, 4);
      det.insertAdjacentHTML("beforeend", `<div class="sub">Recent conversations</div>` +
        (mine.length ? `<ol class="talks">${mine.map(talkHtml).join("")}</ol>` : `<div class="hint">Hasn't spoken to anyone yet.</div>`));
    }

    const log = $("log");
    const items = [];
    for (const line of f.log.filter((l) => !l.includes(" talks to "))) items.push(`<li><b>${f.tick}</b>${esc(line)}</li>`);
    if (items.length) {
      log.insertAdjacentHTML("afterbegin", items.reverse().join(""));
      while (log.children.length > 150) log.lastChild.remove();
    }

    $("banner").hidden = s.population > 0;
    $("banner").textContent = "Extinction. Start a new world →";
  }

  const KIND_ICON = { talk: "💬", fight: "⚔️", trade: "🤝", mate: "💞" };
  const KIND_VERB = { talk: "to", fight: "attacking", trade: "trading with", mate: "courting" };

  function talkHtml(t) {
    const who = (id) => `<a data-id="${id}">${esc(state.names.get(id) || "?")}</a>`;
    return `<li class="talk ${t.kind}">
      <div class="head"><b>${t.tick}</b> ${KIND_ICON[t.kind] || "💬"} ${who(t.a)} <span>${KIND_VERB[t.kind] || "to"}</span> ${who(t.b)}</div>
      <div class="line ${t.llm ? "llm" : ""}">${who(t.a)}: “${esc(t.said)}”${t.llm ? LLM_MARK : ""}</div>
      ${t.reply ? `<div class="line reply ${t.replyLlm ? "llm" : ""}">${who(t.b)}: “${esc(t.reply)}”${t.replyLlm ? LLM_MARK : ""}</div>` : ""}
    </li>`;
  }

  const LLM_MARK = ` <span class="mark" title="written by the LLM (unmarked lines are pre-written)">🧠</span>`;

  function renderTalks() {
    const llmOnly = $("llm-only").checked;
    const shown = llmOnly ? state.talks.filter((t) => t.llm || t.replyLlm) : state.talks;
    $("talks").innerHTML = shown.length ? shown.map(talkHtml).join("")
      : `<li class="hint">${llmOnly ? "No LLM-written lines yet." : "Nobody has spoken yet. NPCs talk when they meet."}</li>`;
  }

  function select(id) {
    state.selected = state.selected === id ? null : id;
    const sc = scene();
    const s = state.sprites.get(state.selected);
    if (sc && sc.cameras) {
      if (s) sc.cameras.main.startFollow(s.sprite, true, 0.08, 0.08);
      else sc.cameras.main.stopFollow();
    }
    if (state.frame) renderPanel({ ...state.frame, dialogue: [], log: [] });
  }

  // --- networking ---------------------------------------------------------------------
  function send(msg) {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) state.ws.send(JSON.stringify(msg));
  }

  function connect() {
    const ws = new WebSocket(WS_URL);
    state.ws = ws;
    ws.onopen = () => { $("conn").textContent = "live"; $("conn").className = "pill ok"; };
    ws.onclose = () => {
      $("conn").textContent = "reconnecting…"; $("conn").className = "pill bad";
      setTimeout(connect, 1500);
    };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "control") {
        state.paused = msg.paused;
        state.tickMs = msg.tick_ms;
        state.seed = msg.seed;
        $("btn-pause").textContent = msg.paused ? "▶" : "⏸";
        $("speed").value = 1050 - msg.tick_ms;
        $("speed-val").textContent = `${msg.tick_ms} ms/tick`;
        if (document.activeElement !== $("seed")) $("seed").value = msg.seed;
      } else if (msg.type === "frame") {
        if (state.frame && msg.tick < state.frame.tick) resetView();
        state.frame = msg;
        render(msg);
        renderPanel(msg);
        if (params.has("follow") && state.selected === null && !state.followed) {  // ?follow=<npc id>
          state.followed = true;
          select(Number(params.get("follow")));
        }
      }
    };
  }

  function resetView() {
    for (const id of [...state.sprites.keys()]) removeNpc(id);
    for (const b of state.bubbles.values()) b.el.remove();
    state.bubbles.clear();
    for (const gr of state.graves) gr.img.destroy();
    state.graves = [];
    state.names.clear();
    state.selected = null;
    state.talks = [];
    renderTalks();
    $("log").innerHTML = "";
  }

  $("btn-pause").onclick = () => send({ cmd: state.paused ? "resume" : "pause" });
  $("btn-step").onclick = () => send({ cmd: "step" });
  $("speed").oninput = (e) => send({ cmd: "speed", tick_ms: 1050 - Number(e.target.value) });
  $("btn-reset").onclick = () => {
    const v = $("seed").value;
    const seed = v === "" || Number(v) === state.seed ? Math.floor(Math.random() * 1e6) : Number(v);
    send({ cmd: "reset", seed });
  };
  $("npc-list").onclick = (e) => {
    const li = e.target.closest("li[data-id]");
    if (li) select(Number(li.dataset.id));
  };
  $("llm-only").onchange = () => {
    try { localStorage.setItem("genesis.llmOnly", $("llm-only").checked ? "1" : ""); } catch (e) { /* storage off */ }
    renderTalks();
  };
  try { $("llm-only").checked = localStorage.getItem("genesis.llmOnly") === "1"; } catch (e) { /* storage off */ }
  // clicking a name in a conversation follows that NPC
  for (const box of [$("talks"), $("detail")]) box.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-id]");
    if (a) select(Number(a.dataset.id));
  });
  document.addEventListener("keydown", (e) => {
    if (e.code === "Space" && e.target.tagName !== "INPUT") {
      e.preventDefault();
      send({ cmd: state.paused ? "resume" : "pause" });
    }
  });

  connect();
})();
