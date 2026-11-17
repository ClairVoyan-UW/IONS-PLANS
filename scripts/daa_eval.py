#!/usr/bin/env python3
"""Frozen scoring functions for the PLANS 2027 radar–camera DAA study.

Every definition (M-of-N, gate, bins, target, speeds, CI method) is read from configs/eval_config.yaml.
Nothing here hard-codes a study number. The four configurations go through exactly the same functions;
only the input track file changes.

Input tables (CSV; times in seconds; angles in degrees; ranges in metres). See eval/README.md.
  truth.csv    flight_id, encounter_id, t_s, range_m, az_deg, el_deg [, excluded]
               (or own_e/own_n/own_u + intr_e/intr_n/intr_u instead of range/az/el; see truth_from_gps)
  flights.csv  flight_id, duration_s, split
  tracks_<configuration>.csv
               flight_id, track_id, t_s, radar_hit, radar_az_deg, radar_el_deg,
               cam_hit, cam_az_deg, cam_el_deg, est_az_deg, est_el_deg, est_range_m
               One row per scan of the declaring sensor, missed scans included (hit = 0).
"""
from __future__ import annotations

import hashlib
import math

import numpy as np
import pandas as pd
import yaml
from scipy.stats import binomtest, chi2

CONFIGURATIONS = ("camera_only", "radar_only", "radar_cued_camera", "full_fusion")
KT_TO_MS = 0.514444


# --------------------------------------------------------------------------------------------- config
def load_config(path):
    """Read and check the frozen config. Adds '_sha256' (hash of the file bytes) for provenance."""
    with open(path, "rb") as f:
        raw = f.read()
    cfg = yaml.safe_load(raw)
    _validate(cfg)
    cfg["_sha256"] = hashlib.sha256(raw).hexdigest()
    return cfg


def _validate(cfg):
    d = cfg["declaration"]
    m, n, mc = d["m"], d["n"], d["camera_shortcut"]["m_cued"]
    if not (1 <= m <= n):
        raise ValueError(f"need 1 <= m <= n, got m={m}, n={n}")
    if not (1 <= mc <= m):
        raise ValueError(f"need 1 <= m_cued <= m, got m_cued={mc}, m={m}")
    if d["camera_shortcut"]["gate_deg"] <= 0:
        raise ValueError("gate_deg must be > 0")
    edges = cfg["range_bins_km"]
    if len(edges) < 2 or list(edges) != sorted(edges):
        raise ValueError("range_bins_km must be >= 2 increasing edges")
    if set(cfg["configurations"]) != set(CONFIGURATIONS):
        raise ValueError(f"configurations must be exactly {CONFIGURATIONS}")
    for name, c in cfg["configurations"].items():
        if c["declare_on"] not in ("radar", "camera"):
            raise ValueError(f"{name}: declare_on must be radar or camera")
        if c["camera_shortcut"] and c["declare_on"] != "radar":
            raise ValueError(f"{name}: the camera shortcut applies only when declaring on radar")


# --------------------------------------------------------------------------------------------- geometry
def angular_sep_deg(az1, el1, az2, el2):
    """Great-circle angle between two directions (degrees). NaN in, NaN out."""
    az1, el1, az2, el2 = (np.radians(np.asarray(x, dtype=float)) for x in (az1, el1, az2, el2))
    c = np.sin(el1) * np.sin(el2) + np.cos(el1) * np.cos(el2) * np.cos(az1 - az2)
    return np.degrees(np.arccos(np.clip(c, -1.0, 1.0)))


def truth_from_gps(own_enu, intr_enu):
    """Range, azimuth (from north, clockwise) and elevation of the intruder seen from ownship.
    own_enu, intr_enu: (N, 3) arrays of east/north/up metres from GPS (same local frame)."""
    d = np.asarray(intr_enu, float) - np.asarray(own_enu, float)
    rng = np.linalg.norm(d, axis=1)
    az = np.degrees(np.arctan2(d[:, 0], d[:, 1]))
    el = np.degrees(np.arcsin(np.clip(d[:, 2] / np.maximum(rng, 1e-9), -1, 1)))
    return rng, az, el


