# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PyVisionic is a Python web application for visualizing Hyundai/Kia Connect API data with a focus on battery performance analysis. The application retrieves vehicle data (with a 30 API call/day limit) and provides modern web-based charts for analyzing battery usage patterns in relation to temperature and driving habits.

## Development Setup

### Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate  # On macOS/Linux
```

### Dependencies
```bash
pip install -r requirements.txt
```

### Running the Application
```bash
python run.py
```

### Linting
```bash
pylint src/
```

## Key Constraints

- **API Rate Limit**: Hyundai/Kia Connect API allows only 30 calls per day
- **Accessibility**: All web UI components must meet WCAG AAA standards
- **Security**: API credentials must be stored securely (environment variables or encrypted storage)

## Architecture Considerations

- Data persistence layer needed for storing API responses (30/day limit requires efficient data management)
- Background task scheduler for optimizing API call timing
- Web framework should support modern, responsive UI with charting capabilities
- Future requirement: Prometheus metrics exporter endpoint
- Use CSV for storage as the needs are low

## Testing

When implementing tests, ensure coverage for:
- API rate limiting logic
- Data storage and retrieval
- Chart data transformation
- WCAG AAA compliance verification

## Reminders

- Don't forget to use the venv
- Use docker compose not docker-compose