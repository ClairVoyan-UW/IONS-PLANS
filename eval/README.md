# Evaluation (frozen definitions)

One config, one set of scoring functions, four configurations. Once tagged `eval-freeze-v1`, these rules
don't change. A change means rerunning every configuration on every dataset.

| File | What |
|---|---|
| [`configs/eval_config.yaml`](../configs/eval_config.yaml) | **Every number**: M-of-N, camera gate, association gates, range bins, target, speeds, CI levels |
| [`scripts/daa_eval.py`](../scripts/daa_eval.py) | Scoring functions: `find_declaration`, `declaration_range`, `p_decl_by_bin`, `p_decl_by_range`, `false_decl_rate` |
| [`scripts/run_eval.py`](../scripts/run_eval.py) | Runs all four configurations through the same functions and writes the result tables |
| [`scripts/make_synthetic_eval_data.py`](../scripts/make_synthetic_eval_data.py) | Made-up inputs for a smoke test (not results) |
| [`scripts/aot_truth_for_eval.py`](../scripts/aot_truth_for_eval.py) | AOT E0 tables → `truth.csv` + `flights.csv` |
| [`tests/test_daa_eval.py`](../tests/test_daa_eval.py) | Unit tests for the rules |

```
python3 -m pip install -r requirements-eval.txt
python3 -m pytest tests/
```

## Definitions (summary; the config is authoritative)

- **Declaration** = first confirmed track. A track is confirmed at the first declaring-sensor scan that is a hit
  and whose last N scans hold ≥ M hits (3-of-5). The camera may shorten this to `m_cued` radar hits, but only
  when a camera detection lies within `gate_deg` of the radar track's direction. The declaring scan is always a
  radar hit, and camera detections outside the gate are ignored. Camera-only applies the same M-of-N to
  camera frames.
- **R_d** = true (GPS) range at the declaration time, interpolated from truth.
- **P_decl(r)**, counted **per encounter**. For bin [lo, hi): n = approaching encounters whose truth starts at or
  beyond hi and later reaches lo; k = those declared no later than first reaching lo. Two-sided 95%
  Clopper–Pearson interval.
- **Target**: P_decl ≥ 0.95 by 4.4 km. `pass_point` uses the point estimate and `pass_ci_lower` uses the CP
  lower bound; both are reported. Note: with zero misses you need **≥ 59 eligible encounters** before the
  lower bound can reach 0.95. With one miss you need about 93.
- **False declarations per flight hour** = confirmed tracks matching no truth intruder ÷ flight hours, with an
  exact Poisson CI. A track matching an excluded (QC-failed) encounter counts as neither real nor false.
- **Speeds**: each encounter is assigned to 100 / 130 / 170 kt by its mean closing speed on approach (±15 kt),
  and results are reported per speed and for all encounters together.

## Input format (one folder per dataset)

`truth.csv`: one row per truth sample.

| column | |
|---|---|
| `flight_id`, `encounter_id` | one encounter = one intruder pass |
| `t_s` | seconds, same clock as the track files |
| `range_m`, `az_deg`, `el_deg` | true range and direction from GPS (or give `own_e,own_n,own_u,intr_e,intr_n,intr_u` in metres and they are computed) |
| `excluded` | optional; True = QC-failed, not counted |

`flights.csv`: `flight_id`, `duration_s` (sensor-on time, used for flight hours), `split` (fit / tune / heldout).

`tracks_<configuration>.csv` for `camera_only`, `radar_only`, `radar_cued_camera`, `full_fusion`: **one row
per scan of the declaring sensor, missed scans included** (hit = 0), from the track's first hit onward.

| column | |
|---|---|
| `flight_id`, `track_id`, `t_s` | |
| `radar_hit`, `radar_az_deg`, `radar_el_deg` | radar detection associated to this track in this scan (empty for camera-only) |
| `cam_hit`, `cam_az_deg`, `cam_el_deg` | best camera detection for this track since the previous scan (empty for radar-only) |
| `est_az_deg`, `est_el_deg` | the track's estimated direction = centre of the radar gate (camera direction for camera-only) |
| `est_range_m` | the track's range estimate (empty if the configuration has none) |

Angles must be in the same frame as the truth (for AOT: each camera's own frame).

## Running it

```
# smoke test on made-up data (~3 s)
python3 scripts/make_synthetic_eval_data.py --out /tmp/eval_synth
python3 scripts/run_eval.py --data /tmp/eval_synth --out /tmp/eval_synth/results

# Pair A: AOT truth geometry with synthetic detections (~20 s); replace the tracks_*.csv with real ones from E2
python3 scripts/aot_truth_for_eval.py --out /tmp/aot_eval
python3 scripts/make_synthetic_eval_data.py --truth /tmp/aot_eval/truth.csv --flights /tmp/aot_eval/flights.csv --out /tmp/aot_eval
python3 scripts/run_eval.py --data /tmp/aot_eval --out results/eval/pairA_dryrun

# Pair B: AERPAW truth + radar tracks, only the configurations you have
python3 scripts/run_eval.py --data <aerpaw_eval_dir> --out results/eval/pairB_dryrun --configs radar_only
```

Held-out flights are refused unless `--allow-heldout` is passed. Every run writes `run_meta.json` with the
config SHA-256 and git commit. A run that isn't from a clean, tagged commit prints a warning and counts as a
dry run.

## Things to note from the first dry run (AOT fit+tune geometry, synthetic detections)

- Only **158** AOT encounters start beyond 4.4 km and reach it (84 / 41 / 5 at 100 / 130 / 170 kt). The 95%
  target can be shown with a CP lower bound only at 100 kt, and only if almost nothing is missed. The 170 kt
  case must come from simulation.
- **`min_lead_s` matters a lot.** With 0 s, encounters whose truth starts just beyond 4.4 km have no time to
  collect 3 hits, and radar-only drops to 0.83. With 5 s (= N scans at 1 Hz) it is 0.97. Decide this before the
  freeze.
- AOT truth covers only planned (GPS-fitted) intruders, so a real camera track on an unplanned aircraft counts
  as a false declaration. State this, or exclude those tracks with a documented rule.
