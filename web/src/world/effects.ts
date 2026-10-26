// Short-lived visual effects: chips, smoke, coins, dust, flying scrolls, floating text.

import * as THREE from "three";
import { CSS2DObject } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import { COLORS } from "./models";

interface Effect {
  obj: THREE.Object3D;
  age: number;
  life: number;
  /** k runs 0..1 over the effect's life. */
  step(k: number, dt: number): void;
  material?: THREE.Material;
}

const GEO = {
  chip: new THREE.BoxGeometry(0.07, 0.07, 0.07),
  puff: new THREE.IcosahedronGeometry(0.2, 0),
  coin: new THREE.CylinderGeometry(0.08, 0.08, 0.025, 8),
  dust: new THREE.IcosahedronGeometry(0.14, 0),
  scroll: new THREE.CylinderGeometry(0.07, 0.07, 0.34, 6),
};

export class Effects {
  private items: Effect[] = [];

  constructor(private readonly scene: THREE.Scene) {}

  update(dt: number): void {
    for (const e of this.items) {
      e.age += dt;
      e.step(Math.min(1, e.age / e.life), dt);
    }
    const done = this.items.filter((e) => e.age >= e.life);
    for (const e of done) {
      this.scene.remove(e.obj);
      e.material?.dispose();
    }
    if (done.length) this.items = this.items.filter((e) => e.age < e.life);
  }

  /** Debris flying off a struck anvil or dummy. */
  chips(at: THREE.Vector3, palette = [0xc8a36a, 0x9a9a9a, 0xffd27a]): void {
    for (let i = 0; i < 6; i++) {
      const color = palette[i % palette.length];
      const material = new THREE.MeshStandardMaterial({
        color,
        emissive: i % 3 === 2 ? 0xff9a20 : 0x000000,
        flatShading: true,
        transparent: true,
      });
      const m = new THREE.Mesh(GEO.chip, material);
      m.position.copy(at);
      const vel = new THREE.Vector3((Math.random() - 0.5) * 2.6, 1.6 + Math.random() * 1.6, (Math.random() - 0.5) * 2.6);
      this.add(m, 0.6, material, (k, dt) => {
        vel.y -= 9 * dt;
        m.position.addScaledVector(vel, dt);
        m.rotation.x += dt * 8;
        material.opacity = 1 - k;
      });
    }
  }

  smoke(at: THREE.Vector3): void {
    const material = new THREE.MeshStandardMaterial({ color: 0x6a6a6a, flatShading: true, transparent: true, opacity: 0.6 });
    const m = new THREE.Mesh(GEO.puff, material);
    m.position.set(at.x + (Math.random() - 0.5) * 0.3, at.y, at.z + (Math.random() - 0.5) * 0.3);
    const drift = (Math.random() - 0.5) * 0.4;
    this.add(m, 1.8, material, (k, dt) => {
      m.position.y += dt * 1.1;
      m.position.x += dt * drift;
      m.scale.setScalar(1 + k * 2.4);
      material.opacity = 0.6 * (1 - k);
    });
  }

  coins(at: THREE.Vector3): void {
    for (let i = 0; i < 14; i++) {
      const material = new THREE.MeshStandardMaterial({
        color: COLORS.gold,
        metalness: 0.7,
        roughness: 0.3,
        emissive: 0x7a5200,
        transparent: true,
      });
      const m = new THREE.Mesh(GEO.coin, material);
      m.position.copy(at);
      const a = (i / 14) * Math.PI * 2;
      const vel = new THREE.Vector3(Math.cos(a) * 1.8, 3 + Math.random() * 1.5, Math.sin(a) * 1.8);
      this.add(m, 1.0, material, (k, dt) => {
        vel.y -= 9 * dt;
        m.position.addScaledVector(vel, dt);
        m.rotation.x += dt * 10;
        material.opacity = k < 0.7 ? 1 : 1 - (k - 0.7) / 0.3;
      });
    }
  }

  /** Puff of dust, used when something is built. */
  dust(at: THREE.Vector3, radius = 1.2): void {
    for (let i = 0; i < 16; i++) {
      const material = new THREE.MeshStandardMaterial({ color: 0xc9b48a, flatShading: true, transparent: true, opacity: 0.8 });
      const m = new THREE.Mesh(GEO.dust, material);
      const a = (i / 16) * Math.PI * 2;
      m.position.set(at.x + Math.cos(a) * radius * 0.6, 0.2, at.z + Math.sin(a) * radius * 0.6);
      const vel = new THREE.Vector3(Math.cos(a) * 2.2, 0.6, Math.sin(a) * 2.2);
      this.add(m, 0.8, material, (k, dt) => {
        m.position.addScaledVector(vel, dt);
        vel.multiplyScalar(1 - dt * 3);
        m.scale.setScalar(1 + k * 1.5);
        material.opacity = 0.8 * (1 - k);
      });
    }
  }

  /** A sealed order scroll arcing from one place to another. */
  flyScroll(from: THREE.Vector3, to: THREE.Vector3, seconds: number): void {
    const g = new THREE.Group();
    const s = new THREE.Mesh(GEO.scroll, new THREE.MeshStandardMaterial({ color: COLORS.parchment, flatShading: true }));
    s.rotation.z = Math.PI / 2;
    s.castShadow = true;
    const seal = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.04, 0.02, 6), new THREE.MeshStandardMaterial({ color: COLORS.red }));
    seal.position.z = 0.07;
    seal.rotation.x = Math.PI / 2;
    g.add(s, seal);
    const height = 2.5 + from.distanceTo(to) * 0.15;
    this.add(g, seconds, undefined, (k) => {
      const e = k < 0.5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2;
      g.position.lerpVectors(from, to, e);
      g.position.y += Math.sin(Math.PI * e) * height;
      g.rotation.y = k * Math.PI * 4;
    });
  }

  floatText(at: THREE.Vector3, text: string, className: string): void {
    const el = document.createElement("div");
    const inner = document.createElement("div");
    inner.className = `w-float ${className}`;
    inner.textContent = text;
    el.appendChild(inner);
    const obj = new CSS2DObject(el);
    obj.position.copy(at);
    this.add(obj, 1.6, undefined, () => {});
  }

  private add(obj: THREE.Object3D, life: number, material: THREE.Material | undefined, step: Effect["step"]): void {
    this.scene.add(obj);
    this.items.push({ obj, age: 0, life, step, material });
  }
}
