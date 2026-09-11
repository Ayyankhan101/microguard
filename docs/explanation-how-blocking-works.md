# How real-time blocking works

Microguard's batch commands read a log file after the fact. Real-time blocking
answers a harder question: given a request that has not been served yet, and
whatever else this client has done recently, should it be allowed through?

This document explains the design. For what the knobs do, see the
[API reference](reference-live-api.md).

---

## The problem

Blocking inline is different from classifying a log file in three ways, and each
one shapes the design.

**You have no history for a request in isolation.** A single `GET /products/1`
from a browser user agent is indistinguishable from the same request sent by a
scraper. The signal is in the sequence: how fast, how varied, how uniform. So
something has to remember what this client did a moment ago, across processes and
across restarts.

**You have no outcome yet.** A proxy decides before the application responds, so
there is no status code, no response size, no timing. Two of the nineteen
features are computed from status codes and are simply absent.

**You are in the critical path.** Whatever this costs, every visitor pays it, and
whatever breaks here breaks the site behind it. A bot filter that takes the site
down with it is worse than no bot filter.

---

## The shape

```
   request
      │
      ▼
┌───────────────┐   nginx auth_request        ┌──────────────────┐
│  entrypoint   │   or ASGI/WSGI middleware   │  Redis           │
│               │────── record + read ───────▶│  live:v2:{ip}    │
│  resolve IP   │◀───── session ──────────────│  LIST, cap 200   │
└───────┬───────┘                             └──────────────────┘
        │  session (this request + recent history)
        ▼
┌───────────────────────────────────────────────┐
│  label_session()      ~15 rules  ->  0.60-0.95 │
│  extract_features()   19 floats                │
│  BotDetector.predict()          ->  0.0-1.0    │
└───────────────────────┬───────────────────────┘
                        ▼
              compute_combined_score()
                        │
              score > threshold ?
                   ┌────┴────┐
                 yes         no
                   │          │
                 403        pass through
```

The detection engine is the same code the batch path uses. `extract_features()`
and `label_session()` are untouched by the live path; `LiveSession` duck-types the
batch `Session` so both work against it unchanged. Live code is plumbing around
an existing engine, not a second engine.

---

## Sessions are a sliding window

A session is a Redis LIST keyed by client IP, holding the last 200 requests, with
a TTL refreshed on every append.

The append is one pipeline — `RPUSH`, `LTRIM`, `EXPIRE`, `LRANGE` — and that
matters more than it looks. An earlier version read the session, modified it in
Python, and wrote it back. Two requests arriving together from one client both
read the same state, and the second write erased the first. Measured with 20
concurrent appends against a real Redis, **9 survived**. Losing requests
undercounts `request_count`, and `request_count` is the numerator of the rate
rules — so detection got weakest exactly when a client was hammering hardest.
`RPUSH` is atomic, so the gap does not exist.

The 200-entry cap bounds two things: Redis memory against a session that never
expires naturally, and per-request CPU, since scoring re-reads and re-features the
whole retained list at roughly 2ms per request at that size.

**The cap makes the session clock a window, not a lifetime.** Once trimming
starts, `request_count` is pinned at 200 forever. If the clock still pointed at
when the session first appeared, `duration` would keep growing while the numerator
stayed frozen, and the rate rule would read a sustained flood as *slower the
longer it ran*:

```
200 requests over 10 seconds   ->  1200 req/min  ->  blocked
the same flood at 30 minutes   ->   6.7 req/min  ->  allowed
```

Persistence would be rewarded. So `start_time` and `end_time` come from the
oldest and newest *retained* entries, and the rate stays honest.

---

## The client IP is the trust boundary

Everything above keys on one value: the client IP. If a client can choose its own
IP, it gets a fresh session on every request, `request_count` never climbs past 1,
and every rule that depends on sequence stops firing. The tool would appear to
work and detect nothing.

`X-Forwarded-For` is a header the client sends. `X-Real-IP` is set by nginx from
`$remote_addr` and cannot be forged. So the resolution order is:

1. `X-Real-IP` — proxy-set, trusted
2. `X-Forwarded-For` — **only** when `trust_forwarded_for` is enabled
3. The transport peer address

`trust_forwarded_for` defaults to off, and the nginx snippet in the docs clears
any inbound `X-Forwarded-For`, because nginx forwards client headers to upstreams
by default. Turn it on only when something in front of you overwrites that header
rather than appending to it.

The same reasoning applies on the way out. The middleware attaches its verdict as
`X-Microguard-*` request headers for the wrapped app to read, and header lookups
return the first match — so a client sending its own `X-Microguard-Label: human`
would win if we merely appended ours. Client-supplied `X-Microguard-*` headers are
stripped before ours are attached.

---

## How the score is built

Two independent opinions, blended:

```python
combined = 0.6 * model_score + 0.4 * heuristic_confidence

if heuristic_label == 'bot':
    combined = max(combined, heuristic_confidence)      # floor
elif heuristic_label == 'human':
    combined = min(combined, 1.0 - heuristic_confidence)  # cap
```

The floor stops a confident rule from being diluted by a weak model score: if a
request hits a known vulnerability-scanner path, that is a fact, and a model
shrugging at 0.1 should not talk us out of it. The cap is the mirror image, and it
exists because of a real bug — the model's independent score was overriding a
heuristic that correctly recognized a single-endpoint API session as human.

