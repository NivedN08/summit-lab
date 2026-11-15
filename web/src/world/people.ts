// People, drawn pixel by pixel. Every agent gets a look from a hash of its id, and clothes from
// its role: the director in a gold blazer, librarians in amber cardigans, scientists in white lab
// coats over their team's colour, referees in stripes, solo agents in grey.

import type { Role } from "../data/types";
import { C, DEPT, HAIRS, SKINS, shade } from "./palette";

export type HairStyle = "short" | "bob" | "long" | "bun" | "curly" | "bald" | "ponytail" | "spiky";
const STYLES: HairStyle[] = ["short", "bob", "long", "bun", "curly", "bald", "ponytail", "spiky"];

export type Look = {
  key: string;
  skin: string[];
  hair: string[];
  style: HairStyle;
  coat: string;
  coatShade: string;
  shirt: string;
  pants: string;
  shoes: string;
  stripes: boolean;
  glasses: boolean;
  badge: string;
};

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

export function lookFor(id: string, role: Role): Look {
  const h = hash(id);
  const skin = SKINS[h % SKINS.length];
  const hair = HAIRS[(h >>> 4) % HAIRS.length];
  let style = STYLES[(h >>> 8) % STYLES.length];
  const dept = DEPT[role];
  let coat = C.paper;
  let coatShade = "#d3d9ea";
  let shirt = dept;
  let pants = "#3a4170";
  let stripes = false;
  if (role === "director") {
    coat = "#e0a93a";
    coatShade = "#b9832a";
    shirt = "#2b2f55";
    pants = "#2b2f55";
    style = "short";
  } else if (role === "library") {
    coat = "#e57f36";
    coatShade = "#bb6429";
    shirt = "#fbe9d0";
    pants = "#5a4636";
  } else if (role === "referee") {
    coat = "#f4f6fb";
    coatShade = "#2a2f52";
    shirt = DEPT.referee;
    pants = "#22264a";
    stripes = true;
  } else if (role === "solo") {
    coat = "#d9dded";
    coatShade = "#b2b8cf";
    shirt = "#7d87ab";
    pants = "#4a5070";
  }
  return {
    key: `${id}|${role}`,
    skin,
    hair,
    style,
    coat,
    coatShade,
    shirt,
    pants,
    shoes: "#24284a",
    stripes,
    glasses: ((h >>> 12) & 7) === 0 || role === "director",
    badge: dept,
  };
}

export type Dir = 0 | 1 | 2 | 3; // down, up, left, right
export type Pose = "stand" | "walk" | "sit";

export const SPR_W = 14;
export const SPR_H = 22;
/** Feet sit at this point of the sprite box. */
export const FOOT_X = 7;
export const FOOT_Y = 20;

const cache = new Map<string, HTMLCanvasElement>();

/**
 * One frame of a person, cached. `frame` is the walk phase 0..3 (or the typing phase for `sit`).
 * The sprite is 14 x 22 with the feet at (7, 20).
 */
export function personSprite(look: Look, dir: Dir, pose: Pose, frame: number): HTMLCanvasElement {
  const key = `${look.key}|${dir}|${pose}|${frame}`;
  const hit = cache.get(key);
  if (hit) return hit;
  const cv = document.createElement("canvas");
  cv.width = SPR_W;
  cv.height = SPR_H;
  const ctx = cv.getContext("2d")!;
  drawPerson(ctx, look, dir, pose, frame);
  cache.set(key, cv);
  return cv;
}

