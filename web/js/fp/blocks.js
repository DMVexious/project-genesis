/* The ground in first person: one block per tile, all in two instanced meshes
 * (solid ground, and see-through water). The world is flat, so heights are only
 * for looks: water lies lower, mountains stand as tall stacks (nobody can walk
 * there anyway). Tile (x, y) is the block from x..x+1 (east) and y..y+1 (south). */
import * as THREE from "../../vendor/three.module.min.js";
import { terrainOf } from "../land.js";
import { GROUND, shade, tileHash } from "../textures.js";
import { Builder, GRAIN } from "./models.js";

const UNIT = new Builder(1).box(0, 0, 0, 1, 1, 1, 0xffffff, { jitter: 0 }).geometry();  // a block from 0..1
const solidMat = new THREE.MeshLambertMaterial({ map: GRAIN, vertexColors: true });
const waterMat = new THREE.MeshLambertMaterial({ map: GRAIN, vertexColors: true, transparent: true, opacity: 0.78, depthWrite: false });

// top of the ground, and the height of the water's surface, for each kind
const FLOOR = { deep: -0.75, shallow: -0.32 };
const WATER_TOP = { deep: -0.12, shallow: -0.1 };
const SEASON_GRASS = { spring: 0x6a9c40, summer: 0x5f8f3c, autumn: 0x8a8a3a, winter: 0xd8e2e6 };
const SOFT = new Set(["grass", "forest"]);  // ground that takes on the season

const land = { mesh: null, water: null, key: "", season: "", tiles: [], waterTiles: [], heights: null, W: 0, H: 0 };
const m = new THREE.Matrix4(), c = new THREE.Color();

const colorOf = (t, season) => {
  if (!t) return 0xb04ad0;
  if (SOFT.has(t.key) && season && season !== "summer") {
    const g = SEASON_GRASS[season];
    return t.key === "forest" ? shade(g, 0.82) : g;
  }
  if (GROUND[t.key]) return GROUND[t.key].base;
  return parseInt((t.color || "#888888").slice(1), 16);
};

export const groundTop = (x, y) => {
  if (!land.heights || x < 0 || y < 0 || x >= land.W || y >= land.H) return 0;
  return Math.max(0, land.heights[y * land.W + x]);
};

// (Re)build when the map or the season changes.
export function buildBlocks(scene, f, season) {
  const key = `${f.world}:${f.trev}:${f.width}x${f.height}`;
  if (key === land.key && season === land.season) return false;
  land.key = key;
  land.season = season;
  if (land.mesh) { scene.remove(land.mesh); land.mesh.dispose(); }
  if (land.water) { scene.remove(land.water); land.water.dispose(); }
  const W = f.width, H = f.height;
  land.W = W; land.H = H;
  land.heights = new Float32Array(W * H);
  const solid = [], wet = [];
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const t = terrainOf(f.terrain[y][x]);
    const k = t && t.key, h = tileHash(x, y);
    const base = colorOf(t, season), tint = shade(base, 0.94 + ((h >>> 5) % 7) * 0.02);
    if (k === "deep" || k === "shallow") {
      const floor = FLOOR[k];
      solid.push([x, y, floor - 1, floor, k === "deep" ? 0x6a6050 : GROUND.sand ? GROUND.sand.dark : 0xc9b278]);
      wet.push([x, y, floor, WATER_TOP[k], k === "deep" ? 0x2f6f9f : 0x5fa9c9]);
      land.heights[y * W + x] = floor;
    } else if (k === "peak") {
      const top = 2 + ((h >>> 7) % 3);
      for (let i = 0; i < top; i++) solid.push([x, y, i - 1, i, shade(tint, 0.9 + 0.04 * i)]);
      solid.push([x, y, top - 1, top, h % 2 ? 0xe8eef2 : shade(tint, 1.1)]);
      land.heights[y * W + x] = top;
    } else {
      solid.push([x, y, -1, 0, tint]);
      if (SOFT.has(k) && season !== "winter") solid.push([x, y, -0.12, 0.001, shade(tint, 1.05)]);
    }
  }
  const make = (list, mat) => {
    const mesh = new THREE.InstancedMesh(UNIT, mat, list.length);
    list.forEach(([x, y, y0, y1, color], i) => {
      m.makeScale(1, y1 - y0, 1).setPosition(x, y0, y);
      mesh.setMatrixAt(i, m);
      mesh.setColorAt(i, c.setHex(color));
    });
    mesh.instanceMatrix.needsUpdate = true;
    mesh.instanceColor.needsUpdate = true;
    mesh.computeBoundingSphere();
    scene.add(mesh);
    return mesh;
  };
  land.mesh = make(solid, solidMat);
  land.water = make(wet, waterMat);
  land.water.renderOrder = 1;
  land.tiles = solid.map(([x, y]) => [x, y]);
  land.waterTiles = wet.map(([x, y]) => [x, y]);
  land.mesh.userData.tiles = land.tiles;
  land.water.userData.tiles = land.waterTiles;
  return true;
}

// What the raycaster may hit on the ground (the tile comes from the instance).
export const groundMeshes = () => [land.mesh, land.water].filter(Boolean);

export function waterTime(t) {
  waterMat.opacity = 0.74 + Math.sin(t / 900) * 0.04;
}

export function clearBlocks(scene) {
  if (land.mesh) { scene.remove(land.mesh); land.mesh.dispose(); }
  if (land.water) { scene.remove(land.water); land.water.dispose(); }
  land.mesh = land.water = null;
  land.key = "";
}
