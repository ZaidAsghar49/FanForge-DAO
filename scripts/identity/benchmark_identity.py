import time
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.identity.identity_engine import IdentityEngine

def benchmark():
    engine = IdentityEngine()
    test_names = [
        "Virat Kohli", "V Kohli", "SPD Smith", "Steve Smith", 
        "DA Warner", "David Warner", "Babar Azam", "B Azam",
        "Jasprit Bumrah", "J Bumrah", "Shahid Afridi", "S Afridi",
        "Rashid Khan", "R Khan", "Kane Williamson", "K Williamson"
    ]
    
    # Warm up
    for name in test_names:
        engine.resolve_for_ingestion(name)
        
    start_time = time.perf_counter()
    iterations = 100
    for _ in range(iterations):
        for name in test_names:
            engine.resolve_for_ingestion(name)
    end_time = time.perf_counter()
    
    total_resolutions = len(test_names) * iterations
    avg_time_ms = ((end_time - start_time) / total_resolutions) * 1000
    
    print(f"Total resolutions: {total_resolutions}")
    print(f"Average time per resolution: {avg_time_ms:.4f} ms")
    
    if avg_time_ms < 5.0:
        print("✅ PERFORMANCE TARGET MET (<5ms)")
    else:
        print("❌ PERFORMANCE TARGET FAILED (>5ms)")

if __name__ == "__main__":
    benchmark()
