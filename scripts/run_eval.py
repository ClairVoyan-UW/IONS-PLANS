#!/usr/bin/env python3
"""Score all four configurations with the frozen rules in configs/eval_config.yaml.

Usage:
  python3 scripts/run_eval.py --data <dir> --out results/eval/<run_name>
  python3 scripts/run_eval.py --data <dir> --out ... --configs radar_only     # a pair with only some inputs

<dir> holds truth.csv, flights.csv and tracks_<configuration>.csv (schema in eval/README.md).
Held-out flights are skipped unless --allow-heldout is given (repo rule: nobody opens them before Dec 1).

Writes to --out:
  p_decl_by_bin.csv   configuration x speed case x range bin: n, k, P_decl, Clopper–Pearson CI
  target.csv          configuration x speed case: P(declared by target range), CI, pass/fail
  false_decl.csv      configuration: false declarations, flight hours, rate per flight hour, Poisson CI
  per_encounter.csv   one row per (configuration, encounter): declaration time, R_d, speed case, edge times
  declared_tracks.csv every confirmed track and whether it matched a real intruder
  run_meta.json       config SHA-256, git commit, inputs, splits, package versions
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd
import scipy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daa_eval as E  # noqa: E402


def _git(*args):
    try:
        return subprocess.check_output(["git", *args], cwd=os.path.dirname(os.path.abspath(__file__)),
                                       stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(os.path.dirname(__file__), "..", "configs", "eval_config.yaml"))
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--configs", default=",".join(E.CONFIGURATIONS),
                    help="comma-separated subset of: " + ",".join(E.CONFIGURATIONS))
    ap.add_argument("--splits", default="fit,tune", help="flight splits to score (default fit,tune)")
    ap.add_argument("--allow-heldout", action="store_true")
    a = ap.parse_args(argv)

    cfg = E.load_config(a.config)
    names = [s.strip() for s in a.configs.split(",") if s.strip()]
    bad = [n for n in names if n not in E.CONFIGURATIONS]
    if bad:
        ap.error(f"unknown configuration(s): {bad}")
    splits = {s.strip() for s in a.splits.split(",") if s.strip()}
    if "heldout" in splits and not a.allow_heldout:
        ap.error("held-out flights requested; pass --allow-heldout only when the team has agreed to open them")

    flights = pd.read_csv(os.path.join(a.data, "flights.csv"))
    if "split" in flights.columns:
        flights = flights[flights["split"].isin(splits)]
    keep = set(flights["flight_id"])
    truth_df = pd.read_csv(os.path.join(a.data, "truth.csv"))
    truth_df = truth_df[truth_df["flight_id"].isin(keep)]
    truth = E.load_truth(truth_df)
    flight_hours = flights["duration_s"].sum() / 3600.0

    edges = cfg["range_bins_km"]
    conf = cfg["p_decl"]["confidence"]
    lead = cfg["p_decl"]["min_lead_s"]
    tgt = cfg["target"]
    speed_groups = [("all", None)] + [(f"{s}kt", s) for s in cfg["closing_speeds_kt"]]

    bins_out, tgt_out, fa_out, enc_out, decl_out, inputs = [], [], [], [], [], {}
    for name in names:
        path = os.path.join(a.data, f"tracks_{name}.csv")
        if not os.path.exists(path):
            sys.exit(f"missing {path} (use --configs to score only the configurations you have)")
        tracks = pd.read_csv(path)
        tracks = tracks[tracks["flight_id"].isin(keep)]
        inputs[name] = {"file": path, "rows": int(len(tracks)),
                        "tracks": int(tracks.groupby(["flight_id", "track_id"]).ngroups)}

        per_enc, declared = E.score_configuration(tracks, truth, cfg, name)
        enc_out.append(per_enc)
        declared.insert(0, "configuration", name)
        decl_out.append(declared)

        for label, s in speed_groups:
            sub = per_enc if s is None else per_enc[per_enc["speed_case_kt"] == s]
            b = E.p_decl_by_bin(sub, edges, conf, lead)
            b.insert(0, "speed", label)
            b.insert(0, "configuration", name)
            bins_out.append(b)
            t = E.p_decl_by_range(sub, tgt["range_km"], conf, lead)
            t.update({"configuration": name, "speed": label, "target_p": tgt["p_decl_min"],
                      "pass_point": bool(t["n"] > 0 and t["p"] >= tgt["p_decl_min"]),
                      "pass_ci_lower": bool(t["n"] > 0 and t["ci_lo"] >= tgt["p_decl_min"])})
            tgt_out.append(t)
        f = E.false_decl_rate(declared, flight_hours, cfg["false_declarations"]["confidence"])
        f["configuration"] = name
        fa_out.append(f)

    os.makedirs(a.out, exist_ok=True)
    bins = pd.concat(bins_out, ignore_index=True)
    target = pd.DataFrame(tgt_out)[["configuration", "speed", "range_km", "n", "k", "p", "ci_lo", "ci_hi",
                                    "target_p", "pass_point", "pass_ci_lower"]]
    false = pd.DataFrame(fa_out)[["configuration", "false_declarations", "flight_hours",
                                  "per_flight_hour", "ci_lo", "ci_hi"]]
    bins.to_csv(os.path.join(a.out, "p_decl_by_bin.csv"), index=False)
    target.to_csv(os.path.join(a.out, "target.csv"), index=False)
    false.to_csv(os.path.join(a.out, "false_decl.csv"), index=False)
    pd.concat(enc_out, ignore_index=True).to_csv(os.path.join(a.out, "per_encounter.csv"), index=False)
    pd.concat(decl_out, ignore_index=True).to_csv(os.path.join(a.out, "declared_tracks.csv"), index=False)

    meta = {"run_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "config_file": os.path.abspath(a.config), "config_sha256": cfg["_sha256"],
            "config_version": cfg.get("version"), "git_commit": _git("rev-parse", "HEAD"),
            "git_dirty": bool(_git("status", "--porcelain")), "git_tags": _git("tag", "--points-at", "HEAD"),
            "data_dir": os.path.abspath(a.data), "splits": sorted(splits), "flights": len(keep),
            "flight_hours": flight_hours,
            "encounters_counted": int(sum(not e.excluded for v in truth.values() for e in v)),
            "inputs": inputs, "python": sys.version.split()[0], "numpy": np.__version__,
            "pandas": pd.__version__, "scipy": scipy.__version__}
    with open(os.path.join(a.out, "run_meta.json"), "w") as f:
        json.dump(meta, f, indent=2)

    pd.set_option("display.width", 160)
    pd.set_option("display.max_columns", 20)
    fmt = {"p": "{:.3f}", "ci_lo": "{:.3f}", "ci_hi": "{:.3f}"}
    print(f"config {cfg.get('version')} sha256 {cfg['_sha256'][:12]}  |  {len(keep)} flights, "
          f"{flight_hours:.2f} h, {meta['encounters_counted']} encounters, splits {sorted(splits)}")
    print("\nP_decl by bin (all speeds):")
    view = bins[bins["speed"] == "all"][["configuration", "bin_km", "n", "k", "p", "ci_lo", "ci_hi"]]
    print(view.to_string(index=False, formatters={k: v.format for k, v in fmt.items()}))
    print(f"\nTarget: P_decl >= {tgt['p_decl_min']} by {tgt['range_km']} km")
    print(target.to_string(index=False, formatters={k: v.format for k, v in fmt.items()}))
    print("\nFalse declarations per flight hour:")
    print(false.to_string(index=False))
    if meta["git_dirty"] or not meta["git_tags"]:
        print("\nNOTE: run is not from a clean, tagged commit; record it as a dry run, not a frozen result.")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
