import json
import sys
from pathlib import Path

ROOT = Path("d:/University/Semester 8th/FYP/AI")
sys.path.insert(0, str(ROOT))

from scripts.analysis.validate_model import validate_claim

claim = "Virat Kohli averages 55 in chases in Australia in 2022"
result = validate_claim(claim)
print(json.dumps(result, indent=4))
