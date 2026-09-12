# Spec 0001: Real-time inline blocking

Epic: [0000-epic-paid-alternative-parity.md](0000-epic-paid-alternative-parity.md)
Status: **SHIPPED** — merged 2026-09-11 (`6df9f01`), refined in v3.0.0

> **Read this before planning against the code below.**
>
> This spec is kept as the record of the reasoning. The API sketches in it are
> NOT what shipped, and planning against them wastes a session — it wasted most
> of one already.
>
> | Spec says | What exists |
> |---|---|
> | `score_live_request(...)` | `LiveScorer.score_request(entry)` — a class, dependencies injected in the constructor |
> | `record_request(...) -> LiveSession` | `-> SessionSnapshot` (session + resolved signals, one round trip) |
> | `SessionStateStore` with get/set/keys/incr_score | Cut to `record_request` + `delete`. The get/set pair lost concurrent appends: 9 of 20 survived under 20 threads |
> | session stored as a JSON blob | `live:v2:{ip}` is a capped LIST; the clock derives from retained entries so the cap is a sliding window |
>
> It also shipped a dashboard, a runtime-configurable threshold, and a decision
> feed (`mg:v1:events`), none of which are in this spec.
>
> The current shape is documented in
> [`docs/reference-live-api.md`](../docs/reference-live-api.md).
Priority: Critical
Effort estimate: 6-8 days (breakdown below)

## Context

Microguard currently only classifies bot traffic **after the fact**, from a
static log file (`microguard scan`) or a one-off live probe of a target
(`microguard probe`). It has no mechanism to score a request and block it
*before* it reaches the application — which is the single defining capability
of every paid bot-management product (Cloudflare Bot Management, DataDome,
PerimeterX, Akamai Bot Manager all sit inline). Without this, Microguard is an
audit tool, not a bot-management tool, no matter how accurate its scoring is.

This spec builds that inline path, reusing the existing, already-real,
already-tested detection engine (`extract_features`, `label_session`,
`BotDetector.predict`) — it adds live plumbing around that engine, it does
not change how bots are detected.

## Current State (verified 2026-09-06)

- `microguard/cli.py::scan_logfile()` (`cli.py:40`) is the only place that
  currently orchestrates parse → session → features → label → score → decide.
  It operates on a fully-read static file (`parser.parse_file()`, `cli.py:62`).
- The heuristic/model score blend is duplicated, inline, in two places, with
  the same math both times:
  - `microguard/cli.py:131-144` (inside `scan_logfile`)
  - `microguard/watch.py` (inside `watch_logfile`, same formula)
  This is not hypothetical risk — this exact duplication caused a real bug
  this session: a fix to the blend logic was made in `cli.py` and NOT
  propagated to `watch.py`, shipped, and only caught by a later test written
  specifically to check `watch.py` independently. See `CHANGELOG.md`
  `[Unreleased]` → "Fixed" → "watch.py had the same heuristic/model
  score-blending asymmetry bug already fixed in cli.py". A third inline copy
  must not be created for this spec.
- `microguard/features.py::Session` (`features.py:93`) is a plain class:
  `__init__(self, ip: str, user_agent: str = "")`, `.add_request(entry:
  LogEntry)`, `.duration` (property), `.request_count` (property),
  `.requests: list[LogEntry]`.
- `microguard/parser.py::LogEntry` (`parser.py` class, `__slots__`) requires:
  `ip, timestamp (datetime), method, url, status (int), size (int), referer,
  user_agent`, optional `raw_line`.
- `microguard/features.py::extract_features(session: Session) -> list[float]`
  (`features.py:173`) — pure function, no I/O, takes a `Session`, returns the
  19-dim feature vector. **Do not modify its signature or the 19-length
  contract** — `data/model.json` was trained against exactly this shape.
- `microguard/labeler.py::label_session(session: Session) -> tuple[str,
  float, str]` (`labeler.py:221`) — returns `(label, confidence, reason)`
  where label is `'bot' | 'human' | 'automated-integration'`.