function drawPerson(ctx: CanvasRenderingContext2D, L: Look, dir: Dir, pose: Pose, frame: number) {
  const mirror = dir === 3;
  const ox = 2;
  const bob = pose === "walk" && (frame === 1 || frame === 3) ? -1 : 0;
  const oy = 1 + bob;
  const r = (x: number, y: number, w: number, h: number, color: string) => {
    ctx.fillStyle = color;
    const px = mirror ? ox + (10 - x - w) : ox + x;
    ctx.fillRect(px, oy + y, w, h);
  };
  const [skin, skinS] = L.skin;
  const [hair, hairS] = L.hair;
  const side = dir === 2 || dir === 3;
  const eye = C.ink;

  // Legs and shoes (skipped when seated: the desk hides them).
  if (pose !== "sit") {
    const ly = 15 - bob; // legs stay on the ground while the body bobs
    if (!side) {
      const lift = pose === "walk" ? (frame === 1 ? 1 : frame === 3 ? -1 : 0) : 0;
      const lh = 3 - Math.max(0, lift);
      const rh = 3 - Math.max(0, -lift);
      r(2, ly, 2, lh, L.pants);
      r(6, ly, 2, rh, L.pants);
      r(2, ly + lh, 2, 1, L.shoes);
      r(6, ly + rh, 2, 1, L.shoes);
      r(3, ly, 1, lh, shade(L.pants, -0.25));
      r(7, ly, 1, rh, shade(L.pants, -0.25));
    } else {
      const stride = pose === "walk" ? [0, 1, 0, -1][frame] : 0;
      r(4 - stride, ly, 2, 3, shade(L.pants, -0.2));
      r(4 + stride, ly, 2, 3, L.pants);
      r(3 - stride, ly + 3, 3, 1, L.shoes);
      r(3 + stride, ly + 3, 3, 1, L.shoes);
    }
  }

  // Long hair falls behind the shoulders in front and side views.
  if (L.style === "long" && dir !== 1) {
    if (!side) {
      r(1, 7, 1, 4, hairS);
      r(8, 7, 1, 4, hairS);
    } else r(6, 7, 3, 4, hairS);
  }

  // Torso.
  const swing = pose === "walk" ? [0, 1, 0, -1][frame] : 0;
  if (!side) {
    r(1, 9, 8, 6, L.coat);
    r(8, 9, 1, 6, L.coatShade);
    if (L.stripes) for (let x = 1; x < 9; x += 2) r(x, 9, 1, 6, L.coatShade);
    if (dir === 0) {
      r(4, 9, 2, 3, L.shirt);
      if (L.coat === C.paper || L.coat === "#d9dded") {
        r(3, 9, 1, 1, L.coatShade);
        r(6, 9, 1, 1, L.coatShade);
        r(2, 12, 1, 1, L.coatShade);
        r(6, 11, 1, 1, L.badge);
      }
      if (L.coat === "#e0a93a") r(4, 10, 2, 3, "#c0393b"); // the director's tie
    } else {
      r(4, 10, 1, 4, L.coatShade);
    }
    // Arms.
    if (pose === "sit") {
      r(0, 10, 1, 3, L.coat);
      r(9, 10, 1, 3, L.coatShade);
    } else {
      r(0, 10 + swing, 1, 4, L.coat);
      r(9, 10 - swing, 1, 4, L.coatShade);
      r(0, 14 + swing, 1, 1, skin);
      r(9, 14 - swing, 1, 1, skinS);
    }
  } else {
    r(2, 9, 6, 6, L.coat);
    r(6, 9, 2, 6, L.coatShade);
    if (L.stripes) for (let x = 2; x < 8; x += 2) r(x, 9, 1, 6, L.coatShade);
    r(4 - swing, 10, 2, 4, shade(L.coat, -0.12));
    r(4 - swing, 14, 2, 1, skin);
  }

  // Head.
  if (!side) {
    r(2, 2, 6, 6, skin);
    r(1, 3, 8, 4, skin);
    r(2, 7, 6, 1, skinS);
    r(4, 8, 2, 1, skinS);
  } else {
    r(2, 2, 6, 6, skin);
    r(1, 4, 1, 2, skin); // nose
    r(3, 7, 5, 1, skinS);
    r(4, 8, 2, 1, skinS);
  }

  // Hair.
  hairFor(r, L.style, dir, hair, hairS, skin);

  // Face.
  if (dir === 0) {
    r(3, 5, 1, 1, eye);
    r(6, 5, 1, 1, eye);
    if (L.glasses) {
      r(2, 4, 3, 1, "#2b2f55");
      r(5, 4, 3, 1, "#2b2f55");
    }
  } else if (side) {
    r(2, 5, 1, 1, eye);
    if (L.glasses) r(1, 4, 3, 1, "#2b2f55");
  }
}

