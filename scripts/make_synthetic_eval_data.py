#!/usr/bin/env python3
"""Synthetic inputs for a smoke test of scripts/run_eval.py. These are NOT study results.

Writes truth.csv, flights.csv and tracks_<configuration>.csv for all four configurations.
Detection probabilities are made-up logistic curves, so the output only checks that the pipeline runs end to
end and that the numbers move in the expected direction (fusion >= radar-only >= camera-only at long range).

  # fully synthetic straight-line encounters at 100 / 130 / 170 kt
  python3 scripts/make_synthetic_eval_data.py --out /tmp/eval_synth
  # synthetic detections on REAL truth geometry (e.g. from scripts/aot_truth_for_eval.py)
  python3 scripts/make_synthetic_eval_data.py --truth /tmp/aot_eval/truth.csv --flights /tmp/aot_eval/flights.csv --out /tmp/aot_eval
"""
import argparse
import os

import numpy as np
import pandas as pd

KT = 0.514444
SCAN_S = 1.0
COLS = ["flight_id", "track_id", "t_s", "radar_hit", "radar_az_deg", "radar_el_deg",
        "cam_hit", "cam_az_deg", "cam_el_deg", "est_az_deg", "est_el_deg", "est_range_m"]


def logistic(r, r50, w):
    return 1.0 / (1.0 + np.exp((r - r50) / w))


def synth_truth(rng, n_flights):
    truth, flights = [], []
    for i in range(n_flights):
        fid = f"synth{i:04d}"
        v = rng.choice([100, 130, 170]) * KT
        r0, rcpa = rng.uniform(4800, 6000), rng.uniform(200, 600)
        t_cpa = np.sqrt(r0 ** 2 - rcpa ** 2) / v
        t = np.arange(0.0, t_cpa + 10.0, 0.1)
        along = v * (t_cpa - t)
        r = np.hypot(along, rcpa)
        az0 = rng.uniform(-20, 20)
        az = az0 + np.degrees(np.arctan2(rcpa, np.maximum(along, 1e-3))) * 0.2
        el = np.full_like(t, rng.uniform(-3, 3))
        truth.append(pd.DataFrame({"flight_id": fid, "encounter_id": f"{fid}_enc0", "t_s": t,
                                   "range_m": r, "az_deg": az, "el_deg": el, "excluded": False}))
        flights.append({"flight_id": fid, "duration_s": t[-1] + 60.0,
                        "split": "fit" if rng.random() < 0.75 else "tune"})
    return pd.concat(truth, ignore_index=True), pd.DataFrame(flights)


def tracks_for(rng, truth, flights, name):
    rows = []
    for (fid, eid), g in truth.groupby(["flight_id", "encounter_id"], sort=False):
        if str(g["excluded"].iloc[0]).lower() in ("true", "1"):
            continue
        ts = np.arange(g.t_s.min(), g.t_s.max(), SCAN_S)
        r = np.interp(ts, g.t_s, g.range_m)
        az = np.interp(ts, g.t_s, g.az_deg)
        el = np.interp(ts, g.t_s, g.el_deg)
        pr = logistic(r, 4900, 250)          # made-up radar Pd(r)
        pc = logistic(r, 1300, 150)          # made-up camera Pd(r), unaided search
        pcc = logistic(r, 2600, 250)         # made-up camera Pd(r) when cued into the radar gate
        rh = rng.random(len(ts)) < pr
        if name == "camera_only":
            ch = rng.random(len(ts)) < pc
        elif name == "radar_only":
            ch = np.zeros(len(ts), bool)
        else:
            ch = rng.random(len(ts)) < pcc
        prim = ch if name == "camera_only" else rh
        if not prim.any():
            continue
        s = int(np.argmax(prim))
        raz, rel = az + rng.normal(0, 0.7, len(ts)), el + rng.normal(0, 0.7, len(ts))
        caz, cel = az + rng.normal(0, 0.1, len(ts)), el + rng.normal(0, 0.1, len(ts))
        rr = r * (1 + rng.normal(0, 0.02, len(ts)))
        if name == "camera_only":
            est_az, est_el, est_r = caz, cel, np.full(len(ts), np.nan)
        elif name == "full_fusion":
            est_az, est_el, est_r = np.where(ch, caz, raz), np.where(ch, cel, rel), rr
        else:
            est_az, est_el, est_r = raz, rel, rr
        for i in range(s, len(ts)):
            rows.append([fid, f"{eid}_t", ts[i], int(rh[i]) if name != "camera_only" else np.nan,
                         raz[i] if rh[i] else np.nan, rel[i] if rh[i] else np.nan,
                         int(ch[i]) if name != "radar_only" else np.nan,
                         caz[i] if ch[i] else np.nan, cel[i] if ch[i] else np.nan,
                         est_az[i], est_el[i], est_r[i] if name != "camera_only" else np.nan])
    # clutter: short false tracks, ~2 per flight hour of which some confirm
    for _, f in flights.iterrows():
        for j in range(rng.poisson(2.0 * f.duration_s / 3600.0 * 5)):
            t0 = rng.uniform(0, max(f.duration_s - 10, 1))
            az0, el0 = rng.uniform(-30, 30), rng.uniform(-10, 10)
            for k in range(8):
                p = 0.30 if name != "full_fusion" else 0.25
                h = int(rng.random() < p)
                c = int(rng.random() < 0.03) if name != "radar_only" else 0
                caz = az0 + rng.uniform(-10, 10)   # clutter camera hits are mostly outside the gate
                rows.append([f.flight_id, f"clutter{j}", t0 + k * SCAN_S,
                             h if name != "camera_only" else np.nan, az0, el0,
                             c if name != "radar_only" else np.nan, caz if c else np.nan, el0 if c else np.nan,
                             az0, el0, rng.uniform(500, 5000) if name != "camera_only" else np.nan])
    return pd.DataFrame(rows, columns=COLS)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=2027)
    ap.add_argument("--flights-n", type=int, default=300)
    ap.add_argument("--truth")
    ap.add_argument("--flights")
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    if a.truth:
        truth, flights = pd.read_csv(a.truth), pd.read_csv(a.flights)
    else:
        truth, flights = synth_truth(rng, a.flights_n)
    os.makedirs(a.out, exist_ok=True)
    if not a.truth:
        truth.to_csv(os.path.join(a.out, "truth.csv"), index=False)
        flights.to_csv(os.path.join(a.out, "flights.csv"), index=False)
    for i, name in enumerate(("camera_only", "radar_only", "radar_cued_camera", "full_fusion")):
        tracks_for(np.random.default_rng([a.seed, i]), truth, flights, name).to_csv(
            os.path.join(a.out, f"tracks_{name}.csv"), index=False)
    print(f"wrote synthetic inputs to {a.out} (seed {a.seed})")


if __name__ == "__main__":
    main()
