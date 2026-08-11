#!/usr/bin/env python3
"""Convenience launcher: starts the data collector and the web server together.

For local development only. Docker runs the two as separate services (see
docker-compose.yml), which is what production uses.
"""

import os
import subprocess  # nosec B404 - launches this project's own entry points
import sys
import time
from pathlib import Path

COLLECTOR_LOG = Path("logs/collector.log")
STARTUP_GRACE_SECONDS = 2
SHUTDOWN_GRACE_SECONDS = 10


def main():
    """Start both processes, then wait on the web server."""
    print("Starting PyVisionic...")
    os.chdir(Path(__file__).parent)
    COLLECTOR_LOG.parent.mkdir(exist_ok=True)

    print(f"Starting data collector (logging to {COLLECTOR_LOG})...")
    # Output goes to a file, never to a pipe. Piping without draining it
    # deadlocks the collector as soon as the OS buffer fills -- it logs on every
    # poll, so that is a matter of hours, and the symptom is a process that is
    # alive but silently no longer collecting.
    with open(COLLECTOR_LOG, "ab", buffering=0) as collector_log:
        # Not a `with` block: these processes must outlive it. Wrapping
        # them would wait on the collector here and never reach the web
        # server. Cleanup is handled in the finally block below.
        # pylint: disable=consider-using-with
        collector = subprocess.Popen(  # nosec B603 - fixed argv, no shell
            [sys.executable, "data_collector.py"],
            stdout=collector_log,
            stderr=subprocess.STDOUT,
        )

        time.sleep(STARTUP_GRACE_SECONDS)
        if collector.poll() is not None:
            print(f"Error: data collector exited immediately; see {COLLECTOR_LOG}")
            return 1

        print("Data collector started successfully")
        print("Starting web server...")
        print(f"Web interface will be available at http://localhost:{os.getenv('PORT', '5000')}")
        print("\nPress Ctrl+C to stop\n")

        try:
            web = subprocess.Popen(  # nosec B603 - fixed argv, no shell  # pylint: disable=consider-using-with
                [sys.executable, "-m", "src.web.app"]
            )
            web.wait()
        except KeyboardInterrupt:
            print("\n\nShutting down...")
        finally:
            collector.terminate()
            try:
                collector.wait(timeout=SHUTDOWN_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                print("Collector did not stop in time; killing it")
                collector.kill()
                collector.wait()
            print("PyVisionic stopped")

    return 0


if __name__ == "__main__":
    sys.exit(main())
