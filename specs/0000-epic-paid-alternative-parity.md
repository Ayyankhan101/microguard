# Epic: Close the gap with paid bot-management platforms

Status: **COMPLETE** — all four children shipped, v3.0.0 (2026-09-12)

> Every child spec carries a reconciliation header. Read it before planning
> against that spec's code sketches: all four describe APIs that shipped
> differently, and three of them were re-based against the merged foundation
> rather than implemented as written.
>
> The accurate design record is
> [`docs/designs/remaining-epic.md`](../docs/designs/remaining-epic.md).
Owner: solo (REPO_MODE: solo)
Created: 2026-09-06

## Context

Microguard v2.0 is a log-analysis CLI: `microguard scan <logfile>` batch-processes
a static access log and reports bot traffic after the fact; `microguard probe`
does a one-off live fingerprint of a target. It has no way to block a bot
request before it reaches the backend, no shared threat intelligence, no
resistance to a bot that spoofs headers/timing, and no way to adapt to a
specific deployment's own traffic patterns over time.

Paid platforms (Cloudflare Bot Management, DataDome, PerimeterX/HUMAN, Akamai
Bot Manager) do all four. This epic closes that gap in four sequenced child
specs so Microguard becomes something a small team could actually run instead
of paying for one of those — not by matching their global scale, but by
covering the 80% of value that doesn't require a multi-tenant network effect.

This was the ORIGINAL intent of this project — `plan.md` at the repo root
(first commit, predates the log-analysis pivot) already describes "detect and
block malicious bot traffic in real-time" via a "framework-agnostic adapter
layer." This epic is that original plan, executed properly, on top of the
now-real (not synthetic) detection engine v2.0 built.

## Decisions locked for this epic (do not re-litigate in child specs)

These were decided explicitly by the project owner before drafting — child
specs implement them, they don't re-derive them:

1. **Both** an nginx `auth_request` microservice AND a Python WSGI/ASGI
   in-process middleware ship (not just one) — see 0001.
2. **Redis** is the live session-state store, from day one — not in-memory.
   This means **the real-time blocking feature is NOT zero-dependency** —
   it requires a running Redis instance. This is a deliberate, known
   trade-off, not an oversight. It must be reflected honestly in the README
   (a new "Real-time mode" section separate from the existing "Zero external
   dependencies" claim, which stays true for `scan`/`probe`/`watch`).
3. **AbuseIPDB is included from day one** in the threat-intel spec (0002),
   not deferred — accept that this adds a "get a free API key" setup step
   for that one optional signal (the feature must degrade gracefully with no
   key set, per 0002's spec).

## Child specs

| # | Title | Status | Shipped in |
|---|-------|--------|------------|
| [0001](0001-real-time-blocking.md) | Real-time inline blocking | Shipped | `6df9f01`, refined in v3.0.0 |
| [0002](0002-threat-intel-feeds.md) | Threat-intel feed integration | Shipped, one item open | v3.0.0 |
| [0003](0003-evasion-resistant-fingerprinting.md) | Evasion-resistant fingerprinting | Shipped | v3.0.0 |
| [0004](0004-adaptive-learning.md) | Per-deployment adaptive learning | Shipped | v3.0.0 |

**Decision 3 of the locked list below did not survive contact.** The spec's
global `MICROGUARD_DISABLE_THREAT_INTEL` switch was replaced by per-source
promotion in `mg:v1:config`: every signal is observe-only until an operator
promotes it, which is strictly more control than one env var. The AbuseIPDB
"from day one" part held — it ships, keyed, and is inert without a key.

**The open item** is spec 0002's hosting-range combination rule. The signal is
resolved and recorded; no rule reads it, and it is deliberately absent from
`KNOWN_SIGNAL_SOURCES` so nobody can promote a control that does nothing.

## Dependency graph

```
0001 Real-time blocking (foundation)
  │  introduces: live per-request scoring path, Redis session store,
  │  microguard/live/ package, shared microguard/scoring.py
  │
  ├──> 0002 Threat-intel feeds
  │      adds a new high-confidence heuristic rule that 0001's live
  │      scoring path picks up automatically (same label_session() call)
  │
  ├──> 0003 Evasion-resistant fingerprinting
  │      adds a new endpoint + signal to the microservice from 0001
  │
  └──> 0004 Adaptive learning
         retrains against outcomes CAPTURED by 0001's live scoring path;
         cannot exist before there is live traffic to learn from
```

**Sequencing rationale:** 0001 is the foundation because 0002-0004 all hook
into the live per-request scoring pipeline it introduces (`microguard/live/`).
None of them can be meaningfully built, tested, or even make sense before
that pipeline exists — 0002 has no live score to add a signal to, 0003 has no
live request to attach a fingerprint to, 0004 has no live outcomes to learn
from. Build and ship 0001 completely (including its own tests passing) before
starting 0002-0004. 0002-0004 have no ordering constraint relative to each
other and can be built in any order, or in parallel by different sessions,
once 0001 is merged.

## Out of scope (for the whole epic, not just one child)

- Rewriting or changing behavior of the existing `microguard scan`,
  `microguard probe`, or `microguard scan --watch` commands. They are
  untouched by this epic — real-time blocking is a NEW, additive command
  (`microguard serve`) and a new importable middleware, not a replacement.
- A hosted/SaaS version of Microguard.
- Matching paid vendors' cross-customer global threat-intelligence network —
  that requires a network effect across many real deployments this project
  doesn't have. 0002 covers what's achievable with free/keyless public feeds
  plus one optional keyed feed (AbuseIPDB) instead.
- Any paid CAPTCHA/challenge vendor integration (Cloudflare Turnstile,
  hCaptcha, reCAPTCHA). 0003 covers only free/open-source challenge options
  (e.g. Anubis-style proof-of-work) if a challenge step is built at all —
  confirm exact scope inside 0003, it's marked TBD there.
- Multi-tenant / SaaS-style deployment of the `microguard serve` microservice
  (auth, billing, per-customer isolation). Each deployment is assumed to be
  one team running it for their own traffic.

## Definition of Done (epic-level)

1. All 4 child specs' individual acceptance criteria pass.
2. `microguard scan`, `microguard probe`, `microguard scan --watch` (existing
   commands) still pass their full existing test suite unmodified —
   confirms this epic was purely additive.
3. README has a new top-level section (e.g. "Real-Time Blocking (beta)")
   documenting: the Redis dependency, the `microguard serve` command, the
   nginx config snippet, and the middleware install instructions —
   clearly separated from the existing zero-dependency CLI documentation
   so the two don't get conflated (this is the exact kind of doc/reality
   mismatch this project has twice caught and fixed already this session —
   see CHANGELOG "Fixed" entries for the Python-version and probe-framing
   corrections; don't reintroduce the same class of bug in docs).
4. A new top-level integration test proves the full live path end-to-end:
   start `microguard serve` against a real (test-container) Redis, fire a
   sequence of requests through it that a human session and a bot session
   would produce, assert the bot gets blocked (403) and the human doesn't
   (200) — not mocked, an actual running process.

## Related

- `plan.md` (repo root) — original pre-v2.0 project vision this epic
  completes.
- `CHANGELOG.md` `[2.0.0]` entry — the real-training-data rebuild this
  epic's detection logic (`label_session`, `extract_features`,
  `BotDetector`) is built on top of, unchanged.
