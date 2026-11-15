// Tiny pixel icons for speech bubbles over people's heads. Each is a 7 x 7 grid.

export type Icon = "search" | "write" | "read" | "question" | "alert" | "scroll" | "check" | "cross" | "gavel" | "star" | "paper" | "zzz";

const GRIDS: Record<Icon, string[]> = {
  search: ["..###..", ".#...#.", ".#...#.", ".#...#.", "..###..", ".....#.", "......#"],
  write: ["......#", ".....##", "....##.", "...##..", "..##...", ".##....", "#......"],
  read: ["##...##", "#.#.#.#", "#.#.#.#", "#.#.#.#", "#.#.#.#", "##.#.##", "...#..."],
  question: [".###...", "#...#..", "....#..", "...#...", "..#....", ".......", "..#...."],
  alert: ["...#...", "...#...", "...#...", "...#...", "...#...", ".......", "...#..."],
  scroll: [".#####.", "#.....#", ".#...#.", ".#.#.#.", ".#...#.", "#.....#", ".#####."],
  check: [".......", "......#", ".....#.", "#...#..", ".#.#...", "..#....", "......."],
  cross: ["#.....#", ".#...#.", "..#.#..", "...#...", "..#.#..", ".#...#.", "#.....#"],
  gavel: [".##....", "####...", ".####..", "..###..", "....#..", ".....#.", "..####."],
  star: ["...#...", "...#...", "#######", ".#####.", "..###..", ".##.##.", "#.....#"],
  paper: [".####..", ".#..##.", ".#...#.", ".#.#.#.", ".#...#.", ".#.#.#.", ".#####."],
  zzz: ["####...", "..#....", ".#.....", "####...", "....###", ".....#.", "....###"],
};

const cache = new Map<string, HTMLCanvasElement>();

/** A bubble (11 x 12 with a tail) holding an icon in the given colour. */
export function bubble(icon: Icon, color: string): HTMLCanvasElement {
  const key = `${icon}|${color}`;
  const hit = cache.get(key);
  if (hit) return hit;
  const cv = document.createElement("canvas");
  cv.width = 11;
  cv.height = 12;
  const ctx = cv.getContext("2d")!;
  ctx.fillStyle = "#1b1f3a";
  ctx.fillRect(1, 0, 9, 1);
  ctx.fillRect(1, 10, 9, 1);
  ctx.fillRect(0, 1, 1, 9);
  ctx.fillRect(10, 1, 1, 9);
  ctx.fillRect(4, 11, 2, 1);
  ctx.fillStyle = "#fbfcff";
  ctx.fillRect(1, 1, 9, 9);
  ctx.fillRect(4, 10, 2, 1);
  ctx.fillStyle = color;
  const g = GRIDS[icon];
  for (let y = 0; y < 7; y++) for (let x = 0; x < 7; x++) if (g[y][x] === "#") ctx.fillRect(2 + x, 2 + y, 1, 1);
  cache.set(key, cv);
  return cv;
}
