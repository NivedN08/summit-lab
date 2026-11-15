// The lab floor: a canvas that replays a run. People walk only when information moves (a
// librarian carrying findings to the director, the director carrying a question to a team); notes
// travel the corridors; answers meet only at the hatch in the blind wall. Every movement is
// started by a real event in the run's log.

import type { Ev, Role } from "../data/types";
import type { Replay } from "../sim/replay";
import { bubble, type Icon } from "./icons";
import {
  CABINETS, DESKS, ELEVATOR, ELEVATOR_QUEUE, FIX, H, HEAD_DESKS, HEAD_SEATS, MARGIN, ROLE_ROOM, ROOMS, SEATS, TILE,
  VISITOR_SPOTS, W, findPath, roomAt, roomById, type Desk, type RoomId, type Spot,
} from "./layout";
import { C, DEPT, colorOf, headDept, shade } from "./palette";
import { FOOT_X, FOOT_Y, lookFor, personSprite, type Dir, type Look } from "./people";
import { buildStatic, OFF } from "./staticLayer";

export type Target =
  | { type: "agent"; id: string }
  | { type: "room"; id: RoomId }
  | { type: "fixture"; id: "hatch" | "vault" | "exa" | "elevator" }
  | { type: "study"; id: string };

type Step = { go: Spot; face?: Dir } | { wait: number; icon?: Icon; face?: Dir; carry?: Carry };
type Carry = "scroll" | "note" | null;

type Actor = {
  id: string;
  role: Role;
  study: string | null;
  look: Look;
  home: Spot;
  x: number;
  y: number;
  dir: Dir;
  path: { x: number; y: number }[];
  steps: Step[];
  wait: number;
  carry: Carry;
  walkPhase: number;
  visible: boolean;
  atHome: boolean;
  bubble: { icon: Icon; color: string; until: number } | null;
  releaseAt: number;
};

type Flyer = { pts: { x: number; y: number }[]; len: number; d: number; speed: number; color: string; kind: "note" | "answer" | "envelope"; onArrive?: () => void; label?: string };
type Floater = { x: number; y: number; text: string; color: string; until: number; born: number };

export type EngineCallbacks = {
  onTick: (t: number, playing: boolean) => void;
  onSelect: (target: Target | null) => void;
  onHover: (target: Target | null) => void;
};

const WALK_TPS = 4.5; // tiles per real second at 10x

export class LabEngine {
  private ctx: CanvasRenderingContext2D;
  private staticCv: HTMLCanvasElement;
  private actors = new Map<string, Actor>();
  private flyers: Flyer[] = [];
  private floaters: Floater[] = [];
  private evIndex = 0;
  private events: Ev[];
  private raf = 0;
  private lastNow = 0;
  private lastTick = 0;
  private real = 0; // real seconds since start, for animation
  private cam = { x: 0, y: 0, z: 2 };
  private camTo: { x: number; y: number; z: number } | null = null;
  /** The person the camera keeps in view while they walk, until the user pans or zooms. */
  private follow: string | null = null;
  private dpr = 1;
  private cw = 0;
  private ch = 0;
  private drag: { sx: number; sy: number; cx: number; cy: number; moved: boolean; id: number } | null = null;
  private pinch: { d: number; z: number } | null = null;
  private pointers = new Map<number, { x: number; y: number }>();
  private hover: Target | null = null;
  private lastFlyAt = new Map<string, number>();
  private hatchFlash: { until: number; agree: boolean; study: string } | null = null;
  private exaFlash = 0;
  private drawerFlash = new Map<string, number>();
  private roomLight = new Map<RoomId, number>();
  private elevatorNext = 0;
  private visitorSlot = 0;
  private resizeObs: ResizeObserver;
  private desks: Desk[];

  t = 0;
  playing = false;
  speed = 12;
  filter: string | null = null;
  selected: Target | null = null;
  /** While false the floor is drawn but nobody can click it (the landing's backdrop). */
  interactive = true;
  /** Slow automatic drift, used behind the landing page. */
  drift = false;

  constructor(private canvas: HTMLCanvasElement, private replay: Replay, private cb: EngineCallbacks) {
    this.ctx = canvas.getContext("2d")!;
    this.staticCv = buildStatic();
    this.events = [...replay.run.events].sort((a, b) => replay.timeOf(a) - replay.timeOf(b));
    // Heads' desks appear only in runs that had department heads (D-22).
    this.desks = replay.run.agents.some((a) => a.role === "head") ? [...DESKS, ...HEAD_DESKS] : DESKS;
    for (const a of replay.run.agents) {
      const home = this.homeOf(a.id, a.role, a.study);
      if (!home) continue;
      this.actors.set(a.id, {
        id: a.id, role: a.role, study: a.study, look: lookFor(a.id, a.role), home,
        x: home.px, y: home.py, dir: 0, path: [], steps: [], wait: 0, carry: null, walkPhase: 0,
        visible: false, atHome: true, bubble: null, releaseAt: 0,
      });
    }
    for (const r of ROOMS) this.roomLight.set(r.id, 0);
    this.bind();
    this.resizeObs = new ResizeObserver(() => this.resize());
    this.resizeObs.observe(canvas);
    this.resize();
    this.fit(false);
    this.seek(0);
    this.raf = requestAnimationFrame(this.frame);
  }

  destroy() {
    cancelAnimationFrame(this.raf);
    this.resizeObs.disconnect();
    this.unbind();
  }

  // ---- Public controls ------------------------------------------------------------------------

  setPlaying(p: boolean) {
    if (p && this.t >= this.replay.end) this.seek(0);
    this.playing = p;
    this.cb.onTick(this.t, this.playing);
  }

  setSpeed(s: number) {
    this.speed = s;
  }

  seek(t: number) {
    this.t = Math.max(0, Math.min(this.replay.end, t));
    this.flyers = [];
    this.floaters = [];
    this.hatchFlash = null;
    this.evIndex = 0;
    while (this.evIndex < this.events.length && this.replay.timeOf(this.events[this.evIndex]) <= this.t) this.evIndex++;
    for (const a of this.actors.values()) {
      const st = this.replay.status(a.id, this.t);
      a.visible = a.role === "director" || st.present;
      a.x = a.home.px;
      a.y = a.home.py;
      a.dir = 0;
      a.path = [];
      a.steps = [];
      a.wait = 0;
      a.carry = null;
      a.atHome = true;
      a.bubble = null;
    }
    for (const r of ROOMS) this.roomLight.set(r.id, this.roomActive(r.id) ? 1 : 0);
    this.cb.onTick(this.t, this.playing);
  }

  setFilter(study: string | null) {
    this.filter = study;
  }

  setSelected(target: Target | null) {
    this.selected = target;
  }

