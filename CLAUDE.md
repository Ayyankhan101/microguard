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
