import pandas as pd
import numpy as np
from scripts.analysis.prediction.feature_builder import build_innings_features, encode_categorical_features, build_sequence_dataset
from scripts.analysis.prediction.train_models import train_batting_models, train_bowling_models
from scripts.analysis.prediction.predict_next_performance import predict_next_batting, predict_next_bowling

try:
    from scripts.analysis.prediction.lstm_forecaster import train_lstm_model, predict_next_sequence
    LSTM_AVAILABLE = True
except Exception as _lstm_import_err:
    LSTM_AVAILABLE = False
    print(f"[LSTM] Not available: {_lstm_import_err}. Falling back to sklearn ensemble only.")

def run_prediction_pipeline(df_subject_full: pd.DataFrame, subject: str, is_batting: bool, filters: dict):
    """
    Orchestrates the predictive analysis pipeline.
    Takes the full historical dataset for the player, builds features,
    trains models on the fly, and predicts their next performance 
    based on the current context (filters).
    """
    if df_subject_full is None or df_subject_full.empty:
        return {"error": "No historical data available for prediction."}

    # 1. Feature Engineering
    df_features = build_innings_features(df_subject_full, subject, is_batting)
    df_encoded = encode_categorical_features(df_features)
    
    if len(df_encoded) < 10:
        return {"error": f"Not enough innings ({len(df_encoded)}) for reliable predictions (need at least 10)."}

    # 2. Extract Target Context from filters
    # Estimate the scenario the user is asking about
    target_context = {"is_chasing": 0, "neutral_venue": 0, "opp_freq": 0.05} # default small freq
    
    innings = filters.get("innings")
    if innings is not None and str(innings) == "2":
        target_context["is_chasing"] = 1
        
    neutral = filters.get("neutral_venue")
    if neutral:
        target_context["neutral_venue"] = 1
        
    opp = filters.get("opposition")
    if opp and "opposition" in df_encoded.columns:
        # Check freq
        opp_mask = df_encoded["opposition"].str.lower().str.contains(opp.lower(), na=False)
        if opp_mask.any():
            target_context["opp_freq"] = opp_mask.mean()

    # 3. Model Training & Prediction
    if is_batting:
        models, metrics = train_batting_models(df_encoded)
        if models is None:
            return {"error": "Training failed."}
        preds = predict_next_batting(models, df_encoded, target_context)
        
        # ---- LSTM Integration ----
        try:
            if LSTM_AVAILABLE:
                seq_length = 5
                available_cols = df_encoded.columns.tolist()
                feature_cols = [c for c in ["runs", "strike_rate", "balls_faced", "is_out", "neutral_venue", "is_chasing", "opp_freq"] if c in available_cols]
                target_cols = ["runs", "target_50", "target_100"]
                
                X_seq, y_seq = build_sequence_dataset(df_encoded, seq_length=seq_length, feature_cols=feature_cols, target_cols=target_cols)
                if len(X_seq) > 0:
                    lstm_model = train_lstm_model(X_seq, y_seq, is_batting=True, epochs=50)
                    
                    latest_seq = df_encoded[feature_cols].tail(seq_length).values
                    latest_seq = np.expand_dims(latest_seq, axis=0)
                    
                    lstm_preds = predict_next_sequence(lstm_model, latest_seq)
                    if lstm_preds is not None:
                        lstm_expected_runs = max(0, float(lstm_preds[0]))
                        
                        def sigmoid(x):
                            x = np.clip(x, -500, 500)
                            return 1 / (1 + np.exp(-x))
                        
                        lstm_prob_50 = round(float(sigmoid(lstm_preds[1])) * 100)
                        lstm_prob_100 = round(float(sigmoid(lstm_preds[2])) * 100)
                        
                        # Ensemble: average LSTM + sklearn
                        preds["expected_runs"] = round((preds.get("expected_runs", 0) + lstm_expected_runs) / 2)
                        preds["prob_50"] = round((preds.get("prob_50", 0) + lstm_prob_50) / 2)
                        preds["prob_100"] = round((preds.get("prob_100", 0) + lstm_prob_100) / 2)
                        preds["model"] = "Time-Series + GBM Ensemble"
        except Exception as lstm_err:
            print(f"[LSTM] Batting inference failed: {lstm_err}. Using sklearn predictions.")
        # --------------------------

        # Determine confidence based on MAE
        mae = metrics.get('runs_mae_gb', 20)
        confidence = max(0, min(100, 100 - mae * 1.5))
        preds["confidence"] = round(confidence)
        
        # Calculate Expected Average as an interval
        expected_runs = preds.get('expected_runs', 0)
        margin = max(3, expected_runs * 0.1) # 10% margin
        preds["expected_average_range"] = f"{round(expected_runs - margin)}–{round(expected_runs + margin)}"
        preds["expected_runs"] = round(expected_runs)
        
    else:
        models, metrics = train_bowling_models(df_encoded)
        if models is None:
            return {"error": "Training failed."}
        preds = predict_next_bowling(models, df_encoded, target_context)
        
        # ---- LSTM Integration ----
        try:
            if LSTM_AVAILABLE:
                seq_length = 5
                available_cols = df_encoded.columns.tolist()
                feature_cols = [c for c in ["runs_conceded", "economy", "balls_bowled", "wickets", "neutral_venue", "is_chasing", "opp_freq"] if c in available_cols]
                target_cols = ["wickets", "economy"]
                
                X_seq, y_seq = build_sequence_dataset(df_encoded, seq_length=seq_length, feature_cols=feature_cols, target_cols=target_cols)
                if len(X_seq) > 0:
                    lstm_model = train_lstm_model(X_seq, y_seq, is_batting=False, epochs=50)
                    
                    latest_seq = df_encoded[feature_cols].tail(seq_length).values
                    latest_seq = np.expand_dims(latest_seq, axis=0)
                    
                    lstm_preds = predict_next_sequence(lstm_model, latest_seq)
                    if lstm_preds is not None:
                        lstm_expected_wickets = max(0, float(lstm_preds[0]))
                        lstm_expected_econ = max(0, float(lstm_preds[1]))
                        
                        # Ensemble
                        preds["expected_wickets"] = round((preds.get("expected_wickets", 0) + lstm_expected_wickets) / 2, 1)
                        preds["expected_economy"] = round((preds.get("expected_economy", 0) + lstm_expected_econ) / 2, 2)
                        preds["model"] = "Time-Series + GBM Ensemble"
        except Exception as lstm_err:
            print(f"[LSTM] Bowling inference failed: {lstm_err}. Using sklearn predictions.")
        # --------------------------

        mae = metrics.get('wickets_mae', 1.0)
        confidence = max(0, min(100, 100 - mae * 20))
        preds["confidence"] = round(confidence)

    return preds

def format_prediction_output(preds: dict, is_batting: bool) -> str:
    """
    Returns a formatted string for terminal output.
    """
    if "error" in preds:
         return f"\n[Predictive Analysis] \n  ❌ {preds['error']}\n"
         
    out = []
    out.append("\n==================================================")
    out.append("  PREDICTIVE ANALYSIS (Next Match Estimates)")
    out.append("==================================================")
    
    if is_batting:
        out.append(f"  Expected Average Range : {preds.get('expected_average_range', '?')}")
        out.append(f"  Expected Strike Rate   : {preds.get('expected_sr', '?')}")
        out.append(f"  Probability of 50+     : {preds.get('prob_50', 0)}%")
        out.append(f"  Probability of 100+    : {preds.get('prob_100', 0)}%")
        out.append(f"  Expected Runs          : {preds.get('expected_runs', '?')}")
    else:
        out.append(f"  Expected Wickets       : {preds.get('expected_wickets', '?')}")
        out.append(f"  Expected Economy       : {preds.get('expected_economy', '?')}")
        
    out.append(f"  Model Confidence Score : {preds.get('confidence', '?')}%")
    out.append("==================================================\n")
    return "\n".join(out)
