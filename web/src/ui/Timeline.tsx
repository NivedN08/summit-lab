import { useRef } from "react";
import { fmtClock, fmtElapsed, type Moment, type Replay } from "../sim/replay";

const TICK: Record<Moment["kind"], string> = {
  brief: "var(--library)",
  agree: "var(--good)",
  disagree: "var(--bad)",
  referee: "var(--referee)",
  final: "var(--gold)",
  grade: "var(--snow)",
  challenge: "var(--director)",
  questions: "var(--team-c)",
};

export const SPEEDS = [1, 4, 12, 30, 60];

type Props = {
  R: Replay;
  t: number;
  playing: boolean;
  speed: number;
  filter: string | null;
  /** The part of the run the scrubber spans: the demo clip, or the whole run. */
  lo: number;
  hi: number;
  onPlay: () => void;
  onSeek: (t: number) => void;
  onSpeed: (s: number) => void;
  onNext: (dir: 1 | -1) => void;
};

export function Timeline({ R, t, playing, speed, filter, lo, hi, onPlay, onSeek, onSpeed, onNext }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const span = Math.max(1, hi - lo);
  const at = (time: number) => `${Math.max(0, Math.min(1, (time - lo) / span)) * 100}%`;
  const seekAt = (clientX: number) => {
    const r = ref.current!.getBoundingClientRect();
    onSeek(lo + Math.max(0, Math.min(1, (clientX - r.left) / r.width)) * span);
  };
  const moments = R.moments.filter((m) => (!filter || !m.study || m.study === filter) && m.t >= lo && m.t <= hi);
  return (
    <div className="timeline box" role="group" aria-label="Replay controls">
      <button className="btn icon gold" onClick={onPlay} aria-label={playing ? "Pause" : "Play"} title={playing ? "Pause (space)" : "Play (space)"}>
        {playing ? <PauseIcon /> : <PlayIcon />}
      </button>
      <label className="sr-only" htmlFor="speed">
        Speed
      </label>
      <select id="speed" className="speed" value={speed} onChange={(e) => onSpeed(Number(e.target.value))} title="Replay speed">
        {SPEEDS.map((s) => (
          <option key={s} value={s}>
            {s}x
          </option>
        ))}
      </select>
      <div className="clock">
        <b>{fmtClock(R.run.started, t)}</b>
        <span>{fmtElapsed(t)} into the run</span>
      </div>
      <div
        className="scrub"
        ref={ref}
        role="slider"
        tabIndex={0}
        aria-label="Replay position"
        aria-valuemin={Math.round(lo)}
        aria-valuemax={Math.round(hi)}
        aria-valuenow={Math.round(t)}
        aria-valuetext={`${fmtElapsed(t)} into the run`}
        onPointerDown={(e) => {
          (e.target as HTMLElement).setPointerCapture(e.pointerId);
          seekAt(e.clientX);
        }}
        onPointerMove={(e) => {
          if (e.buttons) seekAt(e.clientX);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowRight") onSeek(Math.min(hi, t + 15));
          if (e.key === "ArrowLeft") onSeek(Math.max(lo, t - 15));
        }}
      >
        <div className="track" />
        <div className="fill" style={{ width: at(t) }} />
        {moments.map((m, i) => (
          <div key={i} className="tick" title={m.label} style={{ left: at(m.t), background: TICK[m.kind] }} />
        ))}
        <div className="head" style={{ left: at(t) }} />
      </div>
      <button className="btn icon hide-sm" onClick={() => onNext(-1)} aria-label="Previous moment" title="Previous moment (←)">
        <PrevIcon />
      </button>
      <button className="btn" onClick={() => onNext(1)} title="Jump to the next moment (→)">
        <NextIcon />
        <span className="hide-sm">Next moment</span>
      </button>
    </div>
  );
}

const PlayIcon = () => (
  <svg width="14" height="14" viewBox="0 0 7 7" shapeRendering="crispEdges" aria-hidden="true">
    <path d="M1 0h1v7H1zM2 1h1v5H2zM3 2h1v3H3zM4 3h1v1H4z" fill="currentColor" />
  </svg>
);
const PauseIcon = () => (
  <svg width="14" height="14" viewBox="0 0 7 7" shapeRendering="crispEdges" aria-hidden="true">
    <path d="M1 0h2v7H1zM4 0h2v7H4z" fill="currentColor" />
  </svg>
);
const NextIcon = () => (
  <svg width="14" height="14" viewBox="0 0 7 7" shapeRendering="crispEdges" aria-hidden="true">
    <path d="M0 0h1v7H0zM1 1h1v5H1zM2 2h1v3H2zM3 3h1v1H3zM5 0h2v7H5z" fill="currentColor" />
  </svg>
);
const PrevIcon = () => (
  <svg width="14" height="14" viewBox="0 0 7 7" shapeRendering="crispEdges" aria-hidden="true">
    <path d="M6 0h1v7H6zM5 1h1v5H5zM4 2h1v3H4zM3 3h1v1H3zM0 0h2v7H0z" fill="currentColor" />
  </svg>
);
