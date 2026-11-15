import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { listRuns, loadRun, type Source } from "./data/load";
import type { Run } from "./data/types";
import { Replay } from "./sim/replay";
import { Chat, type Msg } from "./ui/Chat";
import { Landing } from "./ui/Landing";
import { LabLog } from "./ui/LabLog";
import { FixturePanel, PersonPanel, RoomPanel, StudyPanel } from "./ui/Panels";
import { Timeline } from "./ui/Timeline";
import { buildTour } from "./ui/tour";
import { LabEngine, type Target } from "./world/engine";

type View = "landing" | "leaving" | "lab";

export function App() {
  const [source, setSource] = useState<Source | null>(null);
  const [runName, setRunName] = useState<string | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>("landing");
  const replay = useMemo(() => (run ? new Replay(run) : null), [run]);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<LabEngine | null>(null);
  const tRef = useRef(0);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(12);
  const [selected, setSelected] = useState<Target | null>(null);
  const [panel, setPanel] = useState<"details" | "chat" | null>(null);
  const [filter, setFilter] = useState<string | null>(null);
  const [chatAgent, setChatAgent] = useState("guide");
  const [chats, setChats] = useState<Record<string, Msg[]>>({});
  const [tour, setTour] = useState<number | null>(null);
  const [welcome, setWelcome] = useState(false);
  const tourSteps = useMemo(() => (replay ? buildTour(replay) : []), [replay]);

  // ---- Loading ----------------------------------------------------------------------------------
  useEffect(() => {
    listRuns()
      .then((s) => {
        setSource(s);
        setRunName(s.runs[0]?.run ?? null);
      })
      .catch(() => setError("Could not find any runs. Run python3 build_data.py in web/ first."));
  }, []);

  useEffect(() => {
    if (!source || !runName) return;
    let stop = false;
    loadRun(runName, source.server)
      .then((r) => !stop && setRun(r))
      .catch((e) => setError(String(e.message ?? e)));
    // A run still in progress is polled, so the floor keeps up with the lab.
    const live = source.runs.find((r) => r.run === runName)?.live;
    const timer = live
      ? window.setInterval(() => {
          loadRun(runName, true)
            .then((r) => !stop && setRun((old) => (old && old.events.length === r.events.length ? old : r)))
            .catch(() => {});
        }, 8000)
      : 0;
    return () => {
      stop = true;
      if (timer) clearInterval(timer);
    };
  }, [source, runName]);

  // ---- The engine -------------------------------------------------------------------------------
  useEffect(() => {
    if (!replay || !canvasRef.current) return;
    const engine = new LabEngine(canvasRef.current, replay, {
      onTick: (time, p) => {
        tRef.current = time;
        setT(time);
        setPlaying(p);
      },
      onSelect: (target) => {
        if (!target) {
          setSelected(null);
          setPanel((p) => (p === "details" ? null : p));
          return;
        }
        setSelected(target);
        setPanel("details");
      },
      onHover: () => {},
    });
    engine.setSpeed(speed);
    engineRef.current = engine;
    if (import.meta.env.DEV) (window as unknown as { __lab: LabEngine }).__lab = engine;
    const live = source?.runs.find((r) => r.run === replay.run.run)?.live;
    if (tRef.current > 0) engine.seek(Math.min(tRef.current, replay.end));
    else if (live) engine.seek(replay.end);
    engine.interactive = view === "lab";
    return () => engine.destroy();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [replay]);

  useEffect(() => {
    const e = engineRef.current;
    if (!e) return;
    e.setSelected(selected);
  }, [selected]);

  useEffect(() => {
    engineRef.current?.setFilter(filter);
  }, [filter]);

  const engine = () => engineRef.current;

  // ---- Actions ------------------------------------------------------------------------------------
  const select = useCallback((target: Target, focus = true) => {
    if (target.type === "study") {
      setFilter(target.id);
    }
    setSelected(target);
    setPanel("details");
    if (focus && target.type !== "study") engine()?.focus(target);
  }, []);

  const talk = useCallback((agent: string) => {
    setChatAgent(agent);
    setPanel("chat");
  }, []);

  const chooseStudy = (id: string | null) => {
    setFilter(id);
    if (id) {
      setSelected({ type: "study", id });
      setPanel("details");
    } else if (selected?.type === "study") {
      setSelected(null);
      setPanel(null);
    }
  };

  const enter = () => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    setView("leaving");
    const e = engine();
    if (e) {
      e.interactive = true;
      const z = e.fitZoom();
      e.setCamera(52 * 16, 9 * 16, z * 3.2);
      e.focus(null);
    }
    window.setTimeout(() => {
      setView("lab");
      setWelcome(true);
      const en = engine();
      if (en && !en.playing && en.t < (replay?.end ?? 0) - 1) en.setPlaying(true);
    }, reduced ? 0 : 1050);
  };

  const goTour = (i: number | null) => {
    const e = engine();
    setWelcome(false);
    setTour(i);
    if (i === null || !e) return;
    const s = tourSteps[i];
    if (!s) return;
    const end = replay?.end ?? 0;
    e.seek(s.t >= end ? end : Math.max(0, s.t - 6));
    e.setSpeed(4);
    setSpeed(4);
    e.setPlaying(s.t < end);
    if (s.focus) e.focus(s.focus);
    else e.focus(null);
    if (s.select) {
      if (s.select.type === "study") setFilter(s.select.id);
      setSelected(s.select);
      setPanel("details");
    } else {
      setSelected(null);
      setFilter(null);
      setPanel(null);
    }
  };

  const nextMoment = (dir: 1 | -1) => {
    const e = engine();
    if (!e || !replay) return;
    const list = replay.moments.filter((m) => !filter || !m.study || m.study === filter);
    const cur = e.t;
    const m = dir === 1 ? list.find((x) => x.t > cur + 1) : [...list].reverse().find((x) => x.t < cur - 3);
    if (!m) return;
    e.seek(Math.max(0, m.t - 4));
    e.setPlaying(true);
  };

  // Keyboard: space plays, arrows step between moments, escape closes.
  useEffect(() => {
    if (view !== "lab") return;
    const onKey = (ev: KeyboardEvent) => {
      const tag = (ev.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (ev.target as HTMLElement)?.getAttribute("role") === "slider") return;
      const e = engine();
      if (!e) return;
      if (ev.key === " ") {
        ev.preventDefault();
        e.setPlaying(!e.playing);
      } else if (ev.key === "ArrowRight") nextMoment(1);
      else if (ev.key === "ArrowLeft") nextMoment(-1);
      else if (ev.key === "Escape") {
        if (tour !== null) setTour(null);
        else {
          setPanel(null);
          setSelected(null);
        }
      } else if (ev.key === "+" || ev.key === "=") e.zoomBy(1.25);
      else if (ev.key === "-") e.zoomBy(0.8);
      else if (ev.key === "0") e.fit(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (error) {
    return (
      <div style={{ padding: 40 }}>
        <h1 className="wordmark" style={{ fontSize: 48 }}>
          Summit Lab
        </h1>
        <p>{error}</p>
      </div>
    );
  }

  const R = replay;
  const runInfo = source?.runs.find((r) => r.run === runName);
  const labAgents = run ? run.agents.filter((a) => a.role !== "solo").length : 0;
  const status = !run ? "Loading the lab…" : runInfo?.live ? `Live now: ${run.label}, ${labAgents} agents at work` : `Replaying ${run.label.replace(/^GeneBench-Pro test/, "the GeneBench-Pro test run")} with ${labAgents} lab agents`;

  const chatPeople = R
    ? Array.from(
        new Set(
          [
            "director",
            selected?.type === "agent" ? selected.id : "",
            ...(filter ? R.run.agents.filter((a) => a.study === filter && a.role !== "solo").map((a) => a.id) : []),
            ...Object.keys(chats),
            chatAgent,
          ].filter((id) => id && id !== "guide" && R.agents.has(id)),
        ),
      )
    : [];

  const nav = R ? { R, t, select, talk, filter: chooseStudy } : null;

  return (
    <>
      <div className="lab" inert={view !== "lab" ? true : undefined}>
        <canvas ref={canvasRef} className="floor" aria-label="The lab floor. Click a person to see what they are working on." />
        {R && view !== "landing" && (
          <>
            <header className="topbar">
              <button className="brand" onClick={() => location.reload()} title="Back to the summit">
                <b>Summit Lab</b>
              </button>
              {source && source.runs.length > 1 && (
                <>
                  <label className="sr-only" htmlFor="run">
                    Run
                  </label>
                  <select
                    id="run"
                    className="run-pick"
                    value={runName ?? ""}
                    onChange={(e) => {
                      tRef.current = 0;
                      setFilter(null);
                      setSelected(null);
                      setPanel(null);
                      setTour(null);
                      setRunName(e.target.value);
                    }}
                  >
                    {source.runs.map((r) => (
                      <option key={r.run} value={r.run}>
                        {r.live ? "Live: " : ""}
                        {r.label}
                      </option>
                    ))}
                  </select>
                </>
              )}
              <nav className="chips" aria-label="Studies">
                <button className="chip" aria-pressed={filter === null} onClick={() => chooseStudy(null)}>
                  All studies
                </button>
                {R.run.studies.map((s) => {
                  const fin = R.finalEvent(s.id, t);
                  const working = R.run.agents.some((a) => a.study === s.id && a.role !== "solo" && R.status(a.id, t).working);
                  const graded = R.graded(t) && s.grade;
                  return (
                    <button key={s.id} className="chip" aria-pressed={filter === s.id} onClick={() => chooseStudy(filter === s.id ? null : s.id)} title={s.title}>
                      <span className={`dot ${working ? "working" : fin ? "done" : ""}`} title={working ? "Agents working" : fin ? "Finished" : "Waiting"} />
                      {s.id}
                      {graded && <span className={`mark ${s.grade!.lab ? "pass" : "fail"}`}>{s.grade!.lab ? "passed" : "failed"}</span>}
                    </button>
                  );
                })}
              </nav>
              <button className="btn quiet hide-sm" onClick={() => goTour(0)}>
                Tour
              </button>
              <button className="btn gold" onClick={() => talk(chatAgent)}>
                Ask the lab
              </button>
            </header>

            {panel && nav && (
              <aside className="side box" aria-label={panel === "chat" ? "Ask the lab" : "Details"}>
                <div className="side-head">
                  <div className="tabs" role="tablist">
                    {selected && (
                      <button className="tab" role="tab" aria-selected={panel === "details"} onClick={() => setPanel("details")}>
                        Details
                      </button>
                    )}
                    <button className="tab" role="tab" aria-selected={panel === "chat"} onClick={() => setPanel("chat")}>
                      Ask
                    </button>
                  </div>
                  <button className="btn quiet icon" onClick={() => setPanel(null)} aria-label="Close panel" title="Close (Esc)">
                    <svg width="12" height="12" viewBox="0 0 6 6" shapeRendering="crispEdges" aria-hidden="true">
                      <path d="M0 0h1v1H0zM1 1h1v1H1zM2 2h2v2H2zM4 1h1v1H4zM5 0h1v1H5zM1 4h1v1H1zM0 5h1v1H0zM4 4h1v1H4zM5 5h1v1H5z" fill="currentColor" />
                    </svg>
                  </button>
                </div>
                {panel === "chat" ? (
                  <Chat R={R} t={t} server={!!source?.server} agent={chatAgent} setAgent={setChatAgent} people={chatPeople} chats={chats} setChats={setChats} select={(x) => select(x)} />
                ) : (
                  <div className="side-body">
                    {selected?.type === "agent" && <PersonPanel id={selected.id} {...nav} />}
                    {selected?.type === "study" && <StudyPanel id={selected.id} {...nav} />}
                    {selected?.type === "room" && <RoomPanel id={selected.id} {...nav} />}
                    {selected?.type === "fixture" && <FixturePanel id={selected.id} {...nav} />}
                  </div>
                )}
              </aside>
            )}

            <div className="mapctl">
              <button className="btn icon" onClick={() => engine()?.zoomBy(1.3)} aria-label="Zoom in" title="Zoom in (+)">
                +
              </button>
              <button className="btn icon" onClick={() => engine()?.zoomBy(1 / 1.3)} aria-label="Zoom out" title="Zoom out (-)">
                −
              </button>
              <button className="btn icon" onClick={() => engine()?.fit(true)} aria-label="Show the whole lab" title="Whole lab (0)">
                <svg width="14" height="14" viewBox="0 0 7 7" shapeRendering="crispEdges" aria-hidden="true">
                  <path d="M0 0h3v1H1v2H0zM4 0h3v3H6V1H4zM0 4h1v2h2v1H0zM6 4h1v3H4V6h2z" fill="currentColor" />
                </svg>
              </button>
            </div>

            {tour === null && !welcome && <LabLog R={R} t={t} filter={filter} select={(x) => select(x)} />}

            {welcome && tour === null && (
              <div className="tour box" role="dialog" aria-label="Welcome">
                <h2>Welcome to the lab floor</h2>
                <p>Everyone here is an AI agent replaying a real run. People walk when information moves between departments. Click anyone to see what they're working on, or ask them.</p>
                <div className="row">
                  <button className="btn" onClick={() => setWelcome(false)}>
                    Explore on my own
                  </button>
                  <button className="btn gold" onClick={() => goTour(0)} autoFocus>
                    Take the 1-minute tour
                  </button>
                </div>
              </div>
            )}

            {tour !== null && tourSteps[tour] && (
              <div className="tour box" role="dialog" aria-label="Tour">
                <span className="count">
                  {tour + 1} of {tourSteps.length}
                </span>
                <h2>{tourSteps[tour].title}</h2>
                <p>{tourSteps[tour].text}</p>
                <div className="row">
                  <button className="btn quiet" onClick={() => goTour(null)}>
                    End tour
                  </button>
                  {tour > 0 && (
                    <button className="btn" onClick={() => goTour(tour - 1)}>
                      Back
                    </button>
                  )}
                  {tour < tourSteps.length - 1 ? (
                    <button className="btn gold" onClick={() => goTour(tour + 1)} autoFocus>
                      Next
                    </button>
                  ) : (
                    <button className="btn gold" onClick={() => goTour(null)} autoFocus>
                      Explore the lab
                    </button>
                  )}
                </div>
              </div>
            )}

            <Timeline
              R={R}
              t={t}
              playing={playing}
              speed={speed}
              filter={filter}
              onPlay={() => engine()?.setPlaying(!engine()!.playing)}
              onSeek={(x) => engine()?.seek(x)}
              onSpeed={(s) => {
                setSpeed(s);
                engine()?.setSpeed(s);
              }}
              onNext={nextMoment}
            />
          </>
        )}
      </div>
      {view !== "lab" && <Landing onEnter={enter} ready={!!replay} status={status} live={!!runInfo?.live} leaving={view === "leaving"} />}
    </>
  );
}
