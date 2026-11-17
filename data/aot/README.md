# AOT dataset (Amazon Airborne Object Tracking, real camera video with range truth)

**Source:** https://registry.opendata.aws/airborne-object-tracking
**Bucket:** `s3://airborne-obj-detection-challenge-training` (public, `--no-sign-request`)
**License:** CDLA-Permissive-1.0
**Size used here:** label metadata only, `part{1,2,3}/ImageSets/groundtruth.json` (~435 MB each, 1.37 GB total) plus
AOT's `valid_encounters_maxRange700_maxGap3_minEncLen30.json` (3–10 MB each). The ~13 TB of images are **not** needed for E0.

> Raw data is **not** stored in this repo. Download it with `bash scripts/get_aot.sh` (needs the AWS CLI:
> `python3 -m pip install awscli`). Everything under `data/aot/raw/` is ignored by git.
> Then regenerate every file below with:
> ```
> python3 scripts/aot_audit.py --gt data/aot/raw/part*/ImageSets/groundtruth.json   # ~1 min
> python3 scripts/aot_audit_checks.py                                                # ~5 min
> ```
> Both are stdlib-only and deterministic (split seed 2027).

## What we use it for

- **E0** (Pair A): data audit, encounter table, flight-level split, data card (this folder).
- **E2**: real camera detections vs. true range (visible detection range per range bin).
- **Intruder tracks for the radar emulator**: per-frame range / bearing / elevation of crewed intruders.

## Files

| File | What | Key fields |
|---|---|---|
| `DATA_CARD.md` | One-page summary (auto-generated) | headline counts, range-bin tables, split balance, excluded encounters, caveats |
| `encounters.csv` | One row per **camera-view** encounter | `encounter_id` (`<flight>_<camera>_<obj>_<k>`), `phys_id`, `is_primary_view`, `flight_id`, `part`, `camera`, `obj_id`, `cls`, start/end frame + time, `n_frames`, `duration_s`, min/max/median range, `closest_bin`, `farthest_bin`, `n_range_jumps`, `excluded`, `excluded_reason`, frames per planning bin, `split` |
| `frames.csv.gz` (51.6 MB) | One row per labelled frame inside an encounter | `flight_id`, `part`, `camera`, `obj_id`, `cls`, `encounter_id`, `frame`, `time` (ns), `range_m`, `range_bin`, `bearing_deg`, `elevation_deg` (camera frame), `bb_cx`, `bb_cy`, `bb_w`, `bb_h`, `bb_placeholder`, `above_horizon` |
| `range_bins.csv` | Counts per range bin | `phys_*` (headline: physical encounters), `view_*` (camera-view encounters), frames per split, `phys_placeholder_pct` |
| `splits.csv` | Flight-level split | `flight_id`, `split` (fit / tune / heldout), `n_phys_encounters`, `n_view_encounters`, `n_excluded`, `cameras`, `farthest_bin` |
| `qc_report.md` | All QC checks behind the card | file basics, part overlap, cross-camera agreement, camera1 mounting fit, range QC, cross-check vs AOT's encounter list |
| `camera1_offset_per_flight.csv` | Per-flight camera1 yaw/pitch fit | `flight_id`, `n_pairs`, `yaw_deg`, `pitch_deg`, `roll_deg`, `median_resid_deg`, `date_utc` |

Definitions:
- **Camera-view encounter:** a contiguous run of one planned crewed intruder (one that has `range_distance_m`) in one flight and one camera, with gaps of at most 3 frames and at least 30 frames. On ≤700 m this rule reproduces every encounter in AOT's own list, and 99.5% of them frame for frame.
- **Physical encounter (headline):** the camera views of the same flight and object whose time spans overlap. Range stats come from the view with the most frames.
- **Split:** by `flight_id` (never by frame), 60/20/20, stratified by the farthest range bin each flight reaches, seed 2027. Both camera views of a flight always land in the same split; the script asserts this.

## Data card (E0, Nov 16)

- **3257 physical encounters** in 2620 flights (fit 1964 / tune 643 / held-out 650). Classes: Helicopter 2714, Airplane 543.
- **Range covered:** labelled out to **7.7 km**. 1st–99th percentile is 121–5049 m; the minimum is 25 m.
- Beyond 0.7 km by closest range: 204 encounters at 0.7–1.0 km, 197 at 1.0–1.5, 243 at 1.5–2.5, 177 at 2.5–4.6 and 41 beyond 4.6. By "passes through the bin" the counts are 2156, 2078, 1841, 1105 and 204. See `DATA_CARD.md` for frames and the per-split balance.
- **Video:** 10 fps, 2448×2048, 8-bit grayscale, clips of about 120 s, the same across all 4,154 flights.
- **Two cameras:** part1/part2 are camera0. Part3 is camera1, and 716 of its 1531 flights are the same flights as part1/part2. Range truth agrees between the two cameras (median |Δr| about 0.6 m, which is time-offset motion).
- **Excluded:** 7 camera-view encounters with broken range truth (range steps > 300 m/s, ranges near 0 m). They're kept in `encounters.csv` with `excluded=True`. The raw minimums of 0.0, 7.8 and 5.9 m all come from these encounters.

## Caveats to state in the paper

- **Bearing/elevation are in each camera's own frame**, from a pinhole model with an **assumed FOV of 67.8°×56.8°, which must be confirmed**. AOT has no ownship attitude, so there's no horizon- or North-referenced angle.
- **Camera1 mounting** is estimated from labels, not surveyed (`qc_report.md` §4):
  - The global fit puts camera1 at yaw **57.05°** to the right of camera0 (pitch −0.02°, roll −0.60°, median residual 0.27°).
  - **About 13% of flights form a second cluster near 55.3°**, and there's no clean date boundary between the two clusters.
  - Ownship-frame bearings are therefore **not** provided; they're deferred to the Nov 30 intruder-track task. E0 doesn't need the offset, because the radar can be emulated in each camera's own frame.
- **Class only, no aircraft make or model.** Only **planned** intruders (fitted with GPS) have range. Unplanned aircraft, birds and drones without range are excluded. Planned drones (3,662 frames) are excluded as not crewed.
- **Placeholder boxes:**
  - 76% of headline frames have fixed 6×6 or 16×16 px boxes: 79% at 0.7–1.0 km, 95% at 1.0–1.5 km, and ≥98% beyond 1.5 km.
  - So long-range labels **may be GPS-projected positions rather than visually drawn boxes (unverified)**.
  - Box size isn't a range cue, and IoU matching is unreliable, so E1 should match by centre distance.
  - **Visible detection range must come from E2, not from label coverage.**
- AOT's own `valid_encounters` list leaves out about 10–30% of the flights that have ≤700 m encounters (an unpublished selection), so our counts are higher than any figure based on that list.

## Open questions for the lead

1. **Do helicopters count as crewed intruders?** They're 2714 of the 3257 physical encounters (Airplane 543). `DATA_CARD.md` also has an airplane-only range-bin table. Note its held-out set is thin at long range: 1 encounter at 2.5–4.6 km and 2 beyond 4.6 km by closest range.
2. **Which bin-count definition goes in the paper?** "By closest range" counts each encounter once; "every bin passed through" counts it in each bin it crosses.
3. **E1 must use centre-distance matching beyond about 1 km**, because of the placeholder boxes. Please confirm.
4. **Ownship-frame bearings** for the Nov 30 intruder-track task. Option (a): a per-flight offset, possible only for flights seen by both cameras. Option (b): the global 57.05°, with a documented error of about 1.7° on about 13% of flights.
5. Confirm the camera intrinsics (FOV 67.8°×56.8°) with the AOT authors or the paper.
