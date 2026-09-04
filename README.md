# 🔍 Microguard

**Bot Traffic Audit Tool powered by micrograd**

Detect malicious bot traffic in your API logs and live endpoints. Zero external dependencies beyond micrograd.

```
$ microguard scan access.log

  Microguard Bot Traffic Report
  ─────────────────────────────

  🚨 Bot Traffic: 50.0%  CRITICAL

  Total sessions:  4
  Human sessions:  2
  Bot sessions:    2

  █████░░░░░  50.0%

  IP                 Score Bar          Risk      Label    Reqs    Dur Heuristic Rule
  ─────────────────────────────────────────────────────────────────────────────────────
  10.0.0.50          0.95 █████████░ DANGER    bot     1   0.0s known bot/monitoring UA
  192.168.1.100      0.39 ███░░░░░░░ LOW       human     5  34.0s known browser, reasonable session
```

## Features

- **19 HTTP-level features** for bot detection (timing, behavioral, header analysis)
- **24 heuristic rules** across 4 confidence tiers (Cloudflare WAF, API key, botnet detection)
- **micrograd neural network** — 85 parameters, ~1.8KB model size
- **Live URL probing** — test endpoints directly, not just log files
- **Continuous monitoring** — watch mode tails log files in real time
- **Multiple output formats** — terminal, JSON, colored JSON, and HTML reports
- **Score interpretation** — SAFE / LOW / WARNING / DANGER risk labels
- **Zero external dependencies** — only requires micrograd

## Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/microguard.git
cd microguard

# Install in development mode
pip install -e .

# Or install directly
pip install .
```

### Requirements

- Python 3.8+
- [micrograd](https://github.com/karpathy/micrograd) (installed automatically)

## Quick Start

```bash
# Run with no arguments — auto-scans a sample log file
microguard

# Scan your own logs
microguard scan /var/log/nginx/access.log

# Probe a live URL
microguard probe https://example.com

# Watch mode — continuously monitor a log file
microguard scan access.log --watch
```

### Scan a Log File

```bash
# Basic scan
microguard scan /var/log/nginx/access.log

# Verbose output (feature vectors + heuristic rules)
microguard scan access.log --verbose

# Colored JSON for terminal reading
microguard scan access.log --json-pretty

# Machine-readable JSON
microguard scan access.log --output json

# Save HTML report
microguard scan access.log --output html --output-file report.html

# Adjust bot threshold (default: 0.7)
microguard scan access.log --threshold 0.5
```

### Probe a Live URL

```bash
# Single probe
microguard probe https://example.com

# Multiple probes for timing analysis
microguard probe https://example.com --count 5

# Verbose probe (full feature breakdown)
microguard probe https://example.com --verbose

# Save results as HTML
microguard probe https://example.com --output html --output-file probe.html

# Custom User-Agent
microguard probe https://example.com --user-agent "MyBot/1.0"
```

### Continuous Monitoring

```bash
# Watch a log file for new bot traffic
microguard scan access.log --watch

# Lower threshold for more sensitivity
microguard scan access.log --watch --threshold 0.5
```

## Score Interpretation

| Score Range | Risk Level | Meaning |
|-------------|------------|---------|
| 0.00 - 0.30 | **SAFE** | No bot signals detected |
| 0.31 - 0.59 | **LOW** | Minor signals, likely human |
| 0.60 - 0.79 | **WARNING** | Suspicious, investigate |
| 0.80 - 1.00 | **DANGER** | High confidence bot traffic |

## CLI Reference

```
microguard scan <logfile> [OPTIONS]
microguard probe <url> [OPTIONS]
microguard info
```

### Scan Options

```
  -f, --format {auto,nginx,json}    Log file format (default: auto-detect)
  -t, --threshold FLOAT             Bot score threshold (default: 0.7)
  -m, --model PATH                  Path to pre-trained model file
  -o, --output {terminal,json,html} Output format (default: terminal)
  -O, --output-file PATH            Write report to file instead of stdout
  --timeout INT                     Session timeout in minutes (default: 30)
  -v, --verbose                     Show feature vectors and heuristic rules
  -j, --json-pretty                 Colored JSON for terminal reading
  -w, --watch                       Continuously monitor log file
