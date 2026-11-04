#!/usr/bin/env python3
"""Storm Charm: how well do simple weather signals precede rain?

Reads an hourly weather CSV (e.g. a CWA station-year) and checks, for every hour
that is NOT currently raining, whether a signal fires and whether measurable rain
follows within the next N hours.

Expected CSV columns (header names as in the merged station file):
    datetime, station_pressure, temperature, dew_point, relative_humidity,
    precipitation

Usage:
    python analyze_station_rain.py taipei_466920_hourly_2025.csv
    python analyze_station_rain.py data.csv --horizon 2 --rain-threshold 0.1 \
        --split 2025-07-01 --out-dir results/

Requires: pandas, numpy.  scikit-learn is optional (time-split model check).

Assumptions and limits:
  * Data are hourly and regular (gaps are reindexed to NaN and dropped).
  * Seasons use calendar months for the northern hemisphere.
  * Thresholds below are fixed a priori, not tuned; results are exploratory.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RENAME = {
    "station_pressure": "P",
    "temperature": "T",
    "dew_point": "Td",
    "relative_humidity": "RH",
    "precipitation": "R",
}
SEASONS = [("冬", [12, 1, 2]), ("春", [3, 4, 5]), ("夏", [6, 7, 8]), ("秋", [9, 10, 11])]
FEATURES = ["RH", "gap", "dT1", "dT2", "dP3", "dP6", "dgap2", "dRH2"]


def load(path):
    df = pd.read_csv(path, encoding="utf-8-sig", parse_dates=["datetime"])
    df = df.rename(columns=RENAME)
    missing = [c for c in ["datetime", "P", "T", "Td", "RH", "R"] if c not in df.columns]
    if missing:
        sys.exit(f"missing columns in {path}: {missing}")
    df = df.sort_values("datetime").drop_duplicates("datetime").set_index("datetime")
    full = pd.date_range(df.index.min(), df.index.max(), freq="h")
    n_missing = len(full.difference(df.index))
    df = df.reindex(full)
    print(f"rows: {len(df)}  range: {df.index.min()} -> {df.index.max()}  "
          f"missing timestamps: {n_missing}")
    return df


def build(df, horizon, rain_threshold):
    d = df.copy()
    d["rain"] = d["R"] >= rain_threshold
    d["gap"] = d["T"] - d["Td"]  # dew-point depression
    d["dP1"], d["dP3"], d["dP6"] = d["P"].diff(1), d["P"].diff(3), d["P"].diff(6)
    d["dT1"], d["dT2"] = d["T"].diff(1), d["T"].diff(2)
    d["dgap2"], d["dRH2"] = d["gap"].diff(2), d["RH"].diff(2)

    rain = d["rain"].astype(float).where(d["R"].notna())
    future = pd.concat([rain.shift(-k) for k in range(1, horizon + 1)], axis=1)
    d["y"] = future.max(axis=1) > 0                      # rain within next `horizon` hours
    d["valid"] = (~d["rain"]) & future.notna().all(axis=1) & d["R"].notna()

    month = d.index.month
    d["season"] = "?"
    for name, months in SEASONS:
        d.loc[month.isin(months), "season"] = name
    d["hr"] = d.index.hour
    return d


def auc(x, y):
    """Rank-based AUC (Mann-Whitney). 0.5 = random, 1.0 = perfect."""
    x, y = np.asarray(x), np.asarray(y, dtype=bool)
    n1, n0 = y.sum(), (~y).sum()
    if n1 == 0 or n0 == 0:
        return np.nan
    ranks = pd.Series(x).rank().values
    return (ranks[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def signal_table(v):
    sig = {
        "濕度>=85": v["RH"] >= 85,
        "濕度>=90": v["RH"] >= 90,
        "露點差<=2": v["gap"] <= 2,
        "露點差<=3且縮小": (v["gap"] <= 3) & (v["dgap2"] < 0),
        "氣壓3h降>=1": v["dP3"] <= -1,
        "氣壓3h降>=2": v["dP3"] <= -2,
        "氣溫1h降>=2": v["dT1"] <= -2,
        "氣溫2h降>=3": v["dT2"] <= -3,
        "濕度>=85且氣溫2h降>=2": (v["RH"] >= 85) & (v["dT2"] <= -2),
    }
    rows = []
    for scope, sub in [("全年", v)] + [(s, v[v["season"] == s]) for s, _ in SEASONS]:
        base, pos = sub["y"].mean(), sub["y"].sum()
        for name, s in sig.items():
            s = s.loc[sub.index]
            n, hit = int(s.sum()), int((s & sub["y"]).sum())
            prec = hit / n if n else np.nan
            rows.append({
                "範圍": scope, "訊號": name, "觸發": n, "命中": hit,
                "精準度": prec, "基準": base,
                "倍數": prec / base if n else np.nan,
                "涵蓋率": hit / pos if pos else np.nan,
            })
    return pd.DataFrame(rows)


def auc_table(v):
    feats = {
        "相對濕度": v["RH"],
        "露點差(越小越像)": -v["gap"],
        "露點差2h縮小": -v["dgap2"],
        "濕度2h上升": v["dRH2"],
        "氣壓1h下降": -v["dP1"],
        "氣壓3h下降": -v["dP3"],
        "氣壓6h下降": -v["dP6"],
        "氣溫1h下降": -v["dT1"],
        "氣溫2h下降": -v["dT2"],
    }
    rows = []
    for name, x in feats.items():
        row = {"特徵": name, "全年": auc(x, v["y"])}
        for s, _ in SEASONS:
            m = (v["season"] == s).values
            row[s] = auc(x.values[m], v["y"].values[m])
        rows.append(row)
    return pd.DataFrame(rows)


def pressure_tide(d, v):
    """Pressure has a daily cycle; check how much it hides, and a de-tided variant."""
    by_hr = d.groupby("hr")["dP3"].mean()
    clim = d.groupby("hr")["P"].transform(
        lambda s: s.rolling(7, center=True, min_periods=3).mean())
    d = d.assign(Pa=d["P"] - clim)
    d["dPa3"], d["dPa6"] = d["Pa"].diff(3), d["Pa"].diff(6)
    vv = v.join(d[["Pa", "dPa3", "dPa6"]]).dropna(subset=["Pa", "dPa3", "dPa6"])
    res = {
        "原始3h下降": auc(-vv["dP3"], vv["y"]),
        "偏離同時段均值": auc(-vv["Pa"], vv["y"]),
        "偏離值3h下降": auc(-vv["dPa3"], vv["y"]),
        "偏離值6h下降": auc(-vv["dPa6"], vv["y"]),
    }
    return by_hr, res


def time_split_model(v, split):
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import roc_auc_score
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        print("(scikit-learn not installed; skipping time-split model check)")
        return None
    vv = v.dropna(subset=FEATURES + ["y"])
    tr, te = vv[vv.index < split], vv[vv.index >= split]
    if tr["y"].sum() == 0 or te["y"].sum() == 0:
        print("(not enough positives on one side of the split; skipping)")
        return None
    sets = {
        "只用濕度": ["RH"],
        "濕度+露點差": ["RH", "gap"],
        "+氣溫變化": ["RH", "gap", "dT1", "dT2"],
        "+氣壓變化": ["RH", "gap", "dT1", "dT2", "dP3", "dP6"],
        "全部特徵": FEATURES,
    }
    print(f"train {len(tr)} rows ({int(tr['y'].sum())} pos) | "
          f"test {len(te)} rows ({int(te['y'].sum())} pos) | split at {split}")
    out = {}
    for name, cols in sets.items():
        sc = StandardScaler().fit(tr[cols])
        m = LogisticRegression(max_iter=2000).fit(sc.transform(tr[cols]), tr["y"])
        out[name] = roc_auc_score(te["y"], m.predict_proba(sc.transform(te[cols]))[:, 1])
    return pd.Series(out, name="AUC")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--horizon", type=int, default=2, help="hours ahead to look for rain")
    ap.add_argument("--rain-threshold", type=float, default=0.1, help="mm per hour")
    ap.add_argument("--split", default=None, help="train/test split date (default: mid-year)")
    ap.add_argument("--out-dir", default=None, help="save result tables as CSV here")
    a = ap.parse_args()

    pd.set_option("display.width", 160)
    pd.set_option("display.float_format", lambda x: f"{x:.3f}")

    d = build(load(a.csv), a.horizon, a.rain_threshold)
    print(f"rainy hours (>= {a.rain_threshold} mm): {int(d['rain'].sum())}")

    v = d[d["valid"]].dropna(subset=["RH", "gap", "dP3", "dP6", "dT1", "dT2", "dgap2"]).copy()
    print(f"\nvalid hours: {len(v)} | rain within {a.horizon}h: {int(v['y'].sum())} | "
          f"base rate: {v['y'].mean():.3f}")
    print(v.groupby("season")["y"].agg(["size", "sum", "mean"]).to_string())

    print("\n== AUC per feature (0.5 = random) ==")
    t_auc = auc_table(v)
    print(t_auc.to_string(index=False))

    print("\n== Signal precision / coverage ==")
    t_sig = signal_table(v)
    print(t_sig[t_sig["範圍"] == "全年"].drop(columns="範圍").to_string(index=False))
    for s, _ in SEASONS:
        print(f"\n[{s}]")
        print(t_sig[t_sig["範圍"] == s].drop(columns="範圍").to_string(index=False))

    print("\n== Pressure daily cycle (mean 3h change by hour of day) ==")
    by_hr, tide = pressure_tide(d, v)
    print(by_hr.round(2).to_string())
    print("AUC of pressure variants:", {k: round(x, 3) for k, x in tide.items()})

    split = a.split or f"{d.index.min().year}-07-01"
    print("\n== Time-split logistic regression (out-of-sample AUC) ==")
    t_model = time_split_model(v, split)
    if t_model is not None:
        print(t_model.to_string())

    if a.out_dir:
        out = Path(a.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        t_auc.to_csv(out / "auc_by_feature.csv", index=False)
        t_sig.to_csv(out / "signal_performance.csv", index=False)
        if t_model is not None:
            t_model.to_csv(out / "time_split_model_auc.csv")
        print(f"\nsaved tables to {out}/")


if __name__ == "__main__":
    main()