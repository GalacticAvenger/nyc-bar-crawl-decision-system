"""Pytest configuration — adds project root to sys.path so `src` imports work."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
