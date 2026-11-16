"""Absolute source entry point usable after pkexec clears PYTHONPATH."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from offline_apt_builder.worker import main
if __name__ == '__main__':
    raise SystemExit(main())
