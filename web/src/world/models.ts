// Low-poly models built from primitives: peons, huts, work props, carried items.

import * as THREE from "three";
import type { Role } from "../types";

export const COLORS = {
  skin: 0x6fae45,
  skinDark: 0x4f8a2e,
  cloth: 0x7a4f2a,
  leather: 0x5a3a1e,
  boot: 0x3b2a1a,
  wood: 0x8a5a33,
  woodDark: 0x5a3820,
  thatch: 0xa8843f,
  thatchDark: 0x8a6a2f,
  stone: 0x7d7466,
  bone: 0xece2c6,
  metal: 0x9aa0a6,
  gold: 0xf2c23a,
  parchment: 0xf1e3b5,
  red: 0xb3261e,
};

export const ROLE_COLOR: Record<Role, number> = {
  chieftain: 0xb3261e,
  coder: 0xd9822b,
  reviewer: 0x8e6bd6,
  tester: 0x3a7bd5,
};

/** Size multiplier of each role's building. */
export const HUT_SCALE: Record<Role, number> = { chieftain: 1.6, coder: 1, reviewer: 1, tester: 1 };

const materials = new Map<string, THREE.MeshStandardMaterial>();

export function mat(
  color: number,
  opts: { emissive?: number; emissiveIntensity?: number; roughness?: number; metalness?: number } = {},
): THREE.MeshStandardMaterial {
  const key = `${color}|${opts.emissive ?? 0}|${opts.emissiveIntensity ?? 1}|${opts.roughness ?? 0.85}|${opts.metalness ?? 0}`;
  let m = materials.get(key);
  if (!m) {
    m = new THREE.MeshStandardMaterial({
      color,
      flatShading: true,
      roughness: opts.roughness ?? 0.85,
      metalness: opts.metalness ?? 0,
      emissive: opts.emissive ?? 0x000000,
      emissiveIntensity: opts.emissiveIntensity ?? 1,
    });
    materials.set(key, m);
  }
  return m;
}

function mesh(geo: THREE.BufferGeometry, material: THREE.Material, x = 0, y = 0, z = 0): THREE.Mesh {
  const m = new THREE.Mesh(geo, material);
  m.position.set(x, y, z);
  m.castShadow = true;
  m.receiveShadow = true;
  return m;
}

// --- peon ---

export interface PeonParts {
  root: THREE.Group;
  body: THREE.Group;
  head: THREE.Group;
  legL: THREE.Group;
  legR: THREE.Group;
  armL: THREE.Group;
  armR: THREE.Group;
  tool: THREE.Group;
  carry: THREE.Group;
}

export function buildPeon(role: Role): PeonParts {
  const root = new THREE.Group();
  const body = new THREE.Group();
  root.add(body);

  const leg = (x: number) => {
    const g = new THREE.Group();
    g.position.set(x, 0.42, 0);
    g.add(mesh(new THREE.BoxGeometry(0.15, 0.4, 0.17), mat(COLORS.leather), 0, -0.2, 0));
    g.add(mesh(new THREE.BoxGeometry(0.18, 0.09, 0.26), mat(COLORS.boot), 0, -0.4, 0.04));
    return g;
  };
  const legL = leg(-0.12);
  const legR = leg(0.12);
  root.add(legL, legR);

  body.add(mesh(new THREE.CylinderGeometry(0.28, 0.22, 0.5, 7), mat(COLORS.skin), 0, 0.68, 0));
  body.add(mesh(new THREE.CylinderGeometry(0.24, 0.25, 0.12, 7), mat(COLORS.cloth), 0, 0.46, 0));
  body.add(mesh(new THREE.BoxGeometry(0.24, 0.22, 0.05), mat(COLORS.cloth), 0, 0.34, 0.2));
  body.add(mesh(new THREE.BoxGeometry(0.08, 0.08, 0.04), mat(COLORS.metal, { metalness: 0.5, roughness: 0.4 }), 0, 0.46, 0.25));

  const head = new THREE.Group();
  head.position.set(0, 0.92, 0.05);
  body.add(head);
  const skull = mesh(new THREE.IcosahedronGeometry(0.22, 0), mat(COLORS.skin), 0, 0.15, 0);
  skull.scale.set(1.05, 0.95, 1);
  head.add(skull);
  head.add(mesh(new THREE.BoxGeometry(0.32, 0.13, 0.24), mat(COLORS.skinDark), 0, 0.03, 0.06));
  head.add(mesh(new THREE.BoxGeometry(0.3, 0.05, 0.08), mat(COLORS.skinDark), 0, 0.24, 0.16));
  for (const sx of [-1, 1]) {
    head.add(mesh(new THREE.ConeGeometry(0.035, 0.13, 5), mat(COLORS.bone), sx * 0.1, 0.13, 0.17));
    head.add(
      mesh(new THREE.BoxGeometry(0.06, 0.035, 0.02), mat(0xffd23f, { emissive: 0xffb000, emissiveIntensity: 0.7 }), sx * 0.085, 0.18, 0.2),
    );
    const ear = mesh(new THREE.ConeGeometry(0.065, 0.24, 4), mat(COLORS.skin), sx * 0.26, 0.17, -0.02);
    ear.rotation.z = -sx * Math.PI * 0.42;
    head.add(ear);
  }

  const arm = (sx: number) => {
    const g = new THREE.Group();
    g.position.set(sx * 0.33, 0.86, 0);
    g.add(mesh(new THREE.BoxGeometry(0.14, 0.42, 0.15), mat(COLORS.skin), 0, -0.2, 0));
    g.add(mesh(new THREE.BoxGeometry(0.15, 0.12, 0.16), mat(COLORS.skinDark), 0, -0.44, 0));
    return g;
  };
  const armL = arm(-1);
  const armR = arm(1);
  body.add(armL, armR);

  const tool = new THREE.Group();
  tool.position.set(0, -0.46, 0.02);
  armR.add(tool);
  buildTool(tool, role);

  const carry = new THREE.Group();
  carry.position.set(0, 0.62, 0.38);
  carry.visible = false;
  body.add(carry);

  addRoleGear(role, body, head);
  return { root, body, head, legL, legR, armL, armR, tool, carry };
}

