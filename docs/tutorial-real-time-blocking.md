# Block your first bot

By the end of this you will have microguard running as a live service, and you
will watch it allow a normal browser request and block a vulnerability scanner —
using the same scoring engine that would sit in front of your production traffic.

This runs entirely on your machine. No nginx, no application to wrap, nothing to
deploy. About five minutes.

## What you'll need

- Python 3.10 or newer
- A Redis server you can start locally ([install](https://redis.io/docs/latest/operate/oss_and_stack/install/install-redis/))
- `curl`

## Step 1: Install and start Redis

```bash
pip install 'microguard[live]'
```

Microguard keeps each visitor's recent request history in Redis, so a request can
be judged against what that client did a moment ago. Start one:

```bash
redis-server --daemonize yes --port 6379
redis-cli ping
```

```
PONG
```

## Step 2: Start the check server

```bash
microguard serve --redis-url redis://localhost:6379/9
```

```
microguard check server listening on 127.0.0.1:8400
  redis: redis://localhost:6379/9
  threshold: 0.85
  session TTL: 1800s
  model: loaded
  trust X-Forwarded-For: False
```

Read that banner — it is the configuration microguard actually resolved, not what
you hoped for. `model: loaded` means the trained model is contributing to scores.
If it says `NOT LOADED (heuristics only)`, scoring still works but runs on the
rule engine alone.

Leave this running and open a second terminal for the rest.

## Step 3: Send a normal request

The server answers `GET /check`, reading the real request's details from headers —
which is how nginx will hand them over later.

```bash
curl -i http://127.0.0.1:8400/check \
  -H "X-Real-IP: 203.0.113.5" \
  -H "User-Agent: Mozilla/5.0" \
  -H "X-Original-URI: /products/1"
```

```
HTTP/1.0 200 OK
X-Microguard-Label: human
X-Microguard-Score: 0.5
X-Microguard-Model-Score: 0.7312
X-Microguard-Heuristic: human
X-Microguard-Reason: no strong signals either way

{"ip": "203.0.113.5", "label": "human", "score": 0.5, ...}
```

**200, and there is your first result.** A visitor nobody has seen before, with
no history, gets through. That is the intended default: no evidence of bot
behavior means allow.

Notice `X-Microguard-Reason` says "no strong signals either way". Microguard is
not claiming this is a human — it is saying it has nothing to go on yet.

## Step 4: Send something that looks like an attack

Same server, different path. `/wp-admin/setup-config.php` is a WordPress
installer probe, one of the most common things scanners look for.

```bash
curl -i http://127.0.0.1:8400/check \
  -H "X-Real-IP: 203.0.113.9" \
  -H "User-Agent: Mozilla/5.0" \
  -H "X-Original-URI: /wp-admin/setup-config.php"
```

```
HTTP/1.0 403 Forbidden
X-Microguard-Label: bot
X-Microguard-Score: 0.95
X-Microguard-Model-Score: 0.7312
X-Microguard-Heuristic: bot
X-Microguard-Reason: vulnerability scanner pattern detected

{"ip": "203.0.113.9", "label": "bot", "score": 0.95, ...}
```

**403 on the very first request.** Identical user agent, identical everything
except the path. Behind nginx, this is where the request stops — the visitor
never reaches your application.

## Step 5: Read the decision

The interesting part is not the verdict, it is the breakdown. Pretty-print the
body:

```bash
curl -s http://127.0.0.1:8400/check \
  -H "X-Real-IP: 203.0.113.9" \
  -H "User-Agent: Mozilla/5.0" \
  -H "X-Original-URI: /wp-admin/admin.php" | python -m json.tool
```

```json
{
    "ip": "203.0.113.9",
    "label": "bot",
    "score": 0.95,
    "model_score": 0.7312190965985853,
    "heuristic_label": "bot",
    "heuristic_confidence": 0.95,
    "heuristic_reason": "vulnerability scanner pattern detected",
    "request_count": 2,
    "duration": 0.0041,
    "model_loaded": true
}
```

Three things worth pausing on:

- **`request_count` is 2.** This is the same IP as step 4, so microguard
  remembered it. History accumulates per client, which is what lets the timing and
  volume rules work at all.
- **`heuristic_confidence` and `model_score` are separate numbers.** The rules
  were 95% sure; the model independently said 0.73. You can always see which half
  drove a decision.
- **`score` is 0.95, not the average.** A confident rule sets a floor — a weak
  model score cannot talk microguard out of a scanner path. See
  [how blocking works](explanation-how-blocking-works.md#how-the-score-is-built).

## Step 6: Watch a session build

Sessions are why this is more than a URL blocklist. Send six requests as one
client and watch what microguard knows after each:

```bash
for i in 1 2 3 4 5 6; do
  code=$(curl -s -o /tmp/mg.json -w "%{http_code}" http://127.0.0.1:8400/check \
    -H "X-Real-IP: 198.51.100.7" \
    -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)" \
    -H "X-Original-URI: /page/$i")
  python -c "import json;d=json.load(open('/tmp/mg.json'));print(f'$i  $code  count={d[\"request_count\"]}  {d[\"heuristic_reason\"]}')"
done
```

```
1  200  count=1  no strong signals either way
2  200  count=2  no strong signals either way
3  200  count=3  no strong signals either way
4  200  count=4  no strong signals either way
5  200  count=5  exploring 5 different endpoints
6  200  count=6  exploring 6 different endpoints
```

`request_count` climbs, and at request 5 the reason changes: microguard now has
enough history to say something about the *shape* of this session rather than
shrugging. This client stays allowed, correctly — a browser moving between pages
is what a real visitor looks like.

That accumulated history is what the timing and volume rules read. Without it,
every request would be judged alone, and a scraper pacing itself would be
indistinguishable from a person.

**A note on what you just saw:** the scanner in step 4 was blocked on its first
request, because a vulnerability-scanner path is damning on its own. The
sequence-based rules work differently — at the default threshold they contribute
to the score but do not block unaided, because "many requests to one endpoint"
also describes a polling client or a dashboard on a refresh timer. That is a
deliberate setting, and [tuning blocking](howto-tune-blocking.md) covers how to
change it.

## Clean up

```bash
redis-cli -n 9 flushdb
```

Stop the server with Ctrl-C.

## What you built

A working inline bot filter. It scored real requests, remembered clients across
requests, blocked on evidence rather than on a static list, and told you exactly
why in every response.

The scoring you just watched is identical in production — the only thing that
changes is what calls it:

- **Behind nginx?** [Deploy behind nginx](howto-deploy-behind-nginx.md) — the same
  `microguard serve` you just ran, wired to `auth_request`.
- **A FastAPI or Flask app?** [Deploy in-process](howto-deploy-in-process.md) —
  no separate service.
- **Blocking too much or too little?** [Tune blocking](howto-tune-blocking.md).
- **Every flag and field:** [API reference](reference-live-api.md).

Before going anywhere near production, read the
[client IP section](explanation-how-blocking-works.md#the-client-ip-is-the-trust-boundary).
Sessions key on the client IP, so getting that wrong is the one mistake that makes
microguard look like it is working while detecting nothing.