  /** Ease the camera to a target: a person, a room, a fixture, or the whole lab. */
  focus(target: Target | null, zoom?: number) {
    this.follow = target?.type === "agent" ? target.id : null;
    if (!target) return this.fit(true);
    let x = 0;
    let y = 0;
    let z = zoom ?? Math.max(this.cam.z, this.fitZoom() * 1.9);
    if (target.type === "agent") {
      const a = this.actors.get(target.id);
      if (!a) return;
      x = a.x;
      y = a.y - 8;
      z = zoom ?? Math.max(this.cam.z, Math.min(4, this.fitZoom() * 2.4));
    } else if (target.type === "room") {
      const r = roomById[target.id];
      x = (r.x + r.w / 2) * TILE;
      y = (r.y + r.h / 2) * TILE;
      z = zoom ?? Math.min(4, Math.min(this.cw / ((r.w + 6) * TILE), this.ch / ((r.h + 6) * TILE)));
    } else if (target.type === "fixture") {
      const p = target.id === "hatch" ? FIX.hatch : target.id === "vault" ? { px: 42 * TILE, py: 26 * TILE } : target.id === "exa" ? FIX.exa : ELEVATOR;
      x = p.px;
      y = p.py;
      z = zoom ?? Math.max(this.cam.z, Math.min(4, this.fitZoom() * 2.4));
    } else if (target.type === "study") {
      x = (W * TILE) / 2;
      y = (H * TILE) / 2;
      z = this.fitZoom();
    }
    this.camTo = { x, y, z };
  }

  fit(animate: boolean) {
    this.follow = null;
    const to = { x: (W * TILE) / 2, y: (H * TILE) / 2 + 4, z: this.fitZoom() };
    if (animate) this.camTo = to;
    else this.cam = { ...to };
  }

  zoomBy(f: number) {
    const a = this.follow ? this.actors.get(this.follow) : null;
    if (a) {
      this.camTo = { x: this.cam.x, y: this.cam.y, z: this.clampZ(this.cam.z * f) };
      return;
    }
    const z = this.clampZ(this.cam.z * f);
    this.camTo = { x: this.cam.x, y: this.cam.y, z };
  }

  /** Camera position as a fraction of fit, for the landing transition. */
  setCamera(x: number, y: number, z: number) {
    this.cam = { x, y, z };
    this.camTo = null;
  }

  /** The zoom that shows the whole building. */
  wholeZoom() {
    return Math.min(this.cw / ((W + 2) * TILE), this.ch / ((H + 3) * TILE));
  }

  /** The starting zoom. On a phone the whole floor makes people a few pixels tall, so start closer. */
  fitZoom() {
    const whole = this.wholeZoom();
    return this.cw < 700 ? Math.max(whole, 0.8) : whole;
  }

  // ---- Homes ------------------------------------------------------------------------------------

  private homeOf(id: string, role: Role, study: string | null): Spot | null {
    const i = study ? this.replay.studyIndex.get(study) ?? 0 : 0;
    switch (role) {
      case "director":
        return SEATS.director;
      case "head": {
        const d = headDept(id);
        return d && d in HEAD_SEATS ? HEAD_SEATS[d as keyof typeof HEAD_SEATS] : null;
      }
      case "library":
      case "team-a":
      case "team-b":
      case "team-c":
        return SEATS[role][i % 8];
      case "referee":
        return SEATS.referee[i % 8];
      case "solo": {
        const attempt = Number(id.split("-").pop()) || 1;
        return SEATS.solo[i % 8][(attempt - 1) % 4];
      }
    }
    return null;
  }

  private roomOf(a: Actor): RoomId {
    return a.role === "head" ? ((headDept(a.id) as RoomId | null) ?? "lobby") : ROLE_ROOM[a.role];
  }

  private roomActive(room: RoomId): boolean {
    for (const a of this.actors.values()) {
      if (this.roomOf(a) !== room) continue;
      if (this.replay.status(a.id, this.t).working) return true;
    }
    if (room === "corridor" || room === "lobby") return true;
    if (room === "vault") return this.t > 30;
    return false;
  }

  // ---- The clock and the events it crosses --------------------------------------------------

  private frame = (now: number) => {
    const dt = Math.min(0.1, this.lastNow ? (now - this.lastNow) / 1000 : 0);
    this.lastNow = now;
    this.real += dt;
    if (this.playing) {
      const before = this.t;
      this.t = Math.min(this.replay.end, this.t + dt * this.speed);
      this.crossEvents(before, this.t);
      if (this.t >= this.replay.end) {
        this.playing = false;
        this.cb.onTick(this.t, false);
      }
    }
    if (this.drift && !this.camTo) {
      this.cam.x += Math.sin(this.real / 9) * 0.18;
      this.cam.y += Math.cos(this.real / 11) * 0.08;
    }
    this.update(dt);
    this.draw();
    if (now - this.lastTick > 100) {
      this.lastTick = now;
      this.cb.onTick(this.t, this.playing);
    }
    this.raf = requestAnimationFrame(this.frame);
  };

  private crossEvents(_from: number, to: number) {
    while (this.evIndex < this.events.length && this.replay.timeOf(this.events[this.evIndex]) <= to) {
      this.handle(this.events[this.evIndex]);
      this.evIndex++;
    }
  }

