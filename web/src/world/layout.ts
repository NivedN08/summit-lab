// The floor plan of the lab, in 16-pixel tiles. Rooms list their inner floor; walls sit one tile
// outside. The two top rows of every room are its back wall seen from the front (3/4 view), where
// shelves, windows and boards hang. Team A and Team B share a wall with no door: the only thing
// that passes through it is the comparison hatch, which the lab's code operates, not the agents.

import type { Role } from "../data/types";

export const TILE = 16;
export const W = 69; // building width in tiles, walls included
export const H = 34;
export const MARGIN = 7; // snowy ground drawn around the building

export type RoomId = "lobby" | "director" | "library" | "team-a" | "team-b" | "referee" | "team-c" | "vault" | "solo" | "corridor";

export type Room = {
  id: RoomId;
  name: string;
  x: number;
  y: number;
  w: number;
  h: number;
  floor: "terrazzo" | "carpet" | "wood" | "tile" | "rubber" | "steel" | "lino" | "stone";
  /** Door tiles in the wall shared with the corridor: x of the first of two tiles. */
  door?: number;
  doorSide?: "bottom" | "top";
};

export const ROOMS: Room[] = [
  { id: "lobby", name: "Lobby", x: 1, y: 1, w: 7, h: 32, floor: "stone" },
  { id: "director", name: "Director's office", x: 9, y: 1, w: 12, h: 12, floor: "carpet", door: 14, doorSide: "bottom" },
  { id: "library", name: "Library", x: 22, y: 1, w: 16, h: 12, floor: "wood", door: 29, doorSide: "bottom" },
  { id: "team-a", name: "Team A", x: 39, y: 1, w: 14, h: 12, floor: "tile", door: 45, doorSide: "bottom" },
  { id: "team-b", name: "Team B", x: 54, y: 1, w: 14, h: 12, floor: "lino", door: 60, doorSide: "bottom" },
  { id: "corridor", name: "Corridor", x: 9, y: 14, w: 59, h: 4, floor: "terrazzo" },
  { id: "referee", name: "Referee", x: 9, y: 19, w: 12, h: 14, floor: "rubber", door: 14, doorSide: "top" },
  { id: "team-c", name: "Team C", x: 22, y: 19, w: 13, h: 14, floor: "tile", door: 27, doorSide: "top" },
  { id: "vault", name: "Memory vault", x: 36, y: 19, w: 13, h: 14, floor: "steel", door: 41, doorSide: "top" },
  { id: "solo", name: "Solo agents", x: 50, y: 19, w: 18, h: 14, floor: "lino", door: 57, doorSide: "top" },
];

export const roomById = Object.fromEntries(ROOMS.map((r) => [r.id, r])) as Record<RoomId, Room>;

export type Spot = { tx: number; ty: number; px: number; py: number };

/** A seat: the tile a person stands on (for paths) and the pixel point of their feet. */
function seat(tx: number, ty: number, dx = 0): Spot {
  return { tx, ty, px: tx * TILE + TILE / 2 + dx, py: ty * TILE + TILE - 1 };
}

export type Desk = { x: number; y: number; w: number; kind: "desk" | "small" | "bench" | "big" };

/** Two rows of four desks, one per study, in a room of width 13-16. */
function eightDesks(room: Room, firstX: number, rows: [number, number]) {
  const seats: Spot[] = [];
  const desks: Desk[] = [];
  for (let i = 0; i < 8; i++) {
    const col = i % 4;
    const row = i < 4 ? 0 : 1;
    const x = firstX + col * 3;
    const y = room.y + rows[row];
    desks.push({ x, y: y + 1, w: 2, kind: "desk" });
    seats.push(seat(x, y, TILE / 2));
  }
  return { seats, desks };
}

const libraryDesks = eightDesks(roomById.library, 23, [3, 7]);
const teamADesks = eightDesks(roomById["team-a"], 40, [3, 7]);
const teamBDesks = eightDesks(roomById["team-b"], 55, [3, 7]);
const teamCDesks = eightDesks(roomById["team-c"], 23, [3, 7]);

