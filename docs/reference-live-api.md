# Real-time blocking API reference

Complete technical description of `microguard/live/` — the inline path that scores
a request and blocks it before it reaches your application. For a guided
walkthrough see the [tutorial](tutorial-real-time-blocking.md); for why it behaves
this way see [how blocking works](explanation-how-blocking-works.md).

Everything here requires the `live` extra:

```bash
pip install 'microguard[live]'        # adds redis>=5.0,<6
pip install 'microguard[live,fastapi]'  # + fastapi, uvicorn
pip install 'microguard[live,flask]'    # + flask
```

Importing `microguard.live` without `redis` installed raises `ImportError` with an
actionable message. The base `pip install microguard` stays dependency-free and
`microguard scan` keeps working.

---

## `microguard serve`

Starts the HTTP check server for nginx's `auth_request` module. One endpoint,
`GET /check`.

```bash
microguard serve [--host HOST] [--port PORT] [--redis-url URL]
                 [--block-threshold FLOAT] [--session-ttl SECONDS]
                 [--trust-forwarded-for]
```

| Flag | Type | Default | Effect |
|---|---|---|---|
| `--host` | str | `127.0.0.1` | Bind address. Keep on loopback unless nginx is on another host. |
| `--port` | int | `8400` | Listen port. |
| `--redis-url` | str | `redis://localhost:6379` | Redis connection URL. Accepts `redis://host:port/db`. |
| `--block-threshold` | float | `0.85` | Blocks when the blended score **exceeds** this. See [tuning](howto-tune-blocking.md). |
| `--session-ttl` | int | `1800` | Seconds of inactivity before a session expires. Refreshed on every request. |
| `--trust-forwarded-for` | flag | off | Honor `X-Forwarded-For` for the client IP. **Only** enable behind a proxy that overwrites it. |

The server refuses to start if Redis is unreachable (it pings once at boot). It
prints its resolved configuration, including whether the model loaded:

```
microguard check server listening on 127.0.0.1:8400
  redis: redis://localhost:6379
  threshold: 0.85
  session TTL: 1800s
  model: loaded
  trust X-Forwarded-For: False
```

`model: NOT LOADED (heuristics only)` means scoring is running without the
trained model. Requests are still scored, but only by the heuristic rules.

### `GET /check`

Reads request metadata from headers, scores it, and answers 200 (allow) or
403 (block). Any other path returns 404.

| Request header | Used for | Fallback |
|---|---|---|
| `X-Real-IP` | Client IP (session key) | `X-Forwarded-For` if `--trust-forwarded-for`, else the transport peer address |
| `X-Forwarded-For` | Client IP, **only** when trusted | ignored by default |
| `User-Agent` | Heuristic rules, feature extraction | `""` |
| `X-Original-Method` | HTTP method of the real request | `GET` |
| `X-Original-URI` | Path of the real request | `/` |

