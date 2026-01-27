# Repository Guidelines

This repository is currently documentation-only. The `README.md` defines the target architecture, planned directories, and configuration patterns for a multi-agent quantitative trading system. Use this guide to keep contributions aligned until source code lands.

## Project Structure & Module Organization

- Current state: only `README.md` exists in the repo root.
- Planned layout (from the README): `orchestrator/`, `agents/`, `tools/`, `fusion/`, `risk/`, `execution/`, `validation/`, `deepseek/`, `data/`, `config/`, `tests/`, `logs/`, and `reports/`.
- Keep modules cohesive (e.g., MT5 data access in `tools/mt5_tools.py`, fusion logic in `fusion/`, risk limits in `risk/`).

## Build, Test, and Development Commands

No build, test, or run commands are committed yet. When code is added, document canonical commands here and in `README.md`. Examples that align with the planned structure:

```bash
python orchestrator/main.py    # run the orchestrator (once it exists)
pytest                         # run tests (once a test runner is chosen)
```

## Coding Style & Naming Conventions

- Default to standard Python conventions: 4-space indentation, `snake_case` for functions/variables, and `CapWords` for classes.
- Keep module and file names lowercase with underscores (e.g., `risk_config.py`, `tool_registry.py`).
- Use lower_snake_case keys in YAML/JSON configs, matching the patterns shown in the README examples (e.g., `risk_config.yaml`).

## Testing Guidelines

- No testing framework is configured yet.
- When tests are introduced, place them under `tests/` and name files `test_*.py`, mirroring module names when possible.
- Document how to run tests and any coverage targets once the runner is chosen.

## Commit & Pull Request Guidelines

- This directory is not a git repository yet, so there is no commit history to summarize.
- If you initialize git, use concise, imperative commit messages (e.g., "Add MT5 tool registry") and include issue links in PR descriptions.
- PRs should state the problem, the approach, and how you tested (or why tests are not yet available).

## Security & Configuration Tips

- Keep API keys in environment variables; the README references `DEEPSEEK_API_KEY`.
- Treat `config/` and `logs/` as environment-specific; avoid committing secrets or large generated artifacts.