```

### Probe Options

```
  -n, --count INT                   Number of probes to send (default: 3)
  -d, --delay FLOAT                 Delay between probes in seconds (default: 0.5)
  --method {GET,POST,HEAD,OPTIONS}  HTTP method (default: GET)
  -t, --threshold FLOAT             Bot score threshold (default: 0.7)
  -m, --model PATH                  Path to pre-trained model file
  -o, --output {terminal,json,html} Output format (default: terminal)
  -O, --output-file PATH            Write report to file instead of stdout
  --timeout FLOAT                   Request timeout in seconds (default: 10)
  --no-verify-ssl                   Disable SSL certificate verification
  --user-agent TEXT                 Custom User-Agent header
  -v, --verbose                     Show feature vectors and heuristic rules
  -j, --json-pretty                 Colored JSON for terminal reading
```

### Python API

```python
from microguard import scan_logfile, probe_and_analyze, BotDetector

# Scan a log file
results = scan_logfile(
    filepath="access.log",
    fmt="auto",
    threshold=0.7,
    model_path="data/model.json",
)
print(f"Bot rate: {results['bot_rate']:.1%}")

# Probe a live URL
results = probe_and_analyze(
    url="https://example.com",
    count=3,
    delay=0.5,
    threshold=0.7,
    verbose=True,
)
print(f"Score: {results['combined_score']:.2f} ({results['label']})")

# Load and use the model directly
model = BotDetector("data/model.json")
features = [...]  # 19-dimensional feature vector
score = model.predict(features)  # 0.0 to 1.0
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        MICROGUARD CLI                           │
├─────────────────────────────────────────────────────────────────┤
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│   │  scan    │  │  probe   │  │  watch   │  │  info    │      │
│   └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────────┘      │
│        │              │             │                            │
│        ▼              ▼             ▼                            │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐                     │
│   │ parser   │  │ scanner  │  │ watch.py │                     │
│   │ • Nginx  │  │ • HTTP   │  │ • tail   │                     │
│   │ • JSON   │  │ • Timing │  │ • detect │                     │
│   └────┬─────┘  └────┬─────┘  └────┬─────┘                     │
│        └──────────────┴─────────────┘                            │
│                       ▼                                          │
│              ┌─────────────────┐                                 │
│              │  features.py    │                                 │
│              │  19 extractors  │                                 │
│              └────────┬────────┘                                 │
│           ┌───────────┴───────────┐                              │
│           ▼                       ▼                              │
│   ┌──────────────┐       ┌──────────────┐                       │
│   │  labeler.py  │       │  model.py    │                       │
│   │  24 rules    │       │  MLP(19→4→1) │                       │
│   └──────┬───────┘       └──────┬───────┘                       │
│           └──────────┬──────────┘                                │
│                      ▼                                           │
│            ┌─────────────────┐                                   │
│            │  Score Fusion   │                                   │
│            │  60% model +    │                                   │
│            │  40% heuristic  │                                   │
│            └────────┬────────┘                                   │
│                     ▼                                            │
│   ┌──────────────────────────────────────────────┐               │
│   │  report.py                                   │               │
│   │  • Terminal (ANSI colors, score bars)        │               │
│   │  • JSON (machine-readable)                   │               │
│   │  • JSON Pretty (colored for terminal)        │               │
│   │  • HTML (dark theme, responsive)             │               │
│   │  • Verbose (feature vectors + rules)         │               │
│   └──────────────────────────────────────────────┘               │
└─────────────────────────────────────────────────────────────────┘
```

## Heuristic Rules (24 patterns)

### High Confidence (0.90-0.99)
- Known bot/monitoring user agents (80+ patterns)
- Vulnerability scanner URL patterns (`/wp-admin`, `/phpmyadmin`, etc.)
- Uniform timing patterns (all requests within 1ms)
- HTTP/1.0-only clients
- Cloudflare WAF bypass detection
- Attack tool signatures (Nuclei, ffuf, Masscan, etc.)
- Botnet scanning patterns (Mirai, IoT endpoints)

### Medium Confidence (0.70-0.89)
- High request rates (>100 requests, >50 req/min)
- Same-endpoint scraping (>10 requests to same URL)
- No referrer on all requests (>20 requests)
- High error rate (>50% failed requests)
- Repeated API endpoint abuse
- API key parameter scanning (`?key=`, `?token=`, `?api_key=`)
- Credential brute-force (rapid auth endpoint hits)
- Directory brute-force (70%+ 403/404 responses)
- UA rotation (distributed attacks)

### Low Confidence (0.55-0.69)
- Unknown user agents
- Short fast sessions (<5s, >20 requests)
- Night-time activity patterns

### Human Signals (0.55-0.75)
- Known browser user agents with normal behavior
- Variable timing patterns (high CV)
- Multiple different endpoints explored
- Natural referrer chains
- Behind Cloudflare with normal browser

## Model Architecture

```
Input (19 features)
       │
       ▼
