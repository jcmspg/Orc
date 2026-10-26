// One orc on the map: its model, labels, state animations and walking.

import * as THREE from "three";
import { CSS2DObject } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import type { Orc, OrcState } from "../types";
import type { Effects } from "./effects";
import { buildCarry, buildPeon, type Carry, type PeonParts } from "./models";

const BUBBLE: Partial<Record<OrcState, string>> = {
  thinking: "...",
  reviewing: "?",
  awaiting: "!",
  failed: "x",
};

interface Walk {
  phase: "out" | "pause" | "back";
  from: THREE.Vector3;
  to: THREE.Vector3;
  leg: number;
  pause: number;
  t: number;
  onArrive?: () => void;
}

export class Peon {
  readonly parts: PeonParts;
  readonly root: THREE.Group;
  private readonly scale: number;
  private readonly nameEl: HTMLDivElement;
  private readonly bubbleEl: HTMLDivElement;
  private readonly bubbleObj: CSS2DObject;
  private readonly voiceEl: HTMLDivElement;
  private readonly ring: THREE.Mesh;
  private state: OrcState = "idle";
  private pendingState: OrcState | null = null;
  private walk: Walk | null = null;
  private t = Math.random() * 10;
  private lastStrike = 0;
  private smokeTimer = 0;
  private heading: number;

  constructor(
    readonly orc: Orc,
    readonly home: THREE.Vector3,
    /** Direction the peon faces when at its station (towards its work prop). */
    private readonly facing: number,
    /** Where strikes land: the anvil, dummy, lectern or war table. */
    private readonly workPoint: THREE.Vector3,
    /** Where failure smoke rises from. */
    private readonly smokePoint: THREE.Vector3,
    private readonly fx: Effects,
  ) {
    this.scale = orc.role === "chieftain" ? 1.7 : 1.3;
    this.parts = buildPeon(orc.role);
    this.root = this.parts.root;
    this.root.scale.setScalar(this.scale);
    this.root.position.copy(home);
    this.heading = facing;
    this.root.rotation.y = facing;
    this.root.userData.orcId = orc.id;

    this.ring = new THREE.Mesh(
      new THREE.RingGeometry(0.42, 0.55, 32).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({ color: 0x7cfc00, transparent: true, opacity: 0.9, depthWrite: false }),
    );
    this.ring.position.y = 0.03;
    this.ring.visible = false;
    this.root.add(this.ring);

    this.nameEl = this.label("w-name", orc.name, 0).el;
    const bubble = this.label("w-bubble", "", 1.55);
    this.bubbleEl = bubble.el;
    this.bubbleObj = bubble.obj;
    this.voiceEl = this.label("w-voice", "", 1.95).el;
    this.applyState(orc.state);
  }

  setSelected(on: boolean): void {
    this.ring.visible = on;
    this.nameEl.classList.toggle("selected", on);
  }

  setState(state: OrcState): void {
    if (this.walk) this.pendingState = state;
    else this.applyState(state);
  }

  say(text: string): void {
    this.voiceEl.textContent = text;
    this.voiceEl.classList.remove("show");
    void this.voiceEl.offsetWidth; // restart the CSS animation
    this.voiceEl.classList.add("show");
  }

  /** Walk to `to` carrying something, call `onArrive`, then walk home. */
  startWalk(to: THREE.Vector3, seconds: number, carry: Carry, onArrive?: () => void): void {
    this.parts.carry.clear();
    this.parts.carry.add(buildCarry(carry));
    this.parts.carry.visible = true;
    this.parts.tool.visible = false;
    this.bubbleObj.visible = false;
    this.walk = {
      phase: "out",
      from: this.root.position.clone(),
      to: to.clone(),
      leg: seconds * 0.42,
      pause: seconds * 0.16,
      t: 0,
      onArrive,
    };
  }

  update(dt: number): void {
    this.t += dt;
    const p = this.parts;
    this.resetPose();

    if (this.walk) {
      this.updateWalk(dt);
    } else {
      this.heading = lerpAngle(this.heading, this.facing, 1 - Math.exp(-dt * 6));
      this.animateState(dt);
    }
    this.root.rotation.y = this.heading;
    if (this.ring.visible) this.ring.scale.setScalar(1 + Math.sin(this.t * 4) * 0.05);
    p.body.updateMatrixWorld();
  }