  private handle(e: Ev) {
    const R = this.replay;
    const actor = e.a ? this.actors.get(e.a) : undefined;
    switch (e.k) {
      case "agent.start": {
        if (!actor) break;
        const first = R.agents.get(actor.id)?.sessions[0]?.start === e.t;
        if (first && actor.role !== "director") this.arrive(actor);
        if (actor.role === "head" && e.phase === "head-review" && e.s) {
          // The head goes to the desk of the person whose work it is reviewing.
          const dept = headDept(actor.id);
          const target = dept ? this.actors.get(`${e.s}-${dept}`) : undefined;
          const idle = actor.steps.length === 0 && actor.path.length === 0 && actor.visible && actor.releaseAt <= this.real;
          if (target && target.visible && idle && !first) {
            this.plan(actor, [{ go: this.visitSpot(target), face: 1 }, { wait: 2.2, icon: "read", face: 1 }, { go: actor.home, face: 0 }], null);
          } else this.say(actor, "read", colorOf(actor.role, actor.id), 2.5);
        }
        if (actor.role === "director" && e.phase === "briefing" && e.s) {
          // The study's librarian carries its findings to the director's office.
          const lib = this.actors.get(`${e.s}-library`);
          if (lib && lib.visible) {
            const spot = VISITOR_SPOTS[this.visitorSlot++ % VISITOR_SPOTS.length];
            this.plan(lib, [{ go: spot, face: 1 }, { wait: 2.4, icon: "scroll", face: 1, carry: "scroll" }, { go: lib.home, face: 0 }], "scroll");
          }
        }
        break;
      }
      case "agent.finish":
        if (actor && e.ok === false) this.say(actor, "alert", C.red, 3);
        break;
      case "web.search":
      case "web.contents":
        if (actor) this.say(actor, "search", DEPT.library, 1.6);
        this.exaFlash = this.real + 1.2;
        break;
      case "memory.recall":
        if (actor && actor.atHome && !actor.bubble) this.say(actor, "read", colorOf(actor.role, actor.id), 1.1);
        break;
      case "memory.write": {
        if (!actor) break;
        this.say(actor, "write", colorOf(actor.role, actor.id), 1.3);
        this.sendToVault(actor, e);
        break;
      }
      case "memory.pass_down": {
        const target = e.to ? this.actors.get(e.to) : undefined;
        if (!target || !target.visible) break;
        const sender = this.actors.get(e.a ?? "director") ?? this.actors.get("director");
        const color = sender ? colorOf(sender.role, sender.id) : DEPT.director;
        if (sender?.role === "director" && sender.steps.some((s) => "go" in s && s.go === this.visitSpot(target))) break;
        if (this.throttle(`pd-${e.a}-${e.to}`, 8)) this.fly(this.spotOf(sender), this.deskSpot(target), color, "envelope", () => this.say(target, "paper", color, 1.6));
        break;
      }
      case "head.review": {
        if (!actor) break;
        const color = colorOf(actor.role, actor.id);
        this.say(actor, e.delivered ? "write" : "check", color, 2.5);
        break;
      }
      case "relay.briefing": {
        const target = e.s ? this.actors.get(`${e.s}-team-a`) : undefined;
        if (target) this.directorVisit(target, "paper");
        break;
      }
      case "relay.leak_check": {
        const target = e.to ? this.actors.get(e.to) : undefined;
        if (!target) break;
        if (e.by && e.by.startsWith("head-")) {
          // A head's note: delivered by the envelope that follows; a held-back one stays with the head.
          const head = this.actors.get(e.by);
          if (head && !e.passed) {
            this.say(head, "cross", C.red, 3);
            this.floater(head.home.px, head.home.py - 30, `note held back`, C.red, 3.5);
          }
          break;
        }
        if (e.passed) this.directorVisit(target, "question");
        else this.say(this.actors.get("director")!, "cross", C.red, 2.5);
        break;
      }
      case "candidate": {
        const team = e.team;
        const who = e.s && team ? this.actors.get(`${e.s}-${team}`) : undefined;
        if (!who) break;
        this.say(who, "paper", colorOf(who.role, who.id), 1.8);
        const to = team === "team-a" ? FIX.hatchA : team === "team-b" ? FIX.hatchB : FIX.refTable;
        this.fly(this.deskSpot(who), to, colorOf(who.role, who.id), "answer");
        break;
      }
      case "compare": {
        this.hatchFlash = { until: this.real + 4, agree: !!e.agree, study: e.s ?? "" };
        this.floater(FIX.hatch.px, FIX.hatch.py - 26, `${e.s} ${e.agree ? "agree" : "disagree"}`, e.agree ? C.green : C.red, 4);
        break;
      }
      case "referee.assign": {
        this.fly(FIX.hatchA, FIX.refTable, DEPT["team-a"], "answer", undefined, "X");
        this.fly(FIX.hatchB, FIX.refTable, DEPT["team-b"], "answer", undefined, "Y");
        break;
      }
      case "referee.verdict": {
        const ref = e.s ? this.actors.get(`${e.s}-referee`) : undefined;
        if (ref) this.say(ref, "gavel", DEPT.referee, 3.5);
        this.floater(FIX.refTable.px, FIX.refTable.py - 6, `${e.s}: ${e.pick === "neither" ? "neither" : `picks ${e.pick}`}`, e.pick === "neither" ? C.red : C.green, 4.5);
        break;
      }
      case "study.final": {
        for (const team of ["team-a", "team-b"]) {
          const a = this.actors.get(`${e.s}-${team}`);
          if (a) this.say(a, e.status === "unresolved" ? "cross" : "star", e.status === "unresolved" ? C.red : DEPT.director, 3);
        }
        break;
      }
    }
  }

  private throttle(key: string, runSeconds: number) {
    const last = this.lastFlyAt.get(key) ?? -1e9;
    if (this.t - last < runSeconds) return false;
    this.lastFlyAt.set(key, this.t);
    return true;
  }

  private arrive(a: Actor) {
    a.visible = true;
    a.x = ELEVATOR.px;
    a.y = ELEVATOR.py;
    a.atHome = false;
    a.releaseAt = Math.max(this.real, this.elevatorNext);
    this.elevatorNext = a.releaseAt + 0.22;
    const q = ELEVATOR_QUEUE[Math.floor(Math.random() * ELEVATOR_QUEUE.length)];
    a.steps = [{ go: q }, { go: a.home, face: 0 }];
    a.path = [];
  }

  private directorVisit(target: Actor, icon: Icon) {
    const d = this.actors.get("director");
    if (!d) return;
    const spot = this.visitSpot(target);
    const busy = d.steps.length > 0 || d.path.length > 0;
    if (busy) {
      if (d.steps.some((s) => "go" in s && s.go === spot)) return;
      const queued = d.steps.filter((s) => "go" in s).length;
      if (queued > 4) {
        this.fly(this.spotOf(d), this.deskSpot(target), DEPT.director, "envelope", () => this.say(target, icon, DEPT.director, 2));
        return;
      }
      // Join the round: go on to this desk before heading home.
      const home = d.steps.pop();
      d.steps.push({ go: spot, face: 1 }, { wait: 1.4, icon, face: 1 });
      if (home) d.steps.push(home);
      return;
    }
    this.plan(d, [{ go: spot, face: 1 }, { wait: 1.4, icon, face: 1 }, { go: d.home, face: 0 }], "note");
    this.say(target, icon, DEPT.director, 2.5);
  }

  private plan(a: Actor, steps: Step[], carry: Carry) {
    a.steps = steps;
    a.path = [];
    a.carry = carry;
    a.atHome = false;
  }

  private say(a: Actor, icon: Icon, color: string, seconds: number) {
    a.bubble = { icon, color, until: this.real + seconds };
  }

  private floater(x: number, y: number, text: string, color: string, seconds: number) {
    this.floaters.push({ x, y, text, color, until: this.real + seconds, born: this.real });
    if (this.floaters.length > 8) this.floaters.shift();
  }

  /** Where a visitor stands: across the desk from the person, facing them. */
  private visitSpot(a: Actor): Spot {
    const h = a.home;
    if (a.role === "referee") return { tx: h.tx, ty: h.ty + 3, px: h.px, py: h.py + 3 * TILE };
    if (a.role === "director") return VISITOR_SPOTS[1];
    return { tx: h.tx, ty: h.ty + 2, px: h.px, py: h.py + 2 * TILE };
  }

  private deskSpot(a: Actor): Spot {
    return { tx: a.home.tx, ty: a.home.ty, px: a.home.px, py: a.home.py };
  }

