#!/usr/bin/env python3
"""E0 (Pair A) — AOT data audit.

Reads AOT groundtruth.json file(s) (metadata only, no images needed) and writes:
  data/aot/frames.csv.gz     per-frame range / bearing / elevation of every crewed planned intruder
  data/aot/encounters.csv    one row per camera-view encounter (phys_id links views of the same physical encounter)
  data/aot/range_bins.csv    counts per range bin (physical encounters and camera-view encounters)
  data/aot/splits.csv        flight-level fit / tune / heldout split
  data/aot/DATA_CARD.md      one-page data card (auto-filled numbers; edit the TODO lines by hand)

Usage:
  python3 scripts/aot_audit.py --gt data/aot/raw/part*/ImageSets/groundtruth.json

Stdlib only. Bearing/elevation are in each CAMERA's own frame (relative to its boresight), from a pinhole
model built from the field of view. camera0 and camera1 point in different directions, so never mix their
bearings. Confirm the FOV/intrinsics with the lead before using them in E2/E5.

The same flight_id can appear in several parts (part3 = camera1, recorded on some of the same flights as
part1/part2 = camera0). Frames and encounters are keyed by (part, flight_id); the split is keyed by
flight_id only, so both camera views of a flight always land in the same split.
"""
import argparse, csv, gzip, json, math, os, random, re, statistics
from collections import Counter, defaultdict

BINS_KM = [(0.0, 0.7, "<0.7"), (0.7, 1.0, "0.7-1.0"), (1.0, 1.5, "1.0-1.5"),
           (1.5, 2.5, "1.5-2.5"), (2.5, 4.6, "2.5-4.6"), (4.6, 1e9, ">4.6")]
PLAN_BINS = ["0.7-1.0", "1.0-1.5", "1.5-2.5", "2.5-4.6"]
ORDER = [b for _, _, b in BINS_KM]


def range_bin(r_m):
    km = r_m / 1000.0
    for lo, hi, name in BINS_KM:
        if lo <= km < hi:
            return name
    return ">4.6"


def obj_class(obj_id):
    return re.sub(r"\d+$", "", obj_id or "")


