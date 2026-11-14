# summit-lab working log

This file explains how the lab works and why. It is updated as the work happens, so a reader can follow every decision without having been in the room.

**How to read it.** Sections 1 to 3 describe the lab as it stands now. Sections 4 to 8 are logs: newest entries go at the bottom of each one. Every entry has a date, a time (Pacific) and a status:

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
| Exa | Deep research: finding sources the agents do not already know | Decided; waiting for the key |
| past.dev | Shared memory that agents write to and read from, with limits on who can read what | Decided; waiting for the key |
| Modal | Cloud compute | Open: used only where it gives a real benefit (see section 6) |

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

**How it maps onto past.dev (planned).** One audience per department. A worker's identity belongs to its department's audience. A head's identity belongs to every audience. A pass-down is a new fact, written by a head, addressed to the receiving department's audience.

**What we expect before any run.** To be written before the first run, so it can be compared with what happens.

**Known weak point.** See issue I-1 in section 7.

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

**N-1 · Nov 14, 11:30 · Open · Enforce the memory boundary with infrastructure, not with a prompt.**
Run each worker in its own Modal sandbox that does not hold the past.dev key. The worker reaches memory only through a small gateway that adds the worker's identity itself. A worker then cannot claim to be a head, whatever its prompt says. This would answer issue I-1. Not checked against the Modal or past.dev documentation yet.

## 7. Issues

**I-1 · Nov 14, 11:30 · Open · A read boundary is only as strong as the identity on the request.**
From past.dev's public description, the caller names the identity it is reading as, and one project key covers the whole project. If every agent holds the key, a worker could ask as a head and read everything. To confirm against the API documentation once we have a key. Candidate fix: N-1.

## 8. Research behind decisions

| Topic | Source | Used for |
|---|---|---|
| Audience on write, identity on read | past.dev home page and Memory API docs, https://past.dev/docs/memory-api/overview (home page read Nov 14; docs still to read) | Section 3, D-4, I-1 |
| Which Exa endpoint for which job | `build-with-exa` skill, `.agents/skills/build-with-exa/SKILL.md` | D-3 |
