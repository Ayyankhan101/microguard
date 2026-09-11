# Your first scan

You will find the bots in an access log, learn what the score is made of, and
turn the result into a firewall rule. It takes about ten minutes, and by the end
you will be able to look at any log file and say which visitors were automated
and why.

This is the batch side of microguard: analysis after the fact. Blocking in real
time is [a separate tutorial](tutorial-real-time-blocking.md).

## What you'll need

- Python 3.10 or newer
- A terminal
- Optionally, one of your own nginx or JSON access logs. A sample ships with the
  tool, so you can do the whole thing without one.

## Step 1: Install

```bash
pip install microguard
```

Nothing else. The detector depends on micrograd and the standard library.

## Step 2: See it work

```bash
microguard
```

With no arguments it scans a bundled sample log:

```
  Microguard Bot Traffic Report
  ─────────────────────────────

  🚨 Bot Traffic: 50.0%  CRITICAL

  Total sessions:  2
  Human sessions:  1
  Bot sessions:    1

  IP                 Score Bar          Risk      Label    Reqs    Dur Heuristic Rule
  ────────────────────────────────────────────────────────────────────────────────────
  10.0.0.50          0.95 █████████░ DANGER    bot         1   0.0s known bot/monitoring UA: python-req
  192.168.1.100      0.25 ██░░░░░░░░ SAFE      human       5  34.0s known browser, reasonable session (
```

Two visitors, two verdicts. Read the rightmost column first — it is the reason,
and it is the part you can act on. `10.0.0.50` sent a `python-requests` user
agent. `192.168.1.100` used a browser and spent 34 seconds across several pages.

Everything from here is a variation on this output.

## Step 3: Scan your own log

```bash
microguard scan /var/log/nginx/access.log
```

Gzipped files and JSON logs work too:

```bash
microguard scan access.log.gz
microguard scan app.json --format json
```

If you see `❌ No valid log entries found`, the format was not recognized —
microguard reads nginx combined and JSON-per-line. Keep using the sample for the
rest of this tutorial and come back to
[scanning log files](howto-scan-log-files.md) for format details.

## Step 4: Look at what the score is made of

The 0.95 in step 2 is a blend of two independent opinions. Ask for the breakdown:

```bash
microguard scan data/sample_access.log --verbose
```

You now get, for every session, all 19 numbers the detector extracted plus the
rule that fired:

```
  Session: 10.0.0.50
  ...
  Features:
    time_since_last_request     0.000
    requests_per_minute_1m      1.000
    endpoint_count              1.000
    ua_category                 1.000
    ...
```

`ua_category` of 1.0 means "bot-shaped user agent". `endpoint_count` of 1 means
this actor touched a single path. Those are two of the
[19 features](reference-features.md) that describe a session.

The two opinions being blended are:

- **The rules** — about 24 ordered checks, first match wins. Here, "known
  bot/monitoring UA" fired with confidence 0.95.
- **The model** — an 85-parameter neural network over those 19 features.

They are combined 60% model, 40% rules, except that a confident rule floors or
caps the result. That is why this session scored exactly 0.95: a rule that
certain does not need the model's agreement.
[How detection works](explanation-how-detection-works.md) covers the whole path.

## Step 5: Change the threshold and watch verdicts move

The default threshold is 0.7. Try tightening it:

```bash
microguard scan data/sample_access.log --threshold 0.96
```

The bot count drops to 0 — 0.95 no longer clears the bar. Now loosen it:

```bash
microguard scan data/sample_access.log --threshold 0.2
```

Both sessions are bots, including the browser one at 0.25.

That is the whole tuning exercise in miniature. The threshold decides which rules
are strong enough to flag something on their own: at 0.85 only the 0.90–0.95
rules are, at 0.7 the 0.75 and 0.80 rules join them, and by 0.2 you are flagging
anyone.

## Step 6: Produce something you can act on

Two formats turn a scan into policy. Both emit rules for DANGER-scored IPs only
(above 0.79):

```bash
microguard scan data/sample_access.log --output nginx
# deny 10.0.0.50;

microguard scan data/sample_access.log --output cloudflare
# (ip.src in {10.0.0.50})
```

Write one to a file and include it:

```bash
microguard scan access.log --output nginx --output-file blocked.conf
```

Read it before you load it. An IP is a weak identifier for a person — shared NAT
puts a lot of people behind one address — so treat a deny list as a blunt,
temporary instrument.

And for a report to send someone:

```bash
microguard scan data/sample_access.log --output html --output-file report.html
```

Open it locally. Do not host it on a domain you care about: the HTML report does
not escape user agents from your log, and those come from whoever sent them.

## What you built

You can now scan any nginx or JSON access log, read a verdict and the reason
behind it, inspect the 19 features that produced the score, move the threshold
deliberately, and export firewall rules or a shareable report.

Worth knowing before you rely on it: every signal here comes from a log line.
There is no TLS fingerprinting and no browser challenge, so a bot that produces
plausible log lines looks plausible. The
[known limitations](explanation-how-detection-works.md#what-this-cannot-do) are
written down, and worth reading before you block anyone.

Next:

- [How to scan log files](howto-scan-log-files.md) — formats, JSON output, CI usage
- [Your first dashboard](tutorial-the-dashboard.md) — the same data in a browser
- [Real-time blocking](tutorial-real-time-blocking.md) — stop bots instead of reporting them
- [The heuristic rules](reference-heuristic-rules.md) — every rule and its confidence
