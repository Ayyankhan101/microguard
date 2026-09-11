# Why the dashboard is built this way

The dashboard exists to answer one question: *what did microguard just do to my
traffic, and why?* Everything in its design follows from that, and from the
constraint that answering it must never interfere with the blocking it reports
on.

## The problem

Before the dashboard, the live path was invisible.

`LiveScorer.score_request()` computed a rich decision for every request — the
blended score, the model's contribution, which rule fired, how confident it was,
how many requests that actor had made — returned it to the caller, and the
caller used one field. The rest was discarded microseconds after it was
computed. Redis held per-IP session lists with a 30-minute TTL and nothing else:
no counters, no history, no record that a decision had ever been made.

So the state of the system was unanswerable. Not hard to answer — unanswerable.
"How many requests did we block in the last hour?" had no source of truth. "Why
did this customer get a 403?" could only be answered by reproducing the request
and hoping the session state had not changed. And `microguard serve` exposed
exactly one route, `GET /check`, with no CORS and no health endpoint, so nothing
could ask it anything.

## Recording as a tee, not a dependency

The fix is a `DecisionRecorder`: the scorer writes each decision somewhere a
dashboard can read it. The interesting part is the constraint around it.

Blocking is on the request path of a live site. nginx's `auth_request` turns any
non-2xx/401/403 response from `/check` into a **500 for the visitor**, so an
exception in the check server is not a monitoring gap, it is an outage. The
existing code already took this seriously: scoring failures fail open, allowing
the request rather than breaking it.

Recording inherits that discipline, one step further:

```python
def _record(self, result: dict) -> None:
    if self._recorder is None:
        return
    try:
        self._recorder.record(result)
    except Exception:
        logger.exception("decision recording failed, continuing")
```

Every exception is swallowed. Recording exists for the operator; blocking exists
for the site; the second must never depend on the first. A test asserts that a
recorder which raises leaves the verdict byte-identical, on both the scored path
and the `automated-integration` short circuit.

The call sits inside `_result()` — the single function both return paths build
their payload in — so it is structurally impossible for one path to be recorded
and the other missed. That function exists for the same reason: to stop the two
paths reporting different shapes.

## Counters are cumulative, not derived

The event ring is capped at 1,000 decisions. The counters are not computed from
it.

It would be simpler to store only the ring and count what is in it. But then
"blocked today" would quietly shrink as older decisions scrolled off, and an
operator watching that number drop while nothing improved would be worse off
than with no number. `HINCRBY` on a separate hash costs one more command in a
pipeline that was already being sent.

The same reasoning produces the histogram as its own hash: bucket counts that
survive the ring, so the score distribution describes all traffic rather than
the last thousand requests.

## Two states get shouted about

Most of a dashboard is fields. Two things here are not fields, because they
silently change the meaning of every other number on the page.

**No model loaded.** `_load_model()` returns `None` when `model.json` is missing
or unreadable, and scoring continues on heuristics alone with `model_score`
pinned at 0.0. Nothing fails. Every decision is still made, still blended, still
enforced — on 40% of the intended signal. This went unnoticed once already,
because the function searched for a filename that never existed and said
nothing.

**Failing open.** During a Redis outage every request is allowed with
`heuristic_reason: "scoring unavailable"`. The site is up and completely
unprotected. The docs are explicit that this should be alerted on rather than
treated as noise.

Both render as banners, not as a `model_loaded: false` somewhere in a details
panel. A monitoring tool that reports its own blindness in small print is not
reporting it.

## The score ruler

The decision payload carries four numbers: what the rules concluded and how
strongly, what the model predicted, what the blend came to, and which threshold
it was compared against. A dashboard that shows only the blend tells an operator
that a customer was blocked and nothing about why.

So all four go on one 0–1 scale, in every place a decision appears — the live
feed, the scan table, the detail panels:

```
 0.00                          0.50                     0.85        1.00
  ├─────────────────────────────┼────────────────────────┤───────────┤
                    ■ rules 0.50          ● model 0.73    │      ◆ blend 0.91
                                                      threshold
```

