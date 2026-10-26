// The 3D map: renderer, lighting, RTS camera, input and the link between
// board events and what the player sees.

import * as THREE from "three";
import { CSS2DRenderer } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import { board, onEvent, select } from "../store";
import type { Orc, OrcEvent, Point } from "../types";
import { Effects } from "./effects";
import { hudInsets, isTyping } from "./hud";
import { buildHut, buildProp, buildTorch, HUT_SCALE, mat } from "./models";
import { Peon } from "./Peon";
import { buildTerrain } from "./terrain";

/** Engine positions are in pixels; the world uses metres-ish units. */
const PX = 1 / 40;
const FOV = 32;
const PITCH = 0.92; // radians above the horizon
const MIN_DIST = 8;
const MAX_DIST = 75;

interface Rig {
  target: THREE.Vector3;
  dist: number;
  yaw: number;
}

interface Site {
  hut: THREE.Group;
  station: THREE.Vector3;
  facing: number;
  workPoint: THREE.Vector3;
  roofTop: THREE.Vector3;
}

export class World {
  private readonly renderer: THREE.WebGLRenderer;
  private readonly labels: CSS2DRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(FOV, 1, 0.5, 400);
  private readonly fx: Effects;
  private readonly clock = new THREE.Clock();
  private readonly raycaster = new THREE.Raycaster();
  private readonly boxEl: HTMLDivElement;
  private readonly resizeObserver: ResizeObserver;

  private rig: Rig = { target: new THREE.Vector3(0, 0, 1), dist: 30, yaw: 0 };
  private goal: Rig = { target: new THREE.Vector3(0, 0, 1), dist: 30, yaw: 0 };
  private userMoved = false;

  private peons = new Map<string, Peon>();
  private sites = new Map<string, Site>();
  private roads = new THREE.Group();
  private swaying: THREE.Object3D[] = [];
  private torches: { light: THREE.PointLight; flame: THREE.Mesh; seed: number }[] = [];
  private growing: { obj: THREE.Object3D; t: number; base: number }[] = [];

  private keys = new Set<string>();
  private drag: { button: number; x: number; y: number; boxing: boolean } | null = null;
  private raf = 0;
  private disposers: Array<() => void> = [];

  constructor(private readonly container: HTMLElement) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    this.renderer.domElement.className = "world-canvas";
    container.appendChild(this.renderer.domElement);

    this.labels = new CSS2DRenderer();
    this.labels.domElement.className = "world-labels";
    container.appendChild(this.labels.domElement);

    this.boxEl = document.createElement("div");
    this.boxEl.className = "select-box";
    container.appendChild(this.boxEl);

    this.fx = new Effects(this.scene);
    this.setupScene();

    const s = board.getState();
    for (const id of s.order) this.addOrc(s.orcs[id], false);
    this.drawRoads();
    this.syncSelection(s.selected);

    this.disposers.push(onEvent((e) => this.handle(e)));
    this.disposers.push(
      board.subscribe((next, prev) => {
        if (next.selected !== prev.selected) this.syncSelection(next.selected);
        if (next.edges !== prev.edges) this.drawRoads();
      }),
    );