// The referee's bench: eight seats in a row behind one long desk, facing the evidence table.
const refSeats: Spot[] = [];
for (let i = 0; i < 8; i++) refSeats.push(seat(11 + i, 22));

// Solo agents: eight columns (one per study) by four rows (one per attempt) of small desks.
const soloSeats: Spot[][] = [];
const soloDesks: Desk[] = [];
for (let c = 0; c < 8; c++) {
  soloSeats.push([]);
  for (let r = 0; r < 4; r++) {
    const x = 51 + c * 2;
    const y = 22 + r * 3;
    soloSeats[c].push(seat(x, y));
    soloDesks.push({ x, y: y + 1, w: 1, kind: "small" });
  }
}

export const SEATS = {
  director: seat(14, 4, TILE / 2),
  library: libraryDesks.seats,
  "team-a": teamADesks.seats,
  "team-b": teamBDesks.seats,
  "team-c": teamCDesks.seats,
  referee: refSeats,
  solo: soloSeats,
};

export const DESKS: Desk[] = [
  { x: 12, y: 5, w: 6, kind: "big" },
  ...libraryDesks.desks,
  ...teamADesks.desks,
  ...teamBDesks.desks,
  ...teamCDesks.desks,
  { x: 11, y: 23, w: 8, kind: "bench" },
  ...soloDesks,
];

/** Where visitors stand in front of the director's desk. */
export const VISITOR_SPOTS: Spot[] = [seat(12, 7), seat(14, 7, TILE / 2), seat(17, 7)];
export const ELEVATOR: Spot = seat(4, 3);
export const ELEVATOR_QUEUE: Spot[] = [seat(3, 5), seat(5, 5), seat(4, 6), seat(3, 7), seat(5, 7)];

/** Fixed things people and notes travel to. */
export const FIX = {
  hatch: { tx: 53, ty: 6, px: 53 * TILE + TILE / 2, py: 6 * TILE + 8 },
  hatchA: seat(52, 6),
  hatchB: seat(54, 6),
  exa: { tx: 36, ty: 4, px: 36 * TILE + 8, py: 4 * TILE + 8 },
  refTable: seat(14, 27, TILE / 2),
  vaultDoor: seat(41, 19, TILE / 2),
  directorDoor: seat(14, 12, TILE / 2),
  coffee: seat(2, 21),
};

/** The vault's eight cabinets, one per study, each with a drawer per department. */
export const CABINETS: { x: number; y: number }[] = [];
for (let i = 0; i < 8; i++) CABINETS.push({ x: 37 + (i % 4) * 3, y: i < 4 ? 22 : 27 });

/** Furniture that blocks walking, besides desks. */
type Block = { x: number; y: number; w: number; h: number };
export const BLOCKS: Block[] = [
  { x: 2, y: 9, w: 5, h: 1 }, // reception desk
  { x: 10, y: 9, w: 2, h: 2 }, // sofa
  { x: 13, y: 26, w: 4, h: 2 }, // evidence table
  { x: 35, y: 3, w: 2, h: 3 }, // search terminal
  ...CABINETS.map((c) => ({ x: c.x, y: c.y, w: 2, h: 2 })),
];

export type Plant = { x: number; y: number; kind: number };
export const PLANTS: Plant[] = [
  { x: 1, y: 12, kind: 0 }, { x: 7, y: 12, kind: 1 }, { x: 1, y: 28, kind: 2 }, { x: 7, y: 31, kind: 0 },
  { x: 20, y: 3, kind: 1 }, { x: 9, y: 3, kind: 2 }, { x: 37, y: 11, kind: 0 }, { x: 22, y: 11, kind: 2 },
  { x: 67, y: 11, kind: 1 }, { x: 39, y: 11, kind: 2 }, { x: 9, y: 31, kind: 1 }, { x: 34, y: 31, kind: 0 },
  { x: 67, y: 31, kind: 2 }, { x: 48, y: 31, kind: 1 }, { x: 22, y: 31, kind: 0 },
  { x: 20, y: 15, kind: 1 }, { x: 38, y: 15, kind: 0 }, { x: 53, y: 15, kind: 2 },
];