  private spotOf(a?: Actor): Spot {
    if (!a) return SEATS.director;
    return { tx: Math.floor(a.x / TILE), ty: Math.floor(a.y / TILE), px: a.x, py: a.y };
  }

  private sendToVault(a: Actor, e: Ev) {
    if (!this.throttle(`mw-${a.id}`, 10) || this.flyers.length > 30) return;
    const i = a.study ? this.replay.studyIndex.get(a.study) ?? 0 : 0;
    const cab = CABINETS[i % 8];
    const drawer = ["library", "team-a", "team-b", "referee", "team-c", "director"].indexOf(a.role);
    const to: Spot = { tx: cab.x, ty: cab.y + 2, px: cab.x * TILE + TILE, py: cab.y * TILE + drawer * 6 };
    const key = `${i}-${drawer}`;
    void e;
    this.fly(this.deskSpot(a), to, colorOf(a.role, a.id), "note", () => this.drawerFlash.set(key, this.real + 1.5));
  }

  private fly(from: Spot, to: Spot, color: string, kind: Flyer["kind"], onArrive?: () => void, label?: string) {
    const tiles = findPath(from.tx, from.ty, to.tx, to.ty);
    if (!tiles.length) return;
    const pts = [{ x: from.px, y: from.py - 12 }];
    for (const [x, y] of tiles.slice(1, -1)) pts.push({ x: x * TILE + TILE / 2, y: y * TILE + 2 });
    pts.push({ x: to.px, y: to.py - 10 });
    let len = 0;
    for (let i = 1; i < pts.length; i++) len += Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
    this.flyers.push({ pts, len, d: 0, speed: 220, color, kind, onArrive, label });
  }

  // ---- Movement -------------------------------------------------------------------------------

  private walkFactor() {
    return this.playing ? Math.max(0.7, Math.min(3, this.speed / 10)) : 0;
  }

  private update(dt: number) {
    const f = this.walkFactor();
    for (const a of this.actors.values()) {
      if (!a.visible) continue;
      if (a.releaseAt > this.real) continue;
      if (a.wait > 0) {
        a.wait -= dt * (f > 0 ? 1 : 0);
        if (a.wait <= 0) a.steps.shift();
        continue;
      }
      if (!a.path.length) {
        const step = a.steps[0];
        if (!step) {
          if (!a.atHome && Math.hypot(a.x - a.home.px, a.y - a.home.py) < 2) {
            a.atHome = true;
            a.carry = null;
            a.dir = 0;
          }
          continue;
        }
        if ("wait" in step) {
          a.wait = step.wait;
          if (step.face !== undefined) a.dir = step.face;
          if (step.icon) this.say(a, step.icon, colorOf(a.role, a.id), step.wait);
          continue;
        }
        const tiles = findPath(Math.floor(a.x / TILE), Math.floor((a.y - 1) / TILE), step.go.tx, step.go.ty);
        a.path = tiles.slice(1).map(([x, y]) => ({ x: x * TILE + TILE / 2, y: y * TILE + TILE - 1 }));
        a.path.push({ x: step.go.px, y: step.go.py });
        a.atHome = false;
        continue;
      }
      const target = a.path[0];
      const dx = target.x - a.x;
      const dy = target.y - a.y;
      const dist = Math.hypot(dx, dy);
      const stepLen = WALK_TPS * TILE * f * dt;
      if (dist <= stepLen) {
        a.x = target.x;
        a.y = target.y;
        a.path.shift();
        if (!a.path.length) {
          const step = a.steps.shift();
          if (step && "go" in step && step.face !== undefined) a.dir = step.face;
        }
      } else {
        a.x += (dx / dist) * stepLen;
        a.y += (dy / dist) * stepLen;
        a.dir = Math.abs(dx) > Math.abs(dy) ? (dx < 0 ? 2 : 3) : dy < 0 ? 1 : 0;
        a.walkPhase += stepLen / 5;
      }
    }
    // Notes in flight.
    const fly = this.playing ? 1 : 0.0;
    for (const fl of this.flyers) fl.d += fl.speed * dt * Math.max(fly, 0) * Math.max(0.8, Math.min(2.5, this.speed / 12));
    const arrived = this.flyers.filter((fl) => fl.d >= fl.len);
    this.flyers = this.flyers.filter((fl) => fl.d < fl.len);
    for (const fl of arrived) fl.onArrive?.();
    this.floaters = this.floaters.filter((fl) => fl.until > this.real);
    // Lights fade in and out with activity.
    for (const r of ROOMS) {
      const want = this.roomActive(r.id) ? 1 : 0;
      const cur = this.roomLight.get(r.id) ?? 0;
      this.roomLight.set(r.id, cur + (want - cur) * Math.min(1, dt * 2.5));
    }
    // Keep a followed person in view while they walk.
    if (this.follow) {
      const a = this.actors.get(this.follow);
      if (a && a.visible) {
        const seated = a.atHome && a.steps.length === 0 && a.path.length === 0;
        const tx = seated ? a.home.px : a.x;
        const ty = (seated ? a.home.py : a.y) - 8;
        const z = this.camTo?.z ?? this.cam.z;
        if (Math.abs(tx - this.cam.x) > 0.5 || Math.abs(ty - this.cam.y) > 0.5 || this.camTo) this.camTo = { x: tx, y: ty, z };
      }
    }
    // Camera easing.
    if (this.camTo) {
      const k = Math.min(1, dt * 5);
      this.cam.x += (this.camTo.x - this.cam.x) * k;
      this.cam.y += (this.camTo.y - this.cam.y) * k;
      this.cam.z += (this.camTo.z - this.cam.z) * k;
      if (Math.abs(this.camTo.x - this.cam.x) < 0.3 && Math.abs(this.camTo.y - this.cam.y) < 0.3 && Math.abs(this.camTo.z - this.cam.z) < 0.002) {
        this.cam = { ...this.camTo };
        this.camTo = null;
      }
    }
    this.clampCam();
  }

  // ---- Drawing --------------------------------------------------------------------------------

  private resize() {
    const rect = this.canvas.getBoundingClientRect();
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    this.cw = Math.max(1, rect.width);
    this.ch = Math.max(1, rect.height);
    this.canvas.width = Math.round(this.cw * this.dpr);
    this.canvas.height = Math.round(this.ch * this.dpr);
  }

  private clampZ(z: number) {
    return Math.max(this.wholeZoom() * 0.8, Math.min(8, z));
  }

  private clampCam() {
    const mx = MARGIN * TILE * 0.8;
    this.cam.x = Math.max(-mx, Math.min(W * TILE + mx, this.cam.x));
    this.cam.y = Math.max(-mx, Math.min(H * TILE + mx, this.cam.y));
  }

  /** World pixel -> CSS pixel. */
  private toScreen(x: number, y: number) {
    return { x: (x - this.cam.x) * this.cam.z + this.cw / 2, y: (y - this.cam.y) * this.cam.z + this.ch / 2 };
  }