Reading left to right answers the diagnostic sequence
[the tuning guide](howto-tune-blocking.md) already prescribes: is a model loaded
at all, what did the rules say, do the rules and the model agree, where did the
blend land relative to the bar. One glance distinguishes "the model is dead" (no
model mark) from "the rules were weak but the model was certain" from "this was
a borderline call at a threshold you chose".

`block_threshold` had to be added to the decision payload for this. Without it,
a dashboard cannot tell a changed threshold from a changed score — the same
0.80 that was allowed yesterday is blocked today and nothing on screen explains
it. It is `null` on the fail-open payload, where no threshold was consulted, and
the ruler draws no gate rather than inventing one.

## Changing the threshold without a restart

Tuning previously meant editing a flag and restarting the check server. That
drops every in-flight session — which is the accumulated per-actor history the
timing and rate rules depend on — so the tuning you just did is evaluated
against a system that has forgotten everything. You cannot watch the effect of a
change that resets the state you are measuring.

The override lives in Redis (`mg:v1:config`) and every scoring process reads it
per request through a `threshold_source`, cached about five seconds. The cache
is the compromise: an uncached read adds a Redis round trip to a path that runs
for every request to the protected site, for a value that changes a few times a
day.

Every failure path returns `None`, which means "use whatever this process was
started with". A config store that is down must not start blocking everyone or
stop blocking anyone. That is the same fail-safe posture as the recorder,
applied to a knob instead of a write.

It is off by default. `--allow-config-writes` is required, and every accepted
change is logged with its old and new value, because this is a control plane: at
0.0 every visitor is blocked, and at 1.0 none are.

## Why a poll, not a subscription

The SSE stream polls the recorder every two seconds rather than subscribing to a
Redis channel.

The decisions are written by a *different process* — the check server or the
middleware inside your app — so there is no in-process callback to hang off. A
pub/sub channel would work, but it would add a second delivery mechanism to keep
consistent with the durable ring, and a subscriber that misses a message has no
way to notice. Polling the same ring the REST endpoint reads means there is one
source of truth, and a browser that reconnects catches up automatically.

Two seconds is a human-refresh interval, not a latency budget. Nothing depends
on the dashboard being current.

## Where the code lives, and why

`microguard/events.py` holds the `DecisionRecorder` protocol and the in-memory
implementation. `microguard/live/redis_events.py` holds the Redis one. That
split is not organizational tidiness: `microguard/live/__init__.py` raises
`ImportError` when redis-py is not installed, so anything under `live/` is
unimportable without it. The dashboard has to start and serve its Scan and Model
tabs on a base install, so the protocol cannot live there.

The dashboard imports `live/` lazily, inside the functions that need it, and
degrades to a process-local recorder when Redis is absent — reporting that
degradation through `/api/health` rather than refusing to start.

## Security posture

This UI reports every blocked visitor and can change who gets blocked. It is a
control plane, and it is treated as one:

- **Loopback by default, no accounts.** `--token` adds a shared secret compared
  with `compare_digest` for deployments that cannot stay on loopback. That is
  one secret over whatever transport you terminate, not an authentication
  system, and it is documented as such.
- **No endpoint scans a server-side path.** Only uploads and the samples shipped
  in `data/`, resolved through `realpath` so `../setup.py` is a 404. A path
  parameter would be path traversal by construction.
- **Exports are always `text/plain` with an attachment disposition.**
  `report.format_html()` interpolates attacker-controlled user agents and URLs
  without escaping them. Served inline from the dashboard's own origin, that is
  stored XSS. The offline report keeps its behavior; the dashboard refuses to
  be the delivery vehicle.
- **The SPA mount sits last and at the root**, so `/api` routes match first and
  a mistyped endpoint returns a JSON 404 rather than a 200 carrying the HTML
  shell. An API that answers 200 with a page for a typo is considerably harder
  to debug from a browser.

## Related

- [How to run the dashboard](howto-run-the-dashboard.md)
- [Live API reference](reference-live-api.md) — endpoints, payloads, Redis keys
- [How blocking works](explanation-how-blocking-works.md) — the path being reported on
- [How to tune blocking](howto-tune-blocking.md) — the diagnostic order the ruler encodes
- [Working on the GUI](howto-work-on-the-gui.md) — the TypeScript side