class Encounter:
    """Truth for one intruder pass: sorted arrays plus the approach geometry used for P_decl."""

    def __init__(self, encounter_id, flight_id, t, r, az, el, excluded=False):
        o = np.argsort(t)
        self.id, self.flight_id, self.excluded = encounter_id, flight_id, bool(excluded)
        self.t, self.r = np.asarray(t, float)[o], np.asarray(r, float)[o]
        self.az = np.degrees(np.unwrap(np.radians(np.asarray(az, float)[o])))
        self.el = np.asarray(el, float)[o]
        i_cpa = int(np.argmin(self.r))
        self.t0, self.t_end, self.t_cpa = self.t[0], self.t[-1], self.t[i_cpa]
        self.start_range_m, self.min_range_m = self.r[0], self.r[i_cpa]
        dt = self.t_cpa - self.t0
        self.closing_speed_kt = ((self.start_range_m - self.min_range_m) / dt / KT_TO_MS) if dt > 0 else math.nan
        self._ta, self._ra = self.t[: i_cpa + 1], self.r[: i_cpa + 1]  # approach segment only

    def time_reaching(self, range_m):
        """First time on approach when true range drops to range_m (interpolated). None if it never does,
        or if the encounter already starts inside range_m."""
        ra, ta = self._ra, self._ta
        if ra[0] <= range_m or ra.min() > range_m:
            return None
        i = int(np.argmax(ra <= range_m))
        f = (ra[i - 1] - range_m) / (ra[i - 1] - ra[i])
        return float(ta[i - 1] + f * (ta[i] - ta[i - 1]))

    def at(self, t):
        return (float(np.interp(t, self.t, self.r)), float(np.interp(t, self.t, self.az)),
                float(np.interp(t, self.t, self.el)))


def load_truth(truth_df):
    """truth.csv -> {flight_id: [Encounter, ...]}. Accepts range/az/el columns or GPS ENU columns."""
    df = truth_df.copy()
    if "range_m" not in df.columns:
        own = df[["own_e", "own_n", "own_u"]].to_numpy()
        intr = df[["intr_e", "intr_n", "intr_u"]].to_numpy()
        df["range_m"], df["az_deg"], df["el_deg"] = truth_from_gps(own, intr)
    if "excluded" not in df.columns:
        df["excluded"] = False
    df["excluded"] = df["excluded"].astype(str).str.lower().isin(["true", "1"])
    out = {}
    for (fid, eid), g in df.groupby(["flight_id", "encounter_id"], sort=False):
        out.setdefault(fid, []).append(
            Encounter(eid, fid, g.t_s.to_numpy(), g.range_m.to_numpy(), g.az_deg.to_numpy(),
                      g.el_deg.to_numpy(), g.excluded.iloc[0]))
    return out


# --------------------------------------------------------------------------------------------- the four functions
def find_declaration(track_log, m, n, declare_on="radar", shortcut=None):
    """Time of the first confirmed track, or None.

    track_log: rows of ONE track, one per scan of the declaring sensor (missed scans included).
    Confirmed at the first scan that is a hit and whose last n scans hold >= m hits.
    shortcut: None, or {'m_cued': int, 'gate_deg': float}. If any of the last n scans has a camera
    detection within gate_deg of the track's estimated direction (est_az/el = the radar gate centre),
    only m_cued radar hits are needed. Camera detections outside the gate are ignored.
    """
    tl = track_log.sort_values("t_s")
    t = tl["t_s"].to_numpy(float)
    hit = tl[f"{'cam' if declare_on == 'camera' else 'radar'}_hit"].fillna(0).astype(float).to_numpy() > 0
    if shortcut is not None:
        cam = tl["cam_hit"].fillna(0).astype(float).to_numpy() > 0
        sep = angular_sep_deg(tl["cam_az_deg"], tl["cam_el_deg"], tl["est_az_deg"], tl["est_el_deg"])
        in_gate = cam & (np.nan_to_num(sep, nan=np.inf) <= shortcut["gate_deg"])
    else:
        in_gate = np.zeros(len(t), bool)
    for i in range(len(t)):
        if not hit[i]:
            continue
        lo = max(0, i - n + 1)
        need = shortcut["m_cued"] if in_gate[lo:i + 1].any() else m
        if hit[lo:i + 1].sum() >= need:
            return float(t[i])
    return None


