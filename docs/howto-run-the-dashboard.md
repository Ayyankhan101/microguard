# How to run the dashboard

Watch live blocking decisions, explore a scan, and inspect the model in a browser.
By the end you will have `microguard dashboard` serving on localhost, showing real
decisions from your check server.

## Prerequisites

- `pip install 'microguard[dashboard,live]'`
- A running deployment ([nginx](howto-deploy-behind-nginx.md) or
  [in-process](howto-deploy-in-process.md)) if you want the live tab to show
  anything. The scan and model tabs work without one.

## Start it

```bash
microguard dashboard
```

```
microguard dashboard on http://127.0.0.1:8500
  redis: redis://localhost:6379
  live feed: connected
  config writes: disabled
  api token: none
```

Open `http://127.0.0.1:8500`. Three tabs:

| Tab | What it answers |
|---|---|
| Live | What did microguard block just now, and why? |
| Scan | What is in this log file, and which feature drove each verdict? |
| Model | What did the network learn, and what does moving the bar cost? |

## Point it at the right Redis

The live tab reads the decisions your check server and middleware write, so both
must use the same Redis:

```bash
microguard dashboard --redis-url redis://redis-host:6379
```

If it cannot connect, the dashboard still starts — the scan and model tabs do not
need Redis — and says so twice: `live feed: NOT CONNECTED` at startup, and
`redis not connected` in the sidebar.

## Read a decision

Each row in the live feed carries the whole decision on one scale:

| Mark | Meaning |
|---|---|
| Blue square | what the heuristic rules concluded, and how strongly |
| Purple square | what the model alone predicted |
| White diamond | the blended score the verdict used |
| Vertical line | the block threshold in force for that request |

Click a row for the full breakdown. Read it in the order
[tuning](howto-tune-blocking.md) prescribes: is a model loaded at all, what did
the rules say, then rules versus model, then the blend.

Two states are called out loudly because they change every verdict:

- **Heuristics only** — no model is loaded on the scoring process, so
  `model_score` is 0.00 everywhere and the blend collapses to the rules.
- **Failing open** — requests could not be scored and were allowed through
  unchecked. Usually Redis. Treat it as an alert, not as noise.

## Change the threshold from the browser

Off by default, because it decides who gets blocked on a live site:

```bash
microguard dashboard --allow-config-writes
```

The slider then writes to `mg:v1:config` in Redis, and every scoring process
sharing that Redis picks it up within about five seconds — no restart, so
in-flight sessions survive. Clearing the override falls back to whatever each
process was started with. Every change is logged with its old and new value.

Setting it to 1.00 blocks nothing: that is observe-only mode, and it is the safe
way to watch scores on live traffic before you start enforcing.

## Exposing it beyond localhost

The dashboard binds `127.0.0.1` and has no accounts. It shows every blocked
visitor and, with config writes enabled, changes the blocking threshold — treat
it as a control plane, not a status page.

If it has to be reachable from elsewhere, require a shared secret:

```bash
microguard dashboard --host 0.0.0.0 --token "$(openssl rand -hex 16)"
```

Every `/api` request then needs `X-Microguard-Token`. This is one secret over
whatever transport you terminate, not an authentication system; put it behind
your own TLS and access controls.

## Scan a log without the CLI

The scan tab takes a bundled sample or an uploaded file, then lets you move the
bot threshold and watch the counts and verdicts re-slice — no re-parsing. The
detail drawer shows all 19 features behind a session, and the export buttons
produce exactly what `microguard scan --output json|html|nginx|cloudflare` does.

Uploads go to a temporary file, are capped at 64 MB, and are deleted after the
scan. Server-side paths are not accepted; only files you upload and the samples
shipped in `data/`.

## If the UI does not load

```
  UI: NOT BUILT (run 'npm ci && npm run build' in gui/) - API only
```

A pip install ships the built UI. This message means you are running from a
source checkout where `gui/` has not been built yet:

```bash
cd gui && npm ci && npm run build
```

The build writes `microguard/dashboard/static/`, which is what the Python server
serves. Node is needed only to build; running the dashboard never needs it.

## Related

- [Tune blocking](howto-tune-blocking.md) — what the numbers mean
- [Live API reference](reference-live-api.md) — the decision payload
- [How blocking works](explanation-how-blocking-works.md) — the path a request takes
