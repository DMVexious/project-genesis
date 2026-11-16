/* The sky in first person: daylight and night, sunset glow, fog, rain and snow
 * falling around you, and the warm light of fires and homes after dark.
 * The hour's darkness comes from the same formula the top-down view uses. */
import * as THREE from "../../vendor/three.module.min.js";
import { reduceMotion } from "../fx.js";
import { darkOf, fogOf } from "../sky.js";
import { lightSpots } from "./things.js";

const DAY = new THREE.Color(0x9fc8ec), NIGHT = new THREE.Color(0x0a0e22), DUSK = new THREE.Color(0xd8805a);
const GREY = new THREE.Color(0x8a929a);
const sky = { dark: 0, target: 0, warm: 0, fog: 0, fogTarget: 0, weather: "", fresh: true };
let hemi, sun, fog, rain, snow, lamps = [];
const N_LAMPS = 6;

export function initAtmos(scene) {
  if (hemi) return;
  hemi = new THREE.HemisphereLight(0xdfefff, 0x5a4a30, 1.6);
  sun = new THREE.DirectionalLight(0xfff2d8, 1.8);
  sun.position.set(-0.6, 1, 0.35);
  scene.add(hemi, sun, sun.target);
  fog = new THREE.Fog(DAY.clone(), 12, 44);
  scene.fog = fog;
  scene.background = DAY.clone();
  for (let i = 0; i < N_LAMPS; i++) {
    const l = new THREE.PointLight(0xffa04a, 0, 7, 1.6);
    scene.add(l);
    lamps.push(l);
  }
  rain = particles(1600, 0x9ab8d8, 0.03);
  snow = particles(900, 0xffffff, 0.045);
  scene.add(rain.points, snow.points);
}

function particles(n, color, size) {
  const pos = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) pos.set([(Math.random() - 0.5) * 30, Math.random() * 14, (Math.random() - 0.5) * 30], i * 3);
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  const points = new THREE.Points(g, new THREE.PointsMaterial({ color, size, transparent: true, opacity: 0.8, depthWrite: false }));
  points.visible = false;
  points.frustumCulled = false;
  return { points, pos, n };
}

// Each frame from the server: what the sky should become.
export function atmosFrame(f, opts = {}) {
  const c = f.clock;
  sky.target = darkOf(c);
  sky.fogTarget = fogOf(c);
  sky.warm = c && c.light > 0 && c.light < 1 ? 1 - Math.abs(c.light - 0.5) * 2 : 0;
  if (opts.snap || sky.fresh) { sky.dark = sky.target; sky.fog = sky.fogTarget; sky.fresh = false; }
  sky.weather = (c && c.weather) || "";
}

// Every animation frame: ease toward it, and move the weather with the camera.
export function updateAtmos(scene, cam, t, dt) {
  sky.dark += (sky.target - sky.dark) * Math.min(1, dt / 900);
  sky.fog += (sky.fogTarget - sky.fog) * Math.min(1, dt / 2500);
  const day = 1 - sky.dark / 0.7;  // 1 at noon, ~0 at deep night
  const bg = scene.background;
  bg.copy(NIGHT).lerp(DAY, Math.max(0, day));
  if (sky.warm > 0.3) bg.lerp(DUSK, (sky.warm - 0.3) * 0.5 * Math.max(0.2, day));
  if (sky.fog > 0.05) bg.lerp(GREY, Math.min(0.7, sky.fog) * Math.max(0.3, day));
  fog.color.copy(bg);
  fog.near = 4 + 10 * (1 - sky.fog);
  fog.far = 16 + 30 * (1 - sky.fog * 0.75) * (0.55 + 0.45 * day);
  hemi.intensity = 0.25 + 1.4 * day;
  sun.intensity = 1.9 * Math.max(0, day - 0.1);
  sun.color.setHex(sky.warm > 0.5 ? 0xffb07a : 0xfff2d8);
  sun.position.set(cam.position.x - 6, cam.position.y + 10, cam.position.z + 3.5);
  sun.target.position.copy(cam.position);
  // warm light from the nearest fires and lit homes, once the light fails
  const near = lightSpots().map((s) => ({ ...s, d: (s.x - cam.position.x) ** 2 + (s.z - cam.position.z) ** 2 }))
    .sort((a, b) => a.d - b.d).slice(0, N_LAMPS);
  const night = Math.max(0, 1 - day * 1.6);
  lamps.forEach((l, i) => {
    const s = near[i];
    if (!s || night < 0.05) { l.intensity = 0; return; }
    const flick = s.fire && !reduceMotion ? 0.85 + 0.15 * Math.sin(t / 90 + i * 7) * Math.sin(t / 37 + i) : 1;
    l.position.set(s.x, 0.6, s.z);
    l.intensity = s.power * night * 2.2 * flick;
  });
  fall(rain, sky.weather === "rain" || sky.weather === "storm", cam, dt * (sky.weather === "storm" ? 0.016 : 0.012), 0.002 * dt);
  fall(snow, sky.weather === "snow", cam, dt * 0.0016, 0.0006 * dt);
}

function fall(p, on, cam, dy, dx) {
  p.points.visible = on && !reduceMotion;
  if (!p.points.visible) return;
  const a = p.pos;
  for (let i = 0; i < p.n; i++) {
    a[i * 3 + 1] -= dy * (0.8 + (i % 5) * 0.1);
    a[i * 3] -= dx;
    if (a[i * 3 + 1] < -1) { a[i * 3 + 1] += 15; a[i * 3] = (Math.random() - 0.5) * 30; }
  }
  p.points.geometry.attributes.position.needsUpdate = true;
  p.points.position.set(cam.position.x, 0, cam.position.z);
}

export function resetAtmos() { sky.fresh = true; }
