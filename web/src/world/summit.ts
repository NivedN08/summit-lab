// The landing: the lab on its summit at night, drawn at a quarter of screen resolution so every
// art pixel is a 4 x 4 block. Windows light up in department colours; a gondola runs up the cable.

import { DEPT } from "./palette";

const PX = 4;

type Star = { x: number; y: number; p: number; b: number };
type Flake = { x: number; y: number; v: number; d: number };

export class SummitScene {
  private ctx: CanvasRenderingContext2D;
  private w = 0;
  private h = 0;
  private stars: Star[] = [];
  private flakes: Flake[] = [];
  private ridges: { far: number[]; mid: number[]; main: number[] } = { far: [], mid: [], main: [] };
  private raf = 0;
  private t0 = performance.now();
  private obs: ResizeObserver;
  /** Where the building sits, as fractions of the canvas, for the zoom-in transition. */
  anchor = { x: 0.68, y: 0.4 };

  constructor(private canvas: HTMLCanvasElement, private reduced: boolean) {
    this.ctx = canvas.getContext("2d")!;
    this.obs = new ResizeObserver(() => this.resize());
    this.obs.observe(canvas);
    this.resize();
    this.raf = requestAnimationFrame(this.frame);
  }

  destroy() {
    cancelAnimationFrame(this.raf);
    this.obs.disconnect();
  }

  private resize() {
    const r = this.canvas.getBoundingClientRect();
    this.w = Math.max(80, Math.ceil(r.width / PX));
    this.h = Math.max(60, Math.ceil(r.height / PX));
    this.canvas.width = this.w;
    this.canvas.height = this.h;
    const portrait = r.height > r.width * 1.1;
    this.anchor = portrait ? { x: 0.56, y: 0.3 } : { x: 0.68, y: 0.4 };
    let s = 11;
    const rnd = () => ((s = (s * 16807) % 2147483647) / 2147483647);
    this.stars = Array.from({ length: Math.floor((this.w * this.h) / 70) }, () => ({ x: Math.floor(rnd() * this.w), y: Math.floor(rnd() * this.h * 0.62), p: rnd() * 6.28, b: rnd() }));
    this.flakes = Array.from({ length: Math.floor(this.w / 4) }, () => ({ x: rnd() * this.w, y: rnd() * this.h, v: 2 + rnd() * 4, d: rnd() * 6.28 }));
    const px = Math.floor(this.anchor.x * this.w);
    const py = Math.floor(this.anchor.y * this.h) + 10;
    const far: number[] = [];
    const mid: number[] = [];
    const main: number[] = [];
    for (let x = 0; x < this.w; x++) {
      far.push(Math.floor(this.h * 0.5 + Math.sin(x / 17) * 6 + Math.sin(x / 7.3) * 3 + Math.sin(x / 41 + 1) * 10));
      mid.push(Math.floor(this.h * 0.64 + Math.sin(x / 23 + 2) * 8 + Math.sin(x / 9.1) * 3 + Math.cos(x / 53) * 9));
      const dx = x - px;
      const slope = dx < 0 ? 0.62 : 0.78;
      const jag = Math.sin(x / 3.1) * 1.4 + Math.sin(x / 7.7) * 2.2 + (Math.abs(dx) < 19 ? 0 : Math.sin(x / 1.7) * 0.8);
      main.push(Math.floor(Math.abs(dx) < 18 ? py : py + (Math.abs(dx) - 18) * slope + jag));
    }
    this.ridges = { far, mid, main };
  }

  private frame = (now: number) => {
    const t = (now - this.t0) / 1000;
    this.draw(this.reduced ? 0 : t);
    if (!this.reduced) this.raf = requestAnimationFrame(this.frame);
  };

  private px(x: number, y: number, c: string) {
    this.ctx.fillStyle = c;
    this.ctx.fillRect(x, y, 1, 1);
  }

  private rect(x: number, y: number, w: number, h: number, c: string) {
    this.ctx.fillStyle = c;
    this.ctx.fillRect(Math.floor(x), Math.floor(y), w, h);
  }

