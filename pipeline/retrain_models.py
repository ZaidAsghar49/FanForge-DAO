import pandas as pd
import joblib
import json
import os
from pathlib import Path
from datetime import datetime
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error

# Config
FEATURES_DIR = Path("data/features")
MODELS_DIR = Path("models")
REGISTRY_PATH = MODELS_DIR / "model_registry.json"

def get_next_version():
    if not REGISTRY_PATH.exists():
        return 1
    with open(REGISTRY_PATH, 'r') as f:
        registry = json.load(f)
    if not registry:
        return 1
    versions = [m['model_version'] for m in registry]
    return max(versions) + 1

def retrain_models():
    """Trains global predictive models and versions them."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    
    batting_file = FEATURES_DIR / "batting_features.parquet"
    if not batting_file.exists():
        print("[-] Features not found. Run feature_builder first.")
        return

    df = pd.read_parquet(batting_file).dropna(subset=['rolling_runs_5', 'rolling_sr_5', 'opp_avg_runs', 'runs'])
    
    features = ['rolling_runs_5', 'rolling_sr_5', 'opp_avg_runs']
    target = 'runs'
    
    X = df[features]
    y = df[target]
    
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    print(f"[*] Training Batting Model (v{get_next_version()})...")
    model = HistGradientBoostingRegressor(max_iter=100)
    model.fit(X_train, y_train)
    
    mae = mean_absolute_error(y_test, model.predict(X_test))
    print(f"[+] Batting Model MAE: {mae:.2f}")
    
    # Versioning
    version = get_next_version()
    model_path = MODELS_DIR / f"batting_model_v{version}.pkl"
    joblib.dump(model, model_path)
    
    # Update Registry
    entry = {
        "model_version": version,
        "type": "batting",
        "training_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dataset_size": len(df),
        "metrics": {"mae": mae},
        "path": str(model_path)
    }
    
    registry = []
    if REGISTRY_PATH.exists():
        with open(REGISTRY_PATH, 'r') as f:
            registry = json.load(f)
            
    registry.append(entry)
    with open(REGISTRY_PATH, 'w') as f:
        json.dump(registry, f, indent=4)
        
    print(f"[+] Model v{version} registered.")

if __name__ == "__main__":
    retrain_models()
