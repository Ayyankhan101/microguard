# Changelog

## [Unreleased]

### Added
- **Real-time inline blocking** (`microguard/live/`, spec 0001) — the live path
  that had shipped across seven commits without a changelog entry:
  - `microguard serve` — a threaded stdlib HTTP server answering nginx
    `auth_request` on `GET /check` with 200/403 plus the full decision as JSON
    and `X-Microguard-*` headers.
  - `MicroguardASGI` / `MicroguardWSGI` — the same scoring in-process for
    FastAPI/Starlette and Flask/WSGI apps.
  - `LiveScorer` — the one place real-time scoring happens; returns the whole
    decision (blended score, model score, heuristic label/confidence/reason),
    not just the verdict.
  - `RedisSessionStateStore` — session history as a capped Redis LIST
    (`live:v2:{ip}`, 200 entries, sliding TTL), appended and read back in one
    atomic pipeline. The earlier get/mutate/set shape lost concurrent appends
    from the same actor: 9 of 20 survived under load.
  - `scoring.compute_combined_score()` — the single blend, shared by `scan`,
    `watch` and the live path.
  - Six Diataxis documents under `docs/` covering the live path.
- **Web dashboard** — `microguard dashboard` serves a TypeScript SPA and its API
  on one port (default `127.0.0.1:8500`):
  - Live tab: decisions streamed over SSE, a score histogram, most-blocked IPs,
    and loud treatment of the two states that silently change every verdict —
    no model loaded, and failing open.
  - Scan tab: upload or pick a bundled log, re-slice verdicts against a
    threshold client-side, open any session's 19 features, and export through
    the existing `report.py` formatters.
  - Model tab: the 19 → 4 → 1 network, per-input influence, and a confusion
    matrix / ROC / score distribution that move with the threshold. A perfect
    result is labelled as a caution, not a win.
  - `gui/` — Vite + React + TypeScript, 87 tests. The build is copied into
    `microguard/dashboard/static/` and shipped in the wheel, so running the
    dashboard never needs Node. New `dashboard` extra.
- **Decision recording** — `microguard/events.py` (`DecisionRecorder` protocol,
  in-memory implementation) and `microguard/live/redis_events.py` (Redis-backed,
  `mg:v1:` keys). `LiveScorer` tees every decision to it, including the
  `automated-integration` short circuit. Recorder failures are logged and
  swallowed: recording exists for the dashboard, blocking exists for the site,
  and the second must never depend on the first.
- **Runtime block threshold** — `RedisRuntimeConfig` stores an override in
  `mg:v1:config`, and `LiveScorer` reads it once per request (cached ~5s). The
  dashboard can move the live threshold with `--allow-config-writes`, and every
  scoring process sharing that Redis follows within seconds, without a restart
  dropping in-flight sessions. An unreachable or unreadable config leaves the
  configured threshold standing.
- `block_threshold` added to the decision payload — the bar the request was
  actually judged against. `null` on the fail-open payload, where no threshold
  was consulted.
- `microguard dashboard --token` — a shared secret required in
  `X-Microguard-Token` on every `/api` request, for deployments that cannot stay
  on loopback.
- `docs/howto-run-the-dashboard.md`, plus a Dashboard API section in
  `docs/reference-live-api.md`.
- `tests/conftest.py` — shared `make_entry`/`make_session`/`nginx_log_file`
  fixtures, replacing three near-identical hand-rolled `_make_entry` copies
  across `test_features.py`, `test_labeler_rules.py`.
- `tests/test_cli.py` (20 tests) — `cli.py` (550 lines: `scan_logfile()`,
  `main()`, all argument parsing, score-blending) had zero dedicated tests
  before this. Covers exit codes, output formats, the `automated-integration`
  exclusion, the probe ws://-vs-http(s) dispatch, and a direct regression
  test for the heuristic/model score-blending symmetric cap.
- `TestWatchLogfile` in `tests/test_watch.py` (4 tests) — `watch_logfile()`'s
  130-line orchestration loop was untested (only its two small helpers
  were). Required adding a test-only `_max_iterations` seam to
  `watch.py::watch_logfile` to terminate its otherwise-infinite loop.
