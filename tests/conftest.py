import sys
from pathlib import Path

# Tests run against the working tree, installed or not.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
