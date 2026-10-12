"""Entrypoint & alias for detect_captcha.py.

Usage:
  python detect.py "https://example.com"
"""
import sys
from pathlib import Path

# Ensure seo-captcha-detector directory is in sys.path
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from detect_captcha import main, run_detection

if __name__ == "__main__":
    sys.exit(main())