  private toWorld(sx: number, sy: number) {
    return { x: (sx - this.cw / 2) / this.cam.z + this.cam.x, y: (sy - this.ch / 2) / this.cam.z + this.cam.y };
  }

  private draw() {
    const ctx = this.ctx;
    const z = this.cam.z;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = "#aeb8d9";
    ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
    const s = z * this.dpr;
    const ox = Math.round((this.cw / 2 - this.cam.x * z) * this.dpr);
    const oy = Math.round((this.ch / 2 - this.cam.y * z) * this.dpr);
    ctx.setTransform(s, 0, 0, s, ox, oy);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(this.staticCv, -OFF, -OFF);

    this.drawRoomLights(ctx);
    this.drawWorldObjects(ctx);
    this.drawFlyers(ctx);
    this.drawBubbles(ctx);

    // Screen-space text.
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.drawLabels(ctx);
  }

  private drawRoomLights(ctx: CanvasRenderingContext2D) {
    for (const r of ROOMS) {
      const light = this.roomLight.get(r.id) ?? 0;
      const dim = 0.42 * (1 - light);
      if (dim > 0.01) {
        ctx.fillStyle = `rgba(18, 22, 52, ${dim})`;
        ctx.fillRect(r.x * TILE, r.y * TILE, r.w * TILE, r.h * TILE);
      }
    }
    // The hatch light.
    const hf = this.hatchFlash && this.hatchFlash.until > this.real ? this.hatchFlash : null;
    const lampC = hf ? (hf.agree ? C.green : C.red) : "#5d6590";
    ctx.fillStyle = lampC;
    ctx.fillRect(53 * TILE + 4, 5 * TILE + 6, 8, 4);
    if (hf) {
      ctx.fillStyle = hf.agree ? "rgba(83,209,139,0.25)" : "rgba(239,91,91,0.25)";
      ctx.fillRect(51 * TILE, 4 * TILE, 5 * TILE, 4 * TILE);
    }
    // The search terminal.
    const on = this.exaFlash > this.real;
    ctx.fillStyle = on ? "#7fe0ff" : "#26305e";
    ctx.fillRect(35 * TILE + 3, 3 * TILE + 3, 2 * TILE - 6, TILE);
    if (on) {
      ctx.fillStyle = "#1c2346";
      for (let i = 0; i < 4; i++) ctx.fillRect(35 * TILE + 6, 3 * TILE + 6 + i * 3, 10 + ((i * 7 + Math.floor(this.real * 8)) % 12), 1);
    }
    // Vault drawers flash when a note lands.
    for (const [key, until] of this.drawerFlash) {
      if (until < this.real) continue;
      const [i, d] = key.split("-").map(Number);
      const cab = CABINETS[i];
      ctx.fillStyle = ["#ee8a3c", "#4c7cf2", "#ea5f9d", "#2fbf8f", "#9b6cf2", "#f2c14e"][d] ?? C.lamp;
      ctx.fillRect(cab.x * TILE + 4, cab.y * TILE - 1 + Math.min(d, 4) * 6, 4, 4);
    }
  }

  private dimFor(study: string | null, role: Role) {
    if (!this.filter) return 1;
    if (role === "director" || role === "head") return 1;
    return study === this.filter ? 1 : 0.28;
  }

  private drawWorldObjects(ctx: CanvasRenderingContext2D) {
    type D = { y: number; draw: () => void };
    const list: D[] = [];
    // Desks, with whoever sits at them.
    const seatOwner = new Map<string, Actor>();
    for (const a of this.actors.values()) seatOwner.set(`${a.home.tx},${a.home.ty}`, a);
    for (const d of this.desks) {
      const top = d.y * TILE;
      const seats = this.seatsAt(d);
      list.push({ y: top + 0.5, draw: () => this.drawDesk(ctx, d, seats.map((s) => seatOwner.get(`${s.tx},${s.ty}`))) });
      for (const s of seats) {
        const a = seatOwner.get(`${s.tx},${s.ty}`);
        list.push({ y: top - 1.5, draw: () => this.drawChair(ctx, s, a) });
      }
    }
    for (const a of this.actors.values()) {
      if (!a.visible) continue;
      if (a.releaseAt > this.real) continue;
      const seated = a.atHome && a.steps.length === 0 && a.path.length === 0;
      if (seated) list.push({ y: a.home.py + 0.2, draw: () => this.drawSeated(ctx, a) });
      else list.push({ y: a.y, draw: () => this.drawStanding(ctx, a) });
    }
    list.sort((p, q) => p.y - q.y);
    for (const item of list) item.draw();
  }

  private seatsAt(d: Desk): Spot[] {
    if (d.kind === "big") return [SEATS.director];
    if (d.kind === "head") return Object.values(HEAD_SEATS).filter((s) => s.tx === d.x + 1);
    if (d.kind === "bench") return SEATS.referee;
    const out: Spot[] = [];
    for (const group of [SEATS.library, SEATS["team-a"], SEATS["team-b"], SEATS["team-c"]])
      for (const s of group) if (s.tx === d.x && s.ty === d.y - 1) out.push(s);
    for (const col of SEATS.solo) for (const s of col) if (s.tx === d.x && s.ty === d.y - 1) out.push(s);
    return out;
  }

  private drawChair(ctx: CanvasRenderingContext2D, s: Spot, a?: Actor) {
    const alpha = a ? this.dimFor(a.study, a.role) : this.filter ? 0.4 : 1;
    ctx.globalAlpha = alpha;
    ctx.fillStyle = "#3a4170";
    ctx.fillRect(s.px - 5, s.py - 10, 10, 4);
    ctx.fillStyle = "#4d5583";
    ctx.fillRect(s.px - 5, s.py - 6, 10, 5);
    ctx.globalAlpha = 1;
  }