  private draw(t: number) {
    const { w, h } = this;
    // Sky in dithered bands.
    const bands = ["#0f1330", "#141a3d", "#1a2148", "#212855", "#2b2f63", "#3a3570", "#4b3a76"];
    const bh = (h * 0.68) / bands.length;
    for (let i = 0; i < bands.length; i++) {
      this.rect(0, i * bh, w, Math.ceil(bh) + 1, bands[i]);
      if (i + 1 < bands.length) {
        this.ctx.fillStyle = bands[i + 1];
        const y0 = Math.floor((i + 1) * bh) - 2;
        for (let y = y0; y < y0 + 2; y++) for (let x = (y % 2); x < w; x += 2) this.ctx.fillRect(x, y, 1, 1);
      }
    }
    this.rect(0, h * 0.68, w, h, "#4b3a76");
    // Stars.
    for (const s of this.stars) {
      const tw = Math.sin(t * 1.6 + s.p) * 0.5 + 0.5;
      if (s.b > 0.85) this.px(s.x, s.y, tw > 0.5 ? "#fff6dc" : "#9fa8d8");
      else if (tw > 0.3) this.px(s.x, s.y, s.b > 0.5 ? "#c9d0f0" : "#7c86bd");
    }
    // Moon.
    const mx = Math.floor(w * 0.86);
    const my = Math.floor(h * 0.14);
    for (let y = -6; y <= 6; y++) for (let x = -6; x <= 6; x++) if (x * x + y * y <= 36) this.px(mx + x, my + y, x + y > 4 ? "#c9d1ef" : "#eef2fa");
    this.px(mx - 2, my - 1, "#c9d1ef");
    this.px(mx + 1, my + 2, "#c9d1ef");
    this.px(mx + 2, my - 3, "#d7ddf4");
    // Far ridge, clouds, mid ridge.
    this.ridge(this.ridges.far, "#2b3567", "#4a568f", 3);
    this.clouds(t, h * 0.52, "#3c4580", 0.6);
    this.ridge(this.ridges.mid, "#212a58", "#3d4882", 2);
    // The summit, lit from the moon on its right face.
    const main = this.ridges.main;
    const ax = Math.floor(this.anchor.x * w);
    const py = main[ax];
    for (let x = 0; x < w; x++) {
      const top = main[x];
      const lit = x > ax;
      // The moon lights the right face down to a diagonal ridge line falling from the summit.
      this.rect(x, top, 1, h - top, "#121737");
      if (lit) {
        const by = Math.min(h, Math.floor(py + (x - ax) / 0.42));
        if (by > top) this.rect(x, top, 1, by - top, "#36428a");
        if (by > top && by < h) this.rect(x, by, 1, 1, "#4a58a6");
      }
      // Snow on the upper slopes and ledges, and a moonlit rim along the ridge line.
      const snowDepth = Math.max(0, 10 - Math.abs(x - ax) / 7);
      if (snowDepth > 0.5) this.rect(x, top, 1, Math.ceil(snowDepth * (lit ? 1.3 : 0.9)), lit ? "#e6ecf9" : "#7e8bc0");
      this.rect(x, top, 1, 1, lit ? "#eef2fa" : "#5d6aa6");
      if (snowDepth <= 0.5 && ((x * 13) % 11 === 0 || x % 5 === 0)) this.rect(x, top + 1, 1, 2, lit ? "#c3cdec" : "#3d4880");
      // Rock strata.
      if ((x * 7) % 19 === 0 && top + 8 < h) this.rect(x, top + 6 + (x % 9), 3, 1, lit ? "#2a3470" : "#0d1130");
    }
    this.clouds(t * 1.4, h * 0.78, "#5a5390", 1);
    this.building(t, ax, main[ax]);
    this.gondola(t, ax, main[ax]);
    // Snowfall.
    for (const f of this.flakes) {
      const y = (f.y + t * f.v) % h;
      const x = (f.x + Math.sin(t * 0.7 + f.d) * 3 + w) % w;
      this.px(Math.floor(x), Math.floor(y), f.v > 4.5 ? "#eef2fa" : "#aab3dc");
    }
  }

  private ridge(line: number[], body: string, cap: string, capH: number) {
    for (let x = 0; x < this.w; x++) {
      this.rect(x, line[x], 1, this.h - line[x], body);
      const peak = line[x] < (line[x - 1] ?? 1e9) && line[x] <= (line[x + 1] ?? 1e9);
      const n = Math.abs(Math.sin(x * 12.9898) * 43758.5453) % 1;
      this.rect(x, line[x], 1, peak ? capH + 1 : n > 0.72 ? capH : 1, cap);
    }
  }

