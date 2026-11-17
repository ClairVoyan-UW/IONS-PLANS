#!/usr/bin/env python3
"""Convert the AOT E0 tables into the evaluation truth format (truth.csv + flights.csv).

  python3 scripts/aot_truth_for_eval.py --out /tmp/aot_eval            # fit + tune flights only

Uses the primary camera view of each physical encounter. range_m is AOT's GPS-derived range;
az_deg/el_deg are bearing/elevation in that camera's own frame (see data/aot/README.md caveats), so the
tracks scored against this truth must use the same camera frame. Held-out flights are skipped unless
--allow-heldout is given. Flight duration is the AOT clip length (about 120 s, DATA_CARD.md).
"""
import argparse
import os

import pandas as pd

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "aot")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frames", default=os.path.join(ROOT, "frames.csv.gz"))
    ap.add_argument("--encounters", default=os.path.join(ROOT, "encounters.csv"))
    ap.add_argument("--splits", default=os.path.join(ROOT, "splits.csv"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--include-splits", default="fit,tune")
    ap.add_argument("--allow-heldout", action="store_true")
    ap.add_argument("--clip-s", type=float, default=120.0)
    a = ap.parse_args()

    want = {s.strip() for s in a.include_splits.split(",")}
    if "heldout" in want and not a.allow_heldout:
        ap.error("held-out flights requested without --allow-heldout")
    enc = pd.read_csv(a.encounters)
    enc = enc[(enc["is_primary_view"] == 1) & enc["split"].isin(want)]
    fr = pd.read_csv(a.frames, usecols=["flight_id", "encounter_id", "time", "range_m",
                                        "bearing_deg", "elevation_deg"])
    fr = fr[fr["encounter_id"].isin(set(enc["encounter_id"]))]
    t_ref = fr.groupby("flight_id")["time"].transform("min")
    truth = pd.DataFrame({"flight_id": fr["flight_id"], "encounter_id": fr["encounter_id"],
                          "t_s": (fr["time"] - t_ref) / 1e9, "range_m": fr["range_m"],
                          "az_deg": fr["bearing_deg"], "el_deg": fr["elevation_deg"]})
    truth["excluded"] = truth["encounter_id"].map(enc.set_index("encounter_id")["excluded"]).astype(bool)

    sp = pd.read_csv(a.splits)
    sp = sp[sp["flight_id"].isin(set(truth["flight_id"]))]
    flights = pd.DataFrame({"flight_id": sp["flight_id"], "duration_s": a.clip_s, "split": sp["split"]})

    os.makedirs(a.out, exist_ok=True)
    truth.to_csv(os.path.join(a.out, "truth.csv"), index=False)
    flights.to_csv(os.path.join(a.out, "flights.csv"), index=False)
    print(f"wrote {len(truth)} truth rows, {truth.encounter_id.nunique()} encounters, "
          f"{len(flights)} flights ({sorted(want)}) to {a.out}")


if __name__ == "__main__":
    main()
