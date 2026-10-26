// Procedural landscape: a flat dirt clearing for the village, rolling hills,
// forest, rocks and grass around it. Everything is flat-shaded low-poly.

import * as THREE from "three";

/** Deterministic PRNG so the map looks the same every launch. */
export function mulberry32(seed: number): () => number {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hash(x: number, y: number): number {
  const s = Math.sin(x * 127.1 + y * 311.7) * 43758.5453;
  return s - Math.floor(s);
}

function noise(x: number, y: number): number {
  const ix = Math.floor(x);
  const iy = Math.floor(y);
  const fx = x - ix;
  const fy = y - iy;
  const u = fx * fx * (3 - 2 * fx);
  const v = fy * fy * (3 - 2 * fy);
  const a = hash(ix, iy);
  const b = hash(ix + 1, iy);
  const c = hash(ix, iy + 1);
  const d = hash(ix + 1, iy + 1);
  return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
}

function fbm(x: number, y: number): number {
  let sum = 0;
  let amp = 0.5;
  let freq = 1;
  for (let i = 0; i < 4; i++) {
    sum += amp * noise(x * freq, y * freq);
    freq *= 2;
    amp *= 0.5;
  }
  return sum;
}

const CLEARING = { rx: 17, rz: 13, cz: 1 };

/** 0 at the village centre, 1 at the edge of the clearing. */
export function clearingDist(x: number, z: number): number {
  return Math.sqrt((x / CLEARING.rx) ** 2 + ((z - CLEARING.cz) / CLEARING.rz) ** 2);
}

export function heightAt(x: number, z: number): number {
  const hill = THREE.MathUtils.smoothstep(clearingDist(x, z), 1.05, 2.1);
  return hill * (0.3 + fbm(x * 0.07 + 3, z * 0.07 - 7) * 6);
}

const GRASS = [0x5f8f3a, 0x6a9a40, 0x568535, 0x72a446];

// Ground palette: colour comes from smooth noise so it varies gently across
// the map instead of flickering triangle to triangle.
const GRASS_DARK = new THREE.Color(0x4f7e30);
const GRASS_LIGHT = new THREE.Color(0x86b04c);
const MEADOW_DARK = new THREE.Color(0x6a9a3c);
const MEADOW_LIGHT = new THREE.Color(0x93bb57);
const DIRT_DARK = new THREE.Color(0x8a6a42);
const DIRT_LIGHT = new THREE.Color(0xae8c5c);
const ROCK = new THREE.Color(0x8d877c);

export function buildTerrain(): THREE.Group {
  const group = new THREE.Group();
  const rnd = mulberry32(42);
  group.add(buildGround(rnd));
  group.add(...buildForest(rnd));
  group.add(buildRocks(rnd));
  group.add(buildGrass(rnd));
  return group;
}

function buildGround(rnd: () => number): THREE.Mesh {
  const geo = new THREE.PlaneGeometry(170, 170, 120, 120);
  geo.rotateX(-Math.PI / 2);
  const p = geo.attributes.position;
  for (let i = 0; i < p.count; i++) p.setY(i, heightAt(p.getX(i), p.getZ(i)));

  const flat = geo.toNonIndexed();
  flat.computeVertexNormals();
  const fp = flat.attributes.position;
  const colors = new Float32Array(fp.count * 3);
  const c = new THREE.Color();
  for (let i = 0; i < fp.count; i += 3) {
    const cx = (fp.getX(i) + fp.getX(i + 1) + fp.getX(i + 2)) / 3;
    const cy = (fp.getY(i) + fp.getY(i + 1) + fp.getY(i + 2)) / 3;
    const cz = (fp.getZ(i) + fp.getZ(i + 1) + fp.getZ(i + 2)) / 3;
    const d = clearingDist(cx, cz);
    const n = fbm(cx * 0.12 + 11, cz * 0.12 - 5);
    const jitter = (rnd() - 0.5) * 0.02;
    // Trodden plaza around the town hall, fading into the meadow.
    const plaza = 1 - THREE.MathUtils.smoothstep(Math.hypot(cx / 6.5, (cz - 1.2) / 5) + (n - 0.45) * 0.5, 0.75, 1.05);
    if (cy > 4.4) {
      c.copy(ROCK).offsetHSL(0, 0, jitter);
    } else if (d < 1.05) {
      c.lerpColors(MEADOW_DARK, MEADOW_LIGHT, n);
      if (plaza > 0) c.lerp(new THREE.Color().lerpColors(DIRT_DARK, DIRT_LIGHT, n), plaza);
      c.offsetHSL(0, 0, jitter);
    } else {
      c.lerpColors(GRASS_DARK, GRASS_LIGHT, n).offsetHSL(0, 0, jitter);
    }
    for (let k = 0; k < 3; k++) colors.set([c.r, c.g, c.b], (i + k) * 3);
  }
  flat.setAttribute("color", new THREE.BufferAttribute(colors, 3));

  const ground = new THREE.Mesh(
    flat,
    new THREE.MeshStandardMaterial({ vertexColors: true, flatShading: true, roughness: 1 }),
  );
  ground.receiveShadow = true;
  ground.name = "ground";
  return ground;
}

function buildForest(rnd: () => number): THREE.InstancedMesh[] {
  type Spot = { x: number; y: number; z: number; s: number; rot: number; pine: boolean };
  const spots: Spot[] = [];
  for (let i = 0; spots.length < 520 && i < 8000; i++) {
    const x = (rnd() - 0.5) * 150;
    const z = (rnd() - 0.5) * 150;
    const d = clearingDist(x, z);
    if (d < 1.12 || (d < 1.45 && rnd() < 0.55)) continue;
    spots.push({ x, z, y: heightAt(x, z), s: 0.75 + rnd() * 0.85, rot: rnd() * Math.PI * 2, pine: rnd() < 0.62 });
  }
  const pines = spots.filter((s) => s.pine);
  const rounds = spots.filter((s) => !s.pine);

  const trunkGeo = new THREE.CylinderGeometry(0.12, 0.2, 1.1, 5).translate(0, 0.55, 0);
  const pineLow = new THREE.ConeGeometry(1.0, 1.7, 7).translate(0, 1.55, 0);
  const pineHigh = new THREE.ConeGeometry(0.72, 1.4, 7).translate(0, 2.4, 0);
  const roundGeo = new THREE.IcosahedronGeometry(1.0, 0).translate(0, 2.0, 0);
  const white = () => new THREE.MeshStandardMaterial({ color: 0xffffff, flatShading: true, roughness: 0.95 });

  const make = (geo: THREE.BufferGeometry, list: Spot[], tint: (i: number) => number, squash = 1) => {
    const mesh = new THREE.InstancedMesh(geo, white(), list.length);
    const dummy = new THREE.Object3D();
    const color = new THREE.Color();
    list.forEach((s, i) => {
      dummy.position.set(s.x, s.y - 0.05, s.z);
      dummy.rotation.set(0, s.rot, 0);
      dummy.scale.set(s.s, s.s * squash, s.s);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
      mesh.setColorAt(i, color.set(tint(i)));
    });
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    return mesh;
  };

  const pineGreens = [0x2f5e2a, 0x34692d, 0x2a5325, 0x3b7331];
  const leafGreens = [0x4f8a33, 0x5c9a3a, 0x6aa640, 0x7aa83c, 0x8f9f3a];
  return [
    make(trunkGeo, spots, () => 0x6b4a2b),
    make(pineLow, pines, (i) => pineGreens[i % pineGreens.length]),
    make(pineHigh, pines, (i) => pineGreens[(i + 1) % pineGreens.length]),
    make(roundGeo, rounds, (i) => leafGreens[i % leafGreens.length], 0.9),
  ];
}

function buildRocks(rnd: () => number): THREE.InstancedMesh {
  const count = 90;
  const mesh = new THREE.InstancedMesh(
    new THREE.DodecahedronGeometry(0.45, 0),
    new THREE.MeshStandardMaterial({ color: 0xffffff, flatShading: true, roughness: 0.9 }),
    count,
  );
  const dummy = new THREE.Object3D();
  const color = new THREE.Color();
  for (let i = 0; i < count; i++) {
    let x = 0;
    let z = 0;
    // Mostly around the clearing's rim, some deeper in the wilds.
    do {
      x = (rnd() - 0.5) * 90;
      z = (rnd() - 0.5) * 90;
    } while (clearingDist(x, z) < 0.85);
    const s = 0.4 + rnd() * 1.3;
    dummy.position.set(x, heightAt(x, z) + 0.05, z);
    dummy.rotation.set(rnd() * 3, rnd() * 3, rnd() * 3);
    dummy.scale.set(s, s * (0.5 + rnd() * 0.4), s);
    dummy.updateMatrix();
    mesh.setMatrixAt(i, dummy.matrix);
    mesh.setColorAt(i, color.set(rnd() < 0.5 ? 0x8d877c : 0x76726a));
  }
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

function buildGrass(rnd: () => number): THREE.InstancedMesh {
  const count = 1400;
  const mesh = new THREE.InstancedMesh(
    new THREE.ConeGeometry(0.07, 0.4, 3).translate(0, 0.2, 0),
    new THREE.MeshStandardMaterial({ color: 0xffffff, flatShading: true, roughness: 1 }),
    count,
  );
  const dummy = new THREE.Object3D();
  const color = new THREE.Color();
  for (let i = 0; i < count; i++) {
    let x = 0;
    let z = 0;
    let d = 0;
    do {
      x = (rnd() - 0.5) * 70;
      z = (rnd() - 0.5) * 60;
      d = clearingDist(x, z);
    } while (d < 0.72 || d > 2.4);
    const s = 0.6 + rnd() * 0.9;
    dummy.position.set(x, heightAt(x, z), z);
    dummy.rotation.set((rnd() - 0.5) * 0.5, rnd() * 3, (rnd() - 0.5) * 0.5);
    dummy.scale.set(s, s, s);
    dummy.updateMatrix();
    mesh.setMatrixAt(i, dummy.matrix);
    mesh.setColorAt(i, color.set(GRASS[i % GRASS.length]).offsetHSL(0, 0, 0.06));
  }
  mesh.receiveShadow = true;
  return mesh;
}
