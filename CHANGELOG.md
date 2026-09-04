# Changelog

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