function buildTool(g: THREE.Group, role: Role): void {
  const handle = (len: number) => {
    const h = mesh(new THREE.CylinderGeometry(0.025, 0.03, len, 5), mat(COLORS.woodDark), 0, 0, len / 2 - 0.05);
    h.rotation.x = Math.PI / 2;
    g.add(h);
  };
  const metal = mat(COLORS.metal, { metalness: 0.6, roughness: 0.35 });
  if (role === "reviewer") {
    const scroll = mesh(new THREE.CylinderGeometry(0.055, 0.055, 0.3, 6), mat(COLORS.parchment), 0, 0, 0.08);
    scroll.rotation.z = Math.PI / 2;
    g.add(scroll);
  } else if (role === "tester") {
    handle(0.5);
    g.add(mesh(new THREE.IcosahedronGeometry(0.1, 0), metal, 0, 0, 0.45)); // mace head
    for (const a of [0, 1, 2, 3]) {
      const spike = mesh(new THREE.ConeGeometry(0.025, 0.08, 4), metal, Math.cos(a * 1.57) * 0.1, Math.sin(a * 1.57) * 0.1, 0.45);
      spike.rotation.z = a * 1.57 - Math.PI / 2;
      g.add(spike);
    }
  } else if (role === "chieftain") {
    handle(0.75);
    g.add(mesh(new THREE.BoxGeometry(0.04, 0.34, 0.24), metal, 0, 0.08, 0.62)); // axe blade
  } else {
    handle(0.48);
    g.add(mesh(new THREE.BoxGeometry(0.13, 0.22, 0.13), metal, 0, 0, 0.44)); // hammer head
  }
}

function addRoleGear(role: Role, body: THREE.Group, head: THREE.Group): void {
  if (role === "chieftain") {
    const helmet = mesh(new THREE.CylinderGeometry(0.2, 0.24, 0.12, 7), mat(COLORS.metal, { metalness: 0.5, roughness: 0.4 }), 0, 0.3, 0);
    head.add(helmet);
    for (const sx of [-1, 1]) {
      const horn = mesh(new THREE.ConeGeometry(0.05, 0.3, 5), mat(COLORS.bone), sx * 0.24, 0.42, 0);
      horn.rotation.z = -sx * 0.6;
      head.add(horn);
      body.add(mesh(new THREE.IcosahedronGeometry(0.13, 0), mat(COLORS.metal, { metalness: 0.5, roughness: 0.4 }), sx * 0.3, 0.9, 0));
    }
    const cape = mesh(new THREE.BoxGeometry(0.5, 0.6, 0.04), mat(COLORS.red), 0, 0.6, -0.24);
    cape.rotation.x = 0.12;
    body.add(cape);
  } else if (role === "reviewer") {
    const glass = mat(0x222222);
    for (const sx of [-1, 1]) {
      const lens = mesh(new THREE.TorusGeometry(0.045, 0.012, 4, 10), glass, sx * 0.085, 0.18, 0.215);
      head.add(lens);
    }
    head.add(mesh(new THREE.ConeGeometry(0.22, 0.3, 6), mat(0x5b3f9a), 0, 0.4, 0)); // wizard-ish hood
  } else {
    const band = mesh(new THREE.TorusGeometry(0.2, 0.035, 4, 10), mat(ROLE_COLOR[role]), 0, 0.24, 0);
    band.rotation.x = Math.PI / 2;
    head.add(band);
  }
}

