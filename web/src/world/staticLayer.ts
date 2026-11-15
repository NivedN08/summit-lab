// The parts of the lab that never move, drawn once at one canvas pixel per art pixel.

import { BLOCKS, CABINETS, H, MARGIN, PLANTS, ROOMS, TILE, W, type Room } from "./layout";
import { C, DEPT, shade } from "./palette";

export const OFF = MARGIN * TILE; // world origin inside the static canvas
export const STATIC_W = (W + MARGIN * 2) * TILE;
export const STATIC_H = (H + MARGIN * 2) * TILE;

type Ctx = CanvasRenderingContext2D;

function rand(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

function rect(ctx: Ctx, x: number, y: number, w: number, h: number, c: string) {
  ctx.fillStyle = c;
  ctx.fillRect(OFF + x, OFF + y, w, h);
}

export function buildStatic(): HTMLCanvasElement {
  const cv = document.createElement("canvas");
  cv.width = STATIC_W;
  cv.height = STATIC_H;
  const ctx = cv.getContext("2d")!;
  ctx.imageSmoothingEnabled = false;
  drawGround(ctx);
  for (const r of ROOMS) drawFloor(ctx, r);
  drawWalls(ctx);
  drawDecor(ctx);
  return cv;
}

// ---- Outside: moonlit snow on the summit ------------------------------------------------------

function drawGround(ctx: Ctx) {
  ctx.fillStyle = "#c3cce6";
  ctx.fillRect(0, 0, STATIC_W, STATIC_H);
  const rnd = rand(7);
  // Drifts: soft bands of lighter and darker snow.
  for (let i = 0; i < 420; i++) {
    const x = Math.floor(rnd() * STATIC_W);
    const y = Math.floor(rnd() * STATIC_H);
    const w = 6 + Math.floor(rnd() * 26);
    ctx.fillStyle = rnd() > 0.5 ? "#d3dbef" : "#b5bfdd";
    ctx.fillRect(x, y, w, 1 + Math.floor(rnd() * 2));
  }
  // Sparkles.
  for (let i = 0; i < 260; i++) {
    ctx.fillStyle = "#f4f7ff";
    ctx.fillRect(Math.floor(rnd() * STATIC_W), Math.floor(rnd() * STATIC_H), 1, 1);
  }
  // Rocks poking through.
  for (let i = 0; i < 26; i++) {
    const x = Math.floor(rnd() * STATIC_W);
    const y = Math.floor(rnd() * STATIC_H);
    if (x > OFF - 20 && x < OFF + W * TILE + 20 && y > OFF - 20 && y < OFF + H * TILE + 20) continue;
    const w = 6 + Math.floor(rnd() * 10);
    ctx.fillStyle = "#5c6696";
    ctx.fillRect(x, y, w, 4);
    ctx.fillStyle = "#7a84b4";
    ctx.fillRect(x + 1, y - 1, w - 3, 2);
    ctx.fillStyle = "#e9eef9";
    ctx.fillRect(x + 2, y - 2, w - 5, 1);
  }
  // Pines along the edges.
  for (let i = 0; i < 34; i++) {
    const edge = i % 4;
    let x = Math.floor(rnd() * STATIC_W);
    let y = Math.floor(rnd() * STATIC_H);
    if (edge === 0) y = 4 + Math.floor(rnd() * (OFF - 40));
    if (edge === 1) y = OFF + H * TILE + 26 + Math.floor(rnd() * (OFF - 50));
    if (edge === 2) x = 6 + Math.floor(rnd() * (OFF - 40));
    if (edge === 3) x = OFF + W * TILE + 24 + Math.floor(rnd() * (OFF - 46));
    if (edge === 2 && y > OFF + 13 * TILE && y < OFF + 19 * TILE) continue; // keep the path clear
    pine(ctx, x, y, 0.8 + rnd() * 0.5);
  }
  // A shoveled path to the lobby door on the west wall.
  const py = OFF + 15 * TILE;
  ctx.fillStyle = "#a9b3d3";
  ctx.fillRect(0, py - 2, OFF, TILE * 2 + 4);
  ctx.fillStyle = "#b9c2df";
  ctx.fillRect(0, py, OFF, TILE * 2);
  for (let x = 4; x < OFF - 8; x += 9) {
    ctx.fillStyle = "#9da8cb";
    ctx.fillRect(x, py + 9 + ((x / 9) % 2) * 9, 3, 2);
  }
  // Lamp posts beside the path.
  for (const x of [20, 62, 104]) {
    ctx.fillStyle = "#3a4170";
    ctx.fillRect(x, py - 18, 2, 16);
    ctx.fillStyle = C.lamp;
    ctx.fillRect(x - 1, py - 21, 4, 3);
  }
  // Shadow of the building on the snow.
  ctx.fillStyle = "#9ea9cc";
  ctx.fillRect(OFF + 6, OFF + H * TILE, W * TILE, 6);
  ctx.fillRect(OFF + W * TILE, OFF + 6, 6, H * TILE);
}

function pine(ctx: Ctx, x: number, y: number, s: number) {
  const h = Math.round(22 * s);
  ctx.fillStyle = "#5a4a48";
  ctx.fillRect(x, y + h - 3, 2, 4);
  for (let i = 0; i < h - 2; i++) {
    const w = Math.max(1, Math.round((i / (h - 2)) * 10 * s));
    ctx.fillStyle = i % 4 === 0 ? "#3f6b74" : "#2f5560";
    ctx.fillRect(x + 1 - Math.floor(w / 2), y + i, w, 1);
    if (i % 4 === 1) {
      ctx.fillStyle = "#e9eef9";
      ctx.fillRect(x + 1 - Math.floor(w / 2), y + i, Math.ceil(w / 2), 1);
    }
  }
}

// ---- Floors -------------------------------------------------------------------------------------

function drawFloor(ctx: Ctx, r: Room) {
  const x0 = r.x * TILE;
  const y0 = r.y * TILE;
  const w = r.w * TILE;
  const h = r.h * TILE;
  const rnd = rand(r.x * 31 + r.y * 7);
  switch (r.floor) {
    case "terrazzo": {
      rect(ctx, x0, y0, w, h, "#cdd3e2");
      for (let i = 0; i < (w * h) / 22; i++) {
        const c = ["#b8bfd2", "#e1e6f0", "#a9b0c8", "#d9c9b8"][Math.floor(rnd() * 4)];
        rect(ctx, x0 + Math.floor(rnd() * w), y0 + Math.floor(rnd() * h), 1, 1, c);
      }
      for (let x = x0 + 48; x < x0 + w; x += 64) rect(ctx, x, y0, 1, h, "#bcc3d6");
      break;
    }
    case "carpet": {
      rect(ctx, x0, y0, w, h, "#3c4778");
      for (let y = y0; y < y0 + h; y += 4)
        for (let x = x0 + ((y / 4) % 2) * 2; x < x0 + w; x += 4) rect(ctx, x, y, 1, 1, "#45528a");
      // A rug under the desk.
      rect(ctx, x0 + 2 * TILE, y0 + 3 * TILE + 8, 8 * TILE, 5 * TILE, "#7a2f45");
      rect(ctx, x0 + 2 * TILE + 3, y0 + 3 * TILE + 11, 8 * TILE - 6, 5 * TILE - 6, "#933a54");
      rect(ctx, x0 + 2 * TILE + 6, y0 + 3 * TILE + 14, 8 * TILE - 12, 5 * TILE - 12, "#7a2f45");
      for (let x = x0 + 2 * TILE + 10; x < x0 + 10 * TILE - 8; x += 8) {
        rect(ctx, x, y0 + 3 * TILE + 14, 2, 2, C.lamp);
        rect(ctx, x, y0 + 8 * TILE + 0, 2, 2, C.lamp);
      }
      break;
    }
    case "wood": {
      for (let y = y0; y < y0 + h; y += 4) {
        const base = ["#a8744a", "#9e6c44", "#b07c50"][Math.floor(rnd() * 3)];
        rect(ctx, x0, y, w, 4, base);
        rect(ctx, x0, y + 3, w, 1, "#86593a");
        for (let x = x0 + Math.floor(rnd() * 30); x < x0 + w; x += 24 + Math.floor(rnd() * 20))
          rect(ctx, x, y, 1, 3, "#86593a");
      }
      // A reading rug.
      rect(ctx, x0 + 3 * TILE, y0 + 6 * TILE, 10 * TILE, 1 * TILE + 8, "#3e6a5e");
      rect(ctx, x0 + 3 * TILE + 2, y0 + 6 * TILE + 2, 10 * TILE - 4, TILE + 4, "#4b7e70");
      break;
    }
    case "tile":
    case "lino": {
      const tint = r.id === "team-a" ? ["#d6e2f8", "#c1d0ef"] : r.id === "team-b" ? ["#f3dde8", "#e4c6d5"]
        : r.id === "team-c" ? ["#e2dcf6", "#cdc2ee"] : ["#dde0ea", "#c9cddb"];
      rect(ctx, x0, y0, w, h, tint[0]);
      const step = r.floor === "tile" ? 16 : 32;
      for (let x = x0; x < x0 + w; x += step) rect(ctx, x, y0, 1, h, tint[1]);
      for (let y = y0; y < y0 + h; y += 16) rect(ctx, x0, y, w, 1, tint[1]);
      for (let i = 0; i < (w * h) / 90; i++)
        rect(ctx, x0 + Math.floor(rnd() * w), y0 + Math.floor(rnd() * h), 1, 1, shade(tint[0], 0.35));
      break;
    }
    case "rubber": {
      rect(ctx, x0, y0, w, h, "#2e5a52");
      for (let y = y0 + 2; y < y0 + h; y += 6)
        for (let x = x0 + 2 + ((y / 6) % 2) * 3; x < x0 + w; x += 6) rect(ctx, x, y, 2, 2, "#37695f");
      // A carpet runner to the evidence table.
      rect(ctx, x0 + 4 * TILE, y0 + 5 * TILE, 4 * TILE, 8 * TILE, "#24473f");
      break;
    }
    case "steel": {
      rect(ctx, x0, y0, w, h, "#555c7c");
      for (let y = y0; y < y0 + h; y += 16)
        for (let x = x0; x < x0 + w; x += 32) {
          rect(ctx, x, y, 32, 1, "#454b68");
          rect(ctx, x, y, 1, 16, "#454b68");
          rect(ctx, x + 3, y + 3, 1, 1, "#727a9c");
          rect(ctx, x + 28, y + 12, 1, 1, "#727a9c");
        }
      break;
    }
    case "stone": {
      rect(ctx, x0, y0, w, h, "#a7aec5");
      for (let y = y0; y < y0 + h; y += 24) {
        rect(ctx, x0, y, w, 1, "#8f97b2");
        for (let x = x0 + ((y / 24) % 2) * 20; x < x0 + w; x += 40) rect(ctx, x, y, 1, 24, "#8f97b2");
      }
      // Welcome mat at the door.
      rect(ctx, x0 + w - 18, y0 + 13 * TILE + 4, 18, 4 * TILE - 8, "#3a4170");
      break;
    }
  }
}

// ---- Walls --------------------------------------------------------------------------------------

const FACE_COLORS: Record<string, [string, string]> = {
  lobby: ["#b9c2dc", "#9ea8c8"],
  director: ["#5b4a6e", "#4a3c5c"],
  library: ["#8a5b3c", "#71492f"],
  "team-a": ["#c3d1ef", "#a6b8e0"],
  "team-b": ["#efcfdd", "#dcb2c5"],
  referee: ["#2b4a45", "#223b37"],
  "team-c": ["#d6cdef", "#bdb1e2"],
  vault: ["#444a69", "#383d58"],
  solo: ["#c9cfe0", "#b0b7cd"],
};

function drawWalls(ctx: Ctx) {
  // Room faces: the two rows below each room's top wall.
  for (const r of ROOMS) {
    if (r.id === "corridor") continue;
    const [face, faceS] = FACE_COLORS[r.id];
    const x0 = r.x * TILE;
    const y0 = r.y * TILE;
    rect(ctx, x0, y0, r.w * TILE, TILE * 2, face);
    rect(ctx, x0, y0 + TILE * 2 - 4, r.w * TILE, 4, faceS); // baseboard
    rect(ctx, x0, y0 + TILE * 2 - 1, r.w * TILE, 1, shade(faceS, -0.25));
    rect(ctx, x0, y0, r.w * TILE, 2, shade(face, 0.18));
  }
  // The corridor sees the short face of the wall above it.
  rect(ctx, 9 * TILE, 14 * TILE, 59 * TILE, 5, C.wallFace);
  rect(ctx, 9 * TILE, 14 * TILE + 5, 59 * TILE, 1, C.wallTrim);
  rect(ctx, 1 * TILE, 1 * TILE, 7 * TILE, 2 * TILE, FACE_COLORS.lobby[0]);

  // Wall caps: every tile not inside a room and inside the building.
  const inside = new Uint8Array(W * H);
  for (const r of ROOMS)
    for (let j = r.y; j < r.y + r.h; j++) for (let i = r.x; i < r.x + r.w; i++) inside[j * W + i] = 1;
  for (let i = 8; i < 9; i++) for (let j = 14; j < 18; j++) inside[j * W + i] = 1; // lobby opening
  for (const r of ROOMS) {
    if (r.door === undefined) continue;
    const wy = r.doorSide === "bottom" ? r.y + r.h : r.y - 1;
    inside[wy * W + r.door] = 2;
    inside[wy * W + r.door + 1] = 2;
  }
  inside[1 * W + 4] = 3; // elevator shaft opening
  for (let j = 0; j < H; j++)
    for (let i = 0; i < W; i++) {
      const v = inside[j * W + i];
      if (v === 1 || v === 3) continue;
      if (v === 2) {
        // Door: a threshold strip.
        rect(ctx, i * TILE, j * TILE, TILE, TILE, "#8c96bd");
        rect(ctx, i * TILE, j * TILE + 6, TILE, 4, "#6f78a0");
        continue;
      }
      const blind = i === 53 && j >= 0 && j <= 13;
      rect(ctx, i * TILE, j * TILE, TILE, TILE, blind ? "#1a1f3f" : C.wallCap);
      rect(ctx, i * TILE, j * TILE, TILE, 1, blind ? "#2a3060" : C.wallCapHi);
      if (blind) {
        // The blind wall: thicker look with hazard ticks on its top.
        for (let k = 2; k < TILE; k += 6) rect(ctx, i * TILE + 6, j * TILE + k, 4, 3, "#f2c14e");
      }
    }
  // Door frames.
  for (const r of ROOMS) {
    if (r.door === undefined) continue;
    const wy = r.doorSide === "bottom" ? r.y + r.h : r.y - 1;
    const c = r.id in DEPT ? DEPT[r.id as keyof typeof DEPT] : C.metal;
    rect(ctx, r.door * TILE - 2, wy * TILE, 2, TILE, c);
    rect(ctx, (r.door + 2) * TILE, wy * TILE, 2, TILE, c);
  }
}

// ---- Things on walls and floors that do not move -------------------------------------------

function drawDecor(ctx: Ctx) {
  const T = TILE;
  // Lobby: the elevator and the reception.
  rect(ctx, 3 * T, 1 * T + 2, 3 * T, 2 * T - 6, "#6f78a0");
  rect(ctx, 3 * T + 4, 1 * T + 6, 3 * T - 8, 2 * T - 10, "#a2a9c8");
  rect(ctx, 4 * T + 7, 1 * T + 6, 2, 2 * T - 10, "#6f78a0");
  rect(ctx, 4 * T + 4, 1 * T + 1, 8, 3, C.lamp);
  rect(ctx, 2 * T, 9 * T - 2, 5 * T, 6, "#d7dcea");
  rect(ctx, 2 * T, 9 * T + 4, 5 * T, 10, "#7a5136");
  rect(ctx, 2 * T + 2, 9 * T + 6, 5 * T - 4, 2, "#a8744a");
  // Coffee machine and water cooler.
  rect(ctx, 1 * T + 1, 20 * T - 6, 12, 18, "#2b2f55");
  rect(ctx, 1 * T + 3, 20 * T - 3, 8, 5, "#ef5b5b");
  rect(ctx, 1 * T + 5, 20 * T + 5, 4, 4, "#fbfcff");
  rect(ctx, 1 * T + 1, 23 * T - 8, 10, 20, "#d7dcea");
  rect(ctx, 1 * T + 2, 23 * T - 14, 8, 8, "#8fd3e8");
  // Benches.
  rect(ctx, 6 * T, 24 * T, 12, 3 * T, "#7a5136");
  rect(ctx, 6 * T + 2, 24 * T + 2, 8, 3 * T - 4, "#a8744a");

  // Director: window over the mountains, bookshelf, sofa, plant.
  const wx = 12 * T;
  rect(ctx, wx, 1 * T + 2, 6 * T, 2 * T - 9, "#1c2346");
  for (let i = 0; i < 18; i++) rect(ctx, wx + 3 + ((i * 37) % (6 * T - 6)), 1 * T + 4 + ((i * 13) % 9), 1, 1, "#eef2fa");
  ctx.fillStyle = "#3b4a7c";
  for (let x = 0; x < 6 * T; x++) {
    const hgt = 5 + Math.round(4 * Math.abs(Math.sin(x / 9)) + 3 * Math.abs(Math.sin(x / 4.3)));
    ctx.fillRect(OFF + wx + x, OFF + 1 * T + 2 + (2 * T - 9) - hgt, 1, hgt);
  }
  rect(ctx, wx - 2, 1 * T + 1, 6 * T + 4, 2, "#8c96bd");
  rect(ctx, wx + 3 * T - 1, 1 * T + 2, 2, 2 * T - 9, "#8c96bd");
  shelf(ctx, 9 * T + 4, 1 * T + 2, 2 * T, 7);
  rect(ctx, 10 * T, 9 * T, 2 * T, 2 * T, "#3d2f52");
  rect(ctx, 10 * T + 2, 9 * T - 3, 2 * T - 4, 6, "#57466f");
  rect(ctx, 10 * T + 2, 9 * T + 6, 2 * T - 4, 2 * T - 10, "#4c3d63");
  // Director's whiteboard: the lab's rule.
  rect(ctx, 18 * T + 6, 1 * T + 3, 2 * T + 4, 2 * T - 10, "#f4f6fb");
  rect(ctx, 18 * T + 8, 1 * T + 6, 20, 1, "#4c7cf2");
  rect(ctx, 18 * T + 8, 1 * T + 10, 14, 1, "#ea5f9d");
  rect(ctx, 18 * T + 8, 1 * T + 14, 24, 1, "#2fbf8f");

  // Library: shelves along the whole wall, and the web search terminal.
  shelf(ctx, 22 * T + 2, 1 * T + 2, 16 * T - 4, 1);
  rect(ctx, 35 * T, 3 * T, 2 * T, 3 * T - 2, "#2b2f55");
  rect(ctx, 35 * T + 2, 3 * T + 2, 2 * T - 4, T + 2, "#1c2346");
  rect(ctx, 35 * T + 4, 5 * T + 2, 2 * T - 8, 6, "#3a4170");

  // Team A and Team B: whiteboards and the comparison hatch.
  for (const x0 of [39, 54]) {
    rect(ctx, (x0 + 1) * T, 1 * T + 3, 4 * T, 2 * T - 10, "#f4f6fb");
    rect(ctx, (x0 + 1) * T, 1 * T + 2, 4 * T, 1, "#8c96bd");
    const ink = x0 === 39 ? "#4c7cf2" : "#ea5f9d";
    for (let k = 0; k < 4; k++) rect(ctx, (x0 + 1) * T + 4, 1 * T + 6 + k * 4, 18 + ((k * 13) % 30), 1, ink);
    rect(ctx, (x0 + 6) * T, 1 * T + 3, 3 * T, 2 * T - 10, "#e6eaf4");
    for (let k = 0; k < 6; k++) rect(ctx, (x0 + 6) * T + 4 + k * 7, 1 * T + 6, 5, 6, ["#ffe08a", "#b7cbfa", "#f6bdd6"][k % 3]);
    shelf(ctx, (x0 + 10) * T, 1 * T + 2, 3 * T, 3);
  }
  // Hatch trays on both sides of the blind wall.
  rect(ctx, 52 * T + 6, 6 * T - 2, 10, 20, "#4d5478");
  rect(ctx, 54 * T, 6 * T - 2, 10, 20, "#4d5478");
  rect(ctx, 52 * T + 8, 6 * T, 6, 16, "#a2a9c8");
  rect(ctx, 54 * T + 2, 6 * T, 6, 16, "#a2a9c8");

  // Referee: the bench's back panel and the evidence table.
  rect(ctx, 10 * T, 19 * T + 2, 10 * T, 2 * T - 6, "#1f3934");
  rect(ctx, 14 * T, 19 * T + 4, 2 * T, 2 * T - 10, "#2fbf8f");
  rect(ctx, 14 * T + 6, 19 * T + 7, 20, 2, "#eef2fa");
  rect(ctx, 14 * T + 14, 19 * T + 9, 4, 8, "#eef2fa");
  rect(ctx, 13 * T, 26 * T, 4 * T, 2 * T, "#7a5136");
  rect(ctx, 13 * T + 2, 26 * T + 2, 4 * T - 4, 2 * T - 8, "#a8744a");
  rect(ctx, 13 * T + 6, 26 * T + 6, 12, 14, "#fbfcff");
  rect(ctx, 15 * T + 10, 26 * T + 6, 12, 14, "#fbfcff");

  // Team C: a poster and shelf.
  rect(ctx, 23 * T, 19 * T + 3, 3 * T, 2 * T - 10, "#f4f6fb");
  shelf(ctx, 30 * T, 19 * T + 2, 4 * T, 5);

  // Vault: racks on the wall, cabinets on the floor.
  for (let x = 36 * T + 4; x < 49 * T - 12; x += 14) {
    rect(ctx, x, 19 * T + 2, 12, 2 * T - 6, "#2b2f55");
    for (let y = 19 * T + 5; y < 21 * T - 6; y += 4) rect(ctx, x + 2, y, 8, 2, "#3a4170");
  }
  for (const c of CABINETS) {
    rect(ctx, c.x * T, c.y * T - 6, 2 * T, 2 * T + 6, "#7b83a6");
    rect(ctx, c.x * T, c.y * T - 6, 2 * T, 2, "#a2a9c8");
    rect(ctx, c.x * T + 2, c.y * T - 2, 2 * T - 4, 2 * T, "#636b90");
    for (let d = 0; d < 5; d++) {
      rect(ctx, c.x * T + 3, c.y * T - 1 + d * 6, 2 * T - 6, 5, "#8a92b4");
      rect(ctx, c.x * T + 14, c.y * T + 1 + d * 6, 4, 1, "#4d5478");
    }
  }
  // The gateway arch at the vault's door.
  rect(ctx, 41 * T - 3, 19 * T, 3, 2 * T, "#2fbf8f");
  rect(ctx, 43 * T, 19 * T, 3, 2 * T, "#2fbf8f");
  rect(ctx, 41 * T - 3, 19 * T, 2 * T + 6, 3, "#2fbf8f");

  // Solo agents: a scoreboard.
  rect(ctx, 51 * T, 19 * T + 3, 6 * T, 2 * T - 10, "#1c2346");
  rect(ctx, 60 * T, 19 * T + 3, 6 * T, 2 * T - 10, "#1c2346");

  // Furniture blocks that are just wood (the reception and table are drawn above).
  void BLOCKS;
  for (const p of PLANTS) plant(ctx, p.x * T, p.y * T, p.kind);
}

function shelf(ctx: Ctx, x: number, y: number, w: number, seed: number) {
  const rnd = rand(seed * 99);
  rect(ctx, x, y, w, 2 * TILE - 6, "#5a3a26");
  for (let row = 0; row < 2; row++) {
    const sy = y + 2 + row * 11;
    rect(ctx, x + 1, sy + 9, w - 2, 2, "#3f281a");
    for (let bx = x + 2; bx < x + w - 3; ) {
      const bw = 2 + Math.floor(rnd() * 3);
      const bh = 6 + Math.floor(rnd() * 3);
      const col = ["#4c7cf2", "#ea5f9d", "#ee8a3c", "#2fbf8f", "#f2c14e", "#9b6cf2", "#eef2fa", "#c0393b"][Math.floor(rnd() * 8)];
      rect(ctx, bx, sy + 9 - bh, bw, bh, col);
      rect(ctx, bx, sy + 9 - bh, 1, bh, shade(col, -0.3));
      bx += bw + (rnd() > 0.85 ? 3 : 0);
    }
  }
}

function plant(ctx: Ctx, x: number, y: number, kind: number) {
  rect(ctx, x + 4, y + 9, 8, 7, C.pot);
  rect(ctx, x + 4, y + 9, 8, 2, shade(C.pot, 0.2));
  rect(ctx, x + 10, y + 11, 2, 5, C.potDark);
  const leaves = [
    [[7, 0, 2, 9], [4, 2, 3, 2], [9, 3, 3, 2], [3, 5, 3, 2], [10, 6, 3, 2]],
    [[5, 1, 6, 8], [3, 3, 10, 4], [6, 0, 4, 2]],
    [[7, -2, 2, 11], [5, 1, 2, 7], [9, 0, 2, 8], [3, 4, 2, 4], [11, 3, 2, 5]],
  ][kind % 3];
  for (const [lx, ly, lw, lh] of leaves) rect(ctx, x + lx, y + ly, lw, lh, C.leaf);
  rect(ctx, x + 7, y + 1, 1, 7, C.leafDark);
  rect(ctx, x + 6, y + 2, 1, 2, C.leafHi);
}