Responds with the [decision payload](#decision-payload) as a JSON body and as
`X-Microguard-*` headers.

---

## Middleware

Both classes take identical arguments, matching the `microguard serve` flags.

```python
MicroguardASGI(app, redis_url="redis://localhost:6379",
               block_threshold=0.85, session_ttl=1800,
               trust_forwarded_for=False)

MicroguardWSGI(app, redis_url="redis://localhost:6379",
               block_threshold=0.85, session_ttl=1800,
               trust_forwarded_for=False)
```

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `app` | ASGI app / WSGI callable | required | The application to wrap. |
| `redis_url` | str | `redis://localhost:6379` | Connection is lazy; construction does not contact Redis. |
| `block_threshold` | float | `0.85` | Blocks when the score **exceeds** this. |
| `session_ttl` | int | `1800` | Seconds of inactivity before a session expires. |
| `trust_forwarded_for` | bool | `False` | Honor `X-Forwarded-For` for the client IP. |

Unknown keyword arguments raise `TypeError`. A misspelled `block_treshold` fails
at construction rather than silently running at the default.

**`MicroguardASGI`** — Starlette, FastAPI, or any ASGI 3 app. Non-HTTP scopes
(WebSocket, lifespan) pass through unscored. Client IP comes from headers, then
`scope["client"][0]`.

**`MicroguardWSGI`** — Flask or any WSGI app. Client IP comes from
`HTTP_X_REAL_IP`, then `HTTP_X_FORWARDED_FOR` when trusted, then `REMOTE_ADDR`.

On a block, both return 403 with the decision payload as the body and never call
the wrapped app. On a pass, both attach the decision as request headers
(`scope["headers"]` / `HTTP_X_MICROGUARD_*`) and forward. Client-supplied
`X-Microguard-*` headers are stripped first, so an app reading them sees only
microguard's values.

See [deploying in-process](howto-deploy-in-process.md).

---

## Decision payload

Returned by `LiveScorer.score_request()`, sent as the JSON body of `/check` and of
any 403.

| Key | Type | Description |
|---|---|---|
| `ip` | str | The client IP used as the session key. |
| `label` | `"bot"` \| `"human"` | The decision. |
| `score` | float 0.0-1.0 | The blended score the decision used. |
| `model_score` | float 0.0-1.0 | What the model alone said. `0.0` when no model is loaded. |
| `heuristic_label` | `"bot"` \| `"human"` \| `"automated-integration"` | What the rules alone said. |
| `heuristic_confidence` | float 0.0-1.0 | How sure the rules were. |
| `heuristic_reason` | str | Which rule fired, in plain English. |
| `request_count` | int | Requests in the retained session history (capped at 200). |
| `duration` | float | Seconds spanned by the retained history. |
| `model_loaded` | bool | Whether the model contributed at all. |
| `block_threshold` | float \| null | The bar this request was judged against, after any runtime override. `null` on the fail-open payload, where no threshold was consulted. |
| `reason` | str | Alias of `heuristic_reason`, kept for older callers. |

```json
{
  "ip": "203.0.113.9",
  "label": "bot",
  "score": 0.95,
  "model_score": 0.7312,
  "heuristic_label": "bot",
  "heuristic_confidence": 0.95,
  "heuristic_reason": "vulnerability scanner pattern detected",
  "request_count": 3,
  "duration": 0.0041,
  "model_loaded": true,
  "block_threshold": 0.85
}
```

### Response headers

The same decision travels as headers, because nginx's `auth_request_set` can only
read response headers, not bodies.

| Header | Source key |
|---|---|
| `X-Microguard-Label` | `label` |
| `X-Microguard-Score` | `score` |
| `X-Microguard-Model-Score` | `model_score` |
| `X-Microguard-Heuristic` | `heuristic_label` |
| `X-Microguard-Reason` | `heuristic_reason` |

---

## Dashboard API

Served by `microguard dashboard` (default `127.0.0.1:8500`). See
[how to run the dashboard](howto-run-the-dashboard.md).

| Method | Path | Returns |
|---|---|---|
| GET | `/api/health` | `{version, model_loaded, redis_connected}` |
| GET | `/api/live/stats` | Counters since the recorder started, plus a 20-bucket score histogram and the most-blocked IPs. |
| GET | `/api/live/events?limit=` | Recent decisions, newest first, each with a `ts`. Capped at 1000. |
| GET | `/api/live/stream` | Server-sent events: `stats` every 2s, `decision` per new decision. |
| GET | `/api/live/config` | `{block_threshold, writable}`. `block_threshold` is `null` when no override is set. |
| PUT | `/api/live/config` | Sets the override. `403` unless started with `--allow-config-writes`, `503` without a shared Redis. |
| GET | `/api/scan/samples` | Log files bundled in `data/`. |
| POST | `/api/scan` | Scans a bundled sample. Body is `scan_logfile()`'s dict, unchanged. |
| POST | `/api/scan/upload` | Scans an uploaded file. 64 MB cap; the temp file is deleted afterwards. |
| POST | `/api/scan/export` | Renders a result through `report.py`. Always `text/plain` with an attachment disposition — `format_html()` does not escape its input. |
| GET | `/api/model` | Architecture, weights, feature names, normalization ranges. |
| POST | `/api/model/evaluate` | Confusion matrix, precision/recall/F1/accuracy, ROC and score distribution on the held-out or adversarial set. |

With `--token`, every `/api` request needs a matching `X-Microguard-Token`.

### Decision recording

`mg:v1:` keys, written by whichever process scores:

| Key | Type | Contents |
|---|---|---|
| `mg:v1:events` | LIST | JSON decisions, newest first, capped at 1000 |
| `mg:v1:counters` | HASH | `total`, `blocked`, `score_sum` |
| `mg:v1:hist` | HASH | bucket index `0`-`19` → count |
| `mg:v1:blocked_ips` | ZSET | IP → times blocked |
| `mg:v1:config` | HASH | `block_threshold` → float, absent when there is no override |

Counters are cumulative rather than derived from the ring, so totals do not shrink
as old decisions scroll out of it.

---

## Python API

### `LiveScorer`

```python
from microguard.live.scorer import LiveScorer

LiveScorer(store, model_path=None, block_threshold=0.85,
           session_ttl=1800, short_circuit_label="automated-integration",
           recorder=None, threshold_source=lambda: None)
```

| Argument | Type | Default | Notes |
|---|---|---|---|
| `recorder` | `DecisionRecorder` \| None | `None` | Where each decision is stored for the dashboard. Errors from it are logged and swallowed; recording can never change or delay a verdict. |
| `threshold_source` | `() -> float \| None` | returns `None` | Consulted once per request. A value overrides `block_threshold`; `None`, or a raise, leaves the configured value standing. |

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `store` | `SessionStateStore` | required | Where session history lives. |
| `model_path` | str \| Path \| None | `None` | `None` resolves the packaged `data/model.json`. |
| `block_threshold` | float | `0.85` | Blocks when the score exceeds this. |
| `session_ttl` | int | `1800` | Passed to the store on every append. |
| `short_circuit_label` | str | `"automated-integration"` | Heuristic label that is never blocked. |

**`score_request(entry: LogEntry) -> dict`** — records the request, scores the
resulting session, returns the [decision payload](#decision-payload).

**`model_loaded: bool`** — whether scores include a model contribution. Read this
rather than inferring it from a `model_score` of 0.0.

A model that cannot be found or cannot be parsed logs a warning at `WARNING`
level and degrades to heuristics. It never raises.

### `RedisSessionStateStore`

```python
from microguard.live.redis_store import RedisSessionStateStore

RedisSessionStateStore(redis_client, prefix="live:v2:", default_ttl=1800)
```

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `redis_client` | `redis.Redis` | required | Construct with `decode_responses=True`. |
| `prefix` | str | `"live:v2:"` | Key namespace. The version guards against value-shape changes. |
| `default_ttl` | int | `1800` | Used when `record_request` is called without a TTL. |

**`record_request(ip, user_agent, entry, ttl_seconds=None) -> LiveSession`** —
appends atomically and returns the session ready to score. One pipeline:
`RPUSH`, `LTRIM`, `EXPIRE`, `LRANGE`.

**`delete(ip) -> None`** — removes a session.

Key schema: `live:v2:{ip}` holds a Redis LIST of JSON `LogEntry` payloads, newest
last, capped at `MAX_SESSION_ENTRIES`.

### `SessionStateStore`

A `typing.Protocol` with two methods, `record_request` and `delete`. Implement it
to back sessions with something other than Redis; `LiveScorer` accepts any object
satisfying it.

### `LiveSession`

Dataclass holding `ip`, `user_agent`, `requests` (a list of `LogEntry`),
`start_time` and `end_time` (float epochs), with `request_count` and `duration`
properties. Duck-types `microguard.features.Session`, so `extract_features()` and
`label_session()` accept it unchanged.

### Constants

| Constant | Module | Value | Meaning |
|---|---|---|---|
| `BLOCK_THRESHOLD_DEFAULT` | `microguard.scoring` | `0.85` | Shared by the scorer, server, middleware, and CLI. |
| `MAX_SESSION_ENTRIES` | `microguard.live.redis_store` | `200` | Retained requests per session. |

---

## Failure behavior

| Condition | Behavior |
|---|---|
| Redis unreachable at startup | `microguard serve` exits. Middleware constructs fine (the connection is lazy). |
| Redis fails during a request | **Fail open.** The request is allowed, a warning is logged, and the payload reports `heuristic_reason: "scoring unavailable"`. |
| Model file missing or corrupt | Warning logged, scoring continues on heuristics, `model_loaded` is `false`. |
| `normalization.json` missing | Warning logged. Scores will be wrong — the model expects normalized inputs. |
| Unknown middleware keyword | `TypeError` at construction. |
| Non-HTTP ASGI scope | Passed through unscored. |

Fail-open is deliberate: nginx treats any `auth_request` response that is not
2xx/401/403 as an error and returns 500 to the visitor, so a Redis blip would
otherwise take the site down. See
[how blocking works](explanation-how-blocking-works.md#failing-open).

---

## Related

- [Tutorial: block your first bot](tutorial-real-time-blocking.md)
- [How to deploy behind nginx](howto-deploy-behind-nginx.md)
- [How to deploy in-process](howto-deploy-in-process.md)
- [How to tune blocking](howto-tune-blocking.md)
- [How blocking works](explanation-how-blocking-works.md)
