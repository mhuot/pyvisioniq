# PyVisionic Tools

Utility scripts for managing and debugging PyVisionic.

## Available Tools

### reprocess_cache.py
Reprocesses the most recent cached API response and stores it in CSV files.

Usage:
```bash
cd /path/to/new-pyvisionic
source venv/bin/activate
python tools/reprocess_cache.py
```

This is useful when:
- The CSV storage format changes
- You need to recover from data collection errors
- Testing data processing without making new API calls