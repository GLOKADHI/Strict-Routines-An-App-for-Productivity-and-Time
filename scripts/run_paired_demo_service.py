"""Run the private demo helper without relying on the startup working directory."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.paired_demo_service import main

if __name__ == '__main__':
    main()
