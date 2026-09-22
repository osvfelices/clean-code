#!/usr/bin/env python3
"""Launcher kept at a stable path so installed hook commands never have to change."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clean_code.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv))