- Coverage tooling: `pytest-cov` (dev-only, `requirements-dev.txt`),
  `pyproject.toml` pytest/coverage config, wired into CI. Baseline: 71%
  overall; no hard gate yet.

### Fixed
- **`microguard probe <url> --verbose` crashed with a `TypeError`** — `cli.py`
  called `format_probe_report(results, verbose=True)`, but that function has
  never taken a `verbose` argument. The function that does the verbose
  rendering, `format_probe_verbose()`, sat unused next to it; it is now wired
  in, and `probe --verbose` is covered in `tests/test_cli.py`. This was the
  last remaining `vulture` finding in the package.
- **`watch.py` had the same heuristic/model score-blending asymmetry bug
  already fixed in `cli.py`** — an independent, undiscovered copy of the
  same logic. A confident heuristic 'human' call (e.g. a GraphQL session)
  could still be overridden by the model's score in watch mode. Found
  while writing `TestWatchLogfile`; fixed with the same symmetric cap.
- `scan_logfile()` raised a raw `FileNotFoundError` for a missing log file
  instead of returning its own established `{'error': ...}` dict shape
  (the graceful path only existed in `main()`'s pre-check, not in the
  underlying function documented and exported as part of the Python API).
- CI's CLI smoke test had `continue-on-error: true`, silently swallowing a
  genuine crash (it was added because `microguard scan` correctly exits 1
  when it detects bots — but that setting also ignores real breakage). Now
  checks stdout for a traceback / the expected report header instead of
  relying on exit code, which can't distinguish "detected bots" from
  "crashed" (both exit 1).

## [2.0.0] - 2026-09-06

v2.0 replaces the synthetic-only bot training data with real ground-truth
attack traffic, fixes a real false-positive bug affecting GraphQL/SOAP/RPC/
gRPC APIs, adds a real WebSocket probe, and corrects several claims in the
tool's own output that didn't hold up (a live-probe "ML score" that wasn't
a valid inference, a 95% accuracy number measured on the training set
itself). Beta: see README's Known Limitations before production use.

### Added
- **Real training data.** `training/groundtruth.py` + `training/build_real_dataset.py`
  build the bot class from real, forensically ground-truth-labeled attack
  traffic (`data/zenodo_data/organization-x/`: 213K real Apache log lines,
  39 real attack-pattern rules — SQLi, RCE, directory scanning, brute-force
  login, etc.) instead of `random.uniform()` synthetic data. Combined with
  real Harvard human session data; synthetic data is now a capped top-up
  for underrepresented attack subtypes only, not the bulk of the bot class.
- **Held-out generalization eval.** `training/train.py::train_model` now
  carves out a session-actor-level stratified split *before* training and
  saves it as `data/eval_holdout.json`; `tests/test_training_quality.py::TestHeldOutAccuracy`
  evaluates against it, including a recall check isolated to ground-truth
  (not heuristic) labels specifically.
