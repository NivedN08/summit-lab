import { useEffect, useRef, useState } from "react";
import { SummitScene } from "../world/summit";

type Props = { onEnter: () => void; ready: boolean; status: string; live: boolean; leaving: boolean };

export function Landing({ onEnter, ready, status, live, leaving }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);
  const sceneRef = useRef<SummitScene | null>(null);
  const [origin, setOrigin] = useState({ x: 68, y: 40 });
  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const scene = new SummitScene(ref.current!, reduced);
    sceneRef.current = scene;
    setOrigin({ x: scene.anchor.x * 100, y: scene.anchor.y * 100 });
    return () => scene.destroy();
  }, []);
  return (
    <div className={`landing ${leaving ? "leaving" : ""}`} style={{ ["--ox" as string]: `${origin.x}%`, ["--oy" as string]: `${origin.y}%` }}>
      <canvas ref={ref} aria-hidden="true" />
      <div className="hero">
        <h1 className="wordmark">
          Summit
          <br />
          Lab
        </h1>
        <p>A research lab run by AI agents. Two teams analyse each study without seeing each other's work, so when they agree, it means something.</p>
        <button className="btn gold cta" onClick={onEnter} disabled={!ready} autoFocus>
          View current research
        </button>
        <div className="now">
          {live && <span className="live-dot" aria-hidden="true" />}
          {status}
        </div>
      </div>
    </div>
  );
}
