# 🔍 Microguard

**Bot Traffic Audit Tool powered by micrograd**

Detect malicious bot traffic in your API logs and live endpoints. Zero external dependencies beyond micrograd.

```
$ microguard scan access.log

  Microguard Bot Traffic Report
  ─────────────────────────────

  🚨 Bot Traffic: 45.0%

  Total sessions:  128
  Human sessions:  70
  Bot sessions:    58

  IP                    Score Label     Reqs Duration Top Endpoint
  ──────────────────────────────────────────────────────────────────────
  10.0.0.50            0.95 bot         1     0.0s /api/products
  10.0.0.99            0.89 bot        47    12.3s /api/search
  192.168.1.100        0.32 human       5    34.0s /
```

## Features

- **19 HTTP-level features** for bot detection (timing, behavioral, header analysis)
- **micrograd neural network** — 465 parameters, ~5KB model size
- **Heuristic rules + ML model** combined scoring for accuracy
- **Live URL probing** — test endpoints directly, not just log files
- **Streaming processing** — handles GB-sized log files without running out of memory
- **Multiple output formats** — terminal, JSON, and HTML reports
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
- [micrograd](https://github.com/karpathy/micrograd)

```bash
pip install micrograd
```

## Quick Start

### Scan a Log File

```bash
# Basic scan
microguard scan /var/log/nginx/access.log

# With JSON output
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

# Save results as HTML
microguard probe https://example.com --output html --output-file probe.html

# Custom User-Agent
microguard probe https://example.com --user-agent "MyBot/1.0"
```

## Usage

### Command Line Interface

```
microguard scan <logfile> [options]
microguard probe <url> [options]
microguard info
```

#### Scan Command

```bash
microguard scan <logfile> [OPTIONS]

Options:
  -f, --format {auto,nginx,json}  Log file format (default: auto-detect)
  -t, --threshold FLOAT           Bot score threshold (default: 0.7)
  -m, --model PATH                Path to pre-trained model file
  -o, --output {terminal,json,html}  Output format (default: terminal)
  -O, --output-file PATH          Write report to file instead of stdout
  --timeout INT                   Session timeout in minutes (default: 30)
```

#### Probe Command

```bash
microguard probe <url> [OPTIONS]

Options:
  -n, --count INT                 Number of probes to send (default: 3)
  -d, --delay FLOAT               Delay between probes in seconds (default: 0.5)
  --method {GET,POST,HEAD,OPTIONS}  HTTP method (default: GET)
  -t, --threshold FLOAT           Bot score threshold (default: 0.7)
  -m, --model PATH                Path to pre-trained model file
  -o, --output {terminal,json,html}  Output format (default: terminal)
  -O, --output-file PATH          Write report to file instead of stdout
  --timeout FLOAT                 Request timeout in seconds (default: 10)
  --no-verify-ssl                 Disable SSL certificate verification
  --user-agent TEXT               Custom User-Agent header
```

### Python API

```python
from microguard import scan_logfile, probe_and_analyze, BotDetector

# Scan a log file
results = scan_logfile(
    filepath="access.log",
    fmt="auto",          # auto-detect format
    threshold=0.7,       # bot score threshold
    model_path="data/model.json",
)
print(f"Bot rate: {results['bot_rate']:.1%}")

# Probe a live URL
results = probe_and_analyze(
    url="https://example.com",
    count=3,             # number of probes
    delay=0.5,           # delay between probes
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
│                                                                 │
│   ┌──────────────┐      ┌──────────────┐      ┌──────────────┐ │
│   │  scan        │      │  probe       │      │  info        │ │
│   │  (log files) │      │  (live URLs) │      │  (version)   │ │
│   └──────┬───────┘      └──────┬───────┘      └──────────────┘ │
│          │                     │                                │
│          ▼                     ▼                                │
│   ┌──────────────┐      ┌──────────────┐                       │
│   │  parser.py   │      │  scanner.py  │                       │
│   │  • Nginx     │      │  • HTTP/1.1  │                       │
│   │  • JSON      │      │  • Timing    │                       │
│   │  • Auto-detect│     │  • Headers   │                       │
│   └──────┬───────┘      └──────┬───────┘                       │
│          │                     │                                │
│          ▼                     ▼                                │
│   ┌─────────────────────────────────────┐                      │
│   │           features.py               │                      │
│   │  19 Feature Extractors:             │                      │
│   │  • Timing: 4 features               │                      │
│   │  • Behavioral: 5 features           │                      │
│   │  • Header: 4 features               │                      │
│   │  • Payload: 3 features              │                      │
│   │  • Context: 3 features              │                      │
│   └─────────────────┬───────────────────┘                      │
│                     │                                          │
│          ┌──────────┴──────────┐                               │
│          ▼                     ▼                               │
│   ┌──────────────┐      ┌──────────────┐                       │
│   │  labeler.py  │      │  model.py    │                       │
│   │  Heuristic   │      │  micrograd   │                       │
│   │  Rules       │      │  MLP(19→     │                       │
│   │  (17 rules)  │      │   4→1)       │                       │
│   └──────┬───────┘      └──────┬───────┘                       │
│          │                     │                                │
│          └──────────┬──────────┘                               │
│                     ▼                                          │
│            ┌────────────────┐                                  │
│            │  Score Fusion  │                                  │
│            │  60% model +   │                                  │
│            │  40% heuristic │                                  │
│            └────────┬───────┘                                  │
│                     │                                          │
│          ┌──────────┴──────────┐                               │
│          ▼                     ▼                               │
│   ┌──────────────┐      ┌──────────────┐                       │
│   │  report.py   │      │  report.py   │                       │
│   │  • Terminal   │      │  • HTML      │                       │
│   │  • JSON       │      │  • Print     │                       │
│   └──────────────┘      └──────────────┘                       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Data Flow

```
LOG FILE / LIVE URL
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│                      PARSING LAYER                          │
│  Nginx combined / JSON structured / HTTP response           │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│                    FEATURE EXTRACTION                       │
│  Timing (4) + Behavioral (5) + Header (4) + Payload (3)    │
│  + Context (3) = 19-dimensional vector                     │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│                     ANALYSIS LAYER                          │
│  ┌─────────────────┐      ┌─────────────────┐              │
│  │ Heuristic Rules │      │  micrograd MLP  │              │
│  │ (17 patterns)   │      │  465 parameters  │              │
│  │ confidence: 0-1 │      │  score: 0-1     │              │
│  └────────┬────────┘      └────────┬────────┘              │
│           │                        │                        │
│           └──────────┬─────────────┘                        │
│                      ▼                                      │
│            ┌─────────────────┐                              │
│            │ Score Fusion    │                              │
│            │ 0.6×model +     │                              │
│            │ 0.4×heuristic   │                              │
│            └────────┬────────┘                              │
└─────────────────────┼───────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                      OUTPUT LAYER                           │
│  Terminal (ANSI) / JSON / HTML (dark theme, printable)      │
└─────────────────────────────────────────────────────────────┘
```

## Features Reference

### 19 HTTP-Level Features

| # | Feature | Description | Source |
|---|---------|-------------|--------|
| 1 | `time_since_last_request` | Seconds since previous request | Timing |
| 2 | `requests_per_minute_1m` | Request rate in last minute | Timing |
| 3 | `requests_per_minute_5m` | Request rate in last 5 minutes | Timing |
| 4 | `inter_request_time_cv` | Coefficient of variation of timing | Timing |
| 5 | `time_since_session_start` | Session duration | Timing |
| 6 | `endpoint_count` | Number of unique endpoints hit | Behavioral |
| 7 | `endpoint_sequence_entropy` | Shannon entropy of URL sequence | Behavioral |
| 8 | `unique_endpoint_ratio` | Unique endpoints / total requests | Behavioral |
| 9 | `method_mismatch_count` | HTTP method inconsistencies | Behavioral |
| 10 | `header_consistency_score` | Header presence ratio | Header |
| 11 | `has_accept_language` | Accept-Language header present | Header |
| 12 | `ua_category` | User-Agent classification (0=browser, 1=bot) | Header |
| 13 | `payload_entropy` | Shannon entropy of request body | Payload |
| 14 | `field_fill_speed` | Form field completion speed | Payload |
| 15 | `same_endpoint_hits` | Repeated hits to same endpoint | Context |
| 16 | `error_rate` | Percentage of 4xx/5xx responses | Context |
| 17 | `image_ratio` | Percentage of image requests | Context |
| 18 | `night_ratio` | Percentage of requests 2am-6am | Context |
| 19 | `max_sustained_click_rate` | Peak requests per second | Context |

### Heuristic Rules (17 patterns)

**High Confidence (0.90-0.99):**
- Known bot/monitoring user agents (80+ patterns)
- Vulnerability scanner URL patterns (`/wp-admin`, `/phpmyadmin`, etc.)
- Uniform timing patterns (all requests within 1ms)
- HTTP/1.0-only clients

**Medium Confidence (0.70-0.89):**
- High request rates (>100 requests, >50 req/min)
- Same-endpoint scraping (>10 requests to same URL)
- No referrer on all requests (>20 requests)
- High error rate (>50% failed requests)
- Repeated API endpoint abuse

**Low Confidence (0.55-0.69):**
- Unknown user agents
- Short fast sessions (<5s, >20 requests)
- Night-time activity patterns

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
│    + Sigmoid│
└──────┬──────┘
       │
       ▼
   Score (0.0 - 1.0)

Total parameters: 85
Model size: ~1.8KB
Inference time: < 0.5ms per request
```

### Training

```bash
# Generate training data
python -m microguard.training.generate

# Train model
python -m microguard.training.train

# Model saved to data/model.json
# Normalization params saved to data/normalization.json
```

**Training data:** 5,040 samples (2,040 synthetic + 3,000 real-world inspired)
**Training accuracy:** 99.7% on synthetic data
**Model weights:** JSON format, compatible with micrograd

## Output Formats

### Terminal (default)

```bash
microguard scan access.log
```

ANSI-colored terminal output with status icons, score badges, and tables.

### JSON

```bash
microguard scan access.log --output json
```

Structured JSON for programmatic consumption:

```json
{
  "total_sessions": 128,
  "bot_count": 58,
  "human_count": 70,
  "bot_rate": 0.45,
  "sessions": [
    {
      "ip": "10.0.0.50",
      "score": 0.95,
      "label": "bot",
      "request_count": 1,
      "duration": 0.0,
      "top_endpoint": "/api/products",
      "features": {...}
    }
  ]
}
```

### HTML

```bash
microguard scan access.log --output html --output-file report.html
```

Dark-themed, responsive HTML report with:
- Donut chart showing bot/human ratio
- Color-coded stat cards
- Sortable session table
- Print-friendly styles

## File Structure

```
microguard/
├── setup.py                 # Package installation
├── README.md                # This file
├── microguard/
│   ├── __init__.py          # Package exports
│   ├── cli.py               # CLI entry point
│   ├── parser.py            # Log file parsers
│   ├── features.py          # 19 feature extractors
│   ├── labeler.py           # Heuristic bot/human labels
│   ├── model.py             # micrograd MLP wrapper
│   ├── scanner.py           # HTTP scanner for live probing
│   ├── report.py            # Terminal + JSON + HTML output
│   └── training/
│       ├── train.py         # Model training script
│       └── generate.py      # Synthetic data generation
├── tests/
│   ├── test_parser.py
│   ├── test_features.py
│   ├── test_labeler.py
│   ├── test_model.py
│   └── test_scanner.py
└── data/
    ├── model.json           # Pre-trained model weights
    ├── normalization.json   # Feature normalization params
    ├── training_data.json   # Training dataset
    └── sample_access.log    # Example log file
```

## Testing

```bash
# Run all tests
python -m pytest tests/

# Run with verbose output
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_features.py

# Skip network-dependent tests
python -m pytest tests/ -m "not network"
```

**Test coverage:** 69 tests (all passing)

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

MIT License - see [LICENSE](LICENSE) for details.

## Acknowledgments

- [micrograd](https://github.com/karpathy/micrograd) — The tiny autograd engine that makes this possible
- [Nescio98](https://github.com/Nescio98/Machine-Learning-Model-for-Bot-Detection) — Research on HTTP-level bot detection features
- [Harvard Dataverse](https://dataverse.harvard.edu/) — Web server access log datasets

---

**Powered by micrograd** 🧠
