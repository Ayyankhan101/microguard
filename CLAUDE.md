# Microguard

Bot Traffic Audit Tool powered by micrograd. Detects malicious bot traffic in API logs and live endpoints.

## Project Structure

- `microguard/` — Python package
  - `cli.py` — CLI entry point (scan, probe, info)
  - `parser.py` — Nginx + JSON log parsers
  - `features.py` — 19 feature extractors
  - `labeler.py` — Heuristic bot/human labeling
  - `model.py` — micrograd MLP wrapper
  - `scanner.py` — HTTP scanner for live probing
  - `report.py` — Terminal + JSON + HTML output
  - `training/` — Training pipeline
- `tests/` — pytest tests (69 passing)
- `data/` — Model weights, sample logs

## Commands

```bash
python -m pytest tests/              # Run tests
microguard scan <logfile>            # Scan log file
microguard probe <url>               # Probe live URL
```

## Health Stack

- typecheck: mypy microguard
- lint: ruff check .
- test: pytest
- coverage: pytest --cov=microguard --cov-report=term-missing (baseline: 71%; no hard gate yet — see CHANGELOG)
- deadcode: vulture microguard microguard/vulture_whitelist.py
- shell: skip (no shell scripts)

## Test suite conventions

- Shared `LogEntry`/`Session`/temp-log-file builders live in `tests/conftest.py`
  (`make_entry`, `make_session`, `nginx_log_file` fixtures) — use them
  instead of writing a new local `_make_entry`-style helper.
  `tests/test_groundtruth.py`'s `_entry()` is a deliberate exception (it
  auto-synthesizes a realistic `raw_line`, which the shared fixture
  doesn't need for other tests).
- `cli.py` and `watch.py` are tested end-to-end (`tests/test_cli.py`,
  `TestWatchLogfile` in `tests/test_watch.py`), not just their helpers —
  keep new CLI/watch behavior covered there, not only at the unit level.
- `watch_logfile()` takes a test-only `_max_iterations` param to terminate
  its otherwise-infinite loop; don't use it from product code.

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
- Author a backlog-ready spec/issue → invoke /spec
