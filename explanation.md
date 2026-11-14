# summit-lab working log

This file explains how the lab works and why. It is updated as the work happens, so a reader can follow every decision without having been in the room.

**How to read it.** Sections 1 to 3 describe the lab as it stands now. Sections 4 to 9 are logs: newest entries go at the bottom of each one. Every entry has a date, a time (Pacific) and a status:

- **Decided**: we are doing this.
- **Proposed**: the current plan, not built or tested yet.
- **Open**: not settled.
- **Measured**: backed by a run; the entry says which run.

Nothing in this file is called a result unless a run produced it.

---

## 1. What the lab is

A research lab run by AI agents. It is organised like a human lab:

- **Head researchers** set direction, read across the whole lab, and decide what gets kept.
- **Departments** each own one part of the problem.
- **Workers** sit inside one department and do its day-to-day work.

Status: **Decided** as the overall shape. **Open**: the research target, which departments exist, and how many agents each has.

## 2. Tools

| Tool | What it is for here | Status |
|---|---|---|
| Exa | Deep research: finding sources the agents do not already know | Decided; key works (M-2) |
| past.dev | Shared memory that agents write to and read from, with limits on who can read what | Decided; key works, read boundaries tested (M-1) |
| Modal | Cloud compute | Open: used only where it gives a real benefit (see section 6); login works (M-3) |

Keys live in environment variables only. They are never written into this repository or this log.

## 3. Memory: who can read what

Status: **Proposed**. Nothing is built yet.

The rule we want:

| Role | Writes to | Can read |
|---|---|---|
| Worker | Its own department's memory | Its own department's memory, plus notes a head has passed down to it |
| Head researcher | Any department's memory, plus notes passed down to workers | All memory |

So a worker never reads another department's raw memory. If department A learns something department B needs, a head reads it and passes down only the part B needs.

**Why this shape.**

- It keeps each worker's context small and on topic.
- It gives one place, the head, where cross-department information is filtered. Every pass-down is a decision we can log and inspect.
- It matches how past.dev works: each fact is written with an *audience*, each read names an *identity*, and a read returns only what that identity is allowed to see.

**How it maps onto past.dev (planned, checked against their docs on Nov 14, not yet run).**

- Every agent is a past.dev *identity* with two traits: `department` and `role`.
- Every department has one *audience*, defined by a rule: an identity is in it if its `department` is this one, or its `role` is `head`. A new worker joins its department's memory by having the trait; nobody edits a list.
- A worker's write always names its department's audience.
- A pass-down is a **new** fact with its own id, written by a head and addressed to the receiving department's audience. It cannot be the original fact re-sent: in past.dev, re-sending the same text with another audience moves the fact instead of sharing it.
- Nothing is written without an audience, because a fact with no audience can be read by every identity (issue I-2).
- A pass-down records the id of the fact it came from, so every relay a head makes can be traced back. (Added after reading the MAP-Graph paper in section 8: a summary that crosses a boundary hides where it came from unless the source is kept.)

**Tested on Nov 14 (M-1 in section 9).** With two departments, one worker each and one head, all nine checks passed: workers read their own department only, the head reads both, an unknown identity reads neither, and a pass-down reaches the other department without exposing the original note. This holds when each agent names its own identity honestly. Issue I-1 is about what happens when it does not.

**What we expect before any run.** To be written before the first run, so it can be compared with what happens.

**Known weak points.** Issues I-1, I-2 and I-3 in section 7.

---

## 4. Decision log

**D-1 · Nov 14, 11:20 · Decided · Start from an empty repository.**
This repository was created during the event. Before the event we rehearsed an agent-run lab in a separate, private repository. No file from the rehearsal is in this one. Where a lesson from the rehearsal shapes a decision, the entry says so.

**D-2 · Nov 14, 11:20 · Decided · Exa for research, past.dev for memory.**
These are the two main tools. Exa is how agents find outside sources. past.dev is the shared memory. Each must be part of the main loop, not a side feature.

**D-3 · Nov 14, 11:27 · Decided · Install Exa's `build-with-exa` skill.**
Source: `exa-labs/agent-skills` on GitHub (MIT licence), version 0.3.0, installed with `npx skills add exa-labs/agent-skills --skill "build-with-exa"`. The content hash is recorded in `skills-lock.json`. It is documentation only (one main file and 13 reference files, no scripts): it tells a coding agent which Exa endpoint fits which job and how to shape requests. Checked before use: the main file was read in full and the reference files were scanned for commands that could do harm; none were found.

