# Forwarding wrapper for consolidated validation engine
# This directs all imports to scripts/analysis/validate_model.py to prevent logic duplication.

import os
import sys
from pathlib import Path

# Adjust sys.path so the engine resolves all relative script imports correctly
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.analysis.validate_model import *
