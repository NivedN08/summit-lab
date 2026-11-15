// Ask the server (Claude, answering in character from what the agent could read) and fall back
// to the offline answerer when there is no server or Claude is unavailable.

import type { Replay } from "../sim/replay";
import { answerOffline, type Answer } from "./offline";

export type Turn = { role: "user" | "assistant"; text: string };

export async function ask(R: Replay, useServer: boolean, agent: string, t: number, question: string, history: Turn[]): Promise<Answer> {
  if (useServer) {
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 95_000);
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ run: R.run.run, agent, t, question, history: history.slice(-8) }),
        signal: ctrl.signal,
      });
      clearTimeout(timer);
      if (res.ok) {
        const data = await res.json();
        return { answer: data.answer, sources: data.sources ?? [], mode: "claude", model: data.model };
      }
      const off = answerOffline(R, agent, t, question);
      return { ...off, note: "Claude was unavailable, so this answer comes straight from the run log." };
    } catch {
      const off = answerOffline(R, agent, t, question);
      return { ...off, note: "Claude was unavailable, so this answer comes straight from the run log." };
    }
  }
  await new Promise((r) => setTimeout(r, 350));
  return answerOffline(R, agent, t, question);
}