- `microguard/model.py::BotDetector` (`model.py:23`) — `__init__(model_path:
  str | None)`, `.predict(features: list[float]) -> float`.
- No Redis usage anywhere in the codebase today (`grep -rn redis
  microguard/` returns nothing). No `microguard/live/` package exists. No
  `microguard serve` command exists.
- `setup.py` `install_requires` is `["micrograd"]` only.

## Proposed Change

### 1. Extract the shared scoring function (do this first — unblocks the rest)

New file `microguard/scoring.py`:

```python
"""Shared heuristic/model score blending — the ONE place this logic lives.

Extracted from cli.py::scan_logfile and watch.py::watch_logfile, which had
duplicated this exact formula independently and drifted out of sync once
already (see CHANGELOG). Both must be refactored to call this instead of
keeping their own inline copies.
"""


def compute_combined_score(
    heuristic_label: str,
    heuristic_confidence: float,
    model_score: float,
) -> float:
    """Blend heuristic + model scores: 60% model / 40% heuristic.

    A confident heuristic 'bot' call floors the score up to at least its own
    confidence (a strong rule shouldn't be diluted by a weak model score).
    A confident heuristic 'human' call symmetrically caps the score down —
    without this, the model's independent score can override a heuristic
    that correctly recognizes e.g. a single-endpoint API session as human.
    """
    combined = 0.6 * model_score + 0.4 * heuristic_confidence
    if heuristic_label == 'bot':
        combined = max(combined, heuristic_confidence)
    elif heuristic_label == 'human':
        combined = min(combined, 1.0 - heuristic_confidence)
    return combined
```

Refactor call sites:
- `microguard/cli.py:131-144` → replace the inline block with
  `combined_score = compute_combined_score(heuristic_label, heuristic_conf, model_score)`
  (keep the `automated-integration` short-circuit above it unchanged — that
  branch already returns early and never reaches the blend).
- `microguard/watch.py` (same block, inside the per-session loop in
  `watch_logfile`) → same replacement.

No behavior change — `tests/test_cli.py::TestScoreBlendingSymmetry` and
`tests/test_watch.py::TestWatchLogfile::test_score_blending_symmetry_graphql_not_flagged`
must still pass unmodified after this refactor (they're the regression tests
for this exact logic — if either needs to change, the refactor broke
something).

### 2. Session-state store interface + Redis implementation

New package `microguard/live/__init__.py` (empty, just makes it a package).

New file `microguard/live/state.py`:

```python
"""Live per-actor session tracking for real-time scoring.

LiveSession duck-types microguard.features.Session on purpose: same .ip,
.user_agent, .requests, .duration, .request_count — so
features.extract_features() and labeler.label_session() work UNCHANGED
against it. Do not reimplement feature extraction here.
"""

from typing import Protocol

from ..parser import LogEntry


class LiveSession:
    """Same shape as features.Session — duck-typed, not subclassed, so this
    module has zero import-time dependency on features.py."""

    def __init__(self, ip: str, user_agent: str):
        self.ip = ip
        self.user_agent = user_agent
        self.requests: list[LogEntry] = []

    def add_request(self, entry: LogEntry) -> None: ...  # mirrors Session.add_request
    @property
    def duration(self) -> float: ...
    @property
    def request_count(self) -> int: ...


class SessionStateStore(Protocol):
    def record_request(self, ip: str, user_agent: str, entry: LogEntry) -> LiveSession:
        """Append entry to this actor's session (creating it if new) and
        return the up-to-date LiveSession for scoring."""
        ...
```

New file `microguard/live/redis_store.py`:

```python
"""Redis-backed SessionStateStore. Requires `pip install microguard[live]`.

Key shape: microguard:session:{ip}:{sha256(user_agent)[:16]}
Value: a Redis LIST of JSON-serialized LogEntry dicts (via LogEntry.to_dict()
  / a matching from_dict()), pushed with RPUSH, capped at the last N=200
  entries per session (LTRIM) to bound memory on a session that never expires
  naturally (e.g. a long-lived legitimate client).
TTL: set/refreshed to `session_timeout_minutes * 60` seconds on every write —
  Redis expires the key natively, no separate prune job needed.
"""

class RedisSessionStateStore:
    def __init__(self, redis_url: str, session_timeout_minutes: int = 30): ...
    def record_request(self, ip: str, user_agent: str, entry: LogEntry) -> LiveSession: ...
```

Dependency: add to `setup.py`:
```python
extras_require={
    "live": ["redis>=5.0,<6"],
},
```
Base `pip install microguard` stays dependency-free (only `micrograd`).
`pip install microguard[live]` is required for `microguard serve` / the
middleware. `microguard serve`/middleware entry points must raise a clear
`ImportError` with an actionable message ("Real-time mode requires:
pip install microguard[live]") if `redis` isn't installed — not a raw
`ModuleNotFoundError` traceback.

### 3. Live scoring entrypoint

New file `microguard/live/scorer.py`:

```python
"""The live equivalent of cli.py::scan_logfile's per-session scoring block —
same functions, live data instead of a batch file."""

from ..features import extract_features
from ..labeler import label_session
from ..model import BotDetector
from ..scoring import compute_combined_score
from .state import SessionStateStore


def score_live_request(
    store: SessionStateStore,
    model: BotDetector,
    ip: str,
    user_agent: str,
    method: str,
    url: str,
    status: int,
    size: int,
    referer: str,
    threshold: float = 0.7,
) -> dict:
    """Record this request, score the resulting session, return a dict
    shaped like one entry of scan_logfile()'s 'sessions' list:
    {ip, label, score, model_score, heuristic_label, heuristic_confidence,
     heuristic_reason, request_count, duration}.

    IMPORTANT: `status` for a live request being scored BEFORE your app
    handles it is not yet known. Callers at the proxy layer (auth_request
    fires before the upstream responds) MUST pass status=0 as a sentinel —
    document this in the nginx integration doc, and make sure
    extract_features()'s error_rate/status_code_entropy features degrade
    sensibly for status=0 (verify current behavior with status=0 as part of
    this spec's acceptance criteria — do not assume, test it).
    """
```

### 4. nginx `auth_request` microservice

New file `microguard/live/server.py` — stdlib `http.server`-based (no new
dependency beyond `redis` for the store), single endpoint:

```
GET /check?ip=<ip>&ua=<url-encoded user-agent>&method=<method>&url=<url-encoded path>&referer=<url-encoded referer>
```
Returns HTTP 200 (allow) if `score_live_request(...)['label'] != 'bot'`,
HTTP 403 (block) otherwise. No response body needed (nginx `auth_request`
only inspects the status code).

New CLI command in `microguard/cli.py` (new subparser, same `argparse`
pattern as `scan`/`probe`/`info`):
```
microguard serve --port 8090 --redis-url redis://localhost:6379/0
                  --threshold 0.7 --model data/model.json
                  --session-timeout 30
```

Required nginx config snippet (put this verbatim in the README's new
"Real-Time Blocking" section, and in this spec's acceptance criteria as
something that must be tested against a real nginx, not just described):

```nginx
location = /_microguard_check {
    internal;
    proxy_pass http://127.0.0.1:8090/check?ip=$remote_addr&ua=$http_user_agent&method=$request_method&url=$request_uri&referer=$http_referer;
    proxy_pass_request_body off;
}

location / {
    auth_request /_microguard_check;
    proxy_pass http://your-backend;
}
```

### 5. In-process middleware (Python apps, no nginx needed)

New file `microguard/live/middleware.py`:

- `MicroguardASGIMiddleware` — Starlette/FastAPI-compatible, implements the
  ASGI `__call__(scope, receive, send)` protocol, calls `score_live_request`
  before forwarding to the wrapped app; returns a 403 response directly
  (no upstream call) when blocked.
- `MicroguardWSGIMiddleware` — Flask/plain-WSGI-compatible,
  `__call__(environ, start_response)`, same logic.
- Both take the same constructor args as `microguard serve`'s CLI flags
  (`redis_url`, `threshold`, `model_path`, `session_timeout`) so the two
  integration paths (proxy vs. middleware) are configured identically —
  do not invent a different config shape for the middleware.

## Acceptance Criteria

1. `microguard/scoring.py::compute_combined_score` exists; `cli.py` and
   `watch.py` both call it; zero inline duplication of the formula remains
   anywhere in the codebase (`grep -rn "0.6 \* model" microguard/` returns
   exactly one match, inside `scoring.py`).
2. `tests/test_cli.py` and `tests/test_watch.py`'s existing score-blending
   regression tests pass unmodified after the refactor.
3. `RedisSessionStateStore.record_request` correctly accumulates multiple
   requests from the same `(ip, user_agent)` into one growing session,
   verified against a real Redis (test-container or `redis-server` in CI,
   not a mock) — assert `LiveSession.request_count` increases across calls.
4. `RedisSessionStateStore` sessions expire (TTL) after `session_timeout_minutes`
   of inactivity — verified with a short timeout (e.g. 2 seconds) in a test,
   not asserted from reading the code.
5. `score_live_request` with a synthetic obvious-bot request sequence (same
   fixture shape as `tests/test_cli.py::_bot_session_lines`, adapted to
   individual live calls instead of a log file) returns `label == 'bot'`.
6. `score_live_request` with a synthetic GraphQL-shaped request sequence
   (same as `tests/test_cli.py::_graphql_session_lines`, replayed as
   individual live calls) returns `label == 'human'` — this is the live-path
   regression test for the same score-blending symmetry bug, proving the
   fix applies to the live path too, not just the batch path.
7. `microguard serve` starts, responds `200` to `/check` for a
   never-seen-before IP+UA on its first request (no session yet = no
   evidence of bot behavior = allow by default), and responds `403` after
   enough requests accumulate to trigger a `bot` label — verified by
   actually starting the server as a subprocess and making real HTTP
   requests to it in a test, not by calling internal functions directly.
8. The nginx config snippet in this spec is tested against a real nginx
   (e.g. via a Docker container in CI) proxying to a live `microguard serve`
   instance — request a path enough times to trigger blocking, confirm
   nginx returns 403, not just that the microservice does.
9. `MicroguardASGIMiddleware` wrapped around a minimal FastAPI/Starlette app
   blocks a request with a 403 under the same bot-triggering conditions as
   #7, verified with `httpx.AsyncClient` or `starlette.testclient.TestClient`
   against the wrapped app.
10. `MicroguardWSGIMiddleware` — same as #9 but for a minimal Flask app,
    using Flask's test client.
11. `pip install microguard` (no extras) does NOT install `redis` and does
    NOT fail — `import microguard` still works with zero new dependencies.
    `import microguard.live` without `redis` installed raises the
    documented actionable `ImportError`, not a bare `ModuleNotFoundError`.
12. README has the new "Real-Time Blocking (beta)" section with the nginx
    snippet, the `microguard serve` command reference, and both middleware
    usage examples (FastAPI and Flask), explicitly stating the Redis
    dependency.

## Testing Plan

| Layer | What | Count |
|-------|------|-------|
| Unit | `compute_combined_score` (floor, cap, neutral cases) | +4 |
| Unit | `RedisSessionStateStore.record_request` accumulation + TTL expiry (real Redis, e.g. `pytest` fixture spinning up `redis-server` or a `testcontainers` Redis) | +4 |
| Unit | `score_live_request` bot/human/automated-integration classification, incl. the GraphQL regression case | +4 |
| Integration | `microguard serve` subprocess + real HTTP client hitting `/check` across a bot-triggering and a human-shaped request sequence | +2 |
| Integration | Real nginx (Docker) + `auth_request` + `microguard serve`, confirming 403 propagates through nginx | +1 |
| Integration | ASGI middleware wrapping a minimal FastAPI app (TestClient) | +2 |
| Integration | WSGI middleware wrapping a minimal Flask app (test client) | +2 |
| E2E | Full docker-compose (nginx + microguard serve + Redis + a dummy backend) — bot sequence gets 403 end to end, human sequence gets 200 and reaches the dummy backend | +1 |

## Rollback Plan

Entirely additive — no existing command or file is modified in a
backward-incompatible way except the two mechanical refactors in step 1
(which are covered by existing regression tests). If `microguard serve` or
the middleware has a critical bug post-release, revert is: stop running
`microguard serve` / unmount the middleware. `scan`/`probe`/`watch` are
unaffected either way since they don't depend on `microguard/live/`.

## Effort Estimate

| Component | Estimate |
|-----------|----------|
| `scoring.py` extraction + refactor cli.py/watch.py + verify regression tests | 0.5 day |
| `live/state.py` + `live/redis_store.py` + tests | 1.5 days |
| `live/scorer.py` + tests (incl. status=0 behavior verification) | 1 day |
| `live/server.py` + `microguard serve` CLI command + tests | 1 day |
| nginx config + Docker-based integration test | 1 day |
| ASGI + WSGI middleware + tests | 1.5 days |
| E2E docker-compose test | 0.5 day |
| README "Real-Time Blocking" section | 0.5 day |
| **Total** | **~7.5 days** |

## Files Reference

| File | Change |
|------|--------|
| `microguard/scoring.py` | New — `compute_combined_score()` |
| `microguard/cli.py:131-144` | Replace inline blend with `scoring.compute_combined_score()` call; add `serve` subparser |
| `microguard/watch.py` | Replace inline blend with `scoring.compute_combined_score()` call |
| `microguard/live/__init__.py` | New — empty package marker |
| `microguard/live/state.py` | New — `LiveSession`, `SessionStateStore` protocol |
| `microguard/live/redis_store.py` | New — `RedisSessionStateStore` |
| `microguard/live/scorer.py` | New — `score_live_request()` |
| `microguard/live/server.py` | New — `/check` HTTP microservice |
| `microguard/live/middleware.py` | New — `MicroguardASGIMiddleware`, `MicroguardWSGIMiddleware` |
| `setup.py` | Add `extras_require={"live": ["redis>=5.0,<6"]}` |
| `tests/test_scoring.py` | New |
| `tests/live/test_redis_store.py` | New (needs a real Redis available in CI — add a `redis` service to `.github/workflows/test.yml` for this job) |
| `tests/live/test_scorer.py` | New |
| `tests/live/test_server_integration.py` | New |
| `tests/live/test_middleware.py` | New |
| `tests/live/test_nginx_e2e.py` | New (Docker-gated; skip if Docker unavailable, matching this repo's existing `pytest.mark.skip`-for-network-dependent-test pattern in `tests/test_scanner.py`) |
| `README.md` | New "Real-Time Blocking (beta)" section |
| `.github/workflows/test.yml` | Add a Redis service container for the `live/` test job |

## Out of Scope

- Threat-intel signals, fingerprinting, adaptive learning — separate specs
  (0002-0004), each hooks into `score_live_request` once it exists.
- Horizontal scaling / load-testing the microservice under real production
  QPS. This spec proves correctness, not throughput.
- TLS/HTTPS termination for `microguard serve` — assumed to sit behind
  nginx (which already terminates TLS in the documented setup), not exposed
  directly to the internet.
- An in-memory (non-Redis) state-store implementation. The `SessionStateStore`
  Protocol is designed to allow one later, but building it is not part of
  this spec (decided explicitly — see epic's "Decisions locked" section).

## Related

- Epic: [0000-epic-paid-alternative-parity.md](0000-epic-paid-alternative-parity.md)
- `CHANGELOG.md` `[Unreleased]` — the watch.py/cli.py score-blending
  duplication bug this spec's step 1 permanently closes.
- `plan.md` (repo root) — original real-time vision.