def declaration_range(t, encounter):
    """R_d: true (GPS) range at declaration time t, linearly interpolated. NaN if t is None."""
    if t is None:
        return math.nan
    return encounter.at(t)[0]


def p_decl_by_bin(encounters, edges_km, confidence=0.95, min_lead_s=0.0):
    """P_decl per range bin, counted per encounter, with Clopper–Pearson CIs.

    encounters: DataFrame from score_configuration (one row per counted encounter) with columns
      t_decl, t0, start_range_m and t_reach_<edge>km for every edge.
    For bin [lo, hi): n = encounters starting at/beyond hi (with min_lead_s before reaching hi) that later
    reach lo; k = those declared no later than reaching lo.
    """
    rows = []
    for lo, hi in zip(edges_km[:-1], edges_km[1:]):
        n, k = _count_by_edge(encounters, inner_km=lo, outer_km=hi, min_lead_s=min_lead_s)
        rows.append({"bin_km": f"{lo}-{hi}", "lo_km": lo, "hi_km": hi, "n": n, "k": k,
                     **clopper_pearson(k, n, confidence)})
    return pd.DataFrame(rows)


def p_decl_by_range(encounters, range_km, confidence=0.95, min_lead_s=0.0):
    """P(declared by range_km): eligible = starts at/beyond range_km and reaches it. Used for the 4.4 km target."""
    n, k = _count_by_edge(encounters, inner_km=range_km, outer_km=range_km, min_lead_s=min_lead_s)
    return {"range_km": range_km, "n": n, "k": k, **clopper_pearson(k, n, confidence)}


def false_decl_rate(declared_tracks, flight_hours, confidence=0.95):
    """False declarations per flight hour, with an exact Poisson CI.
    declared_tracks: DataFrame with column 'match' in {'real', 'false', 'excluded'}."""
    k = int((declared_tracks["match"] == "false").sum()) if len(declared_tracks) else 0
    a = 1 - confidence
    lo = 0.0 if k == 0 else chi2.ppf(a / 2, 2 * k) / 2
    hi = chi2.ppf(1 - a / 2, 2 * k + 2) / 2
    h = float(flight_hours)
    nan = math.nan
    return {"false_declarations": k, "flight_hours": h,
            "per_flight_hour": k / h if h > 0 else nan,
            "ci_lo": lo / h if h > 0 else nan, "ci_hi": hi / h if h > 0 else nan}


# --------------------------------------------------------------------------------------------- helpers
def clopper_pearson(k, n, confidence=0.95):
    if n == 0:
        return {"p": math.nan, "ci_lo": math.nan, "ci_hi": math.nan}
    ci = binomtest(int(k), int(n)).proportion_ci(confidence_level=confidence, method="exact")
    return {"p": k / n, "ci_lo": float(ci.low), "ci_hi": float(ci.high)}


def _edge_col(km):
    return f"t_reach_{km:g}km"


def _count_by_edge(enc, inner_km, outer_km, min_lead_s):
    if len(enc) == 0:
        return 0, 0
    t_in, t_out = enc[_edge_col(inner_km)], enc[_edge_col(outer_km)]
    starts_beyond = enc["start_range_m"] >= outer_km * 1000.0
    lead_ok = (t_out - enc["t0"]) >= min_lead_s
    eligible = starts_beyond & t_in.notna() & t_out.notna() & lead_ok
    declared = eligible & enc["t_decl"].notna() & (enc["t_decl"] <= t_in)
    return int(eligible.sum()), int(declared.sum())


