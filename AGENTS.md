# Repository Guidelines

## Project Structure & Module Organization

`main.py` is the AstrBot plugin entry point and contains command, event, and LLM-tool registration. Protocol integrations live in `backends/`: RCON, WebSocket, and MCSManager HTTP. Stateful coordination belongs in `managers/`, while reusable command operations are in `tools/` and message formatting is in `utils/`. Configuration and release metadata are defined by `_conf_schema.json`, `metadata.yaml`, and `requirements.txt`.

Tests are under `tests/`. CI-only smoke helpers are under `scripts/`, and workflows are in `.github/workflows/`. Do not commit AstrBot runtime data, credentials, server exports, logs, caches, or local planning files.

## Build, Test, and Development Commands

- `python -m pip install ruff -r requirements.txt` installs runtime and quality dependencies.
- `ruff check .` runs static lint checks.
- `ruff format --check .` verifies formatting; use `ruff format .` to apply it.
- `python -m unittest discover -s tests -v` runs unit and simulated integration tests.
- `python -m compileall -q .` checks Python syntax across the repository.

GitHub's `Quality` workflow runs lint, formatting, and tests on Python 3.10 and 3.12. The manually dispatched `Full Integration` workflow loads the plugin against supported AstrBot versions and can start a disposable Minecraft server for real RCON testing.

## Coding Style & Naming Conventions

Use four-space indentation and Ruff's default formatting. Prefer `snake_case` for functions, variables, command helpers, and server IDs; use `PascalCase` for classes. Keep async network operations non-blocking and return clear user-facing errors. Server management must resolve targets in this order: explicit `server_name`, the user's `mc use` selection, then the configured default. QQ group bindings are only for optional chat forwarding.

## Testing Guidelines

Use standard-library `unittest`. Name files `test_*.py` and test methods `test_*`. Add regression coverage for routing, permissions, persistence, and command safety. Tests must never contact real user servers or require live credentials.

## Commit & Pull Request Guidelines

Follow the existing concise, imperative style, such as `Add multi-server profiles` or `Fix RCON smoke import path`. Keep commits focused and stage only intended files. For pull requests, explain behavior changes, list validation performed, link relevant issues, and include screenshots only for configuration UI changes. Never place secrets or production endpoints in commits, logs, fixtures, or release archives.

Use Semantic Versioning for public releases. Do not publish every internal milestone: collect related work into one release, use patch versions for compatible fixes, minor versions for deliberate backward-compatible features, and major versions only for breaking changes.
