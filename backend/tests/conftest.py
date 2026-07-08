import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "samples"