  private drawDesk(ctx: CanvasRenderingContext2D, d: Desk, owners: (Actor | undefined)[]) {
    const x = d.x * TILE;
    const y = d.y * TILE;
    const w = d.w * TILE;
    const owner = owners[0];
    const alpha = d.kind === "bench" ? 1 : owner ? this.dimFor(owner.study, owner.role) : this.filter ? 0.4 : 1;
    ctx.globalAlpha = alpha;
    if (d.kind === "big") {
      ctx.fillStyle = C.woodDark;
      ctx.fillRect(x, y - 3, w, 18);
      ctx.fillStyle = C.wood;
      ctx.fillRect(x, y - 3, w, 10);
      ctx.fillStyle = C.woodHi;
      ctx.fillRect(x, y - 3, w, 1);
      ctx.fillStyle = "#5c3d28";
      ctx.fillRect(x + 4, y + 9, w - 8, 1);
      // Papers, a lamp and the director's screen.
      ctx.fillStyle = C.paper;
      ctx.fillRect(x + 62, y - 1, 8, 6);
      ctx.fillRect(x + 66, y, 8, 6);
      ctx.fillStyle = "#2b2f55";
      ctx.fillRect(x + 84, y - 10, 2, 8);
      ctx.fillStyle = C.lamp;
      ctx.fillRect(x + 81, y - 12, 8, 3);
      this.drawMonitor(ctx, x + 14, y - 11, 12, 9, owners[0]);
    } else if (d.kind === "head") {
      // A head's desk: wood, a lamp, a nameplate in its department's colour.
      ctx.fillStyle = C.woodDark;
      ctx.fillRect(x, y - 3, w, 17);
      ctx.fillStyle = C.wood;
      ctx.fillRect(x, y - 3, w, 10);
      ctx.fillStyle = C.woodHi;
      ctx.fillRect(x, y - 3, w, 1);
      ctx.fillStyle = owner ? colorOf(owner.role, owner.id) : C.metal;
      ctx.fillRect(x + w / 2 - 7, y + 9, 14, 3);
      ctx.fillStyle = C.paper;
      ctx.fillRect(x + w - 16, y - 1, 7, 5);
      ctx.fillStyle = "#2b2f55";
      ctx.fillRect(x + w - 5, y - 9, 2, 7);
      ctx.fillStyle = C.lamp;
      ctx.fillRect(x + w - 7, y - 11, 6, 3);
      this.drawMonitor(ctx, x + 2, y - 10, 10, 8, owner);
    } else if (d.kind === "bench") {
      ctx.fillStyle = "#1f3934";
      ctx.fillRect(x - 4, y - 3, w + 8, 18);
      ctx.fillStyle = "#3d6b62";
      ctx.fillRect(x - 4, y - 3, w + 8, 9);
      ctx.fillStyle = "#5a9187";
      ctx.fillRect(x - 4, y - 3, w + 8, 1);
      for (let i = 0; i < owners.length; i++) {
        const a = owners[i];
        if (!a || !a.visible) continue;
        this.drawMonitor(ctx, SEATS.referee[i].px - 9, y - 9, 6, 6, a);
      }
    } else if (d.kind === "small") {
      ctx.fillStyle = C.laminateShade;
      ctx.fillRect(x + 1, y - 2, w - 2, 14);
      ctx.fillStyle = C.laminate;
      ctx.fillRect(x + 1, y - 2, w - 2, 8);
      this.drawMonitor(ctx, x + 10, y - 5, 5, 5, owner);
    } else {
      ctx.fillStyle = C.laminateShade;
      ctx.fillRect(x + 1, y - 2, w - 2, 16);
      ctx.fillStyle = C.laminate;
      ctx.fillRect(x + 1, y - 2, w - 2, 9);
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(x + 1, y - 2, w - 2, 1);
      ctx.fillStyle = "#9aa3c2";
      ctx.fillRect(x + 3, y + 8, 2, 6);
      ctx.fillRect(x + w - 5, y + 8, 2, 6);
      // A mug and some paper.
      ctx.fillStyle = owner ? colorOf(owner.role, owner.id) : C.metal;
      ctx.fillRect(x + w - 6, y - 1, 3, 3);
      ctx.fillStyle = C.paper;
      ctx.fillRect(x + w - 13, y, 6, 4);
      this.drawMonitor(ctx, x + 1, y - 10, 9, 8, owner);
    }
    ctx.globalAlpha = 1;
  }

  private drawMonitor(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, a?: Actor) {
    const st = a && a.visible ? this.replay.status(a.id, this.t) : null;
    const working = !!st?.working;
    const color = a ? colorOf(a.role, a.id) : C.metal;
    if (working) {
      // Screen light spilling onto the desk.
      ctx.fillStyle = color + "33";
      ctx.fillRect(x - 3, y - 2, w + 6, h + 8);
    }
    ctx.fillStyle = "#2b2f55";
    ctx.fillRect(x, y, w, h);
    ctx.fillStyle = "#3a4170";
    ctx.fillRect(x + 1, y + 1, w - 2, 1);
    // Status light on the back of the screen.
    ctx.fillStyle = working ? (Math.floor(this.real * 3 + x) % 5 === 0 ? shade(color, 0.4) : color) : st?.present ? "#53607f" : "#2f355b";
    ctx.fillRect(x + w - 3, y + h - 3, 2, 2);
    ctx.fillStyle = "#4d5478";
    ctx.fillRect(x + Math.floor(w / 2) - 1, y + h, 2, 2);
  }

  private drawSeated(ctx: CanvasRenderingContext2D, a: Actor) {
    const st = this.replay.status(a.id, this.t);
    ctx.globalAlpha = this.dimFor(a.study, a.role);
    const typing = st.working && this.playing;
    const frame = typing ? Math.floor(this.real * 6 + a.home.tx) % 2 : 0;
    const spr = personSprite(a.look, 0, "sit", 0);
    const px = Math.round(a.home.px - FOOT_X);
    const idleNod = !st.working && Math.floor(this.real / 3 + a.home.tx) % 4 === 0 ? 1 : 0;
    const py = Math.round(a.home.py + 6 - FOOT_Y) + idleNod;
    this.drawRing(ctx, a, a.home.px, a.home.py + 3);
    ctx.drawImage(spr, px, py);
    // Hands on the desk.
    const deskTop = (a.home.ty + 1) * TILE - 2;
    ctx.fillStyle = a.look.skin[0];
    ctx.fillRect(a.home.px - 4, deskTop + (frame ? 0 : 1), 2, 1);
    ctx.fillRect(a.home.px + 2, deskTop + (frame ? 1 : 0), 2, 1);
    ctx.globalAlpha = 1;
  }

  private drawStanding(ctx: CanvasRenderingContext2D, a: Actor) {
    ctx.globalAlpha = this.dimFor(a.study, a.role);
    const moving = a.path.length > 0 && this.walkFactor() > 0;
    const frame = moving ? Math.floor(a.walkPhase) % 4 : 0;
    ctx.fillStyle = "rgba(27,31,58,0.25)";
    ctx.fillRect(Math.round(a.x) - 4, Math.round(a.y) - 1, 8, 2);
    this.drawRing(ctx, a, a.x, a.y);
    const spr = personSprite(a.look, a.dir, moving ? "walk" : "stand", frame);
    ctx.drawImage(spr, Math.round(a.x - FOOT_X), Math.round(a.y - FOOT_Y));
    if (a.carry && a.dir !== 1) {
      ctx.fillStyle = a.carry === "scroll" ? "#fff3d6" : C.paper;
      const cx = Math.round(a.x + (a.dir === 2 ? -6 : 4));
      ctx.fillRect(cx, Math.round(a.y) - 9, 4, 3);
      ctx.fillStyle = a.carry === "scroll" ? DEPT.library : DEPT.director;
      ctx.fillRect(cx, Math.round(a.y) - 9, 4, 1);
    }
    ctx.globalAlpha = 1;
  }