Because of the floor, a rule's confidence *is* the score it produces when the
model says nothing. That makes the threshold a statement about which rules are
allowed to block on their own:

| Threshold | Rules that block unaided |
|---|---|
| 0.85 (default) | known bot UA (0.95), scanner paths (0.95), attack tools (0.90), uniform timing (0.90), HTTP/1.0-only (0.90) |
| 0.70 (spec) | the above, plus request count (0.85), single endpoint (0.80), request rate (0.75) |

The default is 0.85 because the three rules that 0.70 promotes are the ones with
innocent explanations. "All requests to one endpoint" describes a polling client
or a single-URL GraphQL app. "High request rate" is the rule that once flagged a
browser page load. Those signals still contribute — they just need the model to
agree before anyone gets blocked.

### Blocking requires exceeding the threshold

A client with no history gets the neutral verdict
`('human', 0.5, 'no strong signals either way')`, which the cap pins at exactly
0.5. Comparing with `>=` meant an operator running a 0.5 threshold blocked every
new visitor on arrival, with a JSON reason reading "no strong signals either way".
Blocking now requires the score to *exceed* the threshold, so a score sitting on
the bar has not cleared it — and a threshold of 1.0 becomes a real never-block
setting.

---

## The model, and being honest about it

Requests are scored by a micrograd MLP (19 inputs, 4 hidden, 1 output) trained on
labeled session data. Two things about it are worth knowing.

**Two of nineteen features are always zero live.** `status_code_entropy` and
`error_rate` need a response that has not happened yet. That looked like it needed
a retrain with those features dropped out. Measured instead: across 1023
evaluation sessions, zeroing both changes no decision, with a mean score shift of
0.000124 on the held-out set and 0.000000 on the adversarial set. The model gives
them almost no weight. `TestLiveFeatureShape` pins that, so a future retrain that
makes the model status-dependent fails a test rather than quietly degrading the
live path.

**A missing model is loud.** If the model cannot be found or parsed, scoring
degrades to heuristics — but it logs a warning, the startup banner says
`model: NOT LOADED (heuristics only)`, and every decision carries
`model_loaded: false`. This is not decoration. The live path once ran for five
commits with the model silently absent, blocking on heuristics alone, because the
loader searched for a filename that did not exist and returned `None` without
comment.

---

## Failing open

If Redis is unreachable mid-request, the request is **allowed**, and a warning is
logged.

That is a deliberate trade against the obvious alternative. nginx treats any
`auth_request` response that is not 2xx, 401, or 403 as an error and returns 500
to the visitor. Without fail-open, a Redis blip stops being a bot-detection
outage and becomes a full site outage, caused by the optional security layer. An
unscored request is a smaller loss than a dead site.

The cost is real and worth stating plainly: while Redis is down, nothing is
blocked. Alert on the warning rather than treating it as noise.

---

## Trade-offs

| Choice | Bought | Paid |
|---|---|---|
| Redis LIST, atomic append | No lost updates under concurrency | A Redis dependency and one round trip per request |
| 200-entry cap | Bounded memory and CPU | Long sessions forget their early history |
| Window clock, not lifetime | Rate rules stay honest under the cap | `duration` is not the session's true age |
| Fail open | A Redis outage does not take the site down | A Redis outage means nothing is blocked |
| Threshold 0.85 | Rules with benign lookalikes cannot block alone | Bots that stay below the high-confidence rules need the model to catch them |
| Thread per connection | Redis waits overlap instead of queueing | More concurrency to reason about |
| IP as the session key | Simple, works behind any proxy | Shared NAT egress shares a session |
| `live:v2:` key prefix | Deploy and rollback never hit WRONGTYPE | Sessions do not carry across the version change |

The last one is worth expanding. Keys used to hold a JSON string; they now hold a
LIST. `RPUSH` against a string is `WRONGTYPE`, so reusing the key name would have
broken every in-flight session on deploy — silently, because the entrypoints fail
open — and again in reverse on rollback. Versioning the prefix makes the crossover
a non-event: old keys are never touched and expire on their own TTL.

---

## Why a thread per connection

The check server uses `ThreadingHTTPServer`. The reason is the Redis round trip,
not CPU. Scoring is pure Python and GIL-bound, so threads buy little there —
measured against a local Redis, throughput went from 728 to 894 req/s and median
latency actually rose. But Python releases the GIL during socket I/O, so waits on
Redis overlap under threads and serialize without them. With a 15ms round trip,
which is what a Redis on another host looks like:

| | 24 concurrent checks | p50 | worst |
|---|---|---|---|
| Single-threaded | 0.61s | 116ms | 612ms |
| Threaded | 0.11s | 21ms | 106ms |

nginx calls `/check` for every request to the protected location, so that tail is
a real visitor waiting.

What the threads share is read-only or already thread-safe: the scorer and its
settings are written once at startup, redis-py hands each thread its own pooled
connection, and `predict()` only reads model parameters.

---

## Related

- [API reference](reference-live-api.md)
- [How to tune blocking](howto-tune-blocking.md)
- [Tutorial: block your first bot](tutorial-real-time-blocking.md)
