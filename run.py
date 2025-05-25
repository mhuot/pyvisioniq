#!/usr/bin/env python3
"""
Main entry point for PyVisionic application.
Starts both the data collector and web server.
"""

import subprocess
import sys
import time
import os
from pathlib import Path

def main():
    """Start both data collector and web server."""
    print("Starting PyVisionic...")
    
    # Ensure we're in the right directory
    os.chdir(Path(__file__).parent)
    
    # Start data collector in background
    print("Starting data collector...")
    collector = subprocess.Popen(
        [sys.executable, "data_collector.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    # Give collector a moment to start
    time.sleep(2)
    
    # Check if collector started successfully
    if collector.poll() is not None:
        print("Error: Data collector failed to start")
        return 1
    
    print("Data collector started successfully")
    
    # Start web server
    print("Starting web server...")
    print("Web interface will be available at http://localhost:5000")
    print("\nPress Ctrl+C to stop\n")
    
    try:
        # Run web server in foreground
        web = subprocess.Popen(
            [sys.executable, "-m", "src.web.app"],
            stdout=sys.stdout,
            stderr=sys.stderr
        )
        
        # Wait for web server to exit or Ctrl+C
        web.wait()
        
    except KeyboardInterrupt:
        print("\n\nShutting down...")
    finally:
        # Clean up
        collector.terminate()
        print("PyVisionic stopped")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())