  private clouds(t: number, y: number, c: string, alpha: number) {
    this.ctx.globalAlpha = alpha;
    for (let i = 0; i < 5; i++) {
      const cw = 30 + ((i * 17) % 24);
      const x = ((i * 83 + t * (1.5 + i * 0.4)) % (this.w + cw * 2)) - cw;
      const cy = y + ((i * 7) % 9) - 4;
      this.rect(x, cy, cw, 3, c);
      this.rect(x + 4, cy - 2, cw - 10, 2, c);
      this.rect(x + 9, cy - 3, cw - 22, 1, c);
    }
    this.ctx.globalAlpha = 1;
  }

  private building(t: number, cx: number, ground: number) {
    const bw = 38;
    const bh = 19;
    const x0 = cx - bw / 2;
    const y0 = ground - bh;
    // Body, roof, shadow side.
    this.rect(x0, y0, bw, bh, "#2a3160");
    this.rect(x0 + bw - 5, y0, 5, bh, "#20264f");
    this.rect(x0 - 1, y0 - 2, bw + 2, 2, "#e4eaf8");
    this.rect(x0 - 1, y0, bw + 2, 1, "#9aa6cf");
    // Antenna with a blinking light, and a dish.
    this.rect(x0 + 6, y0 - 9, 1, 7, "#9aa6cf");
    if (Math.floor(t * 1.2) % 2 === 0) this.rect(x0 + 6, y0 - 10, 1, 1, "#ef5b5b");
    this.rect(x0 + 21, y0 - 5, 4, 1, "#9aa6cf");
    this.rect(x0 + 22, y0 - 4, 2, 2, "#9aa6cf");
    // Two floors of windows. Each lights in a department's colour and flickers as work happens.
    const colors = [DEPT.director, DEPT.library, DEPT.library, DEPT["team-a"], DEPT["team-a"], DEPT["team-b"], DEPT["team-b"], DEPT.referee, DEPT["team-c"], "#7fe0ff", DEPT.solo];
    for (let row = 0; row < 2; row++) {
      for (let i = 0; i < 12; i++) {
        const wx = x0 + 3 + i * 2.6;
        const wy = y0 + 4 + row * 7;
        const k = row * 12 + i;
        const on = Math.sin(t * 0.35 + k * 1.7) + Math.sin(t * 0.13 + k) > -0.6;
        const c = on ? colors[(i + row * 3) % colors.length] : "#3a4170";
        this.rect(wx, wy, 2, 4, c);
        if (on) this.rect(wx, wy, 2, 1, "#fff3d6");
      }
    }
    // Light spilling onto the snow in front.
    this.ctx.globalAlpha = 0.35;
    this.rect(x0 + 2, ground, bw - 4, 2, "#ffc95c");
    this.ctx.globalAlpha = 1;
    // Door.
    this.rect(x0 + 3, ground - 4, 3, 4, "#ffc95c");
  }

  private gondola(t: number, cx: number, ground: number) {
    // The cable runs from the summit station down and off the right edge, away from the words.
    const sx = cx + 25;
    const sy = ground - 9;
    const ex = this.w + 4;
    const ey = Math.floor(this.h * 0.8);
    this.rect(sx, sy, 3, 9, "#3a4170");
    this.ctx.fillStyle = "#8d96c4";
    const steps = Math.ceil(ex - sx);
    for (let i = 0; i <= steps; i++) {
      const f = i / steps;
      const x = sx + i;
      const y = sy + (ey - sy) * f - Math.sin(f * Math.PI) * 5;
      this.ctx.fillRect(Math.floor(x), Math.floor(y), 1, 1);
    }
    // One car going up, slowly; it waits at the top for a moment.
    const cycle = (t / 26) % 1.15;
    const f = 1 - Math.min(1, cycle);
    const x = sx + 1 + f * steps;
    const y = sy + (ey - sy) * f - Math.sin(f * Math.PI) * 5;
    this.rect(x, y + 1, 1, 3, "#8d96c4");
    this.rect(x - 3, y + 4, 7, 5, "#ef5b5b");
    this.rect(x - 2, y + 5, 2, 2, "#ffe3a3");
    this.rect(x + 1, y + 5, 2, 2, "#ffe3a3");
    this.rect(x - 3, y + 8, 7, 1, "#a63a46");
  }
}