def iter_samples(gt):
    s = gt["samples"]
    items = s.items() if isinstance(s, dict) else ((x.get("metadata", {}).get("data_path", str(i)), x) for i, x in enumerate(s))
    for fid, sample in items:
        yield fid, sample


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", nargs="+", required=True, help="groundtruth.json file(s)")
    ap.add_argument("--out-dir", default="data/aot")
    ap.add_argument("--classes", default="Airplane,Helicopter", help="crewed classes to keep (comma list)")
    ap.add_argument("--hfov", type=float, default=67.8, help="horizontal FOV deg (CONFIRM)")
    ap.add_argument("--vfov", type=float, default=56.8, help="vertical FOV deg (CONFIRM)")
    ap.add_argument("--max-gap", type=int, default=3, help="max missing frames inside one encounter")
    ap.add_argument("--min-len", type=int, default=30, help="min frames for an encounter")
    ap.add_argument("--max-range-rate", type=float, default=300.0,
                    help="m/s; range steps faster than this are counted as QC glitches (flagged, not removed)")
    ap.add_argument("--split", default="0.6,0.2,0.2")
    ap.add_argument("--seed", type=int, default=2027)
    a = ap.parse_args()
    keep = set(a.classes.split(","))
    os.makedirs(a.out_dir, exist_ok=True)

    frames = []                     # per-frame rows
    # (part, flight_id) -> (fps, W, H, camera). The same flight_id appears in two parts when both cameras
    # recorded it (part3 = camera1, overlapping part1/part2 = camera0), so flight_id alone is not unique.
    flight_meta = {}
    all_classes = Counter()
    for path in a.gt:
        part = os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(path))))
        with open(path) as f:
            gt = json.load(f)
        camera = gt.get("metadata", {}).get("description", part)
        for fid, sample in iter_samples(gt):
            md = sample.get("metadata", {})
            res = md.get("resolution", {})
            W, H = res.get("width", 2448), res.get("height", 2048)
            fps = md.get("fps", 10.0)
            flight_meta[(part, fid)] = (fps, W, H, camera)
            fx = (W / 2) / math.tan(math.radians(a.hfov / 2))
            fy = (H / 2) / math.tan(math.radians(a.vfov / 2))
            for e in sample.get("entities", []):
                oid = e.get("id")
                if not oid:
                    continue
                cls = obj_class(oid)
                r = (e.get("blob") or {}).get("range_distance_m")
                planned = r is not None and not (isinstance(r, float) and math.isnan(r))
                all_classes[(cls, planned)] += 1
                if cls not in keep or not planned or not e.get("bb"):
                    continue
                left, top, w, h = e["bb"]          # AOT order: [left, top, width, height] — sanity-checked below
                cx, cy = left + w / 2, top + h / 2
                bearing = math.degrees(math.atan((cx - W / 2) / fx))      # + = right of boresight
                elev = math.degrees(math.atan((H / 2 - cy) / fy))         # + = above boresight
                frames.append(dict(flight_id=fid, part=part, camera=camera, obj_id=oid, cls=cls,
                                   frame=int(e["blob"]["frame"]), time=e.get("time"),
                                   range_m=float(r), bearing_deg=round(bearing, 4), elevation_deg=round(elev, 4),
                                   bb_cx=round(cx, 1), bb_cy=round(cy, 1), bb_w=w, bb_h=h,
                                   # AOT's fixed-size boxes for small targets: size carries no information
                                   bb_placeholder=w == h and w in (6, 16),
                                   above_horizon=(e.get("labels") or {}).get("is_above_horizon"),
                                   range_bin=range_bin(float(r))))

    def meta(f):
        return flight_meta[(f["part"], f["flight_id"])]

    bad = sum(1 for f in frames if not (0 <= f["bb_cx"] <= meta(f)[1] and 0 <= f["bb_cy"] <= meta(f)[2]))
    if bad:
        print(f"WARNING: {bad} boxes have centre outside the image — check bbox order [left,top,w,h]")

    # ---- camera-view encounters: contiguous runs per (part, flight, object), gaps <= max_gap frames
    by_obj = defaultdict(list)
    for f in frames:
        by_obj[(f["part"], f["flight_id"], f["obj_id"])].append(f)
    encounters, enc_rows = [], {}
    for (part, fid, oid), rows in sorted(by_obj.items()):
        rows.sort(key=lambda x: x["frame"])
        fps, _, _, camera = flight_meta[(part, fid)]
        runs, cur = [], [rows[0]]
        for r in rows[1:]:
            if r["frame"] - cur[-1]["frame"] <= a.max_gap + 1:
                cur.append(r)
            else:
                runs.append(cur); cur = [r]
        runs.append(cur)
        k = 0
        for run in runs:
            if len(run) < a.min_len:
                for r in run: r["encounter_id"] = ""
                continue
            eid = f"{fid}_{camera}_{oid}_{k}"; k += 1
            rng = [r["range_m"] for r in run]
            for r in run: r["encounter_id"] = eid
            bins_hit = Counter(r["range_bin"] for r in run)
            # QC: range steps faster than a.max_range_rate m/s are physically impossible -> broken range truth
            n_jumps = sum(abs(b["range_m"] - x["range_m"]) * fps / (b["frame"] - x["frame"]) > a.max_range_rate
                          for x, b in zip(run, run[1:]))
            enc_rows[eid] = run
            encounters.append(dict(encounter_id=eid, phys_id="", is_primary_view=0, flight_id=fid, part=part,
                                   camera=camera, obj_id=oid, cls=run[0]["cls"],
                                   start_frame=run[0]["frame"], end_frame=run[-1]["frame"],
                                   start_time=run[0]["time"], end_time=run[-1]["time"],
                                   n_frames=len(run), duration_s=round((run[-1]["frame"] - run[0]["frame"] + 1) / fps, 1),
                                   min_range_m=round(min(rng), 1), max_range_m=round(max(rng), 1),
                                   median_range_m=round(statistics.median(rng), 1),
                                   closest_bin=range_bin(min(rng)), farthest_bin=range_bin(max(rng)),
                                   n_range_jumps=n_jumps, excluded=n_jumps > 0,
                                   excluded_reason=f"range_jump>{a.max_range_rate:g}m/s x{n_jumps}" if n_jumps else "",
                                   **{f"frames_{b}": bins_hit.get(b, 0) for b in PLAN_BINS}))
    # Excluded encounters stay in encounters.csv / frames.csv.gz but are left out of every count below.
    kept = [e for e in encounters if not e["excluded"]]
    excluded = [e for e in encounters if e["excluded"]]

    # ---- physical encounters: merge camera views of the same (flight, object) whose time spans overlap or
    # are within max_gap frames of each other (cameras are not frame-synced, so compare times, not frame indices).
    # Range stats of a physical encounter come from the view with the most frames (primary view).
    by_fo = defaultdict(list)
    for e in kept:
        by_fo[(e["flight_id"], e["obj_id"])].append(e)
    phys = []
    for (fid, oid), es in sorted(by_fo.items()):
        es.sort(key=lambda e: e["start_time"])
        tol = int((a.max_gap + 1) / 10.0 * 1e9)    # ns; AOT is 10 fps everywhere (checked in the data card)
        groups, cur, cur_end = [], [es[0]], es[0]["end_time"]
        for e in es[1:]:
            if e["start_time"] <= cur_end + tol:
                cur.append(e); cur_end = max(cur_end, e["end_time"])
            else:
                groups.append(cur); cur, cur_end = [e], e["end_time"]
        groups.append(cur)
        for k, g in enumerate(groups):
            pid = f"{fid}_{oid}_{k}"
            prim = max(g, key=lambda e: (e["n_frames"], e["camera"] == "camera0"))
            for e in g:
                e["phys_id"] = pid; e["is_primary_view"] = int(e is prim)
            phys.append(dict(phys_id=pid, flight_id=fid, obj_id=oid, cls=prim["cls"], n_views=len(g),
                             cameras="+".join(sorted({e["camera"] for e in g})), primary=prim["encounter_id"],
                             closest_bin=prim["closest_bin"], farthest_bin=prim["farthest_bin"],
                             min_range_m=prim["min_range_m"], max_range_m=prim["max_range_m"]))

    # ---- flight-level split (keyed by flight_id ONLY: both camera views share a split),
    # stratified by the farthest bin any encounter in the flight reaches. Every flight gets a split (excluded
    # encounters included, so the assignment does not depend on QC); all split *stats* use kept encounters only.
    flights = defaultdict(list)
    for e in encounters:
        flights[e["flight_id"]].append(e)
    kept_flights = {e["flight_id"] for e in kept}
    strata = defaultdict(list)
    for fid, es in flights.items():
        strata[max((e["farthest_bin"] for e in es), key=ORDER.index)].append(fid)
    fr = [float(x) for x in a.split.split(",")]
    rnd = random.Random(a.seed)
    assign = {}
    for s in sorted(strata):
        ids = sorted(strata[s]); rnd.shuffle(ids)
        n = len(ids); n_fit = round(n * fr[0]); n_tune = round(n * fr[1])
        for i, fid in enumerate(ids):
            assign[fid] = "fit" if i < n_fit else ("tune" if i < n_fit + n_tune else "heldout")
    for e in encounters:
        e["split"] = assign[e["flight_id"]]
    for p in phys:
        p["split"] = assign[p["flight_id"]]
    seen = defaultdict(set)
    for e in encounters:
        seen[e["flight_id"]].add(e["split"])
    assert all(len(v) == 1 for v in seen.values()), "a flight_id landed in more than one split"

    # ---- write outputs
    fcols = ["flight_id", "part", "camera", "obj_id", "cls", "encounter_id", "frame", "time", "range_m", "range_bin",
             "bearing_deg", "elevation_deg", "bb_cx", "bb_cy", "bb_w", "bb_h", "bb_placeholder", "above_horizon"]
    with gzip.open(os.path.join(a.out_dir, "frames.csv.gz"), "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fcols, extrasaction="ignore"); w.writeheader()
        w.writerows(fr_ for fr_ in frames if fr_.get("encounter_id"))
    if encounters:
        with open(os.path.join(a.out_dir, "encounters.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(encounters[0].keys())); w.writeheader(); w.writerows(encounters)
    with open(os.path.join(a.out_dir, "splits.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["flight_id", "split", "n_phys_encounters", "n_view_encounters", "n_excluded",
                                       "cameras", "farthest_bin"])
        n_phys = Counter(p["flight_id"] for p in phys)
        for fid in sorted(assign):
            es = flights[fid]
            w.writerow([fid, assign[fid], n_phys[fid], sum(not e["excluded"] for e in es), sum(e["excluded"] for e in es),
                        "+".join(sorted({e["camera"] for e in es})), max((e["farthest_bin"] for e in es), key=ORDER.index)])

    kept_ids = {e["encounter_id"] for e in kept}
    enc_frames = [f for f in frames if f.get("encounter_id") in kept_ids]
    prim_ids = {p["primary"] for p in phys}
    prim_frames = [f for f in enc_frames if f["encounter_id"] in prim_ids]
    bin_rows = []
    for _, _, b in BINS_KM:
        bin_rows.append(dict(range_bin=b,
                             phys_closest_in_bin=sum(p["closest_bin"] == b for p in phys),
                             phys_with_frames_in_bin=len({f["encounter_id"] for f in prim_frames if f["range_bin"] == b}),
                             phys_frames_in_bin=sum(f["range_bin"] == b for f in prim_frames),
                             **{f"phys_frames_{s}": sum(f["range_bin"] == b and assign[f["flight_id"]] == s for f in prim_frames)
                                for s in ("fit", "tune", "heldout")},
                             **{f"phys_closest_{s}": sum(p["closest_bin"] == b and p["split"] == s for p in phys)
                                for s in ("fit", "tune", "heldout")},
                             phys_placeholder_pct=round(100 * sum(f["range_bin"] == b and f["bb_placeholder"] for f in prim_frames)
                                                        / max(1, sum(f["range_bin"] == b for f in prim_frames)), 1),
                             view_closest_in_bin=sum(e["closest_bin"] == b for e in kept),
                             view_with_frames_in_bin=len({f["encounter_id"] for f in enc_frames if f["range_bin"] == b}),
                             view_frames_in_bin=sum(f["range_bin"] == b for f in enc_frames)))
    with open(os.path.join(a.out_dir, "range_bins.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(bin_rows[0].keys())); w.writeheader(); w.writerows(bin_rows)

    # ---- data card
    rng_all = [f["range_m"] for f in prim_frames] or [float("nan")]
    pct = statistics.quantiles(rng_all, n=100) if len(rng_all) > 1 else [float("nan")] * 99
    used = {(e["part"], e["flight_id"]) for e in encounters}
    fps_set = sorted({flight_meta[k][0] for k in used})
    res_set = sorted({flight_meta[k][1:3] for k in used})
    split_ct = Counter(assign[f] for f in kept_flights)
    phys_split = Counter(p["split"] for p in phys)
    view_split = Counter(e["split"] for e in kept)
    cls_ct = Counter(p["cls"] for p in phys)
    cam_ct = Counter(p["cameras"] for p in phys)
    view_cam = Counter(e["camera"] for e in kept)
    n_drone = sum(v for (c, planned), v in all_classes.items() if c == "Drone" and planned)
    excl_list = "\n".join(f"- `{e['encounter_id']}` ({e['excluded_reason']}, range {e['min_range_m']}–{e['max_range_m']} m)"
                          for e in excluded) or "- none"
    tbl = "\n".join(f"| {r['range_bin']} | {r['phys_closest_in_bin']} | {r['phys_with_frames_in_bin']} | "
                    f"{r['phys_frames_in_bin']} | {r['phys_frames_fit']} / {r['phys_frames_tune']} / {r['phys_frames_heldout']} | "
                    f"{r['phys_placeholder_pct']}% | {r['view_closest_in_bin']} | {r['view_with_frames_in_bin']} |" for r in bin_rows)
    plc = {r["range_bin"]: r["phys_placeholder_pct"] for r in bin_rows}
    air = [p for p in phys if p["cls"] == "Airplane"]
    air_prim = {p["primary"] for p in air}
    air_frames = [f for f in prim_frames if f["encounter_id"] in air_prim]
    tbl_air = "\n".join(f"| {b} | {sum(p['closest_bin'] == b for p in air)} | "
                        f"{len({f['encounter_id'] for f in air_frames if f['range_bin'] == b})} | "
                        f"{sum(f['range_bin'] == b for f in air_frames)} | "
                        f"{' / '.join(str(sum(p['closest_bin'] == b and p['split'] == s for p in air)) for s in ('fit', 'tune', 'heldout'))} |"
                        for _, _, b in BINS_KM)
    tbl_split = "\n".join(f"| {r['range_bin']} | {r['phys_closest_fit']} / {r['phys_closest_tune']} / {r['phys_closest_heldout']} | "
                          f"{r['phys_frames_fit']} / {r['phys_frames_tune']} / {r['phys_frames_heldout']} |" for r in bin_rows)
    card = f"""# AOT data card (E0, Pair A)

Generated by `scripts/aot_audit.py` (seed {a.seed}, max_gap {a.max_gap}, min_len {a.min_len}, classes {a.classes}).
Source: Amazon Airborne Object Tracking (AOT), https://registry.opendata.aws/airborne-object-tracking (CDLA-Permissive-1.0).
Inputs: {', '.join(os.path.relpath(p) for p in a.gt)}

All counts below exclude QC-failed encounters (see "Excluded encounters").

| Item | Value |
|---|---|
| Flights with ≥1 crewed planned encounter | {len(kept_flights)} |
| **Physical encounters** (camera views merged) | **{len(phys)}** (fit {phys_split['fit']} / tune {phys_split['tune']} / held-out {phys_split['heldout']}) |
| Seen by | {', '.join(f'{k}: {v}' for k, v in sorted(cam_ct.items()))} |
| Camera-view encounters | {len(kept)} ({', '.join(f'{k} {v}' for k, v in sorted(view_cam.items()))}; fit {view_split['fit']} / tune {view_split['tune']} / held-out {view_split['heldout']}) |
| Flights per split | fit {split_ct['fit']} / tune {split_ct['tune']} / held-out {split_ct['heldout']} |
| Frames in physical encounters (primary view) | {len(prim_frames)} (all camera views: {len(enc_frames)}) |
| Range: min / 1st pct / 99th pct / farthest labelled range | {min(rng_all):.0f} / {pct[0]:.0f} / {pct[98]:.0f} / {max(rng_all):.0f} m |
| Frame rate | {', '.join(f'{x:g}' for x in fps_set)} fps |
| Resolution | {', '.join(f'{w}×{h}' for w, h in res_set)}, 8-bit grayscale |
| Intruder types | {', '.join(f'{k}: {v}' for k, v in cls_ct.most_common())} physical encounters (AOT gives class only, no make/model) |
| Excluded by range QC | {len(excluded)} camera-view encounters with range steps > {a.max_range_rate:g} m/s (kept in encounters.csv with `excluded=True`) |
| Drones | {n_drone} planned Drone frames exist in AOT; excluded (not crewed) |
| Bearing/elevation | each camera's own frame, pinhole from FOV {a.hfov}°×{a.vfov}° — TODO confirm intrinsics; camera1 mounting not applied (see qc_report.md) |

## Encounters by range bin

Headline = physical encounters (one per intruder pass, camera views merged; frames from the view with the most frames).
"Closest in bin" counts each encounter once, by its minimum range. "With frames in bin" counts an encounter in every bin it passes through.
"Placeholder" = share of the bin's frames whose box is a fixed 6×6 or 16×16 px placeholder.

| Bin (km) | Physical (closest in bin) | Physical (any frames in bin) | Frames | Frames fit / tune / held-out | Placeholder | Camera-view (closest) | Camera-view (any frames) |
|---|---|---|---|---|---|---|---|
{tbl}

## Split balance per bin (physical encounters)

| Bin (km) | Encounters (closest in bin) fit / tune / held-out | Frames fit / tune / held-out |
|---|---|---|
{tbl_split}

## Airplane-only encounters by range bin (in case helicopters are not counted as crewed)

| Bin (km) | Physical (closest in bin) | Physical (any frames in bin) | Frames | Closest-in-bin fit / tune / held-out |
|---|---|---|---|---|
{tbl_air}

## Excluded encounters
{excl_list}

## Definitions
- Camera-view encounter: contiguous run of one planned crewed intruder (`range_distance_m` present) in one flight and one camera, gaps ≤ {a.max_gap} frames, ≥ {a.min_len} frames.
- Physical encounter: camera-view encounters of the same flight and object whose time spans overlap or are within {a.max_gap + 1} frames.
- Split is by flight_id (never by frame; both cameras of a flight share a split), stratified by the farthest range bin each flight reaches, fixed seed.

## Caveats (TODO: review)
- Ownship attitude is not in AOT, so bearing/elevation are relative to each camera's boresight, not the horizon/North.
- camera1 points ~57° to the right of camera0. Estimated from labels (not surveyed; `qc_report.md`): global fit yaw 57.05° (median residual 0.27°), but ~13% of flights form a second cluster near 55.3° with no clean date boundary. Ownship-frame bearings are therefore NOT provided; they are deferred to the Nov 30 intruder-track task. E0 does not need the offset: the radar can be emulated in each camera's own frame.
- Long-range labels are mostly placeholder boxes ({plc['0.7-1.0']:.0f}% at 0.7–1.0 km, {plc['1.0-1.5']:.0f}% at 1.0–1.5 km, ≥{min(plc['1.5-2.5'], plc['2.5-4.6'], plc['>4.6']):.0f}% beyond 1.5 km), so they may be GPS-projected positions rather than visually drawn boxes (unverified). The farthest labelled range is NOT a visible-detection range: that must come from E2, not from label coverage.
- Range truth comes from the intruder/ownship GPS logging done by the AOT team; non-planned objects have no range and are excluded.
- Long-range bins depend on whether the box is still labelled at that range; frames with range but no box are excluded.
- Most boxes are fixed-size placeholders ({sum(f['bb_placeholder'] for f in prim_frames) / max(1, len(prim_frames)):.0%} of headline frames are 6×6 or 16×16 px; `bb_placeholder` in frames.csv.gz). Box size is NOT usable as a range cue, and IoU matching of small targets is unreliable: E1 should match detections by centre distance. Box centres are still used for bearing/elevation.
"""
    with open(os.path.join(a.out_dir, "DATA_CARD.md"), "w") as f:
        f.write(card)
    print(card)
    print("Class/planned counts over all entities:", dict(all_classes))


if __name__ == "__main__":
    main()
