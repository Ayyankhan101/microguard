# Spec 0003: Evasion-resistant fingerprinting

Epic: [0000-epic-paid-alternative-parity.md](0000-epic-paid-alternative-parity.md)
Depends on: [0001-real-time-blocking.md](0001-real-time-blocking.md) (must be merged first)
Status: **SHIPPED** — v3.0.0 (`29aaf79`), with deliberate deviations

> **Read this before planning against the code below.**
>
> | Spec says | What exists |
> |---|---|
> | serve the script from the `auth_request` endpoint | That location is `internal`; a browser cannot reach it. A second, PUBLIC nginx location is required — see [`howto-deploy-behind-nginx.md`](../docs/howto-deploy-behind-nginx.md) |
> | add `fingerprint_hash` to `LiveSession` | `LiveSession` is derived from a Redis LIST per request, never stored. The hash lives in `mg:v1:fp:{ip}` |
> | `microguard:fp_ips:{hash}` read at scoring time | The cross-IP count is denormalized onto the per-actor record at submission time: the count is keyed by hash, and the hash is only known after reading the IP's record, so the second read cannot be pipelined |
> | check-then-set on the binding | `SET NX`. A GET-then-SET version shipped and was defeated by twelve threads — one IP with a live session could credit unlimited harvested hashes |
> | routes on the check server only | All three hosts serve them. The epic requires both deployment modes; the middleware was a pure pass-through and would have been silently second-class |
>
> **Two things this spec did not anticipate.**
>
> `crypto.subtle` only exists in a **secure context**, so the script is inert on
> a plain-HTTP origin. Combined with the absence rule, that would make an HTTP
> site look like it was full of bots. It logs a console warning instead of
> returning silently.
>
> Rule 1 infers automation from an ABSENT fingerprint, which is also what a
> batch scan, an API client, and a site that never embedded the script look
> like. It is gated on `Signals.fp_resolved` so `microguard scan` cannot label
> every session in every log file a bot.
>
> The honesty constraint in the Context section below still holds and is
> reproduced in the README.
Priority: Medium
Effort estimate: 4-5 days

## Context

Every signal Microguard has today (timing, endpoint diversity, header
consistency) is derived from server-visible request metadata — trivial for a
bot author to spoof once they know the rules (headers and timing can be
randomized deliberately, exactly like `training/generate.py::generate_stealthy_bot_session`
already simulates for eval purposes — see `tests/test_training_quality.py::TestAdversarialRobustness`).
Paid vendors raise the bar with client-side signals a bot has to actually
execute a real browser environment to fake convincingly: canvas/WebGL
fingerprints, font enumeration, JS execution itself as proof of a real
rendering engine.

**Honesty constraint for this spec (carry into the README and any marketing
copy derived from it):** this raises the bar, it does not create an
unbeatable system. A sufficiently resourced bot running real headless Chrome
can pass a fingerprint check. This spec's job is to catch the much larger
population of bots that DON'T bother — plain HTTP clients, simple scripts,
naive scrapers — not to win an arms race against state-of-the-art evasion.
Do not let this spec's output claim otherwise.

## Current State (verified 2026-09-06)

- No client-side JavaScript exists anywhere in this repo. `microguard/`
  is 100% server-side Python.
- `microguard/live/server.py` (from spec 0001) will be the only HTTP-facing
  component this can hook into — this spec depends on 0001 existing first
  because there is no live per-request server to attach a `/fp` endpoint to
  otherwise.
- `microguard/live/state.py`'s `LiveSession` (from spec 0001) has no field
  for a fingerprint today — this spec adds one.
