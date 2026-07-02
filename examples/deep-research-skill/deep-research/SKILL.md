---
name: deep-research
description: >-
  Answer a broad, open-ended, or multi-part question by iteratively searching
  MFS-indexed sources, following up on gaps, and synthesizing a cited report —
  the same "reason and search over private data" job zilliztech/deep-searcher
  does, but as a skill on top of `mfs-find` instead of a standalone framework.
  Use when the user asks for a report, a comprehensive/synthesized answer, a
  "what do we know about X across everything we have", or a question that
  can't be answered from a single search hit. Trigger phrases include "write
  a report on X", "deep-dive into X using our data", "research X across all
  our sources", "give me a comprehensive answer on X with citations",
  "synthesize what we know about X". Do NOT use for a single fact lookup or a
  targeted question with an obvious one-hit answer — use `mfs-find` directly
  for that; this skill is for genuinely broad, multi-angle asks. Requires
  `mfs-find` for the underlying search/read mechanics.
---

# Deep research over MFS-indexed sources

## 1. What this is (and what it replaces)

[deep-searcher](https://github.com/zilliztech/deep-searcher) is an
open-source framework that reasons over private data: it decomposes a
question, iteratively searches a vector database, evaluates whether the
evidence is sufficient, and synthesizes a cited report. Built before
agentic coding tools existed, it had to hand-roll every piece itself —
document loaders, a multi-provider LLM/embedding/vector-DB matrix, and a
custom iterative-retrieval orchestration loop in Python.

None of that orchestration is needed anymore. MFS already does ingestion +
hybrid search over many source types, and an agent's own reasoning loop
already does "search, judge, follow up, repeat" natively once it has a
search tool. This skill is that missing piece: not new retrieval code, just
the **strategy** for running deep-searcher's decompose → search → evaluate
→ synthesize loop through `mfs search` / `mfs cat`.

This skill assumes `mfs-find` for the actual command mechanics (search
modes, locators, `--peek`/`--skim`, index-status diagnosis). Read that
skill for those details — this one only adds the multi-round strategy on
top.

## 2. Precondition: sources must be indexed

Same as `mfs-find`: `mfs status` / `mfs connector inspect <uri>` first. If
nothing relevant is indexed yet, **redirect to `mfs-ingest`** — don't run a
research loop against an empty index.

## 3. The loop

```
 decompose            search rounds              evaluate           synthesize
┌───────────┐   ┌───────────────────────┐   ┌──────────────────┐   ┌───────────┐
│ 2-4 angles│ → │ mfs search per angle,  │ → │ enough coverage? │ → │  cited    │
│ on the Q  │   │ semantic + keyword     │   │ gaps → new angles│   │  report   │
└───────────┘   └───────────────────────┘   └──────┬───────────┘   └───────────┘
                        ▲                            │ not enough
                        └────────────────────────────┘ (max ~4 rounds)
```

1. **Decompose.** Break the question into 2-4 concrete angles before
   searching anything. "Write a report on our rate-limiting story" becomes:
   *current implementation*, *past incidents/bugs*, *design
   discussion/rationale*, *config knobs*. A single search for the raw
   question under-recalls on anything but the narrowest asks.

2. **Search each angle**, scope per `mfs-find` §6-7 (hybrid by default,
   `--all` for genuinely cross-source asks, scoped to 2-3 likely sources
   otherwise):
   ```bash
   mfs search "<angle 1>" <scope> --top-k 15
   mfs search "<angle 2>" <scope> --top-k 15
   ...
   ```
   Track **distinct objects** found (dedupe by `source`), not raw hit
   count — five chunks from one file is one citation, not five.

3. **Evaluate coverage before reading everything.** For each angle: is
   there at least one strong hit? For the question as a whole: do the
   found objects, read together, actually answer it, or do they only
   establish that the topic exists? Common gaps: an angle returned nothing
   (rephrase it, don't drop it silently), all hits are from one source
   type when the question implies more (e.g. only code, no design docs),
   or a hit references something ("see the migration doc") not yet found.

4. **Follow up, don't restart.** Gaps become 1-3 new targeted searches —
   reuse the vocabulary actually found in round 1 (a real error code, a
   real doc title) instead of guessing more synonyms of the original
   question. This is the step deep-searcher's LLM-driven query refinement
   automated; here it's just another `mfs search` call informed by what
   came back.

5. **Stop condition.** Whichever comes first:
   - a round adds no new distinct objects (saturation), or
   - every decomposed angle has a strong hit and no unresolved reference
     remains, or
   - **~4 rounds** (glance at whether the effort is still paying off,
     don't hard-stop exactly at 4 if one more obvious query would close a
     real gap — but don't grind past it chasing marginal recall either).

6. **Read before writing.** `mfs cat --skim` (or `--peek` for code) each
   distinct candidate object before citing it — a search snippet is enough
   to judge relevance, not enough to write a claim from. Full `cat
   --range` only the sections a claim actually rests on.

## 4. Report format

- Structure by the decomposed angles (or by whatever natural sections the
  findings suggest), not by search-round order.
- **Cite inline** with the `source` URI after each claim, e.g. `... retries
  with exponential backoff (server/python/src/mfs_server/engine/pipeline.py)`.
  Never state a fact pulled from search without attributing which source
  it came from — an uncited claim in a "report" is indistinguishable from a
  guess.
- End with a flat **Sources** list of every distinct object cited, so the
  user can jump straight to any of them.
- Say plainly when an angle came up empty ("no design rationale found for
  X — only the implementation") rather than papering over the gap.

## 5. Anti-patterns

- **Don't answer a "write a report" ask from one search call.** That's the
  exact failure mode this skill exists to prevent — one query under-covers
  a multi-angle question even when the top hit looks relevant.
- **Don't keep searching after saturation.** If two rounds in a row surface
  no new distinct objects, stop and write up what's there — more rounds
  won't manufacture evidence that isn't indexed.
- **Don't cite a chunk you didn't read.** A snippet score is a relevance
  signal, not a verified fact.
- **Don't use this for a narrow, single-hit question** ("what does
  `MFS_API_TOKEN` do") — that's `mfs-find`'s job in one call; running the
  full loop on it just burns rounds for no benefit.
- **Don't silently drop an angle that returned nothing** — say so, or
  rephrase it once with different vocabulary before giving up on it.