**D-4 · Nov 14, 11:20 · Proposed · Memory access follows role.**
Workers read their own department; heads read everything and pass down what is needed. Full description in section 3. Alternatives not chosen: one memory everyone can read (no boundary to show, and workers get flooded), and fully separate memories with no heads (departments cannot learn from each other).

**D-5 · Nov 14, 11:20 · Decided · Modal only where it earns its place.**
We use Modal if it does something the lab needs and a laptop cannot. We do not add it for its own sake. Candidate uses are in section 6.

**D-6 · Nov 14, 11:20 · Decided · Keep this log.**
Every agent decision, layout change, observation, idea, issue and source goes here, at the time it happens.

## 5. Agent observations

Things the agents did that we did not expect. Empty until the first run.

## 6. Ideas

**N-1 · Nov 14, 11:30, updated 11:33 · Open · Enforce the memory boundary with infrastructure, not with a prompt.**
The problem is issue I-1: past.dev trusts whatever identity the caller names. The idea is to let the compute platform vouch for who is calling. After reading the Modal docs, the pieces exist:

1. One small **memory gateway** holds the past.dev key. No agent does.
2. Each agent runs in its own Modal sandbox and is handed a Modal *connect token* made for it alone. Modal attaches the token's metadata (the agent's identity) to every request the gateway receives, in a header the docs call unspoofable.
3. The gateway reads the identity from that header, never from the request body, and uses it for the past.dev call. It sets the audience on every write.
4. Each worker sandbox gets an outbound allowlist that leaves out past.dev, so a worker cannot go around the gateway even if it found a key.

A worker then cannot read as a head, whatever its prompt says, and a judge can test it. What must still be checked by running it: that connect tokens work for sandbox-to-sandbox calls, and how the allowlist (a Beta feature) behaves. Modal also has a Beta feature that injects a key into a sandbox's outgoing requests from outside the sandbox. That hides the key but not the identity field, so on its own it does not close I-1.

**N-2 · Nov 14, 11:33 · Open · Fork a researcher.**
Modal can snapshot a sandbox's files and start several new sandboxes from the snapshot. A worker that reaches a fork in its investigation could be copied, with each copy following one branch from the same starting state. Only useful if the lab's work has such forks; not planned yet.

**N-3 · Nov 14, 11:33 · Open · Replay a decision with what the agent knew at the time.**
past.dev can answer a read "as of" an earlier moment. For any decision in this log we could re-run the read as of the decision time and show exactly what memory the agent had, then compare with what it would see now. That makes each decision checkable after the fact.

## 7. Issues

**I-1 · Nov 14, 11:30, confirmed from the docs 11:33 · Open · A read boundary is only as strong as the identity on the request.**
The caller names the identity it is reading as, and one project key covers the whole project. past.dev's access-control page says it directly: a project key "reaches every audience of the project". The same key can also change who is in each audience. So if every agent holds the key, a worker could read as a head, or rewrite the rules. Separate past.dev projects would isolate fully, but they share nothing, and the starter plan allows one project. Candidate fix: N-1. Not yet tested with a key.

**I-2 · Nov 14, 11:33 · Open · A write without an audience is readable by everyone.**
In past.dev a fact sent without an audience is visible to every identity. Re-sending a fact without repeating its audience also makes it visible to everyone. An identity past.dev has never seen gets that shared set and no error. So one careless write leaks across departments. Planned guard: all writes go through one code path that refuses a write with no audience, and the shared set is kept empty unless we decide to use it for lab-wide notices. **Measured Nov 14 (M-1):** a fact written with no audience was read back by a worker in another department and by an identity past.dev had never seen.

**I-3 · Nov 14, 11:33 · Open · Memory is not instant.**
past.dev accepts a write at once but derives memory from it in the background, one write after another. The raw text is searchable within seconds; the derived facts come later, and a read reports how much is still pending. An agent that writes and reads back straight away may not see its own fact in derived form. The event brief also asks teams to keep data to tens of documents. **Measured Nov 14 (M-1), four short facts, one run:** the writer could read its own fact back 2.9 seconds after sending it; past.dev reported each write fully processed after 7 to 17 seconds; reads took 0.6 to 2.0 seconds. So the delay is small at this size. Not yet measured: the delay with many writes queued.

## 8. Research behind decisions

All sources below were read on Nov 14. This section is what documentation and papers say. What we measured ourselves is in section 9.