- **Adversarial eval.** `data/adversarial_eval.json` + `TestAdversarialRobustness`
  measure (don't gate on) recall against a synthetic browser-mimicking
  stealthy-bot generator — an honest measurement of a known blind spot.
- **WebSocket probe.** `microguard probe wss://...` — hand-rolled RFC 6455
  handshake over stdlib `socket`/`ssl` (no new dependency), with its own
  heuristic scorer, terminal/HTML formatters, and 15 tests against an
  in-process echo server.
- **Webhook / RPC-client allowlist.** Recognized senders (Stripe,
  GitHub-Hookshot, Shopify, grpc-go/java/python/..., etc.) are labeled
  `automated-integration` — automated by definition, not scored as a
  security threat, excluded from bot-rate counts.
- **`--output nginx` / `--output cloudflare`** on `microguard scan` —
  auto-generate an nginx deny-list or Cloudflare Firewall Rule expression
  for DANGER-scored (0.80-1.00) IPs, deduped and sorted. Scan-only.
- **`status_code_entropy` feature** (replaces the permanently-hardcoded-0
  `field_fill_speed`) — Shannon entropy of a session's HTTP status codes,
  a real signal distinct from `error_rate`.

### Fixed
- **GraphQL false positive:** `/graphql` was in `API_KEY_SCAN_PATTERNS`,
  branding every legitimate GraphQL client a credential-scanning bot at
  0.75 confidence. Removed.
- **Single-endpoint API false positives:** GraphQL/SOAP/RPC paths are now
  exempt from the "same endpoint = scraper" heuristics, at *both* the
  heuristic-label layer and the score-blending layer in `cli.py` (a
  heuristic fix alone didn't change the final classification — the model's
  independent score could still override it; added a symmetric cap
  mirroring the existing bot-confidence floor).
- **gRPC uniform-timing false positive:** HTTP/2 multiplexing produces
  near-uniform request timing for legitimate clients; the "uniform timing
  = bot" rule now skips sessions with ≥3 distinct `/Service.Method` paths.
- **`probe` no longer feeds live-probe data through the session-trained
  MLP.** `_probe_features_to_vector` mapped unrelated response data into a
  vector shaped for session-log features, then called `model.predict()` on
  it — not a valid inference. Removed; probe scoring is now the heuristic
  alone, and displayed output is relabeled "Automation Fingerprint" instead
  of implying visitor bot/human classification.
- **95% accuracy claim was circular:** it was measured on the model's own
  training set. `TestModelAccuracy` is now explicitly documented as a
  train-set fit sanity check; `TestHeldOutAccuracy` is the real number.
- **Python version claim:** `setup.py`/CI claimed 3.8+ while the codebase
  already used 3.10-only `X | Y` union syntax throughout (pre-existing,
  not introduced this release). Bumped `python_requires` to `>=3.10` and
  dropped the 3.8/3.9 CI legs to match reality.
- Malformed or empty log files rendered as a fake "✅ HEALTHY, 0.0%" report
  in terminal and HTML output instead of surfacing the parse failure — the
  `error` field `scan_logfile()` sets was silently dropped by two of four
  report formatters (`format_json`/`print_report` were unaffected). Also
  fixed the HTML donut chart, which drew a solid danger-red ring for
  zero-session data. Both `format_nginx_denylist`/`format_cloudflare_rule`
  now inherit the same fix.
- `_check_botnet_signatures`'s UA-rotation heuristic false-flagged every
  1-3 request session as a bot — the scaled threshold
  `min(10, request_count * 0.3)` drops below 1 for small sessions, so any
  single-UA session (the normal case) trivially satisfied it.
- `--output-file` to a missing/invalid directory crashed with a raw Python
  traceback instead of a clean CLI error, across all three write sites
  (scan, scan --verbose, probe).
- Cleared all `ruff`/`mypy`/`vulture` findings (270 lint errors, 7 type
  errors, 12 dead-code items) without changing behavior; added `mypy.ini`
  and `microguard/vulture_whitelist.py`.

## [0.1.0] - 2026-09-04

Initial release. Everything built in one session.

### Core Engine
- **Log parser** — Nginx combined and JSON log formats with auto-detection
- **19 feature extractors** — timing (5), behavioral (4), header (3), payload (2), context (1), bonus (4)
- **24 heuristic rules** across 4 confidence tiers for bot/human classification
- **micrograd MLP** — 19→4→1 architecture, 85 parameters, ~1.8KB
- **Score fusion** — 60% ML model + 40% heuristic, capped at heuristic confidence for bots

### CLI Commands
- `microguard scan <logfile>` — scan log files for bot traffic
- `microguard probe <url>` — probe live URLs for bot detection signals
- `microguard scan <logfile> --watch` — continuous log file monitoring
- `microguard info` — show version and feature info
- Auto-sample on first run — bare `microguard` scans bundled sample log

### CLI Flags
- `--verbose` / `-v` — full feature vector breakdown with heuristic rule details
- `--json-pretty` / `-j` — colored JSON output for terminal reading
- `--watch` / `-w` — continuous log monitoring with real-time alerts
- `--output {terminal,json,html}` — multiple output formats
- `--output-file` — save reports to file
- `--threshold` — adjustable bot score threshold (default: 0.7)
- `--user-agent` — custom User-Agent for probe requests

### Output Formats
- **Terminal** — ANSI-colored with score bars, risk labels (SAFE/LOW/WARNING/DANGER), colored recommendations
- **JSON** — machine-readable, indented
- **JSON Pretty** — colored keys, scores, and labels for terminal scanning
- **HTML** — dark theme, donut chart, sortable table, print-friendly
- **Verbose** — per-session feature vectors with bot/human indicators

### Score Interpretation
- SAFE (0.00-0.30) — no bot signals detected
- LOW (0.31-0.59) — minor signals, likely human
- WARNING (0.60-0.79) — suspicious, investigate
- DANGER (0.80-1.00) — high confidence bot traffic

### Heuristic Rules (24 total)

**High Confidence (0.90-0.99):**
- Known bot/monitoring user agents (80+ patterns)
- Vulnerability scanner URL patterns
- Uniform timing patterns
- HTTP/1.0-only clients
- Cloudflare WAF bypass detection
- Attack tool signatures (Nuclei, ffuf, Masscan, etc.)
- Botnet scanning patterns (Mirai, IoT endpoints)

**Medium Confidence (0.70-0.89):**
- High request rates (>100 requests, >50 req/min)
- Same-endpoint scraping
- No referrer on all requests
- High error rate (>50% failed)
- Repeated API endpoint abuse
- API key parameter scanning
- Credential brute-force detection
- Directory brute-force detection
- UA rotation detection

**Low Confidence (0.55-0.69):**
- Unknown user agents
- Short fast sessions
- Night-time activity patterns

**Human Signals (0.55-0.75):**
- Known browsers with normal behavior
- Variable timing patterns
- Multiple endpoints explored
- Natural referrer chains
- Behind Cloudflare with normal browser

### Live Probing
- HTTP/1.1 requests with timing measurement
- Security header detection (CSP, HSTS, X-Frame-Options)
- Body entropy analysis
- Timing consistency analysis (CV calculation)
- Cloudflare CDN detection (CF-Ray, server_bot_score)
- Multi-probe support for timing pattern analysis

### Continuous Monitoring (`--watch`)
- Tails log file every 2 seconds
- Real-time bot detection alerts
- Periodic status updates (every 30s)
- Final summary on Ctrl+C

### Training Pipeline
- Synthetic data generator with realistic bot/human patterns
- Training script with MSE loss on micrograd
- Feature normalization (min-max scaling)
- Combined real + synthetic training data (5,040 samples)
- 99.7% training accuracy

### Testing
- 142 tests across 9 test files
- All tests passing (1 skipped — network-dependent)
- Coverage: parser, features, labeler, model, scanner, report, watch

### CI/CD
- GitHub Actions workflow (`.github/workflows/test.yml`)
- Matrix: 3 OS × 6 Python versions (3.8–3.13)
- Auto-runs on push and PR

### Documentation
- README.md — quick start, CLI reference, architecture diagrams, feature table
- CONTRIBUTING.md — setup, testing, PR process
- LICENSE — MIT
- CLAUDE.md — project context for AI assistants

### Files Created
```
microguard/cli.py          — CLI entry point
microguard/parser.py       — Nginx + JSON parsers
microguard/features.py     — 19 feature extractors
microguard/labeler.py      — 24 heuristic rules
microguard/model.py        — micrograd MLP wrapper
microguard/scanner.py      — HTTP scanner
microguard/watch.py        — Continuous monitoring
microguard/report.py       — Output formatters
microguard/training/       — Training pipeline
tests/test_parser.py       — 16 tests
tests/test_features.py     — 21 tests
tests/test_labeler.py      — 7 tests
tests/test_labeler_rules.py — 18 tests
tests/test_model.py        — 7 tests
tests/test_scanner.py      — 16 tests
tests/test_scanner_extended.py — 16 tests
tests/test_report.py       — 27 tests
tests/test_watch.py        — 8 tests
data/model.json            — Trained model (1.8KB)
data/normalization.json    — Feature normalization
data/sample_access.log     — Demo log file
.github/workflows/test.yml — CI config
README.md                  — Documentation
CONTRIBUTING.md            — Contributor guide
CHANGELOG.md               — This file
LICENSE                    — MIT License
CLAUDE.md                  — AI assistant context
```