// ---- Walkable grid --------------------------------------------------------------------------

export const walk: Uint8Array = new Uint8Array(W * H);

function setRect(x: number, y: number, w: number, h: number, v: number) {
  for (let j = y; j < y + h; j++) for (let i = x; i < x + w; i++) if (i >= 0 && j >= 0 && i < W && j < H) walk[j * W + i] = v;
}

for (const r of ROOMS) {
  // The back wall's two face rows are not floor, except in the corridor and lobby side walls.
  const face = r.id === "corridor" ? 0 : 2;
  setRect(r.x, r.y + face, r.w, r.h - face, 1);
  if (r.door !== undefined) {
    const wy = r.doorSide === "bottom" ? r.y + r.h : r.y - 1;
    setRect(r.door, wy, 2, 1, 1);
    if (r.doorSide === "top") setRect(r.door, r.y, 2, 2, 1); // walk through the face rows
  }
}
setRect(1, 3, 7, 30, 1); // lobby
setRect(8, 14, 1, 4, 1); // lobby opens onto the corridor
setRect(4, 1, 1, 2, 1); // the elevator
for (const d of DESKS) setRect(d.x, d.y, d.w, 1, 0);
for (const b of BLOCKS) setRect(b.x, b.y, b.w, b.h, 0);
for (const p of PLANTS) setRect(p.x, p.y, 1, 1, 0);

export function walkable(x: number, y: number) {
  return x >= 0 && y >= 0 && x < W && y < H && walk[y * W + x] === 1;
}

/** A* on the tile grid, 4-way. Returns tile points from start to goal inclusive, or [] if none. */
export function findPath(sx: number, sy: number, gx: number, gy: number): [number, number][] {
  if (sx === gx && sy === gy) return [[sx, sy]];
  const N = W * H;
  const g = new Float32Array(N).fill(Infinity);
  const came = new Int32Array(N).fill(-1);
  const open: number[] = [];
  const f = new Float32Array(N).fill(Infinity);
  const start = sy * W + sx;
  const goal = gy * W + gx;
  g[start] = 0;
  f[start] = Math.abs(sx - gx) + Math.abs(sy - gy);
  open.push(start);
  const closed = new Uint8Array(N);
  const passable = (i: number) => walk[i] === 1 || i === goal || i === start;
  while (open.length) {
    let bi = 0;
    for (let i = 1; i < open.length; i++) if (f[open[i]] < f[open[bi]]) bi = i;
    const cur = open[bi];
    open.splice(bi, 1);
    if (cur === goal) break;
    closed[cur] = 1;
    const cx = cur % W;
    const cy = (cur / W) | 0;
    const nbrs = [[cx + 1, cy], [cx - 1, cy], [cx, cy + 1], [cx, cy - 1]];
    for (const [nx, ny] of nbrs) {
      if (nx < 0 || ny < 0 || nx >= W || ny >= H) continue;
      const ni = ny * W + nx;
      if (closed[ni] || !passable(ni)) continue;
      const ng = g[cur] + 1;
      if (ng < g[ni]) {
        g[ni] = ng;
        came[ni] = cur;
        f[ni] = ng + Math.abs(nx - gx) + Math.abs(ny - gy);
        if (!open.includes(ni)) open.push(ni);
      }
    }
  }
  if (came[goal] === -1) return [];
  const out: [number, number][] = [];
  for (let c = goal; c !== -1; c = came[c]) out.push([c % W, (c / W) | 0]);
  return out.reverse();
}

/** The room a tile is in, for hit tests. */
export function roomAt(tx: number, ty: number): Room | null {
  for (const r of ROOMS) if (tx >= r.x && tx < r.x + r.w && ty >= r.y && ty < r.y + r.h) return r;
  return null;
}

export const ROLE_ROOM: Record<Role, RoomId> = {
  director: "director",
  library: "library",
  "team-a": "team-a",
  "team-b": "team-b",
  "team-c": "team-c",
  referee: "referee",
  solo: "solo",
  other: "lobby",
};