  private updateWalk(dt: number): void {
    const w = this.walk!;
    const p = this.parts;
    w.t += dt;
    if (w.phase === "pause") {
      if (w.t >= w.pause) {
        w.phase = "back";
        w.t = 0;
        w.from = this.root.position.clone();
        w.to = this.home.clone();
      }
      return;
    }
    const k = Math.min(1, w.t / w.leg);
    const e = k * k * (3 - 2 * k);
    this.root.position.lerpVectors(w.from, w.to, e);
    const dir = new THREE.Vector3().subVectors(w.to, w.from);
    if (dir.lengthSq() > 0.001) this.heading = lerpAngle(this.heading, Math.atan2(dir.x, dir.z), 1 - Math.exp(-dt * 10));

    const stride = Math.sin(this.t * 11);
    p.legL.rotation.x = stride * 0.6;
    p.legR.rotation.x = -stride * 0.6;
    p.body.position.y = Math.abs(stride) * 0.04;
    p.armL.rotation.x = -1.15; // both arms hold the load
    p.armR.rotation.x = -1.15;

    if (k < 1) return;
    if (w.phase === "out") {
      p.carry.visible = false;
      w.onArrive?.();
      w.phase = "pause";
      w.t = 0;
    } else {
      this.walk = null;
      p.tool.visible = true;
      this.root.position.copy(this.home);
      this.applyState(this.pendingState ?? this.state);
      this.pendingState = null;
    }
  }

  private animateState(dt: number): void {
    const p = this.parts;
    const t = this.t;
    switch (this.state) {
      case "idle":
        p.body.position.y = Math.sin(t * 2.2) * 0.015;
        p.armL.rotation.x = Math.sin(t * 1.1) * 0.06;
        p.armR.rotation.x = -Math.sin(t * 1.1) * 0.06;
        p.head.rotation.y = Math.sin(t * 0.45) * 0.45;
        break;
      case "thinking":
        p.head.rotation.z = 0.18;
        p.head.rotation.x = -0.15;
        p.armL.rotation.x = -2.0; // scratching its head
        p.armL.rotation.z = -0.5 + Math.sin(t * 9) * 0.08;
        p.body.position.y = Math.sin(t * 2) * 0.01;
        break;
      case "working": {
        const swing = 0.5 + 0.5 * Math.sin(t * 10);
        p.armR.rotation.x = -0.5 - 1.6 * swing;
        p.armL.rotation.x = -0.4;
        p.body.rotation.x = 0.08 + 0.06 * swing;
        const cycle = Math.floor((t * 10 + Math.PI / 2) / (Math.PI * 2));
        if (cycle !== this.lastStrike) {
          this.lastStrike = cycle;
          this.fx.chips(this.workPoint);
        }
        break;
      }
      case "reviewing":
        p.armR.rotation.x = -1.2;
        p.armL.rotation.x = -1.0;
        p.head.rotation.x = 0.3;
        p.head.rotation.y = Math.sin(t * 1.4) * 0.3;
        break;
      case "awaiting":
        p.body.position.y = Math.abs(Math.sin(t * 5)) * 0.07;
        p.armR.rotation.x = -2.8; // waving for attention
        p.armR.rotation.z = 0.3 + Math.sin(t * 9) * 0.35;
        break;
      case "failed":
        p.body.rotation.x = 0.35;
        p.head.rotation.x = 0.45;
        p.armL.rotation.x = 0.2;
        p.armR.rotation.x = 0.2;
        this.smokeTimer -= dt;
        if (this.smokeTimer <= 0) {
          this.smokeTimer = 0.22;
          this.fx.smoke(this.smokePoint);
        }
        break;
      case "walking":
        break;
    }
  }

  private applyState(state: OrcState): void {
    this.state = state;
    // CSS2DRenderer owns the holder's display style, so hide via the object.
    const glyph = BUBBLE[state];
    this.bubbleObj.visible = Boolean(glyph);
    this.bubbleEl.textContent = glyph ?? "";
    this.bubbleEl.className = `w-bubble s-${state}`;
  }

  private resetPose(): void {
    const p = this.parts;
    p.body.position.y = 0;
    p.body.rotation.set(0, 0, 0);
    p.head.rotation.set(0, 0, 0);
    p.legL.rotation.set(0, 0, 0);
    p.legR.rotation.set(0, 0, 0);
    p.armL.rotation.set(0, 0, -0.12);
    p.armR.rotation.set(0, 0, 0.12);
  }

  /** CSS labels follow the peon; the inner element is free to animate. */
  private label(className: string, text: string, y: number): { el: HTMLDivElement; obj: CSS2DObject } {
    const holder = document.createElement("div");
    const inner = document.createElement("div");
    inner.className = className;
    inner.textContent = text;
    holder.appendChild(inner);
    const obj = new CSS2DObject(holder);
    obj.position.set(0, y, 0);
    this.root.add(obj);
    return { el: inner, obj };
  }
}

function lerpAngle(a: number, b: number, k: number): number {
  let d = b - a;
  while (d > Math.PI) d -= Math.PI * 2;
  while (d < -Math.PI) d += Math.PI * 2;
  return a + d * k;
}
