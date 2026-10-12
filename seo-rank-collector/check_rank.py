"""Forwarder script for backwards compatibility.
Delegates directly to seo-rank-collector/cli.py.
"""
import sys
from pathlib import Path

# Ensure seo-rank-collector is in sys.path and run cli.py main
RANK_DIR = Path(__file__).resolve().parent
if str(RANK_DIR) not in sys.path:
    sys.path.insert(0, str(RANK_DIR))

from cli import main

if __name__ == "__main__":
    sys.exit(main())