  private drawRing(ctx: CanvasRenderingContext2D, a: Actor, x: number, y: number) {
    const sel = this.selected?.type === "agent" && this.selected.id === a.id;
    const hov = this.hover?.type === "agent" && this.hover.id === a.id;
    const inStudy = this.filter && a.study === this.filter;
    if (!sel && !hov && !inStudy) return;
    const pulse = sel ? Math.floor(this.real * 4) % 2 : 0;
    ctx.fillStyle = sel ? C.lamp : hov ? "#ffffffaa" : colorOf(a.role, a.id) + "aa";
    const w = 12 + pulse * 2;
    ctx.fillRect(Math.round(x - w / 2), Math.round(y) - 2, w, 1);
    ctx.fillRect(Math.round(x - w / 2) - 1, Math.round(y) - 1, w + 2, 1);
    ctx.fillRect(Math.round(x - w / 2), Math.round(y), w, 1);
  }

  private drawFlyers(ctx: CanvasRenderingContext2D) {
    for (const fl of this.flyers) {
      let d = fl.d;
      let i = 1;
      for (; i < fl.pts.length; i++) {
        const seg = Math.hypot(fl.pts[i].x - fl.pts[i - 1].x, fl.pts[i].y - fl.pts[i - 1].y);
        if (d <= seg) break;
        d -= seg;
      }
      const a = fl.pts[Math.min(i - 1, fl.pts.length - 1)];
      const b = fl.pts[Math.min(i, fl.pts.length - 1)];
      const seg = Math.hypot(b.x - a.x, b.y - a.y) || 1;
      const x = Math.round(a.x + ((b.x - a.x) * d) / seg);
      const y = Math.round(a.y + ((b.y - a.y) * d) / seg + Math.sin((fl.d / 18) * Math.PI) * -1.5);
      if (fl.kind === "envelope") {
        ctx.fillStyle = "#1b1f3a";
        ctx.fillRect(x - 4, y - 3, 9, 7);
        ctx.fillStyle = C.paper;
        ctx.fillRect(x - 3, y - 2, 7, 5);
        ctx.fillStyle = fl.color;
        ctx.fillRect(x - 3, y - 2, 7, 1);
        ctx.fillRect(x, y - 1, 1, 1);
      } else if (fl.kind === "answer") {
        ctx.fillStyle = "#1b1f3a";
        ctx.fillRect(x - 3, y - 4, 7, 9);
        ctx.fillStyle = C.paper;
        ctx.fillRect(x - 2, y - 3, 5, 7);
        ctx.fillStyle = fl.color;
        ctx.fillRect(x - 2, y - 3, 5, 2);
      } else {
        ctx.fillStyle = fl.color;
        ctx.fillRect(x - 2, y - 1, 4, 3);
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(x - 1, y, 2, 1);
      }
    }
  }

  private drawBubbles(ctx: CanvasRenderingContext2D) {
    for (const a of this.actors.values()) {
      if (!a.visible || !a.bubble || a.bubble.until < this.real) continue;
      if (this.filter && a.study !== this.filter && a.role !== "director") continue;
      const seated = a.atHome && a.steps.length === 0 && a.path.length === 0;
      const x = seated ? a.home.px : a.x;
      const y = seated ? a.home.py + 6 - 22 : a.y - 22;
      const img = bubble(a.bubble.icon, a.bubble.color);
      const bob = Math.round(Math.sin(this.real * 5 + a.home.tx) * 0.6);
      ctx.drawImage(img, Math.round(x - 5), Math.round(y - 12 + bob));
    }
  }

  private drawLabels(ctx: CanvasRenderingContext2D) {
    const z = this.cam.z;
    // Room signs.
    const size = Math.max(10, Math.min(15, 4.2 * z));
    ctx.textBaseline = "middle";
    ctx.textAlign = "center";
    for (const r of ROOMS) {
      if (r.id === "corridor") continue;
      const cx = (r.x + r.w / 2) * TILE;
      const p = this.toScreen(cx, r.y * TILE + (r.id === "lobby" ? 4.5 * TILE : 0.75 * TILE) - 2);
      ctx.font = `600 ${size}px "Pixelify Sans", system-ui, sans-serif`;
      const text = r.name;
      const w = ctx.measureText(text).width + size * 1.2;
      const h = size * 1.55;
      ctx.fillStyle = "rgba(22, 26, 51, 0.88)";
      ctx.fillRect(Math.round(p.x - w / 2), Math.round(p.y - h / 2), Math.round(w), Math.round(h));
      const role = (r.id in DEPT ? r.id : r.id === "director" ? "director" : null) as Role | null;
      ctx.fillStyle = role ? DEPT[role] : r.id === "vault" ? "#7fe0ff" : "#a9b2d3";
      ctx.fillRect(Math.round(p.x - w / 2), Math.round(p.y + h / 2) - 2, Math.round(w), 2);
      ctx.fillStyle = "#eef2fa";
      ctx.fillText(text, p.x, p.y + 0.5);
    }
    // Desk plates with study ids when zoomed in.
    if (z >= 2.6) {
      ctx.font = `500 ${Math.min(11, 3 * z)}px "Atkinson Hyperlegible Next Variable", system-ui, sans-serif`;
      for (const a of this.actors.values()) {
        if (!a.study || a.role === "solo") continue;
        const p = this.toScreen(a.home.px, (a.home.ty + 1) * TILE + 11);
        ctx.fillStyle = "rgba(27,31,58,0.55)";
        ctx.fillText(a.study, p.x, p.y);
      }
    }
    // Floating outcome labels.
    for (const fl of this.floaters) {
      const age = this.real - fl.born;
      const p = this.toScreen(fl.x, fl.y - Math.min(8, age * 6));
      const alpha = Math.min(1, (fl.until - this.real) * 2);
      ctx.globalAlpha = alpha;
      ctx.font = `600 ${Math.max(11, Math.min(14, 3.4 * z))}px "Pixelify Sans", system-ui, sans-serif`;
      const w = ctx.measureText(fl.text).width + 12;
      ctx.fillStyle = "#161a33";
      ctx.fillRect(Math.round(p.x - w / 2), Math.round(p.y - 10), Math.round(w), 20);
      ctx.fillStyle = fl.color;
      ctx.fillRect(Math.round(p.x - w / 2), Math.round(p.y + 8), Math.round(w), 2);
      ctx.fillStyle = "#eef2fa";
      ctx.fillText(fl.text, p.x, p.y);
      ctx.globalAlpha = 1;
    }
    // Name tags for the hovered and selected person.
    const tags: Target[] = [];
    if (this.hover?.type === "agent") tags.push(this.hover);
    if (this.selected?.type === "agent" && !(this.hover?.type === "agent" && this.hover.id === this.selected.id)) tags.push(this.selected);
    for (const t of tags) {
      if (t.type !== "agent") continue;
      const a = this.actors.get(t.id);
      if (!a || !a.visible) continue;
      const seated = a.atHome && a.steps.length === 0 && a.path.length === 0;
      const p = this.toScreen(seated ? a.home.px : a.x, (seated ? a.home.py + 6 : a.y) - 24);
      const name = this.replay.name(a.id);
      ctx.font = `600 13px "Atkinson Hyperlegible Next Variable", system-ui, sans-serif`;
      const w = ctx.measureText(name).width + 14;
      ctx.fillStyle = "#161a33";
      ctx.fillRect(Math.round(p.x - w / 2), Math.round(p.y - 22), Math.round(w), 20);
      ctx.fillStyle = colorOf(a.role, a.id);
      ctx.fillRect(Math.round(p.x - w / 2), Math.round(p.y - 4), Math.round(w), 2);
      ctx.fillStyle = "#eef2fa";
      ctx.fillText(name, p.x, p.y - 12);
    }
  }

