"""Forecast 4: logistic regression with isotonic calibration, specified in docs/PREREG.md before fitting.

Median imputation with missing indicators, standardisation, L2 logistic regression. C is chosen by
out-of-fold Brier score over 5 contiguous calendar blocks of the training year. Isotonic regression
is fit on those out-of-fold predictions; the final regression is refit on all training nights.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import KFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from vogdesk.features import FEATURES
from vogdesk.metrics import brier

C_GRID = (0.01, 0.03, 0.1, 0.3, 1.0, 3.0)
N_FOLDS = 5
ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = ROOT / "models"


def make_pipeline(C: float) -> Pipeline:
    return Pipeline([
        ("impute", SimpleImputer(strategy="median", add_indicator=True)),
        ("scale", StandardScaler()),
        ("lr", LogisticRegression(C=C, max_iter=2000)),  # sklearn default penalty is L2
    ])


@dataclass
class VogModel:
    pipeline: Pipeline
    isotonic: IsotonicRegression
    features: list[str]
    C: float
    oof_brier: dict[float, float]
    train_range: dict[str, tuple[float, float]]
    n_train: int
    n_events: int
    fitted_on: str = "2025-01-01..2025-12-31"
    notes: dict = field(default_factory=dict)

    def raw_probability(self, X: pd.DataFrame) -> np.ndarray:
        return self.pipeline.predict_proba(X[self.features])[:, 1]

    def probability(self, X: pd.DataFrame) -> np.ndarray:
        return self.isotonic.predict(self.raw_probability(X))

    def outside_training_range(self, row: pd.Series) -> list[str]:
        """Features whose value lies outside the training range (DutyLead flag 'regime unseen')."""
        out = []
        for f in self.features:
            v = row.get(f)
            if v is None or (isinstance(v, float) and np.isnan(v)):
                continue
            lo, hi = self.train_range[f]
            if v < lo or v > hi:
                out.append(f)
        return out

    def coefficients(self) -> pd.Series:
        names = self.pipeline.named_steps["impute"].get_feature_names_out(self.features)
        return pd.Series(self.pipeline.named_steps["lr"].coef_[0], index=names)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path)
        export = {
            "features": self.features, "C": self.C, "n_train": self.n_train, "n_events": self.n_events,
            "fitted_on": self.fitted_on, "oof_brier_by_C": {str(k): v for k, v in self.oof_brier.items()},
            "coefficients_standardised": self.coefficients().round(4).to_dict(),
            "intercept": float(self.pipeline.named_steps["lr"].intercept_[0]),
            "impute_medians": dict(zip(self.features, map(float, self.pipeline.named_steps["impute"].statistics_))),
            "scale_mean": list(map(float, self.pipeline.named_steps["scale"].mean_)),
            "scale_std": list(map(float, self.pipeline.named_steps["scale"].scale_)),
            "isotonic_x": list(map(float, self.isotonic.X_thresholds_)),
            "isotonic_y": list(map(float, self.isotonic.y_thresholds_)),
            "train_range": {k: [float(a), float(b)] for k, (a, b) in self.train_range.items()},
            "notes": self.notes,
        }
        path.with_suffix(".json").write_text(json.dumps(export, indent=1))


def fit(X: pd.DataFrame, y: pd.Series, features: list[str] = FEATURES, c_grid=C_GRID) -> VogModel:
    """X and y are indexed by issue date and sorted; folds are contiguous blocks of that order."""
    X = X.sort_index()
    y = y.loc[X.index].astype(float)
    Xf = X[features]
    kf = KFold(n_splits=N_FOLDS, shuffle=False)
    oof = {}
    for C in c_grid:
        pred = np.full(len(Xf), np.nan)
        for tr, te in kf.split(Xf):
            pipe = make_pipeline(C).fit(Xf.iloc[tr], y.iloc[tr])
            pred[te] = pipe.predict_proba(Xf.iloc[te])[:, 1]
        oof[C] = pred
    oof_brier = {C: brier(p, y) for C, p in oof.items()}
    best_C = min(oof_brier, key=oof_brier.get)
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(oof[best_C], y)
    pipe = make_pipeline(best_C).fit(Xf, y)
    rng = {f: (float(np.nanmin(Xf[f])), float(np.nanmax(Xf[f]))) for f in features}
    m = VogModel(pipeline=pipe, isotonic=iso, features=list(features), C=best_C, oof_brier=oof_brier,
                 train_range=rng, n_train=int(len(y)), n_events=int(y.sum()))
    m.notes["oof_predictions_best_C"] = {"brier_raw": oof_brier[best_C],
                                         "brier_isotonic_in_sample": brier(iso.predict(oof[best_C]), y)}
    m.notes["_oof"] = pd.Series(oof[best_C], index=X.index).to_dict()
    return m


PARAMS = MODEL_DIR / "lr_2025.params.json"


class ParamsModel:
    """Forecast 4 rebuilt from its exported parameters, for clones without the joblib pickle.

    Same arithmetic as the fitted pipeline: median imputation with missing indicators, standardisation,
    logistic regression, then the isotonic map by linear interpolation clipped at both ends. A test checks
    it agrees with the pickle to 1e-9 on every development night.
    """

    def __init__(self, params: dict):
        self.params = params
        self.features = list(params["features"])
        self.columns = list(params["columns"])
        self.C = params["C"]; self.n_train = params["n_train"]; self.n_events = params["n_events"]
        self.fitted_on = params["fitted_on"]; self.notes = params.get("notes", {})
        self.train_range = {k: tuple(v) for k, v in params["train_range"].items()}
        self._median = np.array([params["impute_medians"][f] for f in self.features])
        self._mean = np.array(params["scale_mean"]); self._std = np.array(params["scale_std"])
        self._coef = np.array(params["coef"]); self._intercept = float(params["intercept"])
        self._iso_x = np.array(params["isotonic_x"]); self._iso_y = np.array(params["isotonic_y"])
        self._indicator_of = [self.features.index(c.removeprefix("missingindicator_")) for c in self.columns[len(self.features):]]

    def _design(self, X: pd.DataFrame) -> np.ndarray:
        raw = X[self.features].to_numpy(dtype=float)
        missing = np.isnan(raw)
        filled = np.where(missing, self._median, raw)
        ind = missing[:, self._indicator_of].astype(float) if self._indicator_of else np.empty((len(raw), 0))
        return (np.hstack([filled, ind]) - self._mean) / self._std

    def raw_probability(self, X: pd.DataFrame) -> np.ndarray:
        z = self._design(X) @ self._coef + self._intercept
        return 1.0 / (1.0 + np.exp(-z))

    def probability(self, X: pd.DataFrame) -> np.ndarray:
        return np.interp(self.raw_probability(X), self._iso_x, self._iso_y)

    outside_training_range = VogModel.outside_training_range


def export_params(m: VogModel, path: Path = PARAMS) -> None:
    pipe = m.pipeline
    params = {"features": m.features, "columns": list(pipe.named_steps["impute"].get_feature_names_out(m.features)),
              "C": m.C, "n_train": m.n_train, "n_events": m.n_events, "fitted_on": m.fitted_on,
              "notes": {k: v for k, v in m.notes.items() if not k.startswith("_")},
              "impute_medians": dict(zip(m.features, map(float, pipe.named_steps["impute"].statistics_))),
              "scale_mean": list(map(float, pipe.named_steps["scale"].mean_)),
              "scale_std": list(map(float, pipe.named_steps["scale"].scale_)),
              "coef": list(map(float, pipe.named_steps["lr"].coef_[0])), "intercept": float(pipe.named_steps["lr"].intercept_[0]),
              "isotonic_x": list(map(float, m.isotonic.X_thresholds_)), "isotonic_y": list(map(float, m.isotonic.y_thresholds_)),
              "train_range": {k: [float(a), float(b)] for k, (a, b) in m.train_range.items()}}
    path.write_text(json.dumps(params, indent=1))


def load(path: Path = MODEL_DIR / "lr_2025.joblib"):
    """The frozen 2025 model: the pickle when present, else the parameter export (a fresh clone)."""
    if path.exists():
        return joblib.load(path)
    return ParamsModel(json.loads(PARAMS.read_text()))
