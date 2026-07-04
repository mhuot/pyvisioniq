# PyVisionic

A Python web application for monitoring and visualizing Hyundai/Kia Connect API data, with a focus on battery performance, trip analysis, energy efficiency, and charging behavior. Stores data in CSV files and serves an interactive dashboard.

## Table of Contents

- [Features](#features)
- [Architecture Overview](#architecture-overview)
- [Quick Start](#quick-start)
- [Configuration Reference](#configuration-reference)
- [Web Interface](#web-interface)
- [API Reference](#api-reference)
- [Data Storage](#data-storage)
- [Data Management Tools](#data-management-tools)
- [Development](#development)
- [Project Structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [Roadmap / Incomplete Features](#roadmap--incomplete-features)
- [License](#license)

## Features

### Core Functionality
- **Hyundai/Kia Connect integration** via `hyundai-kia-connect-api`, supporting Hyundai, Kia, and Genesis vehicles across all regions (USA, Canada, Europe, China, Australia)
- **Background data collector** that respects a configurable daily API limit (default 30 calls/day), evenly distributes calls throughout the day, and backs off automatically when the upstream rate-limits
- **Two-tier caching**: a short *validity* window (≈45.6 min at 30 calls/day) controls when a new API call is made, and a longer *retention* window (default 48 h) controls how long historical responses are kept on disk
- **CSV storage** with automatic deduplication, easy to back up and inspect
- **Charging session reconstruction** with automatic gap detection and energy/power calculations
- **Weather correlation** via Open-Meteo API, with the vehicle's own temperature sensor as a fallback source
- **Docker-first deployment** via `docker compose`, including reverse-proxy / Let's Encrypt environment variables

### Web Dashboard
- Battery level history with temperature overlay
- Energy consumption breakdown (drivetrain, climate, accessories)
- Trip efficiency trends over time, in mi/kWh
- Per-trip detail view with energy breakdown and speed/regeneration data
- Interactive maps (Leaflet.js) for trip endpoints and current location
- Toggle between metric and imperial units
- Efficiency statistics for last day, week, month, year, and all-time
- Temperature-efficiency correlation chart (5 °C bins)
- Charging temperature-impact chart (charging power by ambient temperature)

### Cache Management UI (`/cache`)
- View, inspect, and delete cached API responses
- Inline JSON viewer with current/valid flags
- Clear cache files older than the retention window
- Manual force-refresh button (respects the daily API limit)

### Debug UI (`/debug`)
- Lists recent error files from the `debug/` directory
- Tails the last 100 lines from `data_collector.log`, `debug.log`, and `app.log`
- Inspects data types and validates stored records

## Architecture Overview

```
Hyundai/Kia API ─► CachedVehicleClient ─► Cache files ─► CSVStorage ─► Flask API ─► Dashboard
                        │                       │              │
                  Rate limiting          Deduplication      CSV files
                  SSL workaround         Weather merge      Charging sessions
                  Error classification
```

Two processes run side-by-side:

1. **`data_collector.py`** — long-running scheduler that calls the Hyundai/Kia API at evenly-spaced intervals derived from `API_DAILY_LIMIT`, then writes the parsed result to CSV.
2. **`src.web.app`** — Flask app (served by Gunicorn in production) that reads from the same CSV files and renders the dashboard plus a JSON API.

The two processes coordinate through the shared `data/` and `cache/` directories. For a deeper architecture write-up, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Quick Start

### Docker (recommended)

1. Clone the repo and create a `.env` from the template:
   ```bash
   git clone https://github.com/yourusername/pyvisionic.git
   cd pyvisionic
   cp .env.example .env
   $EDITOR .env   # fill in BlueLink credentials at minimum
   ```

2. Start the stack:
   ```bash
   docker compose up -d
   ```
   The container starts the data collector in the background and serves the dashboard via Gunicorn on the port specified by `PORT` (default 5000).

3. View logs:
   ```bash
   docker compose logs -f --tail=100
   ```

4. After code changes, rebuild before restarting (the Dockerfile **copies** the source — it is not bind-mounted):
   ```bash
   docker compose down && docker compose build && docker compose up -d
   ```

The container also exposes `VIRTUAL_HOST`, `VIRTUAL_PORT`, `LETSENCRYPT_HOST`, and `LETSENCRYPT_EMAIL` and joins an external `nginx-proxy-network`, so it can sit behind `nginx-proxy` + `acme-companion` for automatic TLS.

### Manual / venv

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in credentials

# In one terminal — background data collector
python data_collector.py

# In another — Flask dev server
python -m src.web.app
```

Or run both at once with the convenience script:

```bash
python run.py
```

For a single one-off collection (useful for testing credentials):

```bash
python data_collector.py --once
```

For production use, run the web layer under Gunicorn:

```bash
gunicorn --bind 0.0.0.0:5000 --workers 4 --threads 2 --timeout 120 src.web.app:app
```

## Configuration Reference

All settings are read from environment variables (typically via `.env`). See [`.env.example`](.env.example) for a complete template.

### Hyundai/Kia Connect (required)

| Variable | Default | Description |
|---|---|---|
| `BLUELINKUSER` | — | Account email |
| `BLUELINKPASS` | — | Account password |
| `BLUELINKPIN` | — | Account PIN (optional, needed only for remote commands) |
| `BLUELINKVID` | — | Vehicle ID. Leave blank on first run to discover available vehicles in the log. |
| `BLUELINKREGION` | `3` | `1`=Europe, `2`=Canada, `3`=USA, `4`=China, `5`=Australia |
| `BLUELINKBRAND` | `2` | `1`=Kia, `2`=Hyundai, `3`=Genesis |

### API & Caching

| Variable | Default | Description |
|---|---|---|
| `API_DAILY_LIMIT` | `30` | Max API calls per day. Drives the collection interval (`1440 / N` minutes) and the cache-validity window. |
| `API_CACHE_ENABLED` | `true` | Enable response caching |
| `CACHE_DURATION_HOURS` | `48` | How long historical cache files are kept on disk |
| `DISABLE_SSL_VERIFY` | `false` | If `true`, applies an SSL workaround (`src/api/ssl_patch.py`) for certificate issues |

### Data & Storage

| Variable | Default | Description |
|---|---|---|
| `BATTERY_CAPACITY_KWH` | `77.4` | Used for energy / kWh calculations |
| `CHARGING_SESSION_GAP_MULTIPLIER` | `1.5` | Multiplier on the collection interval used to decide when a charging session is considered ended |
| `WEATHER_SOURCE` | `meteo` | `meteo` (Open-Meteo API) or `vehicle` (use the car's own temperature sensor) |

### Web Server

| Variable | Default | Description |
|---|---|---|
| `PORT` | `5000` | HTTP port |
| `FLASK_ENV` | `development` | Set to `production` for deployment |
| `SECRET_KEY` | `dev-secret-key` | Flask session signing key — **set a strong value in production** |
| `DEBUG_MODE` | `false` | Enables verbose logging and the `/api/debug/*` endpoints |
| `TZ` | `America/Chicago` | Timezone for the collector schedule and timestamps |

### Reverse Proxy (Docker)

| Variable | Default | Description |
|---|---|---|
| `VIRTUAL_HOST` | `pyvisionic.local` | Hostname routed by `nginx-proxy` |
| `VIRTUAL_PORT` | matches `PORT` | Internal port |
| `LETSENCRYPT_HOST` | — | Domain for the ACME companion |
| `LETSENCRYPT_EMAIL` | — | Renewal-notification email |

## Web Interface

| Route | Purpose |
|---|---|
| `/` | Main dashboard — current status, charts, trips, charging history, map |
| `/cache/` | Cache management UI |
| `/debug` | Debug UI (always available; rich data when `DEBUG_MODE=true`) |
| `/favicon.ico` | Favicon |

## API Reference

All endpoints return JSON. Date parameters accept ISO-8601 (`YYYY-MM-DD`).

### Core data

| Method | Path | Description |
|---|---|---|
| GET | `/api/current-status` | Latest battery level, charging state, range, odometer, temperature |
| GET | `/api/collection-status` | Calls used today and next scheduled collection time |
| GET | `/api/trips` | Trip history. Query params: `page`, `per_page`, `start_date`, `end_date`, `hours`, `min_distance`, `max_distance` |
| GET | `/api/trip/<trip_id>` | Single trip detail with energy breakdown. `trip_id` is a base64 token derived from date + distance + odometer. |
| GET | `/api/battery/history` | Battery-level history with temperature. Query param: `hours` |
| GET | `/api/locations` | GPS points for trip endpoints. Query params: `hours`, `start_date`, `end_date` |
| GET | `/api/charging-sessions` | Charging session history. Query params: `hours`, `start_date`, `end_date` |
| GET | `/api/efficiency-stats` | Aggregate efficiency (mi/kWh) for last day/week/month/year/all-time |
| GET | `/api/temperature-efficiency` | Trip efficiency bucketed by ambient temperature (5 °C bins) |
| GET | `/api/charging-temperature-impact` | Charging power/speed bucketed by ambient temperature |

### Cache & refresh

| Method | Path | Description |
|---|---|---|
| GET | `/api/refresh` | Force a cache refresh (still respects the daily API limit) |
| GET | `/api/clear-cache` | Remove all non-history cache files |
| GET | `/api/debug` | Effective API configuration and cache status |
| GET | `/cache/api/files` | List cache files with sizes, ages, and summary stats |
| GET | `/cache/api/file/<filename>` | Return the contents of a single cache file |
| DELETE | `/cache/api/delete/<filename>` | Delete a cache file (refuses to delete the current valid cache) |
| POST | `/cache/api/clear-old` | Delete cache files older than `CACHE_DURATION_HOURS` |
| POST | `/cache/api/force-update` | Force-refresh the cache via the cache UI |

### Debug (always registered; `/debug` UI uses these)

| Method | Path | Description |
|---|---|---|
| GET | `/api/debug/errors` | Last 20 error files from `debug/` |
| GET | `/api/debug/error/<error_id>` | Full error payload |
| GET | `/api/debug/logs` | Last 100 lines from `data_collector.log`, `debug.log`, `app.log` |
| GET | `/api/debug/data-types` | Type inspection across stored data (battery, trips, sessions) |
| GET | `/api/debug/validate` | Validate stored records; returns up to 50 issues |

## Data Storage

### CSV files (`data/`)
- `battery_status.csv` — battery level, charging state, temperature
- `trips.csv` — trip records with energy-consumption breakdown
- `locations.csv` — GPS endpoints for each trip
- `charging_sessions.csv` — charging sessions with energy added and average power
- `api_call_history.json` — sliding window of API call timestamps used for rate-limit accounting

### Cache (`cache/`)
- Timestamped JSON files containing raw API responses
- A *current* file represents the most recent valid response (per the validity window)
- *History* files are older snapshots kept until `CACHE_DURATION_HOURS` expires
- *Error* files capture failed responses for the `/debug` UI and `analyze_errors.py`

### Logs (`logs/`)
- `collector.log` — data-collector output (Docker)
- App and debug logs as configured

## Data Storage

Data is stored as flat CSV files in `data/`, with deduplication handled via pandas
(`src/storage/csv_store.py`, `CSVStorage`). The files are easy to back up, inspect, and
feed into any external analysis or BI tool. `StorageBackend` (`src/storage/base.py`)
defines the interface, so an alternative backend could be added without touching callers.

## Data Management Tools

All tools live in `tools/` and require an activated venv. One-off historical scripts are kept in `tools/archive/`.

| Tool | Purpose | Notable flags |
|---|---|---|
| `deduplicate_trips_v2.py` | Remove duplicate trip rows from `trips.csv` | — |
| `rebuild_charging_sessions.py` | Merge fragmented charging sessions using the dynamic gap threshold | — |
| `rebuild_sessions_from_battery.py` | Rebuild `charging_sessions.csv` from the battery history | `--preview` |
| `reprocess_cache_complete.py` | Rebuild every CSV from the cache directory (disaster recovery) | — |
| `recompute_is_cached.py` | Re-evaluate the `is_cached` flag across historical rows | `--write-cache` (default is dry-run) |
| `analyze_errors.py` | Summarize errors in `cache/error_*.json` | — |

## Development

### Setup
```bash
make install-dev        # creates venv and installs requirements-dev.txt
source venv/bin/activate
pre-commit install      # optional: enable git hooks
```

### Code quality
The project enforces black formatting, pylint ≥ 8.0, and bandit security scanning.

```bash
make format          # black src/ tools/ data_collector.py tests/
make format-check    # check without modifying
make lint            # pylint
make security        # bandit -r ... -c pyproject.toml
make test            # pytest tests/
make coverage        # pytest with --cov=src
make ci              # format-check + lint + security + test
make clean           # remove .pytest_cache htmlcov .coverage __pycache__
```

### Pre-commit hooks
`.pre-commit-config.yaml` runs black, bandit, trailing-whitespace / end-of-file-fixer, YAML syntax check, large-file check (500 KB), and merge-conflict check on every commit.

### Continuous integration
`.github/workflows/ci.yml` runs three jobs on push and pull request:
- **lint** — `black --check` and `pylint`
- **security** — `bandit -r ... -c pyproject.toml`
- **test** — `pytest --cov=src --cov-report=term-missing --cov-fail-under=40` with the HTML coverage report uploaded as a 14-day artifact

Coverage configuration in `pyproject.toml` deliberately omits some untested modules (web routes, auth modules, SSL patch).

### Debug mode
Set `DEBUG_MODE=true` in `.env` to enable:
- Verbose logging in the console and log files
- Richer error-tracking files in `debug/`
- Detailed error messages in the web UI

## Project Structure

```
pyvisionic/
├── src/
│   ├── api/
│   │   ├── client.py          # CachedVehicleClient — API, caching, rate limiting
│   │   └── ssl_patch.py       # Optional SSL workaround
│   ├── storage/
│   │   ├── base.py            # Abstract StorageBackend interface
│   │   └── csv_store.py       # CSVStorage
│   ├── utils/
│   │   ├── weather.py         # Open-Meteo client
│   │   └── debug.py           # DebugLogger, DataValidator
│   └── web/
│       ├── app.py             # Flask app + core API routes
│       ├── cache_routes.py    # /cache blueprint
│       ├── debug_routes.py    # /debug + /api/debug/* blueprint
│       ├── auth.py            # Entra ID auth helpers (see Roadmap)
│       ├── auth_routes.py     # Auth blueprint (see Roadmap)
│       ├── templates/         # index.html, cache.html, login.html
│       └── static/            # CSS, JS, images
├── tools/                     # Active data-management scripts
│   └── archive/               # One-off migration/fix scripts (already applied)
├── tests/                     # pytest suite
├── docs/                      # Architecture docs
├── data/                      # CSV data files
├── cache/                     # Cached API responses
├── debug/                     # Error tracking files (created on demand)
├── logs/                      # Application logs
├── sessions/                  # Flask-Session storage (not in git)
├── data_collector.py          # Background collector (CLI: --once)
├── run.py                     # Convenience launcher: collector + web server
├── Dockerfile                 # Python 3.11 slim base
├── docker-compose.yml         # Single-service stack with nginx-proxy network
├── Makefile                   # install / format / lint / test / ci targets
├── pyproject.toml             # black, pylint, pytest, coverage, bandit config
├── requirements.txt           # Runtime dependencies
└── requirements-dev.txt       # pytest, black, pylint, bandit, pre-commit
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| No data on the dashboard | Confirm `data_collector.py` is running (`docker compose logs` or `ps`) and that `data/api_call_history.json` is being updated |
| Authentication errors | Re-check `BLUELINKUSER`/`BLUELINKPASS`/region/brand in `.env` |
| Missing `BLUELINKVID` | Start the app once with `BLUELINKVID` unset; the log will list discovered vehicles |
| Stale temperature readings | The vehicle's own sensor can lag — set `WEATHER_SOURCE=meteo` |
| `vehicleStatus` KeyError | Hyundai/Kia API maintenance windows — the client falls back to the last cache automatically |
| SSL certificate errors | Set `DISABLE_SSL_VERIFY=true` to activate `ssl_patch.py` |
| Hitting rate limits | The collector automatically backs off (interval × 1.5, up to ×4); reduce `API_DAILY_LIMIT` to be conservative |
| `numpy.int64` JSON errors | `DataValidator` in `src/utils/debug.py` handles these; ensure data is written through the storage backend rather than raw pandas |
| venv crashes after OS migration | Recreate it: `rm -rf venv && python3.11 -m venv venv && pip install -r requirements.txt` |
| Code changes not reflected in Docker | The image **copies** source, not mounts — rebuild: `docker compose down && docker compose build && docker compose up -d` |

## Roadmap / Incomplete Features

The repository contains scaffolding for two features that are **not currently wired into the running app**. They are documented here so anyone exploring the codebase isn't confused by them:

### Microsoft Entra ID authentication (`src/web/auth.py`, `src/web/auth_routes.py`)
- Blueprint defined, but `app.py` does **not** register it and `init_auth()` is **not** called.
- Required libraries (`identity`, `flask-session`) are **not** in `requirements.txt`.
- To activate: add the dependencies, call `init_auth(app)` and `app.register_blueprint(auth_bp)` in `src/web/app.py`, then set `AUTH_ENABLED`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID`, `AZURE_REDIRECT_URI`, `FLASK_SECRET_KEY`, and `ADMIN_USERS`.

This feature is visible in the source tree because it was partially implemented; treat it as a starting point rather than as shipping functionality.

## License

MIT License — see [`LICENSE`](LICENSE).

## Acknowledgments

- [hyundai-kia-connect-api](https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api) for vehicle connectivity
- Flask, Gunicorn, pandas, Plotly
- Chart.js and Leaflet.js for the dashboard
- Open-Meteo for weather data
