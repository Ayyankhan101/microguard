# 🛡️ Microguard — API Bot Detection Powered by micrograd

> A tiny, zero-dependency, in-process bot detection engine for any Python API.
> Built on top of [karpathy/micrograd](https://github.com/karpathy/micrograd).

---

## 📌 Project Overview

| Detail | Value |
|--------|-------|
| **Name** | Microguard |
| **Goal** | Detect and block malicious bot traffic in real-time using a tiny neural network |
| **Core Engine** | micrograd (~100 lines autograd + ~50 lines neural network) |
| **Framework** | FastAPI middleware (framework-agnostic adapter layer) |
| **Model Size** | < 5KB weights, < 10KB RAM |
| **Inference Time** | < 0.5ms per request |
| **Cost** | $0/month (runs entirely in your existing app) |
| **Dependencies** | micrograd only (no TensorFlow, no PyTorch, no external ML libs) |

---

## 🎯 Problem Statement

- **51% of internet traffic is bots** (Imperva 2025 report)
- **37% is malicious bots** — credential stuffing, scraping, API abuse
- Existing solutions cost **$200–$2,000+/month** (Cloudflare, Akamai, etc.)
- Small-to-medium projects have **no affordable, smart bot detection**
- Rate limiting alone is trivially bypassed by IP rotation and residential proxies

**Microguard fills the gap**: a free, smart, in-process bot detector that learns from YOUR traffic patterns and runs with zero external dependencies.

---

## 🏗️ Architecture

```
                    YOUR API SERVER
┌─────────────────────────────────────────────┐
│                                             │
│  Incoming HTTP Request ──────┐              │
│                              │              │
│                              ▼              │
│  ┌───────────────────────────────────┐      │
│  │    1. SESSION TRACKER             │      │
│  │    Track per-session state:       │      │
│  │    - Request timestamps           │      │
│  │    - Endpoint sequence            │      │
│  │    - Header history               │      │
│  │    - Payload patterns             │      │
│  └───────────────┬───────────────────┘      │
│                  │                          │
│                  ▼                          │
│  ┌───────────────────────────────────┐      │
│  │    2. FEATURE EXTRACTOR           │      │
│  │    Extract 15 features per        │      │
│  │    request (see Features section) │      │
│  └───────────────┬───────────────────┘      │
│                  │                          │
│                  ▼                          │
│  ┌───────────────────────────────────┐      │
│  │    3. micrograd MODEL             │      │
│  │    MLP(15 → 16 → 8 → 1)          │      │
│  │    Output: bot probability 0.0–1.0│      │
│  └───────────────┬───────────────────┘      │
│                  │                          │
│                  ▼                          │
│  ┌───────────────────────────────────┐      │
│  │    4. DECISION ENGINE             │      │
│  │    Score < 0.30  → ✅ ALLOW       │      │
│  │    Score 0.30–0.70 → 📝 LOG       │      │
│  │    Score 0.70–0.90 → ⚠️ CHALLENGE  │      │
│  │    Score > 0.90  → 🚫 BLOCK       │      │
│  └───────────────┬───────────────────┘      │
│                  │                          │
│                  ▼                          │
│          Response to Client                 │
│                                             │
└─────────────────────────────────────────────┘
```

---

## 📂 File Structure

```
microguard/
├── README.md                    # Documentation + quickstart
├── setup.py                     # Package setup (pip install microguard)
├── plan.md                      # This file
│
├── microguard/
│   ├── __init__.py              # Public API exports
│   ├── engine.py                # Re-export micrograd engine
│   ├── detector.py              # Core BotDetector class
│   ├── features.py              # Feature extraction logic
│   ├── session.py               # Per-session state tracker
│   ├── model.py                 # Model definition + train/predict
│   ├── decision.py              # Scoring thresholds + actions
│   ├── middleware.py            # FastAPI / Starlette middleware
│   └── utils.py                 # Helpers (entropy calc, etc.)
│
├── training/
│   ├── __init__.py
│   ├── train.py                 # Training pipeline
│   ├── dataset.py               # Dataset loading (own logs + public)
│   ├── synthetic.py             # Synthetic bot traffic generator
│   └── evaluate.py              # Model evaluation + metrics
│
├── data/
│   ├── sample_human_logs.json   # Example human traffic
│   └── sample_bot_logs.json     # Example bot traffic
│
├── examples/
│   ├── fastapi_example.py       # Full working FastAPI demo
│   ├── flask_example.py         # Flask integration example
│   ├── standalone_example.py    # Standalone usage (no framework)
│   └── train_and_deploy.py      # End-to-end: train → save → serve
│
├── dashboard/
│   ├── app.py                   # Simple Streamlit dashboard
│   └── templates/
│       └── stats.html           # Bot traffic visualization
│
├── tests/
│   ├── __init__.py
│   ├── test_features.py         # Feature extraction tests
│   ├── test_model.py            # Model forward/backward pass tests
│   ├── test_detector.py         # End-to-end detection tests
│   ├── test_middleware.py       # Middleware integration tests
│   └── test_training.py         # Training pipeline tests
│
└── benchmarks/
    ├── benchmark_inference.py   # Latency benchmarks
    └── benchmark_accuracy.py    # Accuracy vs. existing tools
```

---

## 🔢 The 15 Features

### Category 1: Timing Features (Features 1–5)

| # | Feature | Description | Why It Works |
|---|---------|-------------|--------------|
| 1 | `time_since_last_request` | Seconds since the previous request from this session | Humans pause to read; bots fire continuously |
| 2 | `requests_per_minute_1m` | Count of requests in the last 60 seconds | Sudden ramp-up = bot spinning up |
| 3 | `requests_per_minute_5m` | Count of requests in the last 300 seconds | Sustained high rate = automated traffic |
| 4 | `inter_request_time_cv` | Coefficient of variation of time gaps | Humans: high variation (CV > 0.8). Bots: low (CV < 0.2) |
| 5 | `time_since_session_start` | Age of current session in seconds | Brand-new session + instant API hits = bot probe |

### Category 2: Behavioral Features (Features 6–9)

| # | Feature | Description | Why It Works |
|---|---------|-------------|--------------|
| 6 | `endpoint_count` | Number of unique endpoints visited | Humans browse 3–8; bots hit 50+ |
| 7 | `endpoint_sequence_entropy` | Shannon entropy of endpoint path sequence | Humans wander randomly; bots follow fixed paths |
| 8 | `unique_endpoint_ratio` | Unique endpoints / total requests | Bots repeat the same endpoint; humans explore |
| 9 | `method_mismatch_count` | POST requests to GET-only endpoints (and vice versa) | Bots probe APIs without reading docs |

### Category 3: Header Features (Features 10–12)

| # | Feature | Description | Why It Works |
|---|---------|-------------|--------------|
| 10 | `header_consistency_score` | 0–1 score: do headers match a real browser fingerprint? | Bots often have contradictory headers (curl + Chrome UA) |
| 11 | `has_accept_language` | 1 if Accept-Language header present, 0 if missing | Bots rarely set this; all browsers do |
| 12 | `ua_category` | Encoded: 0=known browser, 1=known bot UA, 2=unknown/empty | Immediate flag for curl, wget, python-requests |

### Category 4: Payload Features (Features 13–14)

| # | Feature | Description | Why It Works |
|---|---------|-------------|--------------|
| 13 | `payload_entropy` | Shannon entropy of request body | Random payloads or empty bodies = bot; typed text = human |
| 14 | `field_fill_speed` | Time from form render to submission (client-side hint) | Fill in 50ms = paste/bot; fill in 3s = human typing |

### Category 5: Context Features (Feature 15)

| # | Feature | Description | Why It Works |
|---|---------|-------------|--------------|
| 15 | `same_endpoint_hits` | Count of repeated hits on the exact same endpoint | Credential stuffing: POST /login × 1000 |

---

## 🧠 Model Architecture

```
Input Layer:    15 neurons (one per feature)
Hidden Layer 1: 16 neurons (ReLU activation)
Hidden Layer 2:  8 neurons (ReLU activation)
Output Layer:    1 neuron  (raw logit → sigmoid → probability)

Total parameters: (15×16 + 16) + (16×8 + 8) + (8×1 + 1)
                = 256 + 136 + 9
                = 401 parameters

Model file size: ~5KB (JSON serialized)
Inference time:  < 0.5ms on CPU
```

```
┌──────┐    ┌──────┐    ┌──────┐    ┌──────┐
│  15  │───▶│  16  │───▶│  8   │───▶│  1   │
│ input│    │hidden│    │hidden│    │output│
│      │    │ ReLU │    │ ReLU │    │ sigm │
└──────┘    └──────┘    └──────┘    └──────┘
                           │
                    micrograd handles
                    forward + backward
                    automatically
```

---

## 📋 Implementation Phases

### Phase 1: Core Engine (Days 1–2)

**Goal**: Working feature extractor + micrograd model + basic detector

- [ ] Set up project structure (`microguard/` package)
- [ ] Implement `features.py` — all 15 feature extractors
- [ ] Implement `session.py` — per-session state tracking with TTL expiry
- [ ] Implement `model.py` — MLP wrapper around micrograd.nn
- [ ] Implement `detector.py` — orchestrates features → model → decision
- [ ] Implement `decision.py` — configurable threshold logic
- [ ] Write `utils.py` — entropy calculation, header parsing, etc.
- [ ] Unit tests for all feature extractors

**Deliverable**: `BotDetector` class that accepts a raw HTTP request dict and returns a score + decision.

---

### Phase 2: Synthetic Training Pipeline (Days 3–4)

**Goal**: Generate training data and train the model

- [ ] Implement `training/synthetic.py` — generate realistic bot traffic patterns:
  - Credential stuffing (uniform timing, same endpoint)
  - Scraping (sequential endpoint traversal)
  - API probing (method mismatches, unknown endpoints)
  - Rate attack (high frequency, constant interval)
  - Sophisticated bot (mimics human with noise)
- [ ] Implement `training/dataset.py` — load synthetic + real labeled data
- [ ] Implement `training/train.py` — training loop with micrograd:
  - Binary cross-entropy loss
  - SGD optimizer
  - Epoch logging + early stopping
  - Model checkpointing (save/load weights)
- [ ] Implement `training/evaluate.py` — precision, recall, F1, confusion matrix
- [ ] Create `data/sample_human_logs.json` and `data/sample_bot_logs.json`
- [ ] Train initial model, verify > 85% accuracy on synthetic data

**Deliverable**: Trained model weights file + training pipeline that can be re-run.

---

### Phase 3: Framework Integration (Days 5–6)

**Goal**: Plug into FastAPI/Flask with zero config

- [ ] Implement `middleware.py` — FastAPI/Starlette middleware class
- [ ] Implement Flask adapter (WSGI-compatible)
- [ ] Create `examples/fastapi_example.py` — full working demo with:
  - `/` — normal page
  - `/api/data` — protected endpoint
  - `/api/login` — login endpoint (credential stuffing target)
  - Built-in bot simulator for testing
- [ ] Create `examples/flask_example.py`
- [ ] Create `examples/standalone_example.py` (no framework needed)
- [ ] Integration tests with `httpx` test client

**Deliverable**: `pip install microguard` and add one line to get bot protection.

---

### Phase 4: Training on Real Data (Days 7–8)

**Goal**: Support training on actual production logs

- [ ] Implement `training/collect.py` — log collector middleware that:
  - Captures all features for every request
  - Stores to local SQLite (no external DB dependency)
  - Auto-labels obvious bots (known bad IPs, known bot UAs)
  - Flags uncertain ones for manual review
- [ ] Implement manual labeling CLI:
  ```
  python -m microguard label --last 24h
  # Shows unlabeled sessions, human labels them
  ```
- [ ] Implement incremental retraining:
  ```
  python -m microguard retrain
  # Retrains model on newly labeled data
  # Merges with existing model (transfer learning)
  ```
- [ ] Implement model versioning:
  ```
  python -m microguard model list
  # v1 (2026-01-01): 87% accuracy, 1000 samples
  # v2 (2026-01-08): 91% accuracy, 2500 samples
  
  python -m microguard model promote v2
  ```

**Deliverable**: Full pipeline: collect → label → train → deploy → monitor.

---

### Phase 5: Dashboard + Monitoring (Days 9–10)

**Goal**: Visualize bot traffic and model performance

- [ ] Implement stats tracking (in-memory + optional SQLite):
  - Total requests, blocked, challenged, allowed
  - Bot score distribution histogram
  - Top blocked IPs/endpoints
  - Detection accuracy over time
- [ ] Implement `/microguard/stats` endpoint (JSON API):
  ```json
  {
    "total_requests": 145230,
    "blocked": 12340,
    "challenged": 5432,
    "allowed": 127458,
    "bot_rate": 0.12,
    "avg_score": 0.23,
    "model_version": "v2",
    "uptime_hours": 168
  }
  ```
- [ ] Implement dashboard (Streamlit or simple HTML):
  - Real-time bot traffic chart
  - Score distribution histogram
  - Top suspicious sessions table
  - Model performance metrics
- [ ] Implement webhook alerts:
  ```python
  detector = BotDetector(
      alert_webhook="https://hooks.slack.com/...",
      alert_threshold=0.9
  )
  ```

**Deliverable**: Visibility into what the model is doing + alerting.

---

### Phase 6: Hardening + Documentation (Days 11–12)

**Goal**: Production-ready release

- [ ] Add configuration via environment variables:
  ```
  MICROGUARD_BLOCK_THRESHOLD=0.9
  MICROGUARD_CHALLENGE_THRESHOLD=0.7
  MICROGUARD_LOG_THRESHOLD=0.3
  MICROGUARD_MODEL_PATH=./model.json
  MICROGUARD_EXCLUDED_PATHS=/health,/metrics
  MICROGUARD_SHIELD_MODE=shadow  # shadow | active
  ```
- [ ] Add bypass for known good traffic (allowlists):
  ```python
  detector = BotDetector(
      trusted_ips=["10.0.0.0/8"],
      trusted_api_keys=["sk-..."]
  )
  ```
- [ ] Write comprehensive README.md with:
  - Quickstart (5 lines to get running)
  - Feature documentation
  - Training guide
  - Configuration reference
  - FAQ / troubleshooting
- [ ] Add `setup.py` for PyPI packaging
- [ ] Add benchmark scripts:
  - Latency: measure inference time at scale
  - Accuracy: compare against known bot datasets
- [ ] Final test suite: target > 90% code coverage

**Deliverable**: `pip install microguard` ready for production use.

---

## 🧪 Testing Strategy

### Unit Tests

```python
# test_features.py
def test_timing_feature_human():
    """Humans have variable inter-request times."""
    times = [0.0, 1.2, 3.5, 0.8, 5.2]
    cv = calc_coefficient_of_variation(times)
    assert cv > 0.5  # High variation = human

def test_timing_feature_bot():
    """Bots have uniform inter-request times."""
    times = [0.0, 0.05, 0.10, 0.15, 0.20]
    cv = calc_coefficient_of_variation(times)
    assert cv < 0.2  # Low variation = bot

def test_payload_entropy_human():
    """Human-typed text has medium entropy."""
    payload = "hello my password is secret123"
    entropy = calc_shannon_entropy(payload)
    assert 3.0 < entropy < 4.5

def test_payload_entropy_bot():
    """Random bot payloads have high entropy."""
    payload = "a8f3k2j9x7m1p4q6w0z"
    entropy = calc_shannon_entropy(payload)
    assert entropy > 4.0
```

### Integration Tests

```python
# test_detector.py
def test_detector_allows_human():
    detector = BotDetector(model_path="test_model.json")
    human_request = create_request(
        timing="variable",
        headers="chrome_browser",
        payload="normal_form_data"
    )
    score = detector.score_request(human_request)
    assert score < 0.3

def test_detector_blocks_bot():
    detector = BotDetector(model_path="test_model.json")
    bot_request = create_request(
        timing="uniform_50ms",
        headers="curl_ua",
        payload="random_bytes"
    )
    score = detector.score_request(bot_request)
    assert score > 0.9

def test_detector_catches_credential_stuffing():
    detector = BotDetector(model_path="test_model.json")
    session = []
    for i in range(100):
        session.append(create_login_request(
            email=f"user{i}@bot.com",
            timing_ms=50  # uniform
        ))
    
    # After 10 requests, score should spike
    for req in session[:10]:
        detector.score_request(req)
    
    final_score = detector.score_request(session[10])
    assert final_score > 0.7  # Should challenge by now
```

### Accuracy Benchmarks

```
Target Metrics:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Precision:            > 90% (don't block real users)
Recall:               > 85% (catch most bots)
F1 Score:             > 87%
False Positive Rate:  < 5% (critical — can't block humans)
Latency (p99):        < 1ms
Latency (p50):        < 0.3ms
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 📊 Performance Targets

```
Metric                    Target         How to Measure
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Inference latency (p50)   < 0.3ms        benchmark_inference.py
Inference latency (p99)   < 1.0ms        benchmark_inference.py
Model size (on disk)      < 5KB          ls -la model.json
Memory footprint          < 10KB         tracemalloc
Throughput                > 10K req/s    Locust load test
Accuracy (human recall)   > 95%          Don't block humans
Accuracy (bot recall)     > 85%          Catch most bots
False positive rate       < 3%           Critical metric
Cold start time           < 50ms         Time to first prediction
Training time (1K samples)< 5 seconds    On CPU, no GPU needed
```

---

## 🗓️ Timeline Summary

```
Week 1:
  Mon-Tue  → Phase 1: Core engine (features + model + detector)
  Wed-Thu  → Phase 2: Synthetic training pipeline
  Fri      → Phase 3: FastAPI middleware integration

Week 2:
  Mon-Tue  → Phase 4: Real data collection + labeling CLI
  Wed-Thu  → Phase 5: Dashboard + monitoring
  Fri      → Phase 6: Hardening + docs + PyPI packaging

Week 3 (Buffer):
  Mon-Wed  → Testing, bug fixes, community feedback
  Thu-Fri  → Benchmarking, blog post, GitHub release
```

---

## 🚀 Release Plan

### v0.1.0 — MVP (Week 2)
- Core detector + FastAPI middleware
- Synthetic training pipeline
- Basic accuracy > 85%
- README with quickstart

### v0.2.0 — Real Data (Week 3)
- Production log collection
- Manual labeling CLI
- Incremental retraining
- Accuracy > 90%

### v0.3.0 — Dashboard (Week 4)
- Stats API endpoint
- Streamlit dashboard
- Slack/webhook alerts
- Model versioning

### v1.0.0 — Production Ready (Week 6)
- PyPI package
- Full documentation
- Flask/Django adapters
- Benchmark results published
- Blog post + GitHub launch

---

## 💡 Future Ideas (Post v1.0)

| Feature | Description |
|---------|-------------|
| **Auto-labeling** | Use known bot signatures to auto-label 80% of traffic |
| **Federated learning** | Multiple deployments share model improvements without sharing data |
| **Browser fingerprinting** | Add JS-based fingerprint collection for even smarter detection |
| **IP reputation feed** | Community-shared database of known bot IPs |
| **Adversarial training** | Generate adversarial examples to harden the model |
| **Multi-model ensemble** | Run 3 small models and vote (higher accuracy) |
| **WebAssembly port** | Compile to WASM for browser-side detection |
| **Go/Rust rewrite** | Native performance version for high-throughput APIs |
| **WordPress/Express plugins** | One-click install for popular frameworks |

---

## 📚 References

- [micrograd](https://github.com/karpathy/micrograd) — Core autograd engine
- [Imperva Bad Bot Report 2025](https://www.imperva.com/resources/reports/bad-bot-report/) — Bot traffic statistics
- [ISCX Bot Detection Dataset](https://www.unb.ca/cic/datasets/bot-2014.html) — Training data
- [CICIDS 2017](https://www.unb.ca/cic/datasets/ids-2017.html) — Intrusion detection dataset

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAN | 6 issues, 0 critical gaps |

**VERDICT:** ENG CLEARED — ready to implement

**Architecture decisions locked:**
1. Ship pre-trained model on ISCX dataset (mapped features)
2. Nginx combined + JSON log formats
3. Pad missing features to 0 (train with feature dropout)
4. Stream processing for large files (constant memory)
5. Terminal table + `--json` flag output
6. Accuracy-first performance priority

**NOT in scope:**
- FastAPI middleware (v2)
- Dashboard/monitoring (v2)
- Training pipeline for real data (v2)
- Flask/Django adapters (v2)
- Model versioning CLI (v2)
- Webhook alerts (v2)
- HTML report output (v2)

**What already exists:**
- micrograd (karpathy/micrograd) — core autograd engine, ready to use
- plan.md — full platform plan (deferred to v2)
- Design doc — APPROVED, CLI-First approach

NO UNRESOLVED DECISIONS