// --- carried items ---

export type Carry = "crate" | "gold" | "scroll";

export function buildCarry(kind: Carry): THREE.Group {
  const g = new THREE.Group();
  if (kind === "crate") {
    g.add(mesh(new THREE.BoxGeometry(0.42, 0.32, 0.32), mat(0xa0703a)));
    for (const y of [-0.12, 0.12]) g.add(mesh(new THREE.BoxGeometry(0.44, 0.05, 0.34), mat(COLORS.woodDark), 0, y, 0));
  } else if (kind === "gold") {
    g.add(mesh(new THREE.IcosahedronGeometry(0.2, 0), mat(0x7a5a33), 0, 0, 0));
    const coin = mat(COLORS.gold, { metalness: 0.7, roughness: 0.3, emissive: 0x6a4a00, emissiveIntensity: 0.4 });
    for (const [x, z] of [[-0.07, 0.04], [0.07, 0.02], [0, -0.05]]) {
      g.add(mesh(new THREE.CylinderGeometry(0.07, 0.07, 0.03, 8), coin, x, 0.18, z));
    }
  } else {
    const s = mesh(new THREE.CylinderGeometry(0.07, 0.07, 0.36, 6), mat(COLORS.parchment));
    s.rotation.z = Math.PI / 2;
    g.add(s);
    g.add(mesh(new THREE.CylinderGeometry(0.04, 0.04, 0.02, 6), mat(COLORS.red), 0, 0, 0.07));
  }
  return g;
}

// --- buildings ---

export function buildHut(role: Role): THREE.Group {
  const s = HUT_SCALE[role];
  const g = new THREE.Group();
  g.add(mesh(new THREE.CylinderGeometry(1.0 * s, 1.1 * s, 0.2, 9), mat(COLORS.stone), 0, 0.1, 0));
  g.add(mesh(new THREE.CylinderGeometry(0.9 * s, 0.95 * s, 0.9 * s, 9), mat(COLORS.wood), 0, 0.2 + 0.45 * s, 0));
  // Log bands around the wall.
  for (const y of [0.35, 0.75]) {
    g.add(mesh(new THREE.CylinderGeometry(0.97 * s, 0.97 * s, 0.07, 9), mat(COLORS.woodDark), 0, 0.2 + y * s, 0));
  }
  const roofBase = 0.2 + 0.9 * s;
  g.add(mesh(new THREE.ConeGeometry(1.3 * s, 1.0 * s, 9), mat(COLORS.thatch), 0, roofBase + 0.45 * s, 0));
  g.add(mesh(new THREE.ConeGeometry(0.75 * s, 0.75 * s, 9), mat(COLORS.thatchDark), 0, roofBase + 0.95 * s, 0));
  g.add(mesh(new THREE.ConeGeometry(0.06 * s, 0.5 * s, 5), mat(COLORS.bone), 0, roofBase + 1.5 * s, 0));

  // Bone spikes jutting out of the roof.
  for (let i = 0; i < 6; i++) {
    const a = (i / 6) * Math.PI * 2 + 0.3;
    const spike = mesh(new THREE.ConeGeometry(0.05 * s, 0.55 * s, 5), mat(COLORS.bone));
    spike.position.set(Math.cos(a) * 1.05 * s, roofBase + 0.15 * s, Math.sin(a) * 1.05 * s);
    spike.lookAt(Math.cos(a) * 3 * s, roofBase + 1.4 * s, Math.sin(a) * 3 * s);
    spike.rotateX(Math.PI / 2);
    g.add(spike);
  }

  // Door and frame facing +z.
  g.add(mesh(new THREE.BoxGeometry(0.44 * s, 0.62 * s, 0.1), mat(0x24160a), 0, 0.2 + 0.31 * s, 0.9 * s));
  for (const sx of [-1, 1]) {
    g.add(mesh(new THREE.BoxGeometry(0.07 * s, 0.7 * s, 0.12), mat(COLORS.bone), sx * 0.26 * s, 0.2 + 0.35 * s, 0.92 * s));
  }

  // Role banner.
  const pole = mesh(new THREE.CylinderGeometry(0.035, 0.04, 1.9 * s, 5), mat(COLORS.woodDark), 0.95 * s, 0.95 * s, 0.55 * s);
  g.add(pole);
  const flag = mesh(new THREE.BoxGeometry(0.02, 0.38 * s, 0.55 * s), mat(ROLE_COLOR[role]), 0.95 * s, 1.65 * s, 0.85 * s);
  flag.userData.sway = Math.random() * 10;
  g.add(flag);

  if (role === "chieftain") {
    // Great tusks arching over the door.
    for (const sx of [-1, 1]) {
      const tusk = mesh(new THREE.ConeGeometry(0.12, 1.4, 6), mat(COLORS.bone), sx * 0.75, 0.75, 1.45);
      tusk.rotation.set(0.5, 0, -sx * 0.45);
      g.add(tusk);
    }
  }
  return g;
}

