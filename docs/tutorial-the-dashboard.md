# Watch a bot get blocked

You will start the dashboard, send it real traffic, and watch a request get
blocked in the browser while you read exactly why. Then you will move the
blocking threshold from the UI and watch the same visitor's verdict flip,
without restarting anything.

About fifteen minutes. By the end you will be able to answer "why did this
customer get a 403?" from the screen alone.

## What you'll need

- Python 3.10 or newer
- Redis running locally (`brew install redis && brew services start redis`, or
  `docker run -p 6379:6379 redis:7`)
- Two terminals, and a browser

## Step 1: Install and start the dashboard

```bash
pip install 'microguard[dashboard,live]'
microguard dashboard --allow-config-writes
```

```
microguard dashboard on http://127.0.0.1:8500
  redis: redis://localhost:6379
  live feed: connected
  config writes: enabled
  api token: none
```

`live feed: connected` is the line to check. If it says `NOT CONNECTED`, Redis
is not reachable and the Live tab will stay empty.

`--allow-config-writes` is off by default because it lets the browser change who
gets blocked on a running site. You want it for step 6.

## Step 2: Open it

Go to `http://127.0.0.1:8500`.

Three tabs on the left: **Live**, **Scan**, **Model**. Live is empty — nothing is
scoring yet. Click **Scan**, pick `sample_access.log` from the dropdown, and you
have a result immediately:

- Four tiles across the top: bot traffic percentage, bot sessions, human
  sessions, integrations
- A table of sessions, worst first
- A small horizontal bar in the score column for each row

That bar is the instrument the whole UI is built around. Hold that thought until
step 5.

## Step 3: Start the blocking server

New terminal:

```bash
microguard serve
```

```
microguard check server listening on 127.0.0.1:8400
  redis: redis://localhost:6379
  threshold: 0.85
  session TTL: 1800s
  model: loaded
  trust X-Forwarded-For: False
```

This is the service nginx asks about every request. You are going to ask it
directly, which is exactly what nginx does.

`model: loaded` matters. If it says `NOT LOADED (heuristics only)`, every score
you see next is missing 60% of its signal — and the dashboard will say so
loudly.

## Step 4: Send it a bot

Back in the first terminal:

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "X-Real-IP: 203.0.113.9" \
  -H "User-Agent: curl/8.0" \
  -H "X-Original-URI: /wp-admin/setup-config.php" \
  http://127.0.0.1:8400/check
```

```
403
```

Blocked. Now a normal visitor:

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "X-Real-IP: 198.51.100.7" \
  -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36" \
  -H "X-Original-URI: /products" \
  http://127.0.0.1:8400/check
```

```
200
```

Switch to the browser, **Live** tab. Both decisions are there, newest first,
with the blocked one marked in red and the counters at the top now reading 2
requests, 1 blocked.

You did not refresh the page. The dashboard is streaming decisions over
server-sent events as the check server makes them.

## Step 5: Read why

Look at the bar on the blocked row. It is one 0–1 scale carrying four things:

```
 0.00                          0.50                     0.85        1.00
  ├─────────────────────────────┼────────────────────────┤───────────┤
                                      ● model 0.71        │      ◆ blend 0.95
                                                      threshold   ■ rules 0.95
```

| Mark | Meaning |
|---|---|
| Blue square | what the heuristic rules concluded, and how strongly |
| Purple square | what the model alone predicted |
| White diamond | the blended score the verdict actually used |
| Vertical line | the threshold this request was judged against |

Click the row for the full breakdown. It reads in a specific order, and that
order is the diagnostic procedure:

1. **Is a model loaded at all?** If not, everything below is rules-only.
2. **What did the rules say?** Here:
   `known bot/monitoring UA: curl/8.0`, at confidence 0.95.
3. **Do the rules and the model agree?** Rules 0.95, model 0.71. Both high, so
   this is not a close call.
4. **Where did the blend land relative to the bar?** 0.95 against 0.85.

That is a complete answer to "why was this blocked", and you can give it to
whoever asks.

Worth noticing what the reason is *not*. That request also asked for
`/wp-admin/setup-config.php`, which trips the scanner-path rule — but the rules
are evaluated in order and return on the first match, and the bot-UA rule comes
first. A reason tells you which rule fired, not every rule that would have. See
[the rule reference](reference-heuristic-rules.md).

Compare the allowed row: the rules said human, the marks sit left of the gate,
and the diamond never crosses it.

## Step 6: Move the threshold, without restarting

On the Live tab, find the **block threshold** card. Drag it to `0.00` and click
**apply**.

Now send the *normal* visitor again, from a fresh IP so it has no history:

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "X-Real-IP: 198.51.100.8" \
  -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36" \
  -H "X-Original-URI: /products" \
  http://127.0.0.1:8400/check
```

```
403
```

A browser, blocked, because you set the bar to zero. The check server was never
restarted — it re-reads the threshold from Redis about every five seconds, so
every in-flight session survived the change.

Click **clear override** and try once more with a new IP. Back to `200`.

That restart-free property is the point. Restarting the check server drops every
session in Redis, which is the accumulated history the timing and rate rules
depend on — so you would be evaluating your tuning against a system that just
forgot everything.

**Threshold 1.00 is the useful setting to remember.** Nothing is ever blocked,
but every decision is still scored and recorded. That is observe-only mode, and
it is how you watch scores on real traffic before enforcing anything.

## Step 7: Look at the model

Click **Model**.

The architecture is `19 → 4 → 1`: nineteen features in, four hidden neurons, one
output. 85 parameters, about 1.8 KB.

Below that, every input ranked by how much the network can react to it. Then an
evaluation panel: pick **held-out real traffic** and drag the threshold. The
confusion matrix, the ROC curve and the score distribution all move together, so
you can see what a stricter bar costs in missed bots and what a looser one costs
in blocked customers.

You will see a banner saying the result is perfect and that this is a caution
rather than a win. It is telling the truth: 97% of the labeled attacks in that
dataset are directory scanning, and most bot labels came from the same heuristic
rules the model partly learned to imitate.

Switch the dataset to **adversarial** — bots built to evade the heuristics, never
trained on. The score stays perfect there too, and that is also not reassuring:
the human baseline's non-timing features are largely constant placeholders, so
the model may be separating synthetic vectors from real ones rather than bots
from humans.

Two perfect scores, neither of them strong evidence. That is the honest state of
this model, and it is why the banner is there.

## What you built

A running control plane for bot blocking. You can see every decision as it is
made, explain any one of them from four numbers on a single scale, and retune
enforcement from the browser without dropping session state.

Before you point this at production, two things:

- **The dashboard has no accounts.** It binds loopback for a reason. It reports
  every blocked visitor and, with `--allow-config-writes`, changes who gets
  blocked. If it must be reachable, use `--token` and put your own TLS and access
  controls in front.
- **Run in observe-only first.** Set the threshold to 1.00 against real traffic
  and watch for a day. Your traffic is not the sample log.

Next:

- [Deploy behind nginx](howto-deploy-behind-nginx.md) — wire `/check` into a real site
- [Deploy in-process](howto-deploy-in-process.md) — FastAPI or Flask middleware instead
- [How to tune blocking](howto-tune-blocking.md) — which rules block at which threshold
- [Why the dashboard is built this way](explanation-dashboard-design.md)
