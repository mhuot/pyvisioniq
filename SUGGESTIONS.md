# Project Review — Improvement Suggestions

*Review date: 2026-03-15*

This document captures actionable suggestions for improving the PyVisionIQ codebase, organized by priority.

---

## High Priority

### 1. Fix Dead Code in API Client (`src/api/client.py:733-735`)

Lines 733-735 contain **unreachable duplicate code** after a `raise` statement:

```python
raise last_error
# Non-rate-limit error, don't retry          <-- unreachable
logger.error(f"Non-retryable error: {last_error.message}")  # <-- unreachable
raise last_error                             # <-- unreachable
```

**Fix**: Delete lines 733-735.

---

### 2. Replace Bare `except` Clause (`src/web/app.py:131`)

A bare `except:` silently catches everything including `KeyboardInterrupt` and `SystemExit`:

```python
try:
    ...
    freshness_msg = f" (vehicle data from {age_minutes} minutes ago)"
except:  # ← bare except
    freshness_msg = ""
```

**Fix**: Change to `except (KeyError, TypeError, ValueError):` or at minimum `except Exception:`.

---

### 3. Add Docker Health Check

The container has no health check, so Docker/orchestrators can't detect if the app is unresponsive.

**Fix** — add to `docker-compose.yml`:

```yaml
healthcheck:
  test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-5000}/api/collection-status')"]
  interval: 60s
  timeout: 10s
  retries: 3
  start_period: 30s
```

---

### 4. Supervise Background Data Collector in Docker

The data collector runs via `nohup` with no supervision — if it crashes, it stays down silently.

**Options** (pick one):

- **Simplest**: Split into two services in `docker-compose.yml` — one for gunicorn, one for the collector. Docker handles restarts for each independently.
- **Alternative**: Use a lightweight process manager like `supervisord` or `tini` with a wrapper script that restarts the collector on failure.

Splitting into two services is the Docker-native approach:

```yaml
services:
  web:
    build: .
    command: gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 4 --threads 2 --timeout 120 src.web.app:app
    # ... volumes, env, networks ...

  collector:
    build: .
    command: python data_collector.py
    restart: unless-stopped
    # ... volumes, env (no network/port needed) ...
```

---

### 5. Increase Test Coverage (Currently ~40% minimum)

Major untested areas:

| Area | Impact | Suggestion |
|------|--------|------------|
| Flask routes (`src/web/app.py`) | High | Add tests using Flask test client for key API endpoints |
| Storage factory (`src/storage/factory.py`) | Medium | Test that correct backend is returned for each env var value |
| Cache routes (`src/web/cache_routes.py`) | Medium | Test path traversal protection and file operations |
| Data collector (`data_collector.py`) | High | Test scheduling logic and error recovery |

A realistic near-term goal: raise `--cov-fail-under` to **60%** by adding Flask route tests and storage factory tests.

---

## Medium Priority

### 6. Pin Python 3.12+ and Update Dependencies

Current stack targets Python 3.11 (released Oct 2022). Python 3.12 and 3.13 bring meaningful performance improvements (10-15% faster startup, better error messages).

**Suggested dependency updates** (check changelogs for breaking changes):

| Package | Current | Latest (approx.) | Notes |
|---------|---------|-------------------|-------|
| Flask | 3.0.0 | 3.1.x | Minor improvements |
| pandas | 2.1.4 | 2.2.x | Performance improvements, Copy-on-Write default |
| plotly | 5.18.0 | 5.24.x+ | Bug fixes |
| gunicorn | 21.2.0 | 23.x | Python 3.12+ support improvements |
| black | 24.10.0 | 25.x | Formatting updates |
| pylint | 3.3.3 | 3.4.x+ | New checks |

**Action**: Update `Dockerfile`, `pyproject.toml`, and `ci.yml` to target Python 3.12. Run full test suite after updating dependencies.

---

### 7. Add Type Hints