def associate(t, est_az, est_el, est_range, candidates, assoc_cfg):
    """Truth encounter a declared track belongs to (smallest angle inside the gates), or None."""
    best, best_sep = None, math.inf
    for e in candidates:
        if t < e.t0 - assoc_cfg["max_time_gap_s"] or t > e.t_end + assoc_cfg["max_time_gap_s"]:
            continue
        r, az, el = e.at(t)
        sep = float(angular_sep_deg(est_az, est_el, az, el))
        if not (sep <= assoc_cfg["max_angle_deg"]):
            continue
        if est_range is not None and np.isfinite(est_range):
            if abs(est_range - r) / r > assoc_cfg["max_range_err_frac"]:
                continue
        if sep < best_sep:
            best, best_sep = e, sep
    return best


def speed_case(speed_kt, cases_kt, tol_kt):
    if not np.isfinite(speed_kt):
        return None
    c = min(cases_kt, key=lambda s: abs(s - speed_kt))
    return c if abs(c - speed_kt) <= tol_kt else None


# --------------------------------------------------------------------------------------------- one configuration
def score_configuration(tracks_df, truth, cfg, name):
    """Run the frozen rules for one configuration.
    Returns (per_encounter DataFrame, declared_tracks DataFrame)."""
    c = cfg["configurations"][name]
    d = cfg["declaration"]
    shortcut = d["camera_shortcut"] if c["camera_shortcut"] else None
    edges = sorted(set(cfg["range_bins_km"]) | {cfg["target"]["range_km"]})

    declared = []
    for (fid, tid), g in tracks_df.groupby(["flight_id", "track_id"], sort=False):
        t = find_declaration(g, d["m"], d["n"], c["declare_on"], shortcut)
        if t is None:
            continue
        row = g.loc[(g["t_s"] - t).abs().idxmin()]
        est_r = row.get("est_range_m", math.nan)
        est_r = float(est_r) if pd.notna(est_r) else None
        e = associate(t, float(row["est_az_deg"]), float(row["est_el_deg"]), est_r,
                      truth.get(fid, []), cfg["association"])
        match = "false" if e is None else ("excluded" if e.excluded else "real")
        declared.append({"flight_id": fid, "track_id": tid, "t_decl": t, "match": match,
                         "encounter_id": None if e is None else e.id})
    declared = pd.DataFrame(declared, columns=["flight_id", "track_id", "t_decl", "match", "encounter_id"])

    first = (declared[declared["match"] == "real"].groupby("encounter_id")["t_decl"].min().to_dict()
             if len(declared) else {})
    rows = []
    for fid, encs in truth.items():
        for e in encs:
            if e.excluded:
                continue
            t_decl = first.get(e.id)
            rec = {"configuration": name, "flight_id": fid, "encounter_id": e.id,
                   "t0": e.t0, "start_range_m": e.start_range_m, "min_range_m": e.min_range_m,
                   "closing_speed_kt": e.closing_speed_kt,
                   "speed_case_kt": speed_case(e.closing_speed_kt, cfg["closing_speeds_kt"],
                                               cfg["speed_match_tol_kt"]),
                   "t_decl": t_decl, "R_d_m": declaration_range(t_decl, e)}
            for km in edges:
                rec[_edge_col(km)] = e.time_reaching(km * 1000.0)
            rows.append(rec)
    per_enc = pd.DataFrame(rows)
    for km in edges:  # keep None -> NaN so comparisons work
        if len(per_enc):
            per_enc[_edge_col(km)] = pd.to_numeric(per_enc[_edge_col(km)])
    if len(per_enc):
        per_enc["t_decl"] = pd.to_numeric(per_enc["t_decl"])
    return per_enc, declared