    this.setupInput();
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(container);
    this.resize();
    requestAnimationFrame(() => this.fitVillage(true));
    this.loop();
  }

  dispose(): void {
    cancelAnimationFrame(this.raf);
    this.resizeObserver.disconnect();
    this.disposers.forEach((d) => d());
    this.renderer.dispose();
    this.container.replaceChildren();
  }

  // --- scene setup ---

  private setupScene(): void {
    const sky = 0xa9cde8;
    this.scene.background = new THREE.Color(sky);
    this.scene.fog = new THREE.Fog(sky, 55, 120);

    this.scene.add(new THREE.HemisphereLight(0xd6ecff, 0x5a6a33, 1.1));
    const sun = new THREE.DirectionalLight(0xfff0d4, 2.6);
    sun.position.set(-16, 26, 14);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    const sc = sun.shadow.camera;
    sc.left = -26;
    sc.right = 26;
    sc.top = 26;
    sc.bottom = -26;
    sc.near = 1;
    sc.far = 90;
    sun.shadow.bias = -0.0004;
    sun.shadow.normalBias = 0.03;
    this.scene.add(sun, sun.target);

    this.scene.add(buildTerrain());
    this.scene.add(this.roads);
  }

  // --- orcs and buildings ---

  private addOrc(orc: Orc, animate: boolean): void {
    if (this.peons.has(orc.id)) return;
    const site = this.buildSite(orc);
    const smokePoint = site.roofTop.clone().add(new THREE.Vector3(0.2, -0.3, 0.2));
    const peon = new Peon(orc, site.station, site.facing, site.workPoint, smokePoint, this.fx);
    this.scene.add(peon.root);
    this.peons.set(orc.id, peon);
    if (animate) {
      const peonScale = peon.root.scale.x;
      site.hut.scale.setScalar(0.01);
      peon.root.scale.setScalar(0.01);
      this.growing.push({ obj: site.hut, t: 0, base: 1 }, { obj: peon.root, t: -0.35, base: peonScale });
      this.fx.dust(toWorld(orc.hut), 1.4 * HUT_SCALE[orc.role]);
    }
  }

  private buildSite(orc: Orc): Site {
    const s = HUT_SCALE[orc.role];
    const base = toWorld(orc.hut);
    const hut = buildHut(orc.role);
    hut.position.copy(base);
    hut.userData.orcId = orc.id;
    hut.traverse((o) => {
      if (o.userData.sway !== undefined) this.swaying.push(o);
    });
    this.scene.add(hut);

    // The peon works in front of the door with its prop to its right.
    const station = base.clone().add(new THREE.Vector3(-0.35 * s, 0, 1.55 * s));
    const prop = buildProp(orc.role);
    const propPos = station.clone().add(new THREE.Vector3(orc.role === "chieftain" ? 1.3 : 0.75, 0, 0.15));
    prop.position.copy(propPos);
    prop.userData.orcId = orc.id;
    this.scene.add(prop);

    const workHeight = { coder: 0.6, tester: 0.95, reviewer: 0.9, chieftain: 0.75 }[orc.role];
    const facing = Math.atan2(propPos.x - station.x, propPos.z - station.z);

    if (orc.role === "chieftain") {
      for (const sx of [-1, 1]) {
        const torch = buildTorch();
        torch.group.position.copy(base).add(new THREE.Vector3(sx * 1.25 * s, 0, 1.05 * s));
        this.scene.add(torch.group);
        this.torches.push({ light: torch.light, flame: torch.flame, seed: Math.random() * 100 });
      }
    }

    const site: Site = {
      hut,
      station,
      facing,
      workPoint: propPos.clone().setY(workHeight),
      roofTop: base.clone().setY(0.2 + 2.4 * s),
    };
    this.sites.set(orc.id, site);
    return site;
  }

  private drawRoads(): void {
    for (const child of [...this.roads.children]) {
      this.roads.remove(child);
      (child as THREE.Mesh).geometry?.dispose();
    }
    const { orcs, edges } = board.getState();
    const dirt = mat(0x9a7448, { roughness: 1 });
    const stone = mat(0xd9c38a, { emissive: 0x5a4410, emissiveIntensity: 0.25 });

    for (const e of edges) {
      const a = orcs[e.from];
      const b = orcs[e.to];
      if (!a || !b) continue;
      const pa = toWorld(a.hut).add(new THREE.Vector3(0, 0, 1.3 * HUT_SCALE[a.role]));
      const pb = toWorld(b.hut).add(new THREE.Vector3(0, 0, 1.3 * HUT_SCALE[b.role]));
      const dir = new THREE.Vector3().subVectors(pb, pa);
      const len = dir.length();
      const angle = Math.atan2(dir.x, dir.z);

      if (e.kind === "command") {
        const road = new THREE.Mesh(new THREE.PlaneGeometry(0.9, len).rotateX(-Math.PI / 2), dirt);
        road.position.copy(pa).addScaledVector(dir, 0.5).setY(0.015);
        road.rotation.y = angle;
        road.receiveShadow = true;
        this.roads.add(road);
      } else {
        // Review routes: a trail of golden stepping stones, offset beside the road.
        const side = new THREE.Vector3(dir.z, 0, -dir.x).normalize().multiplyScalar(0.25);
        const geo = new THREE.CylinderGeometry(0.07, 0.09, 0.04, 6);
        for (let d = 0.6; d < len - 0.4; d += 0.75) {
          const st = new THREE.Mesh(geo, stone);
          st.position.copy(pa).addScaledVector(dir, d / len).add(side).setY(0.03);
          st.receiveShadow = true;
          this.roads.add(st);
        }
      }
    }
  }

  // --- events ---

  private handle(e: OrcEvent): void {
    switch (e.type) {
      case "orc.spawned":
        this.addOrc(e.orc, true);
        if (!this.userMoved) requestAnimationFrame(() => this.fitVillage(false));
        break;
      case "orc.state":
        this.peons.get(e.id)?.setState(e.state);
        break;
      case "orc.awaiting":
        this.peons.get(e.id)?.setState("awaiting");
        break;
      case "orc.say":
        this.peons.get(e.id)?.say(e.text);
        break;
      case "orders.sent": {
        const from = this.sites.get(e.from)?.roofTop;
        const to = this.sites.get(e.to)?.station;
        if (from && to) this.fx.flyScroll(from, to.clone().setY(0.9), e.durationMs / 1000);
        break;
      }
      case "crate.sent":
        this.peons.get(e.from)?.startWalk(this.visitorSpot(e.to), e.durationMs / 1000, e.label === "findings" ? "scroll" : "crate");
        break;
      case "gold.delivered": {
        const hall = this.sites.get(e.to);
        this.peons.get(e.from)?.startWalk(this.visitorSpot(e.to), e.durationMs / 1000, "gold", () => {
          if (!hall) return;
          const door = hall.hut.position.clone().add(new THREE.Vector3(0, 1.0, 1.6));
          this.fx.coins(door);
          this.fx.floatText(door.clone().setY(2.6), "+1 gold", "gold");
        });
        break;
      }
      default:
        break;
    }
  }

  /** Where a visitor stands near someone's station, spread out so they don't stack. */
  private visitorSpot(id: string): THREE.Vector3 {
    const st = this.sites.get(id)?.station ?? new THREE.Vector3();
    const side = Math.random() < 0.5 ? -1 : 1;
    return st.clone().add(new THREE.Vector3(side * (0.9 + Math.random() * 0.5), 0, 0.5 + Math.random() * 0.6));
  }

  private syncSelection(ids: string[]): void {
    const set = new Set(ids);
    for (const [id, p] of this.peons) p.setSelected(set.has(id));
  }

  // --- camera ---

  private applyRig(): void {
    const { target, dist, yaw } = this.rig;
    this.camera.position.set(
      target.x + dist * Math.cos(PITCH) * Math.sin(yaw),
      target.y + dist * Math.sin(PITCH),
      target.z + dist * Math.cos(PITCH) * Math.cos(yaw),
    );
    this.camera.lookAt(target);
  }

  /** Camera-relative ground axes. */
  private axes() {
    const yaw = this.goal.yaw;
    return {
      right: new THREE.Vector3(Math.cos(yaw), 0, -Math.sin(yaw)),
      away: new THREE.Vector3(-Math.sin(yaw), 0, -Math.cos(yaw)),
    };
  }

  /** Frame every hut inside the part of the window the HUD leaves free. */
  private fitVillage(snap: boolean): void {
    const pts = [...this.sites.values()].map((s) => s.hut.position);
    if (!pts.length) return;
    const { right, away } = this.axes();
    const xs = pts.map((p) => p.dot(right));
    const zs = pts.map((p) => p.dot(away));
    // Generous padding: roofs stick up on screen and peons stand in front of doors.
    const pad = 3.8;
    const minX = Math.min(...xs) - pad;
    const maxX = Math.max(...xs) + pad;
    const minZ = Math.min(...zs) - pad * 1.2;
    const maxZ = Math.max(...zs) + pad * 1.2;

    const W = this.container.clientWidth;
    const H = this.container.clientHeight;
    if (!W || !H) return;
    const inset = hudInsets(W, H);
    const freeW = Math.max(120, W - inset.left - inset.right);
    const freeH = Math.max(120, H - inset.top - inset.bottom);
    const tanHalf = Math.tan(THREE.MathUtils.degToRad(FOV / 2));
    const aspect = W / H;
    const distW = (maxX - minX) / (freeW / W) / (2 * tanHalf * aspect);
    const distH = ((maxZ - minZ) * Math.sin(PITCH)) / (freeH / H) / (2 * tanHalf);
    // +10% because the depth estimate ignores perspective foreshortening.
    const dist = THREE.MathUtils.clamp(Math.max(distW, distH) * 1.1, MIN_DIST, MAX_DIST);

    // Centre of the village, shifted so it lands in the middle of the free area.
    const centre = right.clone().multiplyScalar((minX + maxX) / 2).add(away.clone().multiplyScalar((minZ + maxZ) / 2));
    const perPx = (2 * dist * tanHalf) / H;
    const sx = inset.left + freeW / 2 - W / 2;
    const sy = inset.top + freeH / 2 - H / 2;
    const target = centre
      .addScaledVector(right, -sx * perPx)
      .addScaledVector(away, (sy * perPx) / Math.sin(PITCH));

    this.goal.target.copy(target);
    this.goal.dist = dist;
    if (snap) {
      this.rig.target.copy(target);
      this.rig.dist = dist;
    }
  }

  private resize(): void {
    const W = this.container.clientWidth;
    const H = this.container.clientHeight;
    if (!W || !H) return;
    this.renderer.setSize(W, H);
    this.labels.setSize(W, H);
    this.camera.aspect = W / H;
    this.camera.updateProjectionMatrix();
    // Let CSS media queries settle before measuring the HUD.
    requestAnimationFrame(() => {
      if (!this.userMoved) this.fitVillage(true);
    });
  }

  // --- input ---

  private setupInput(): void {
    const canvas = this.renderer.domElement;
    const on = <K extends keyof HTMLElementEventMap>(el: HTMLElement | Window, type: K, fn: (e: HTMLElementEventMap[K]) => void, opts?: AddEventListenerOptions) => {
      el.addEventListener(type, fn as EventListener, opts);
      this.disposers.push(() => el.removeEventListener(type, fn as EventListener));
    };

    on(canvas, "contextmenu", (e) => e.preventDefault());

    on(canvas, "pointerdown", (e) => {
      canvas.setPointerCapture(e.pointerId);
      this.drag = { button: e.button, x: e.clientX, y: e.clientY, boxing: false };
      if (e.button !== 0) this.userMoved = true;
    });

    on(canvas, "pointermove", (e) => {
      const d = this.drag;
      if (!d) {
        canvas.style.cursor = this.pick(e.clientX, e.clientY) ? "pointer" : "default";
        return;
      }
      if (d.button === 2) {
        const { right, away } = this.axes();
        const k = this.goal.dist * 0.0019;
        this.goal.target.addScaledVector(right, -e.movementX * k).addScaledVector(away, (e.movementY * k) / Math.sin(PITCH));
      } else if (d.button === 1) {
        this.goal.yaw -= e.movementX * 0.006;
      } else if (d.button === 0) {
        if (!d.boxing && Math.hypot(e.clientX - d.x, e.clientY - d.y) > 6) d.boxing = true;
        if (d.boxing) this.drawBox(d.x, d.y, e.clientX, e.clientY);
      }
    });

    on(canvas, "pointerup", (e) => {
      const d = this.drag;
      this.drag = null;
      if (!d || d.button !== 0) return;
      this.boxEl.style.display = "none";
      if (d.boxing) this.boxSelect(d.x, d.y, e.clientX, e.clientY, e.shiftKey);
      else this.clickSelect(e.clientX, e.clientY, e.shiftKey);
    });

    on(
      canvas,
      "wheel",
      (e) => {
        e.preventDefault();
        this.userMoved = true;
        this.goal.dist = THREE.MathUtils.clamp(this.goal.dist * (e.deltaY > 0 ? 1.12 : 0.89), MIN_DIST, MAX_DIST);
      },
      { passive: false },
    );

    on(window, "keydown", (e) => {
      if (isTyping()) return;
      this.keys.add(e.code);
      if (e.code === "Escape") select([]);
      if (e.code === "KeyH") {
        this.userMoved = false;
        this.goal.yaw = 0;
        this.fitVillage(false);
      }
    });
    on(window, "keyup", (e) => this.keys.delete(e.code));
    on(window, "blur", () => this.keys.clear());
  }

  private pick(x: number, y: number): string | null {
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((x - rect.left) / rect.width) * 2 - 1, -((y - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(ndc, this.camera);
    const targets = [...[...this.peons.values()].map((p) => p.root), ...[...this.sites.values()].map((s) => s.hut)];
    for (const hit of this.raycaster.intersectObjects(targets, true)) {
      let o: THREE.Object3D | null = hit.object;
      while (o && o.userData.orcId === undefined) o = o.parent;
      if (o) return o.userData.orcId as string;
    }
    return null;
  }

  private clickSelect(x: number, y: number, shift: boolean): void {
    const id = this.pick(x, y);
    const current = board.getState().selected;
    if (!id) {
      if (!shift) select([]);
    } else if (shift) {
      select(current.includes(id) ? current.filter((s) => s !== id) : [...current, id]);
    } else {
      select([id]);
    }
  }

  private drawBox(x0: number, y0: number, x1: number, y1: number): void {
    const rect = this.container.getBoundingClientRect();
    Object.assign(this.boxEl.style, {
      display: "block",
      left: `${Math.min(x0, x1) - rect.left}px`,
      top: `${Math.min(y0, y1) - rect.top}px`,
      width: `${Math.abs(x1 - x0)}px`,
      height: `${Math.abs(y1 - y0)}px`,
    });
  }

  private boxSelect(x0: number, y0: number, x1: number, y1: number, shift: boolean): void {
    const rect = this.renderer.domElement.getBoundingClientRect();
    const [minX, maxX] = [Math.min(x0, x1), Math.max(x0, x1)];
    const [minY, maxY] = [Math.min(y0, y1), Math.max(y0, y1)];
    const v = new THREE.Vector3();
    const inside = [...this.peons].filter(([, p]) => {
      v.copy(p.root.position).setY(0.6).project(this.camera);
      const sx = rect.left + ((v.x + 1) / 2) * rect.width;
      const sy = rect.top + ((1 - v.y) / 2) * rect.height;
      return sx >= minX && sx <= maxX && sy >= minY && sy <= maxY;
    });
    const ids = inside.map(([id]) => id);
    select(shift ? [...new Set([...board.getState().selected, ...ids])] : ids);
  }

  // --- frame loop ---

  private loop = (): void => {
    this.raf = requestAnimationFrame(this.loop);
    const dt = Math.min(this.clock.getDelta(), 0.05);
    const time = this.clock.elapsedTime;

    this.updateKeys(dt);
    const k = 1 - Math.exp(-dt * 9);
    this.rig.target.lerp(this.goal.target, k);
    this.rig.dist += (this.goal.dist - this.rig.dist) * k;
    this.rig.yaw += (this.goal.yaw - this.rig.yaw) * k;
    this.applyRig();

    for (const p of this.peons.values()) p.update(dt);
    for (const o of this.swaying) o.rotation.y = Math.sin(time * 2.2 + o.userData.sway) * 0.25;
    for (const t of this.torches) {
      const f = 0.85 + 0.15 * Math.sin(time * 13 + t.seed) + 0.08 * Math.sin(time * 31 + t.seed * 2);
      t.light.intensity = 5 * f;
      t.flame.scale.set(1, f, 1);
    }
    this.updateGrowing(dt);
    this.fx.update(dt);

    this.renderer.render(this.scene, this.camera);
    this.labels.render(this.scene, this.camera);
  };

  private updateKeys(dt: number): void {
    if (isTyping() || !this.keys.size) return;
    const { right, away } = this.axes();
    const speed = this.goal.dist * 0.9 * dt;
    const k = this.keys;
    const dx = (k.has("ArrowRight") || k.has("KeyD") ? 1 : 0) - (k.has("ArrowLeft") || k.has("KeyA") ? 1 : 0);
    const dz = (k.has("ArrowUp") || k.has("KeyW") ? 1 : 0) - (k.has("ArrowDown") || k.has("KeyS") ? 1 : 0);
    const rot = (k.has("KeyE") ? 1 : 0) - (k.has("KeyQ") ? 1 : 0);
    if (dx || dz || rot) this.userMoved = true;
    this.goal.target.addScaledVector(right, dx * speed).addScaledVector(away, dz * speed);
    this.goal.yaw += rot * dt * 1.6;
  }

  /** Buildings and peons pop up with a little overshoot when trained. */
  private updateGrowing(dt: number): void {
    for (const g of this.growing) {
      g.t += dt;
      const k = THREE.MathUtils.clamp(g.t / 0.6, 0, 1);
      const back = 1 + 2.7 * (k - 1) ** 3 + 1.7 * (k - 1) ** 2;
      g.obj.scale.setScalar(Math.max(0.01, back * g.base));
    }
    this.growing = this.growing.filter((g) => g.t < 0.6);
  }
}

function toWorld(p: Point): THREE.Vector3 {
  return new THREE.Vector3(p.x * PX, 0, p.y * PX);
}
