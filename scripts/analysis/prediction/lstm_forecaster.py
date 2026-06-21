"""
lstm_forecaster.py - Player Form Forecaster (Time-Series)

Implements a pure-NumPy recurrent forecasting model using an Exponential Weighted
Moving Average (EWMA) approach with multi-step sequence learning. This avoids any
PyTorch/pickle dependency while still providing genuine time-series intelligence
by learning player form trends from sequential innings data.

Architecture:
  - Phase 1: Exponential smoothing (alpha learned via grid search on train data)
  - Phase 2: Residual correction via Ridge Regression on lagged features
  - Phase 3: Outputs: expected_value, classification thresholds
"""

import numpy as np

try:
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler
    SKLEARN_OK = True
except ImportError:
    SKLEARN_OK = False


class SequenceForecaster:
    """
    A pure-NumPy/sklearn time-series forecaster.
    Learns an adaptive exponential smoothing alpha + ridge residual correction
    from historical sequences to predict the next value.
    """

    def __init__(self, alpha=0.3):
        self.alpha = alpha           # EWM decay
        self.ridge = None
        self.scaler = None
        self.best_alpha = alpha

    def _ewm_predict(self, seq, alpha):
        """Apply exponential weighted average over sequence, return last smoothed value."""
        s = seq[0]
        for x in seq[1:]:
            s = alpha * x + (1 - alpha) * s
        return s

    def _build_lag_features(self, X_seq):
        """
        X_seq: (n_samples, seq_length, n_features)
        Returns: (n_samples, 3 * n_features) — last val, mean, std of each feature.
        """
        last = X_seq[:, -1, :]                       # (n, f)
        mean = X_seq.mean(axis=1)                    # (n, f)
        std  = X_seq.std(axis=1) + 1e-8              # (n, f)
        slope = X_seq[:, -1, :] - X_seq[:, 0, :]    # (n, f) trend direction
        return np.concatenate([last, mean, std, slope], axis=1)

    def fit(self, X_seq, y_target_col):
        """
        X_seq: (n, seq_len, features)
        y_target_col: (n,) — the column to predict
        """
        if len(X_seq) < 3:
            return self

        # Ensure float64 to avoid numpy ufunc type errors with mixed-type arrays
        X_seq = np.array(X_seq, dtype=np.float64)
        y_target_col = np.array(y_target_col, dtype=np.float64)

        # Grid search best alpha on the first feature (primary metric)
        best_alpha, best_err = 0.1, float('inf')
        for a in np.linspace(0.05, 0.95, 19):
            preds = [self._ewm_predict(X_seq[i, :, 0], a) for i in range(len(X_seq))]
            err = np.mean((np.array(preds) - y_target_col) ** 2)
            if err < best_err:
                best_err = err
                best_alpha = a
        self.best_alpha = best_alpha

        # Build lag features and fit ridge residual model
        if SKLEARN_OK:
            lag_feat = self._build_lag_features(X_seq)
            ewm_pred = np.array([self._ewm_predict(X_seq[i, :, 0], self.best_alpha)
                                  for i in range(len(X_seq))])
            residuals = y_target_col - ewm_pred
            self.scaler = StandardScaler()
            lag_feat_s = self.scaler.fit_transform(lag_feat)
            self.ridge = Ridge(alpha=1.0)
            self.ridge.fit(lag_feat_s, residuals)
        return self

    def predict(self, X_latest_seq):
        """
        X_latest_seq: (1, seq_len, features)
        Returns: scalar predicted value
        """
        X_latest_seq = np.array(X_latest_seq, dtype=np.float64)
        ewm_val = self._ewm_predict(X_latest_seq[0, :, 0], self.best_alpha)
        if self.ridge is not None and self.scaler is not None:
            lag_feat = self._build_lag_features(X_latest_seq)
            lag_feat_s = self.scaler.transform(lag_feat)
            residual = self.ridge.predict(lag_feat_s)[0]
            return ewm_val + residual
        return ewm_val


def train_lstm_model(X_train, y_train, is_batting=True, epochs=50, batch_size=8, lr=0.01):
    """
    Train a sequence forecasting model.
    Args:
        X_train: (n_samples, seq_length, n_features)
        y_train: (n_samples, n_targets)
        is_batting: bool
    Returns:
        dict of SequenceForecaster per target column
    """
    if len(X_train) == 0:
        return None

    n_targets = y_train.shape[1]
    models = {}
    for t in range(n_targets):
        fc = SequenceForecaster()
        fc.fit(X_train, y_train[:, t])
        models[t] = fc

    return models


def predict_next_sequence(models, X_latest):
    """
    Predict next values from latest sequence.
    Args:
        models: dict of SequenceForecaster
        X_latest: (1, seq_length, n_features)
    Returns:
        numpy array of predictions, one per target column
    """
    if models is None or len(X_latest) == 0:
        return None

    results = []
    for t in sorted(models.keys()):
        val = models[t].predict(X_latest)
        results.append(float(val))
    return np.array(results)