┌─────────────┐
│   Linear    │  19 → 4 (76 params)
│    + ReLU   │
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Linear    │  4 → 1 (5 params)
│    + Logit  │
└──────┬──────┘
       │
       ▼
   Score (0.0 - 1.0)

Total parameters: 85
Model size: ~1.8KB
Inference time: < 0.5ms per request
```

## File Structure

```
microguard/
├── setup.py                    # Package installation
├── README.md                   # This file
├── LICENSE                     # MIT License
├── .github/workflows/test.yml  # CI: tests on push/PR
├── microguard/
│   ├── __init__.py             # Package exports
│   ├── cli.py                  # CLI entry point (scan, probe, watch, info)
│   ├── parser.py               # Nginx + JSON log parsers
│   ├── features.py             # 19 feature extractors
│   ├── labeler.py              # 24 heuristic bot/human rules
│   ├── model.py                # micrograd MLP wrapper (85 params)
│   ├── scanner.py              # HTTP scanner for live probing
│   ├── watch.py                # Continuous log monitoring
│   ├── report.py               # Terminal + JSON + HTML + Verbose output
│   └── training/
│       ├── train.py            # Model training script
│       └── generate.py         # Synthetic data generation
├── tests/                      # 142 tests
│   ├── test_parser.py          # 16 tests
│   ├── test_features.py        # 21 tests
│   ├── test_labeler.py         # 7 tests
│   ├── test_labeler_rules.py   # 18 tests (Cloudflare, API key, botnet)
│   ├── test_model.py           # 7 tests
│   ├── test_scanner.py         # 16 tests
│   ├── test_scanner_extended.py # 16 tests
│   ├── test_report.py          # 27 tests
│   └── test_watch.py           # 8 tests
└── data/
    ├── model.json              # Pre-trained model weights (1.8KB)
    ├── normalization.json      # Feature normalization params
    ├── training_data.json      # Training dataset
    └── sample_access.log       # Example log file (auto-scanned on first run)
```

## Testing

```bash
# Run all tests (142 tests)
python -m pytest tests/

# Run with verbose output
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_features.py

# Run only new tests
python -m pytest tests/test_labeler_rules.py tests/test_report.py tests/test_watch.py
```

**Test coverage:** 142 tests (all passing)

## CI/CD

GitHub Actions workflow runs on every push and PR:

- **Matrix:** 3 OS (ubuntu, macOS, Windows) × 6 Python versions (3.8–3.13)
- **Steps:** Install → pytest → CLI smoke test
- **Config:** `.github/workflows/test.yml`

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Run tests (`python -m pytest tests/`)
4. Commit your changes (`git commit -m 'Add amazing feature'`)
5. Push to the branch (`git push origin feature/amazing-feature`)
6. Open a Pull Request

## License

MIT License - see [LICENSE](LICENSE) for details.

## Acknowledgments

- [micrograd](https://github.com/karpathy/micrograd) — The tiny autograd engine that makes this possible
- [Nescio98](https://github.com/Nescio98/Machine-Learning-Model-for-Bot-Detection) — Research on HTTP-level bot detection features
- [Harvard Dataverse](https://dataverse.harvard.edu/) — Web server access log datasets

---

**Powered by micrograd** 🧠
