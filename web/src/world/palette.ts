// Every colour in the lab. Shadows lean cool and highlights warm, so the floor reads as one
// building lit by desk lamps on a night shift at the summit.

import type { Role } from "../data/types";

export const C = {
  night: "#161a33",
  night2: "#1d2244",
  ridge: "#2d3a63",
  snow: "#eef2fa",
  snowShade: "#c6cfe6",
  snowDeep: "#9aa6c8",
  rock: "#3a4470",
  lamp: "#ffc95c",
  lampSoft: "#ffe3a3",
  ink: "#1b1f3a",
  wallCap: "#262d55",
  wallCapHi: "#323a6a",
  wallFace: "#c9d0e6",
  wallFaceShade: "#aab3d2",
  wallTrim: "#8c96bd",
  glass: "#8fd3e8",
  wood: "#a8744a",
  woodDark: "#7a5136",
  woodHi: "#c18e60",
  laminate: "#e7ebf4",
  laminateShade: "#c3cadd",
  metal: "#7b83a6",
  metalDark: "#4d5478",
  metalHi: "#a2a9c8",
  screenOff: "#2b3157",
  leaf: "#4f9e6b",
  leafDark: "#356f4c",
  leafHi: "#7cc48f",
  pot: "#c46a4a",
  potDark: "#8f4836",
  paper: "#fbfcff",
  red: "#ef5b5b",
  green: "#53d18b",
};

export const DEPT: Record<Role, string> = {
  director: "#f2c14e",
  library: "#ee8a3c",
  "team-a": "#4c7cf2",
  "team-b": "#ea5f9d",
  "team-c": "#9b6cf2",
  referee: "#2fbf8f",
  solo: "#8c96b8",
  other: "#8c96b8",
};

export const DEPT_SOFT: Record<Role, string> = {
  director: "#fbe3a6",
  library: "#f8c99c",
  "team-a": "#b7cbfa",
  "team-b": "#f6bdd6",
  "team-c": "#d3c0fa",
  referee: "#a6e6cf",
  solo: "#cdd2e4",
  other: "#cdd2e4",
};

export const SKINS = [
  ["#f6d7b9", "#e2b48f"],
  ["#eec39a", "#d09d72"],
  ["#d49a6d", "#b57a50"],
  ["#a86f48", "#875536"],
  ["#7b4c31", "#5e3824"],
];

export const HAIRS = [
  ["#2a211c", "#1a1411"],
  ["#5a3b26", "#422a1a"],
  ["#8a5a2e", "#6a4320"],
  ["#d9b36a", "#b8914a"],
  ["#b44a2e", "#8a3520"],
  ["#c8ccd8", "#a2a7b8"],
  ["#3a3f66", "#272b4a"],
];

export function shade(hex: string, amt: number): string {
  const n = parseInt(hex.slice(1), 16);
  let r = (n >> 16) & 255;
  let g = (n >> 8) & 255;
  let b = n & 255;
  if (amt < 0) {
    // Darken toward a cool indigo, not black.
    const k = -amt;
    r = Math.round(r * (1 - k) + 0x1b * k);
    g = Math.round(g * (1 - k) + 0x1f * k);
    b = Math.round(b * (1 - k) + 0x3a * k);
  } else {
    // Lighten toward a warm white.
    r = Math.round(r + (255 - r) * amt);
    g = Math.round(g + (247 - g) * amt);
    b = Math.round(b + (235 - b) * amt);
  }
  return "#" + ((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1);
}
