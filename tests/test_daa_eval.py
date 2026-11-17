"""Unit tests for the frozen scoring rules. Run: python3 -m pytest tests/"""
import math
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import daa_eval as E  # noqa: E402

CFG = E.load_config(os.path.join(os.path.dirname(__file__), "..", "configs", "eval_config.yaml"))
SC = {"m_cued": 2, "gate_deg": 3.0}


def log(radar, cam=None, cam_az=None):
    n = len(radar)
    cam = cam or [0] * n
    cam_az = cam_az or [0.0] * n
    return pd.DataFrame({"t_s": np.arange(n, dtype=float), "radar_hit": radar, "cam_hit": cam,
                         "cam_az_deg": [a if c else np.nan for a, c in zip(cam_az, cam)],
                         "cam_el_deg": [0.0 if c else np.nan for c in cam],
                         "est_az_deg": 0.0, "est_el_deg": 0.0})


def test_three_of_five_basic():
    assert E.find_declaration(log([1, 1, 1, 0, 0]), 3, 5) == 2.0
    assert E.find_declaration(log([1, 0, 1, 0, 1]), 3, 5) == 4.0
    assert E.find_declaration(log([1, 0, 0, 0, 1, 1]), 3, 5) is None   # first hit has left the window
    assert E.find_declaration(log([1, 1, 0, 0, 0, 0]), 3, 5) is None


def test_declares_only_on_a_hit_scan():
    # 3 hits in the window at scan 2; scan 3 is a miss and must not be the declaration time
    assert E.find_declaration(log([1, 1, 1, 0]), 3, 5) == 2.0


def test_camera_shortcut_inside_gate():
    radar, cam = [1, 0, 1, 0, 0], [0, 1, 0, 0, 0]
    assert E.find_declaration(log(radar, cam, [0, 1.0, 0, 0, 0]), 3, 5, "radar", SC) == 2.0
    assert E.find_declaration(log(radar, cam, [0, 1.0, 0, 0, 0]), 3, 5, "radar", None) is None


def test_camera_outside_gate_is_ignored():
    radar, cam = [1, 0, 1, 0, 0], [0, 1, 0, 0, 0]
    assert E.find_declaration(log(radar, cam, [0, 10.0, 0, 0, 0]), 3, 5, "radar", SC) is None


def test_camera_alone_cannot_declare_with_radar():
    assert E.find_declaration(log([0, 0, 0, 0, 0], [1, 1, 1, 1, 1]), 3, 5, "radar", SC) is None


def test_camera_only_uses_camera_hits():
    df = log([0] * 5, [1, 1, 1, 0, 0])
    assert E.find_declaration(df, 3, 5, "camera") == 2.0


def test_clopper_pearson_known_values():
    ci = E.clopper_pearson(59, 59, 0.95)
    assert ci["ci_lo"] == pytest.approx(0.9394, abs=1e-3)   # 59/59 is not enough for a 0.95 lower bound
    assert E.clopper_pearson(0, 10)["ci_lo"] == 0.0
    assert math.isnan(E.clopper_pearson(0, 0)["p"])


def test_false_rate_poisson():
    f = E.false_decl_rate(pd.DataFrame({"match": ["false", "real", "false", "excluded"]}), 2.0)
    assert f["false_declarations"] == 2 and f["per_flight_hour"] == 1.0
    assert f["ci_lo"] < 1.0 < f["ci_hi"]


def _enc(r0, speed_ms=50.0, rcpa=300.0, eid="e"):
    t = np.arange(0, r0 / speed_ms, 0.1)
    r = np.maximum(r0 - speed_ms * t, rcpa)
    return E.Encounter(eid, "f", t, r, np.zeros_like(t), np.zeros_like(t))


def test_time_reaching_and_range():
    e = _enc(5000.0)
    assert e.time_reaching(4400.0) == pytest.approx(12.0)
    assert e.time_reaching(6000.0) is None                 # starts inside
    assert E.declaration_range(10.0, e) == pytest.approx(4500.0)


def test_p_decl_counts_per_encounter():
    edges = CFG["range_bins_km"]
    rows = []
    for i, (r0, t_decl) in enumerate([(5000, 5.0), (5000, 50.0), (5000, None), (2000, 1.0)]):
        e = _enc(float(r0), eid=f"e{i}")
        rec = {"t0": e.t0, "start_range_m": e.start_range_m, "t_decl": t_decl}
        for km in sorted(set(edges) | {4.4}):
            rec[f"t_reach_{km:g}km"] = e.time_reaching(km * 1000)
        rows.append(rec)
    enc = pd.DataFrame(rows).apply(pd.to_numeric)
    tgt = E.p_decl_by_range(enc, 4.4)
    assert (tgt["n"], tgt["k"]) == (3, 1)                  # only the one declared at t=5 s (4750 m)
    b = E.p_decl_by_bin(enc, edges).set_index("bin_km")
    assert (b.loc["0.7-1.0", "n"], b.loc["0.7-1.0", "k"]) == (4, 3)
    assert (b.loc["2.5-4.6", "n"], b.loc["2.5-4.6", "k"]) == (3, 2)


def test_config_rejects_bad_values(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(open(os.path.join(os.path.dirname(__file__), "..", "configs", "eval_config.yaml"))
                 .read().replace("m_cued: 2", "m_cued: 4"))
    with pytest.raises(ValueError):
        E.load_config(p)
