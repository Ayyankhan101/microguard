# How to probe an endpoint

Fingerprint how automated or hardened a live HTTP or WebSocket endpoint looks
from the outside. By the end you will have a reading on a target's defenses and
know exactly what that reading does and does not mean.

## Read this first

**A probe does not classify visitors.** It is microguard making its own requests
to a target and scoring the target's *responses* — timing, headers, entropy. The
number describes the endpoint you pointed it at, not the traffic arriving there.

The output still says `label: human` or `label: bot`, and the columns say
`Automation Fingerprint`. Read that as "how automated and defended does this
target look", never as "this endpoint's visitors are bots". To classify real
visitors, [scan an access log](howto-scan-log-files.md).

`model_score` is always `0.0` here. The session-trained network takes 19
session features and probe data has no valid mapping onto them, so the model is
deliberately not applied. The score you see is entirely heuristic.

## Prerequisites

- `pip install microguard`
- A URL you are authorized to send requests to

Probing sends real traffic. Three requests with a browser user agent is
unremarkable, but it is still your traffic hitting someone's server — only probe
what you own or have permission to test.

## Probe an HTTP endpoint

```bash
microguard probe https://example.com --count 3
```

```
  Microguard URL Probe Report
  ───────────────────────────

  🌐 Target:    https://example.com
  📡 Status:    HTTP 200
  ⏱️  Response:  0.190s
  🔍 Probes:    2

  ✅ Automation Fingerprint: 0.30 — SAFE (HUMAN)
  📊 Threshold: 0.70

  Analysis:
    Heuristic:  0.30 — no strong bot signals detected

  Key Features:
    Response time:    0.190s
    TTFB:             0.189s
    Body entropy:     5.03
    Timing CV:        0.148
    Security headers: 0
```

`--count 3` (the default) is the minimum worth using: with fewer than three
probes the timing-consistency rule cannot run, and timing consistency is the most
interesting signal a probe produces.

## Interpret the score

The heuristic rules return on first match, in this order:

| Score | Condition | What it tells you |
|---|---|---|
| 0.8 | HTTP 429 | Rate limiting is active |
| 0.7 | HTTP 403 | The target is blocking something, possibly you |
| 0.7 | Timing CV < 0.05 across >2 probes | Responses are machine-uniform |
| 0.6 | Connection error | Refused, timed out, or actively dropped |
| 0.5 | HTTP 5xx, or response > 5s | Server trouble, or throttling |
| 0.4 | Body entropy < 1.0 | Near-empty or templated response |
| 0.3 | Response < 50ms | Cached or CDN-served |
| 0.3 | Body entropy > 7.5 | Compressed or encrypted payload |
| 0.2 | ≥3 security headers | Well-protected site |
| 0.3 | nothing matched | No strong signals |

Note that a *low* score often means the target is well-defended (0.2 for security
headers) and a *high* score often means it pushed back at you (0.7 for a 403).
The scale is "how automated does this interaction look", and a target that
refuses automation scores high precisely because it recognized one.

## Probe a WebSocket

```bash
microguard probe wss://example.com/socket
```

The scheme selects the prober. The WebSocket path performs an RFC 6455 handshake
over a raw socket — no client library — and reports `connected`, `handshake_ok`,
handshake timing, and frame entropy if the server sends anything back.

Same caveat, more strongly: from outside the message stream there is no way to
tell a human-driven WebSocket client from an automated one. This measures the
server's handshake behavior.

## Get machine-readable output

```bash
microguard probe https://example.com --output json --output-file probe.json
jq '.heuristic_reason, .features' probe.json
```

Full shape in the [CLI reference](reference-cli.md#probe-result-schema). A
WebSocket probe adds `protocol`, `connected`, `handshake_ok` and `error`, and
drops `body_preview`.

`--json-pretty` is ANSI-colored and shaped like JSON. Not parseable.

## Flags that do nothing

These are parsed and then never passed to the prober:

```
--method  --model  --timeout  --no-verify-ssl  --user-agent
```

`--model` is inert by design, as described above. The other four are gaps: the
values are accepted and discarded, so `--user-agent 'MyBot/1.0'` still sends the
default Chrome string and `--no-verify-ssl` still verifies. Do not build on them.

The underlying `scanner.probe_url_multiple()` *does* honor `method`, `timeout`,
`user_agent` and `randomize_ua`, so the Python API works where the CLI does not:

```python
from microguard.scanner import extract_probe_features, probe_url_multiple

results = probe_url_multiple(
    "https://example.com", count=3, method="HEAD",
    timeout=5.0, user_agent="MyTool/1.0",
)
print(extract_probe_features(results))
```

## Verification

```bash
microguard probe https://example.com --count 3
echo $?
```

Exit code 0 means the fingerprint scored below the threshold, 1 means at or
above. A reachable public site should print `HTTP 200`, a response time, and a
score around 0.2–0.3. If you get a connection error against a site you can reach
in a browser, the target is refusing the probe's request shape — which is itself
the answer to the question you asked.

## Troubleshooting

**`connection error` on a site that works in a browser** — the target is
filtering by something the probe does not send. It uses a fixed Chrome UA and no
cookies. That is a real finding about the target's defenses, not a tool failure.

**`forbidden (HTTP 403)`** — same. You are being blocked, which scores 0.7.

**`--verbose` used to crash** — it called a formatter with an argument that
function never accepted. Fixed; `probe --verbose` now prints the full feature
breakdown.

**The score barely moves between targets** — most sites hit the "no strong
signals" default at 0.3. The rules only distinguish extremes. A probe is a quick
reading, not a scanner.

## Related

- [CLI reference](reference-cli.md) — every flag and the result schema
- [How to scan log files](howto-scan-log-files.md) — for classifying actual visitors
- [How detection works](explanation-how-detection-works.md) — why the model is not used here