/** Work prop each role stands at: anvil, training dummy, lectern, war table. */
export function buildProp(role: Role): THREE.Group {
  const g = new THREE.Group();
  if (role === "coder") {
    g.add(mesh(new THREE.CylinderGeometry(0.28, 0.32, 0.4, 7), mat(COLORS.woodDark), 0, 0.2, 0));
    g.add(mesh(new THREE.BoxGeometry(0.5, 0.18, 0.26), mat(0x4a4d52, { metalness: 0.6, roughness: 0.4 }), 0, 0.49, 0));
    g.add(mesh(new THREE.ConeGeometry(0.09, 0.25, 4), mat(0x4a4d52, { metalness: 0.6, roughness: 0.4 }), 0.33, 0.49, 0).rotateZ(-Math.PI / 2));
  } else if (role === "tester") {
    g.add(mesh(new THREE.CylinderGeometry(0.05, 0.06, 1.2, 5), mat(COLORS.woodDark), 0, 0.6, 0));
    g.add(mesh(new THREE.CylinderGeometry(0.2, 0.17, 0.5, 7), mat(0xc9a95a), 0, 0.85, 0));
    g.add(mesh(new THREE.IcosahedronGeometry(0.15, 0), mat(0xd9bf7a), 0, 1.25, 0));
    g.add(mesh(new THREE.BoxGeometry(0.6, 0.07, 0.07), mat(COLORS.woodDark), 0, 0.95, 0));
  } else if (role === "reviewer") {
    g.add(mesh(new THREE.CylinderGeometry(0.05, 0.08, 0.8, 5), mat(COLORS.woodDark), 0, 0.4, 0));
    const top = mesh(new THREE.BoxGeometry(0.5, 0.05, 0.38), mat(COLORS.wood), 0, 0.84, 0);
    top.rotation.x = -0.35;
    g.add(top);
    const book = mesh(new THREE.BoxGeometry(0.4, 0.04, 0.3), mat(COLORS.parchment), 0, 0.88, 0.01);
    book.rotation.x = -0.35;
    g.add(book);
  } else {
    g.add(mesh(new THREE.CylinderGeometry(0.75, 0.7, 0.12, 9), mat(COLORS.wood), 0, 0.62, 0));
    g.add(mesh(new THREE.CylinderGeometry(0.12, 0.2, 0.56, 6), mat(COLORS.woodDark), 0, 0.28, 0));
    g.add(mesh(new THREE.BoxGeometry(0.9, 0.02, 0.6), mat(COLORS.parchment), 0, 0.69, 0));
    for (const [x, z, c] of [[-0.2, 0.1, COLORS.red], [0.15, -0.1, 0x3a7bd5], [0.25, 0.15, 0xd9822b]] as const) {
      g.add(mesh(new THREE.ConeGeometry(0.05, 0.14, 4), mat(c), x, 0.77, z));
    }
  }
  return g;
}

export function buildTorch(): { group: THREE.Group; light: THREE.PointLight; flame: THREE.Mesh } {
  const group = new THREE.Group();
  group.add(mesh(new THREE.CylinderGeometry(0.05, 0.07, 1.3, 5), mat(COLORS.woodDark), 0, 0.65, 0));
  group.add(mesh(new THREE.CylinderGeometry(0.12, 0.08, 0.14, 6), mat(0x3a3a3a, { metalness: 0.5 }), 0, 1.33, 0));
  const flame = new THREE.Mesh(
    new THREE.ConeGeometry(0.1, 0.32, 5),
    new THREE.MeshBasicMaterial({ color: 0xffa040 }),
  );
  flame.position.y = 1.55;
  group.add(flame);
  const light = new THREE.PointLight(0xff8a3a, 5, 7, 2);
  light.position.y = 1.7;
  group.add(light);
  return { group, light, flame };
}