  // ---- Input ----------------------------------------------------------------------------------

  private hitTest(sx: number, sy: number): Target | null {
    const w = this.toWorld(sx, sy);
    let best: Actor | null = null;
    let bestY = -Infinity;
    for (const a of this.actors.values()) {
      if (!a.visible || a.releaseAt > this.real) continue;
      const seated = a.atHome && a.steps.length === 0 && a.path.length === 0;
      const x = seated ? a.home.px : a.x;
      const yb = seated ? a.home.py + 4 : a.y;
      if (w.x >= x - 7 && w.x <= x + 7 && w.y >= yb - 20 && w.y <= yb + 2 && yb > bestY) {
        best = a;
        bestY = yb;
      }
    }
    if (best) return { type: "agent", id: best.id };
    const tx = Math.floor(w.x / TILE);
    const ty = Math.floor(w.y / TILE);
    if (tx >= 51 && tx <= 55 && ty >= 4 && ty <= 8) return { type: "fixture", id: "hatch" };
    if (tx >= 35 && tx <= 36 && ty >= 3 && ty <= 5) return { type: "fixture", id: "exa" };
    if (tx >= 3 && tx <= 5 && ty >= 1 && ty <= 3) return { type: "fixture", id: "elevator" };
    const room = roomAt(tx, ty);
    if (room && room.id === "vault") return { type: "fixture", id: "vault" };
    if (room && room.id !== "corridor") return { type: "room", id: room.id };
    return null;
  }

  /** Pointer position in CSS pixels relative to the canvas. */
  private pos(e: MouseEvent) {
    const r = this.canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  private onDown = (e: PointerEvent) => {
    if (!this.interactive) return;
    const { x: ex, y: ey } = this.pos(e);
    try {
      this.canvas.setPointerCapture(e.pointerId);
    } catch {
      /* synthetic or already-released pointers cannot be captured */
    }
    this.pointers.set(e.pointerId, { x: ex, y: ey });
    if (this.pointers.size === 2) {
      const [p, q] = [...this.pointers.values()];
      this.pinch = { d: Math.hypot(p.x - q.x, p.y - q.y), z: this.cam.z };
      this.drag = null;
      return;
    }
    this.drag = { sx: ex, sy: ey, cx: this.cam.x, cy: this.cam.y, moved: false, id: e.pointerId };
    this.follow = null;
  };

  private onMove = (e: PointerEvent) => {
    if (!this.interactive) return;
    const { x: ex, y: ey } = this.pos(e);
    if (this.pointers.has(e.pointerId)) this.pointers.set(e.pointerId, { x: ex, y: ey });
    if (this.pinch && this.pointers.size === 2) {
      const [p, q] = [...this.pointers.values()];
      const d = Math.hypot(p.x - q.x, p.y - q.y);
      this.cam.z = this.clampZ((this.pinch.z * d) / this.pinch.d);
      this.camTo = null;
      return;
    }
    if (this.drag && this.drag.id === e.pointerId) {
      const dx = ex - this.drag.sx;
      const dy = ey - this.drag.sy;
      if (Math.abs(dx) + Math.abs(dy) > 4) this.drag.moved = true;
      if (this.drag.moved) {
        this.cam.x = this.drag.cx - dx / this.cam.z;
        this.cam.y = this.drag.cy - dy / this.cam.z;
        this.camTo = null;
        this.canvas.style.cursor = "grabbing";
      }
      return;
    }
    const hit = this.hitTest(ex, ey);
    const same = JSON.stringify(hit) === JSON.stringify(this.hover);
    if (!same) {
      this.hover = hit;
      this.cb.onHover(hit);
    }
    this.canvas.style.cursor = hit ? "pointer" : "grab";
  };

  private onUp = (e: PointerEvent) => {
    if (!this.interactive) return;
    this.pointers.delete(e.pointerId);
    if (this.pointers.size < 2) this.pinch = null;
    const drag = this.drag;
    this.drag = null;
    this.canvas.style.cursor = "grab";
    if (drag && !drag.moved && drag.id === e.pointerId) {
      const { x: ex, y: ey } = this.pos(e);
      const hit = this.hitTest(ex, ey);
      this.cb.onSelect(hit);
    }
  };

  private onWheel = (e: WheelEvent) => {
    if (!this.interactive) return;
    e.preventDefault();
    const { x: ex, y: ey } = this.pos(e);
    this.follow = null;
    const before = this.toWorld(ex, ey);
    const f = Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0018));
    this.cam.z = this.clampZ(this.cam.z * f);
    const after = this.toWorld(ex, ey);
    this.cam.x += before.x - after.x;
    this.cam.y += before.y - after.y;
    this.camTo = null;
  };

  private onLeave = () => {
    if (this.hover) {
      this.hover = null;
      this.cb.onHover(null);
    }
  };

  private bind() {
    this.canvas.addEventListener("pointerdown", this.onDown);
    this.canvas.addEventListener("pointermove", this.onMove);
    this.canvas.addEventListener("pointerup", this.onUp);
    this.canvas.addEventListener("pointercancel", this.onUp);
    this.canvas.addEventListener("pointerleave", this.onLeave);
    this.canvas.addEventListener("wheel", this.onWheel, { passive: false });
  }

  private unbind() {
    this.canvas.removeEventListener("pointerdown", this.onDown);
    this.canvas.removeEventListener("pointermove", this.onMove);
    this.canvas.removeEventListener("pointerup", this.onUp);
    this.canvas.removeEventListener("pointercancel", this.onUp);
    this.canvas.removeEventListener("pointerleave", this.onLeave);
    this.canvas.removeEventListener("wheel", this.onWheel);
  }

  /** Where a person is on screen, for anchoring HTML near them. */
  screenOf(id: string) {
    const a = this.actors.get(id);
    if (!a) return null;
    return this.toScreen(a.x, a.y);
  }

  camera() {
    return { ...this.cam };
  }
}