type R = (x: number, y: number, w: number, h: number, c: string) => void;

function hairFor(r: R, style: HairStyle, dir: Dir, hair: string, hairS: string, skin: string) {
  const side = dir === 2 || dir === 3;
  if (dir === 1) {
    // Seen from behind the hair covers the head.
    if (style === "bald") {
      r(2, 2, 6, 6, skin);
      r(1, 3, 8, 4, skin);
      r(1, 5, 8, 2, hair);
      return;
    }
    const big = style === "curly";
    r(big ? 0 : 1, big ? 0 : 1, big ? 10 : 8, big ? 8 : 7, hair);
    r(2, 7, 6, 1, hairS);
    if (style === "long") r(1, 8, 8, 3, hair);
    if (style === "bun") r(3, -1, 4, 2, hairS);
    if (style === "ponytail") r(4, 8, 2, 3, hairS);
    return;
  }
  if (side) {
    if (style === "bald") {
      r(5, 4, 3, 2, hair);
      return;
    }
    const big = style === "curly";
    r(big ? 1 : 2, big ? 0 : 1, big ? 8 : 7, 2, hair);
    r(5, 2, 3, big ? 6 : 5, hair);
    r(7, 3, 1, 4, hairS);
    if (style === "bob" || style === "long") r(5, 2, 4, 6, hair);
    if (style === "bun") r(6, -1, 3, 2, hairS);
    if (style === "ponytail") r(8, 3, 2, 4, hairS);
    if (style === "spiky") r(3, 0, 1, 1, hair), r(6, 0, 1, 1, hair);
    return;
  }
  // Front.
  switch (style) {
    case "bald":
      r(1, 3, 1, 2, hair);
      r(8, 3, 1, 2, hair);
      r(4, 2, 2, 1, "#ffffff22");
      return;
    case "curly":
      r(1, 0, 8, 3, hair);
      r(0, 1, 1, 5, hair);
      r(9, 1, 1, 5, hair);
      r(1, 3, 1, 2, hair);
      r(8, 3, 1, 2, hair);
      r(2, 0, 1, 1, hairS);
      r(5, 1, 1, 1, hairS);
      r(7, 0, 1, 1, hairS);
      return;
    default:
      r(2, 1, 6, 2, hair);
      r(1, 2, 1, 2, hair);
      r(8, 2, 1, 2, hair);
      r(2, 3, 2, 1, hairS);
      if (style === "bob" || style === "long") {
        r(1, 3, 1, 4, hair);
        r(8, 3, 1, 4, hair);
      }
      if (style === "bun") r(3, -1, 4, 2, hair), r(4, -1, 2, 1, hairS);
      if (style === "spiky") r(2, 0, 1, 1, hair), r(4, 0, 1, 1, hair), r(6, 0, 1, 1, hair);
      if (style === "ponytail") r(7, 1, 2, 1, hairS);
  }
}

/** A large portrait for panels: the front-facing standing sprite scaled up on its own canvas. */
export function portrait(look: Look, scale = 6): HTMLCanvasElement {
  const src = personSprite(look, 0, "stand", 0);
  const cv = document.createElement("canvas");
  cv.width = SPR_W * scale;
  cv.height = (SPR_H - 1) * scale;
  const ctx = cv.getContext("2d")!;
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(src, 0, 0, SPR_W, SPR_H - 1, 0, 0, cv.width, cv.height);
  return cv;
}