- The 19-feature vector (`features.py::FEATURE_NAMES`) is fixed and
  `data/model.json` is trained against it — fingerprint signals must NOT be
  added to that vector (would require retraining and break the shipped
  model's compatibility). They're a separate heuristic-tier signal, same
  reasoning as spec 0002's threat-intel integration.

## Proposed Change

### 1. Client-side fingerprint script

New file `microguard/live/static/fingerprint.js` — vanilla JS, no build
step, no dependency, roughly 100-150 lines. Website owners embed it via:
```html
<script src="/microguard/fingerprint.js" defer></script>
```
(served by `microguard/live/server.py`'s new `GET /fingerprint.js` route,
which serves the static file — same microservice from spec 0001, one more
endpoint).

Collects, on page load:
- Canvas fingerprint (render fixed text/shapes to an offscreen canvas, hash
  the pixel data — standard technique, many open reference implementations
  exist to model the exact drawing operations against, do not invent novel
  drawing primitives).
- WebGL renderer/vendor strings (`WEBGL_debug_renderer_info`).
- `navigator.hardwareConcurrency`, `screen.width/height`, timezone offset
  (`Intl.DateTimeFormat().resolvedOptions().timeZone`).
- Installed font subset (via canvas `measureText` width-probing against a
  known font list — standard technique).

**Privacy requirement:** hash the combined raw signals client-side
(`SubtleCrypto.digest('SHA-256', ...)`) before sending — the server receives
only a hash, never raw fingerprint components. Document this explicitly in
the README's privacy notes; do not silently collect and transmit raw
identifying data.

POSTs `{fingerprint_hash: string}` to `/microguard/fp` on page load
(`fetch`, `keepalive: true` so it survives a fast navigation away).

### 2. Server-side endpoint + signal

`microguard/live/server.py` gains:
```
POST /fp   body: {"fingerprint_hash": "<hex>"}
```
Identifies the calling session the same way `/check` does (via
`$remote_addr`/`X-Forwarded-For` + `User-Agent`, matching spec 0001's
`score_live_request` actor-identification scheme — reuse it, don't invent a
second one), stores the hash against that `LiveSession` in Redis (extend
`LiveSession`/`RedisSessionStateStore` from 0001 with a `fingerprint_hash`
field).

### 3. New heuristic signals

New function in `microguard/labeler.py`, same `_check_*` pattern as 0002:
```python
def _check_fingerprint_signals(session: Session) -> tuple[bool, str]:
    """
    1. No fingerprint received within N seconds of the session's first
       request (default N=5s, module constant FINGERPRINT_GRACE_SECONDS),
       despite the session having >= M subsequent requests (default M=3) —
       real browsers execute the JS almost immediately; a plain HTTP client
       or curl-style script never calls /fp at all. -> bot signal.
       NOTE: this only applies to sessions hitting page routes that would
       have served the script tag, not raw API-only clients that never load
       an HTML page in the first place (a legitimate REST/GraphQL API
       client has no reason to ever fire this JS) — the exact rule for
       "should this session be expected to have a fingerprint" needs a
       config knob (e.g. only apply to sessions whose first request's URL
       matches a configured 'page routes' pattern) — decide and document
       this exactly during implementation, do not apply it blindly to every
       session including pure API traffic, or it will reintroduce a
       single-endpoint-API-style false positive class this project already
       spent real effort fixing once (see CHANGELOG's GraphQL/gRPC fixes).
    2. The SAME fingerprint_hash appears across many distinct IPs in a short
       window (default: >= 5 distinct IPs sharing one hash within 10
       minutes, both module constants) -> signals a bot farm / proxy
       rotation reusing one browser profile. Requires a cross-session lookup
       (hash -> set of IPs), which needs its own Redis key
       (microguard:fp_ips:{hash}, TTL matching the window) — a new method on
       RedisSessionStateStore or a small addition to redis_store.py.
    """
```

## Acceptance Criteria

1. `fingerprint.js` runs in a real browser (headless Chromium via the
   project's existing pattern — see how `2026-09-06` scratch tooling used
   Playwright for screenshot generation this session, same approach
   applies) and produces a stable SHA-256 hex hash for the same
   browser/environment across repeated runs, and a DIFFERENT hash when a
   meaningfully different environment is simulated (e.g. different
   viewport/timezone via Playwright's context options) — proves the
   fingerprint actually varies with environment rather than being constant.
2. `POST /fp` correctly associates the hash with the right `LiveSession`
   (same actor-identification as `/check`) — verified against a real Redis.
3. `_check_fingerprint_signals` rule 1 fires for a session with 3+ requests
   to page routes and no fingerprint after the grace period; does NOT fire
   for a session confined to configured API-only routes (the exact
   exemption mechanism from the design note above) even with zero
   fingerprint.
4. `_check_fingerprint_signals` rule 2 fires when 5+ distinct IPs share one
   fingerprint hash within the window; does not fire below that threshold.
5. End-to-end: `microguard serve` + the real `fingerprint.js` loaded in a
   real headless-browser session (Playwright) against a page that embeds it
   → the session's fingerprint is recorded and does NOT get bot-flagged by
   rule 1 (proves the whole client-to-server pipeline actually works, not
   just the pieces in isolation).
6. README documents: what the script collects (exact list, matching this
   spec's item 1), that only a hash is transmitted (privacy claim, must be
   true — verify by inspecting the actual network payload in the
   Playwright-based test, don't just assert it in prose), and the explicit
   "this raises the bar, it is not unbeatable" caveat from this spec's
   Context section — word for word intent, not diluted into marketing copy.

## Testing Plan

| Layer | What | Count |
|-------|------|-------|
| Unit | `_check_fingerprint_signals` rule 1 (grace period, page-route exemption) | +3 |
| Unit | `_check_fingerprint_signals` rule 2 (shared-hash-across-IPs threshold) | +2 |
| Integration | `POST /fp` → Redis association (real Redis) | +2 |
| Integration | `fingerprint.js` in real headless Chromium produces a stable, environment-sensitive hash | +2 |
| E2E | Full page load with embedded script → `/fp` called → session not flagged | +1 |
| Privacy | Network payload inspection confirms only the hash is sent, no raw signal components | +1 |

## Rollback Plan

The `/fp` endpoint and `_check_fingerprint_signals` are additive; removing
the `<script>` tag from a site's pages stops fingerprint collection
immediately (script never calls home), and the grace-period rule then simply
never fires (no fingerprint ever arrives, but rule 1 requires page-route
traffic AND enough elapsed time — if this becomes a false-positive problem in
practice, disabling is: don't embed the script, and consider a config flag
to fully disable rule 1 while keeping rule 2, decide the exact flag shape
during implementation).

## Effort Estimate

| Component | Estimate |
|-----------|----------|
| `fingerprint.js` (canvas/WebGL/font collection + hashing) | 1.5 days |
| `/fp` endpoint + Redis extension | 0.5 day |
| `_check_fingerprint_signals` (both rules, incl. page-route exemption design) | 1 day |
| Playwright-based integration tests | 1 day |
| README + privacy documentation | 0.5 day |
| **Total** | **~4.5 days** |

## Files Reference

| File | Change |
|------|--------|
| `microguard/live/static/fingerprint.js` | New |
| `microguard/live/server.py` | Add `GET /fingerprint.js`, `POST /fp` routes (extends spec 0001's file) |
| `microguard/live/state.py` | Add `fingerprint_hash` field to `LiveSession` |
| `microguard/live/redis_store.py` | Add fingerprint storage + cross-IP hash lookup |
| `microguard/labeler.py` | Add `_check_fingerprint_signals`, `FINGERPRINT_GRACE_SECONDS`, related constants |
| `tests/live/test_fingerprint.py` | New |
| `README.md` | New subsection under "Real-Time Blocking" — what's collected, privacy, the honesty caveat |

## Out of Scope

- Defeating browser anti-fingerprinting tech (Chrome fingerprint noise,
  Firefox RFP) — explicitly not this spec's job, see Context.
- Any paid CAPTCHA/challenge vendor (see epic's Out of Scope). If a
  proof-of-work challenge (Anubis-style) is wanted for borderline scores,
  that's a separate future spec, not bundled into this one.
- Mobile app / non-browser client fingerprinting (this is a web-page script
  only).

## Related

- Epic: [0000-epic-paid-alternative-parity.md](0000-epic-paid-alternative-parity.md)
- `microguard/training/generate.py::generate_stealthy_bot_session` — the
  existing synthetic adversarial-bot model this spec's real signals are
  meant to actually catch (that generator simulates spoofed headers/timing;
  this spec adds a signal that generator doesn't currently account for).
