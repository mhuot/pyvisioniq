# Repository Guidelines

## Project Structure & Module Organization
- Application code lives in `src/`: `api` handles Hyundai/Kia Connect calls and caching patches, `storage` manages CSV persistence, `web` exposes the Flask UI, templates, and static assets.
- `data_collector.py` runs the scheduled polling loop; keep long-running jobs out of the web layer.
- Persisted artifacts live in `data/` (CSV snapshots), `cache/` (raw API responses), and `logs/`. Never commit personal vehicle data.
- Support scripts stay in `tools/`, and architectural references in `docs/`. Place new utilities beside existing scripts to keep responsibilities clear.

## Build, Test, and Development Commands
- `python3.11 -m venv venv && source venv/bin/activate` — create a local virtualenv.
- `pip install -r requirements.txt` — install Flask, pandas, and API bindings.
- `python data_collector.py` — start the timed collector loop while validating ingestion changes.
- `python -m src.web.app` — launch the Flask dashboard on `http://localhost:5000` using `.env`.
- `docker compose up --build` — rebuild and run the containerized stack.

## Coding Style & Naming Conventions
- Follow PEP 8: four-space indentation, ~100-character soft limit, and docstrings on public helpers.
- Use `snake_case` for functions and modules, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants.
- Prefer `pathlib.Path` for filesystem work and keep data transforms in pure helpers under `src/utils`.
- Run `python -m black src tests tools` before opening a PR, and resolve lint warnings surfaced by your editor.

## Testing Guidelines
- Automated tests belong under `tests/` with filenames matching `test_*.py`; mirror the module names they cover.
- Install `pytest` locally (`pip install pytest`) and run `python -m pytest` from the project root.
- For data-dependent scenarios, rely on temporary directories or fixtures rather than modifying `data/` and `cache/`. Use `test_charging_session.py` as a manual sanity check.

## Commit & Pull Request Guidelines
- Commit messages follow an imperative, present-tense summary (e.g., "Add cache status tracking to battery data") with optional detail lines.
- Scope each commit to one logical change and include migration notes when touching CSV schemas or API payloads.
- Pull requests should describe behavior changes, list test results, and link to relevant issues. Include screenshots or GIFs when altering the dashboard UI.
- Document new environment variables in `README.md` and update architecture notes when application flows change.