| Topic | Source | Used for |
|---|---|---|
| Audience on write, identity on read; rule and fixed audiences; traits | past.dev Concepts, https://past.dev/docs/memory-api/concepts | Section 3, D-4 |
| What a leaked key reaches; projects versus audiences; pass-downs must be new facts | past.dev Access control, https://past.dev/docs/memory-api/access-control | I-1, I-2, section 3 |
| Background processing, pending reads, "as of" reads, credits and rate limits | past.dev How requests behave, https://past.dev/docs/memory-api/how-it-works | I-3, N-3 |
| Exact request fields; request log and trace routes | past.dev Endpoint reference, https://past.dev/docs/memory-api/api-reference | Section 3 |
| Connect tokens and the verified identity header; outbound allowlists | Modal, Sandbox networking and security, https://modal.com/docs/guide/sandbox-networking | N-1 |
| Injecting a key from outside the sandbox (Beta) | Modal, Sandbox secret injection, https://modal.com/docs/guide/sandbox-secret-injection | N-1 |
| Snapshot a sandbox and start several from it | Modal, Sandbox snapshots, https://modal.com/docs/guide/sandbox-snapshots | N-2 |
| Which Exa endpoint for which job | `build-with-exa` skill, `.agents/skills/build-with-exa/SKILL.md` | D-3 |
| A summary can hide a restricted or untrusted source, so access limits must follow a fact through everything derived from it | Wang et al., "MAP-Graph: Provenance-Aware Shared Memory for Multi-Agent Workflows", Aug 2026, https://arxiv.org/abs/2608.10509 (found with Exa; abstract read, not the full paper) | Pass-downs record their source, section 3 |
| Four ways shared agent memory fails: leaks, stale facts spreading, contradictions that persist, lost provenance. Their live test found a scope rule that held on one API path and not on another | Margalit et al., "Governed Shared Memory for Multi-Agent LLM Systems", Jun 2026, https://arxiv.org/abs/2606.24535 (found with Exa; abstract read, not the full paper) | Why we test boundaries with real calls instead of trusting the docs (M-1) |

**past.dev facts we are building on** (from the pages above):

- A read returns the shared set plus the memory of every audience the reader's identity is in, and nothing else.
- A derived memory is never readable by more identities than the facts it rests on.
- Each derived memory has a kind. A statement the source gives as a guess is marked `hypothetical`, an unadopted option `proposed`, a withdrawn one `retracted`. Useful for a lab, where a hypothesis must not be read as a finding.
- A later value for the same property supersedes the earlier one, and the read says when.
- Every call gets a request id and appears in a request log, so each read an agent makes can be cited as evidence.
- Costs and limits on the starter plan: one credit per 350 bytes written, one credit per read, 500 requests a minute, up to 8,000 tokens of evidence per read, one project.

## 9. Measurements

Each entry gives the command, what came back, and what it does not show.

**M-1 · Nov 14, 11:56 · Measured · past.dev read boundaries hold for honest identities.**
Command: `python3 checks/memory_boundary.py`. Record: `checks/results/memory_boundary_1114115657.json`, which lists the past.dev request id of every read.
The script builds two departments with one worker each and one head, using the rule from section 3, writes one fact per department and one fact with no audience, then reads as each agent.

| Check | Expected | Got |
|---|---|---|
| Worker reads its own department's fact | yes | yes |
| Worker reads the other department's fact | no | no |
| Head reads department A's fact | yes | yes |
| Head reads department B's fact | yes | yes |
| Unknown identity reads department A's fact | no | no |
| Fact with no audience is read by the other department's worker | yes | yes |
| Fact with no audience is read by an unknown identity | yes | yes |
| After a head's pass-down, the other department's worker reads the value | yes | yes |
| ... and still does not get department A's original note | no | no |

9 of 9 as expected. Timings are under I-3.
What it does not show: one run, four short facts, and every agent named its own identity honestly. It says nothing about an agent that names another identity (I-1). The check data was deleted afterwards.
Also seen: with this little memory, a read returned everything the reader was allowed to see, whatever the question. The worker in department B asked about department A's value and got six results, none containing it. So an agent must check what a result says; getting results back does not mean the question was answered.

**M-2 · Nov 14, 11:57 · Measured · Exa key works.**
One search with highlights, three results: HTTP 200 in 2.1 seconds, cost reported by Exa $0.007. A follow-up call fetching the text of two pages cost $0.002. The search returned the two papers now listed in section 8.

**M-3 · Nov 14, 11:58 · Measured · Modal login works.**
Started one sandbox, ran one command in it and shut it down: 3.7 seconds end to end with Modal client 1.6.4. This only shows the account works. Idea N-1 is not tested yet.