The codebase has minimal type annotations (only in `src/utils/debug.py`). Adding type hints to public interfaces improves maintainability and enables `mypy` static analysis.

**Phased approach**:
1. Start with `src/storage/base.py` (abstract interface — defines the contract)
2. Then `src/api/client.py` (most complex module)
3. Add `mypy --strict` to CI as an optional check initially

---

### 8. Consolidate Magic Numbers into Configuration

Scattered constants should be centralized:

```python
# src/config.py (new file)
BATTERY_CAPACITY_KWH = 77.4
DEFAULT_API_DAILY_LIMIT = 30
CHARGING_GAP_MULTIPLIER = 1.5
MAX_BACKOFF_MULTIPLIER = 4
CACHE_RETENTION_HOURS = 48
```

This makes values discoverable, testable, and configurable without hunting through business logic.

---

### 9. Strengthen Path Traversal Protection in Cache Routes

Current check in `cache_routes.py` only validates `..` and `/` in filenames. This can be bypassed with URL-encoded characters.

**Fix**: Use `pathlib` to resolve and verify:

```python
from pathlib import Path

def safe_cache_path(filename: str, cache_dir: str) -> Path:
    """Resolve filename within cache_dir, rejecting traversal attempts."""
    resolved = (Path(cache_dir) / filename).resolve()
    if not resolved.is_relative_to(Path(cache_dir).resolve()):
        raise ValueError("Path traversal detected")
    return resolved
```

---

### 10. Add `EXPOSE` Back to Dockerfile

The Dockerfile comment says EXPOSE was removed because the port is dynamic. However, `EXPOSE` is documentation — it doesn't bind ports. Adding it with a default improves clarity:

```dockerfile
EXPOSE ${PORT:-5000}
```

Or simply `EXPOSE 5000` since docker-compose handles the actual mapping.

---

## Low Priority (Nice-to-Have)

### 11. Add Dependabot or Renovate for Dependency Updates

Automate dependency update PRs to catch security patches early. Add `.github/dependabot.yml`:

```yaml
version: 2
updates:
  - package-ecosystem: pip
    directory: "/"
    schedule:
      interval: weekly
    open-pull-requests-limit: 5
  - package-ecosystem: github-actions
    directory: "/"
    schedule:
      interval: monthly
```

---

### 12. Add Multi-Stage Docker Build

The current image includes `gcc` and build artifacts. A multi-stage build reduces the final image size:

```dockerfile
FROM python:3.12-slim AS builder
RUN apt-get update && apt-get install -y gcc
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
COPY --from=builder /install /usr/local
COPY . /app
WORKDIR /app
RUN mkdir -p data logs cache
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 4 --threads 2 --timeout 120 src.web.app:app"]
```

This removes `gcc` from the final image, shrinking it by ~100MB.

---

### 13. Replace `sys.path.append` Hack in `app.py`

Line 15 of `src/web/app.py` uses `sys.path.append` to find the project root. This is fragile and unnecessary when running as a module (`python -m src.web.app`).

**Fix**: Remove the `sys.path.append` line and ensure the app is always started with `python -m src.web.app` or via gunicorn's module syntax. If needed, add a `pyproject.toml` `[project]` section to make the package properly installable.

---

### 14. Add Rate Limit Headers to API Responses

The `/api/collection-status` endpoint already returns rate limit info in the body. Exposing it via standard headers (`X-RateLimit-Limit`, `X-RateLimit-Remaining`) would help API consumers and monitoring tools.

---

### 15. Consider SQLite as a Middle Ground Between CSV and Oracle

For users who don't need Oracle but want better performance than CSV files (especially with growing datasets), SQLite would be a lightweight alternative that supports proper queries, indexing, and concurrent reads. It could be added as another storage backend via the existing factory pattern.

---

## Summary

| Priority | Count | Key Theme |
|----------|-------|-----------|
| **High** | 5 | Bugs, reliability, test coverage |
| **Medium** | 5 | Modernization, safety, maintainability |
| **Low** | 5 | Polish, performance, developer experience |
