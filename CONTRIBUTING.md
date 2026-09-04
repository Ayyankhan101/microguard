# Contributing to Microguard

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

```bash
# Clone the repository
git clone https://github.com/yourusername/microguard.git
cd microguard

# Install in development mode (installs micrograd + editable package)
pip install -e .

# Install test dependencies
pip install pytest

# Run the test suite
python -m pytest tests/ -v
```

## Project Structure

```
microguard/
├── cli.py          # CLI entry point (scan, probe, watch, info)
├── parser.py       # Nginx + JSON log parsers
├── features.py     # 19 feature extractors
├── labeler.py      # 24 heuristic bot/human rules
├── model.py        # micrograd MLP wrapper (85 params)
├── scanner.py      # HTTP scanner for live probing
├── watch.py        # Continuous log monitoring
├── report.py       # Terminal + JSON + HTML output
└── training/       # Model training pipeline
```

## Making Changes

1. **Create a branch** from `main`:
   ```bash
   git checkout -b feature/my-change
   ```

2. **Make your changes** and add tests for new functionality.

3. **Run the full test suite** before committing:
   ```bash
   python -m pytest tests/ -v
   ```

4. **Commit** with a clear message:
   ```bash
   git commit -m "Add feature X to the labeler"
   ```

5. **Push** and open a Pull Request.

## Testing

We have 142 tests covering all modules:

```bash
# Run all tests
python -m pytest tests/

# Run specific module tests
python -m pytest tests/test_labeler.py -v

# Run new tests only
python -m pytest tests/test_labeler_rules.py tests/test_report.py tests/test_watch.py -v
```

When adding new features:
- Add unit tests in the corresponding `test_*.py` file
- Create a new test file if the feature doesn't fit existing tests
- Aim for tests that run offline (no network calls) — mark network tests with `@pytest.mark.skip(reason="Requires network access")`

## Code Style

- Keep it simple — this is a small, focused tool
- No external dependencies beyond micrograd
- Use `typing` for function signatures
- Docstrings on public functions
- ANSI colors via `_colorize()` in `report.py`

## Reporting Issues

Open a GitHub issue with:
- What you expected
- What actually happened
- Steps to reproduce
- Python version and OS

## Pull Request Process

1. Ensure all tests pass
2. Update README.md if adding user-facing features
3. Add a brief description of what changed and why
4. Reference any related issues

## Architecture Decisions

Before making large changes, consider:
- The model stays lightweight (85 params, ~1.8KB) — no large dependencies
- Heuristic rules do the heavy lifting — the ML model is supplementary
- Zero external dependencies beyond micrograd
- Streaming processing for large log files

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
