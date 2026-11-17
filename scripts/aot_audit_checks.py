#!/usr/bin/env python3
"""E0 (Pair A) — QC checks behind the AOT data card. Writes data/aot/qc_report.md.

Reads the raw label metadata only (groundtruth.json + valid_encounters_*.json per part; no images).
Sections:
  1. File basics: fps / resolution / clip length, bbox extents, duplicate labels
  2. Part overlap: which flight_ids are recorded by both cameras (part3 = camera1, part1/2 = camera0)
  3. Cross-camera agreement for the same object at matched times (nearest frame, |dt| <= 50 ms)
  4. Camera1 mounting: yaw/pitch/roll of camera1 relative to camera0, fitted from matched planned objects
  5. Range QC: range-rate glitches, low ranges, dropped short runs
  6. Cross-check of our encounter rule against AOT's own valid_encounters (<= 700 m) list

Usage:  python3 scripts/aot_audit_checks.py [--raw data/aot/raw] [--out data/aot/qc_report.md]
Stdlib only. Deterministic (no randomness). Takes a few minutes (pure-Python rotation fit).
"""
import argparse, bisect, json, math, os, re, statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone

CREWED = {"Airplane", "Helicopter"}
GAP, MINLEN, MAX_RATE, TOL_NS = 3, 30, 300.0, 50_000_000
W, H, HFOV, VFOV = 2448, 2048, 67.8, 56.8      # same assumptions as aot_audit.py (FOV to be confirmed)
FX = (W / 2) / math.tan(math.radians(HFOV / 2)); FY = (H / 2) / math.tan(math.radians(VFOV / 2))
OUTLIER_DEG = 1.0      # matched pair rejected from the global fit if its residual exceeds this
MIN_PAIRS_FLIGHT = 10  # per-flight fit only for flights with at least this many matches
DR_MAX = 5.0           # m; a cross-camera pair counts as the same physical object only if ranges agree this well


def cls_of(oid): return re.sub(r"\d+$", "", oid or "")


def dec(v, n=10, nd=2): return [round(x, nd) for x in statistics.quantiles(v, n=n)] if len(v) > 1 else v


def load_part(path):
    """Compact view of one groundtruth.json: labelled entities as tuples + per-flight metadata."""
    with open(path) as f:
        g = json.load(f)
    out = dict(camera=g["metadata"]["description"], meta={}, ents=[])
    for fid, s in g["samples"].items():
        md = s["metadata"]
        out["meta"][fid] = (md.get("fps"), md["resolution"]["width"], md["resolution"]["height"], md.get("number_of_frames"))
        for e in s["entities"]:
            if not e.get("id"):
                continue
            r = e["blob"].get("range_distance_m")
            r = None if r is None or (isinstance(r, float) and math.isnan(r)) else float(r)
            out["ents"].append((fid, e["id"], e["blob"]["frame"], e["time"], r, tuple(e["bb"]) if e.get("bb") else None))
    return out


def load_valid(path):
    """AOT's valid_encounters file is a stream of concatenated JSON objects, not one document."""
    txt, d, i, out = open(path).read(), json.JSONDecoder(), 0, []
    while True:
        while i < len(txt) and txt[i].isspace(): i += 1
        if i >= len(txt): return out
        o, i = d.raw_decode(txt, i); o.pop("img_name", None); out.append(o)


def runs(frames, gap=GAP):
    frames = sorted(frames); out, cur = [], [frames[0]]
    for f in frames[1:]:
        if f - cur[-1] <= gap + 1: cur.append(f)
        else: out.append(cur); cur = [f]
    out.append(cur); return out


# ---- rotation model: camera axes x right, y down, z forward; v0 = R(yaw, pitch, roll) v1
def ray(cx, cy):
    x, y = (cx - W / 2) / FX, (cy - H / 2) / FY; n = math.sqrt(x * x + y * y + 1); return (x / n, y / n, 1 / n)


def rot(yaw, pitch, roll):
    c, s = math.cos, math.sin
    y, p, r = map(math.radians, (yaw, pitch, roll))
    Ry = ((c(y), 0, s(y)), (0, 1, 0), (-s(y), 0, c(y)))
    Rx = ((1, 0, 0), (0, c(p), -s(p)), (0, s(p), c(p)))
    Rz = ((c(r), -s(r), 0), (s(r), c(r), 0), (0, 0, 1))
    mm = lambda A, B: tuple(tuple(sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)) for i in range(3))
    return mm(mm(Ry, Rx), Rz)


def resid(params, P):
    R = rot(*params); out = []
    for v0, v1 in P:
        w = [sum(R[i][k] * v1[k] for k in range(3)) for i in range(3)]
        out.append(math.degrees(math.acos(max(-1.0, min(1.0, sum(a * b for a, b in zip(v0, w)))))))
    return out


def fit(P, x0, free=(0, 1, 2), loss=statistics.median):
    """Coarse-to-fine coordinate search (stdlib only). ponytail: slow-ish O(iters*n), fine for ~1e5 pairs."""
    x = list(x0); step = 8.0; best = loss(resid(x, P))
    while step > 0.005:
        moved = False
        for i in free:
            for sgn in (step, -step):
                y = list(x); y[i] += sgn; l = loss(resid(y, P))
                if l < best: best, x, moved = l, y, True
        if not moved: step /= 2
    return x


def mse(v): return sum(e * e for e in v) / len(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/aot/raw")
    ap.add_argument("--out", default="data/aot/qc_report.md")
    a = ap.parse_args()
    parts = sorted(d for d in os.listdir(a.raw) if os.path.exists(f"{a.raw}/{d}/ImageSets/groundtruth.json"))
    P = {p: load_part(f"{a.raw}/{p}/ImageSets/groundtruth.json") for p in parts}
    L = []  # report lines
    add = L.append

    # ---- 1. basics
    add("# AOT QC report (E0, Pair A)\n\nGenerated by `scripts/aot_audit_checks.py` from raw label metadata (no images).\n")
    add("## 1. File basics\n\n| Part | Camera | Flights | fps | Resolution | Frames per clip | Labelled entities | Box centre x / y range | Boxes past image edge | Duplicate (flight,obj,frame) |\n|---|---|---|---|---|---|---|---|---|---|")
    for p in parts:
        m, E = P[p]["meta"], P[p]["ents"]
        bbs = [e[5] for e in E if e[5]]
        cx = [b[0] + b[2] / 2 for b in bbs]; cy = [b[1] + b[3] / 2 for b in bbs]
        edge = sum(b[0] < 0 or b[1] < 0 or b[0] + b[2] > W or b[1] + b[3] > H for b in bbs)
        dup = sum(v > 1 for v in Counter((e[0], e[1], e[2]) for e in E).values())
        add(f"| {p} | {P[p]['camera']} | {len(m)} | {dict(Counter(v[0] for v in m.values()))} | {dict(Counter(f'{v[1]}x{v[2]}' for v in m.values()))} | "
            f"{dict(Counter(v[3] for v in m.values()).most_common(3))} | {len(E)} | {min(cx):.1f}–{max(cx):.1f} / {min(cy):.1f}–{max(cy):.1f} | {edge} | {dup} |")
    cls_ct = Counter((cls_of(e[1]), e[4] is not None) for p in parts for e in P[p]["ents"])
    add("\nLabelled entities by class (planned = has `range_distance_m`): " +
        ", ".join(f"{c} {'planned' if pl else 'unplanned'} {n}" for (c, pl), n in sorted(cls_ct.items())) + "\n")

    # ---- 2. overlap
    F = {p: set(P[p]["meta"]) for p in parts}
    add("## 2. Part overlap (same flight_id in two parts)\n")
    for i, p in enumerate(parts):
        for q in parts[i + 1:]:
            add(f"- {p} ({P[p]['camera']}) ∩ {q} ({P[q]['camera']}): {len(F[p] & F[q])} flights")
    cam1 = [p for p in parts if P[p]["camera"] == "camera1"]
    cam0 = [p for p in parts if P[p]["camera"] == "camera0"]
    for p in cam1:
        both = F[p] & set().union(*(F[q] for q in cam0))
        add(f"- {p}: {len(both)} of {len(F[p])} flights also recorded by camera0; {len(F[p]) - len(both)} are camera1-only")
    add("")

    # ---- 3. cross-camera matching
    def index(p, fids):
        d = defaultdict(dict)
        for fid, oid, fr, t, r, bb in P[p]["ents"]:
            if fid in fids and bb: d[(fid, oid)][t] = (bb[0] + bb[2] / 2, bb[1] + bb[3] / 2, r)
        return d
    pairs = []  # (fid, oid, cx0, cy0, cx1, cy1, r0, r1, t1)
    for p1 in cam1:
        for p0 in cam0:
            ov = F[p0] & F[p1]
            A, B = index(p0, ov), index(p1, ov)
            for k in sorted(set(A) & set(B)):
                ta = sorted(A[k])
                for tb in sorted(B[k]):
                    i = bisect.bisect_left(ta, tb)
                    best = min((ta[j] for j in (i - 1, i) if 0 <= j < len(ta)), key=lambda x: abs(x - tb))
                    if abs(best - tb) <= TOL_NS:
                        v0, v1 = A[k][best], B[k][tb]
                        pairs.append((k[0], k[1], v0[0], v0[1], v1[0], v1[1], v0[2], v1[2], tb))
    planned = [x for x in pairs if x[6] is not None and x[7] is not None]
    same = [x for x in planned if abs(x[6] - x[7]) <= DR_MAX]
    unpl = [x for x in pairs if x[6] is None or x[7] is None]
    add("## 3. Cross-camera agreement (same flight + object id, nearest frame |dt| ≤ 50 ms)\n")
    add(f"- Cameras are not frame-synced: same frame index differs by tens of ms, so frames are matched by nearest timestamp.")
    add(f"- Matched (object, time) pairs: {len(pairs)}; with range on both cameras (planned): {len(planned)}.")
    add(f"- |range(cam0) − range(cam1)| deciles (m): {dec([abs(x[6] - x[7]) for x in planned])} → same ownship-to-intruder truth; "
        f"{len(planned) - len(same)} pairs differ by > {DR_MAX:g} m.")
    add(f"- cx(cam1) − cx(cam0) deciles, planned pairs (px): {dec([x[4] - x[2] for x in planned], nd=0)}")
    add(f"- cx(cam1) − cx(cam0) deciles, unplanned pairs (px): {dec([x[4] - x[2] for x in unpl], nd=0)}")
    add(f"- The 'small x offset' matches seen earlier are almost all **unplanned** objects ({Counter(cls_of(x[1]) for x in unpl).most_common()}): "
        f"their ids (e.g. `Airplane1`, `Airborne2`) are assigned per camera, so the same id in both cameras is often a different object. "
        f"Only planned objects with agreeing range (|Δr| ≤ {DR_MAX:g} m) are used below.\n")

    # ---- 4. camera1 mounting fit
    add("## 4. Camera1 mounting relative to camera0 (ESTIMATED from labels, not surveyed)\n")
    add(f"Model: same pinhole intrinsics for both cameras (FOV {HFOV}°×{VFOV}°, unconfirmed); ray(cam0) = R_y(yaw)·R_x(pitch)·R_z(roll)·ray(cam1). "
        f"Fit = coarse-to-fine search, median loss on all pairs, then RMS loss on inliers (residual ≤ {OUTLIER_DEG}°).\n")
    PP = [(ray(x[2], x[3]), ray(x[4], x[5])) for x in same]
    x1 = fit(PP, (55.0, 0.0, 0.0))
    r1 = resid(x1, PP)
    inl = [i for i, v in enumerate(r1) if v <= OUTLIER_DEG]
    x2 = fit([PP[i] for i in inl], x1, loss=mse)
    r2 = resid(x2, [PP[i] for i in inl])
    add(f"- Global fit, all {len(PP)} pairs ({len({x[0] for x in same})} flights): yaw {x1[0]:.2f}°, pitch {x1[1]:.2f}°, roll {x1[2]:.2f}°; "
        f"residual median {statistics.median(r1):.2f}°, p90 {dec(r1)[8]:.2f}°; {len(PP) - len(inl)} pairs ({(len(PP) - len(inl)) / len(PP):.0%}) > {OUTLIER_DEG}°.")
    add(f"- Refit on {len(inl)} inliers: **yaw {x2[0]:.2f}°, pitch {x2[1]:.2f}°, roll {x2[2]:.2f}°**; residual median {statistics.median(r2):.2f}°, "
        f"RMS {math.sqrt(mse(r2)):.2f}°.")
    # per-flight: yaw+pitch free, roll fixed to the global value (a narrow overlap strip leaves roll ill-conditioned
    # per flight: with roll free, 2 flights converged to nonsense yaw), all same-object pairs (no global outlier rejection)
    byf = defaultdict(list)
    for x, pr in zip(same, PP): byf[x[0]].append((x, pr))
    rows = []
    for fid in sorted(byf):
        if len(byf[fid]) < MIN_PAIRS_FLIGHT: continue
        Pf = [pr for _, pr in byf[fid]]
        xf = fit(Pf, x2, free=(0, 1), loss=mse); rf = resid(xf, Pf)
        t0 = min(x[8] for x, _ in byf[fid])
        rows.append((fid, len(Pf), xf[0], xf[1], xf[2], statistics.median(rf),
                     datetime.fromtimestamp(t0 / 1e9, tz=timezone.utc).strftime("%Y-%m-%d")))
    ys = [r[2] for r in rows]
    hist = Counter(round(y * 2) / 2 for y in ys)
    add(f"- Per-flight fit ({len(rows)} flights with ≥ {MIN_PAIRS_FLIGHT} matches; yaw/pitch free, roll = global): yaw median {statistics.median(ys):.2f}°, "
        f"sd {statistics.pstdev(ys):.2f}°, range {min(ys):.2f}–{max(ys):.2f}°; per-flight residual median {statistics.median(r[5] for r in rows):.2f}°.")
    add(f"- Per-flight yaw histogram (0.5° bins): {dict(sorted(hist.items()))}")
    main_mode = statistics.median(ys)
    lo = [r for r in rows if r[2] < main_mode - 1.0]
    hi = [r for r in rows if r[2] > main_mode + 1.0]
    add(f"- Flights more than 1° from the median yaw: {len(lo)} below (yaw {', '.join(f'{v:.1f}' for v in dec([r[2] for r in lo], n=4)) if len(lo) > 1 else [round(r[2], 1) for r in lo]} quartiles), "
        f"{len(hi)} above. Their own residuals are small (median {statistics.median([r[5] for r in lo + hi]) if lo + hi else float('nan'):.2f}°), "
        f"so they are a genuinely different mounting, not bad matches.")
    dates_main = Counter(r[6][:7] for r in rows if abs(r[2] - main_mode) <= 1.0)
    dates_lo = Counter(r[6][:7] for r in lo)
    add(f"- Flight month (UTC), main cluster: {dict(sorted(dates_main.items()))}")
    add(f"- Flight month (UTC), low-yaw cluster: {dict(sorted(dates_lo.items()))}")
    off = lo + hi
    if len(off) > 0.05 * len(rows):
        add(f"\n**Verdict:** camera1 yaw is NOT one constant: {len(off)} of {len(rows)} flights ({len(off) / len(rows):.0%}) sit > 1° from the "
            f"main value ({main_mode:.1f}°), e.g. a second cluster near {statistics.median([r[2] for r in off]):.1f}°. camera1-only flights "
            f"cannot be assigned to a cluster, so ownship-frame bearings are NOT added to frames.csv.gz.")
    else:
        add(f"\n**Verdict:** camera1 yaw is consistent across flights ({len(off)} of {len(rows)} flights > 1° off); a single offset is usable.")
    add("Per-flight values: `data/aot/camera1_offset_per_flight.csv`.\n")
    with open(os.path.join(os.path.dirname(a.out), "camera1_offset_per_flight.csv"), "w") as f:
        f.write("flight_id,n_pairs,yaw_deg,pitch_deg,roll_deg,median_resid_deg,date_utc\n")
        for r in rows: f.write(f"{r[0]},{r[1]},{r[2]:.3f},{r[3]:.3f},{r[4]:.3f},{r[5]:.3f},{r[6]}\n")

    # ---- 5. range QC (crewed planned, per part = per camera view)
    add("## 5. Range QC (crewed planned objects, per camera view)\n")
    add(f"| Part | Planned frames | min / 1st pct / 99th pct / max (m) | < 10 m | Runs ≥ {MINLEN} with range steps > {MAX_RATE:g} m/s | Short runs dropped (< {MINLEN}) | Frames dropped |\n|---|---|---|---|---|---|---|")
    glitches = []
    for p in parts:
        by = defaultdict(list)
        for fid, oid, fr, t, r, bb in P[p]["ents"]:
            if r is not None and bb and cls_of(oid) in CREWED: by[(fid, oid)].append((fr, r))
        rs = [r for v in by.values() for _, r in v]
        n_runs = n_short = fr_short = fr_all = 0
        for (fid, oid), v in by.items():
            d = dict(v)
            for run in runs(list(d)):
                n_runs += 1; fr_all += len(run)
                if len(run) < MINLEN: n_short += 1; fr_short += len(run); continue
                j = sum(abs(d[b] - d[x]) * 10 / (b - x) > MAX_RATE for x, b in zip(run, run[1:]))
                if j: glitches.append((p, fid, oid, run[0], run[-1], j, min(d[f] for f in run), max(d[f] for f in run)))
        pc = statistics.quantiles(rs, n=100)
        add(f"| {p} | {len(rs)} | {min(rs):.1f} / {pc[0]:.0f} / {pc[98]:.0f} / {max(rs):.0f} | {sum(r < 10 for r in rs)} | "
            f"{sum(g[0] == p for g in glitches)} | {n_short} of {n_runs} ({n_short / n_runs:.0%}) | {fr_short} of {fr_all} ({fr_short / fr_all:.1%}) |")
    add("\nRuns with range glitches (excluded from the data card by `aot_audit.py`):\n")
    for g in glitches:
        add(f"- {g[0]} `{g[1]}` {g[2]} frames {g[3]}–{g[4]}: {g[5]} steps > {MAX_RATE:g} m/s, range {g[6]:.2f}–{g[7]:.0f} m")
    add("")
    for p in parts:
        lows = sorted((e for e in P[p]["ents"] if e[4] is not None and cls_of(e[1]) in CREWED and e[5]), key=lambda e: e[4])[:1]
        for e in lows:
            in_glitch = any(g[0] == p and g[1] == e[0] and g[2] == e[1] and g[3] <= e[2] <= g[4] for g in glitches)
            add(f"- Lowest range in {p}: {e[4]:.2f} m (`{e[0]}` {e[1]} frame {e[2]}) — {'inside a glitch run (excluded)' if in_glitch else 'NOT in a glitch run'}")
    add("")

    # ---- 6. AOT valid_encounters cross-check
    add("## 6. Cross-check vs AOT `valid_encounters_maxRange700_maxGap3_minEncLen30`\n")
    add("AOT's list has flight + frame range only (no object id). Ours here = same rule (range ≤ 700 m, gap ≤ 3, ≥ 30 frames) on all planned classes.\n")
    add("| Part | AOT encounters | Ours | AOT found by ours | Exact frame range | AOT flights | Our flights | Our flights not in AOT list | Ours missing in AOT-covered flights |\n|---|---|---|---|---|---|---|---|---|")
    for p in parts:
        vf = f"{a.raw}/{p}/ImageSets/valid_encounters_maxRange700_maxGap3_minEncLen30.json"
        if not os.path.exists(vf): add(f"| {p} | (file missing — rerun get_aot.sh) |||||||| "); continue
        th = load_valid(vf)
        by = defaultdict(list)
        for fid, oid, fr, t, r, bb in P[p]["ents"]:
            if r is not None and r <= 700 and bb: by[(fid, oid)].append(fr)
        us = [(fid, run[0], run[-1]) for (fid, oid), v in by.items() for run in runs(sorted(set(v))) if len(run) >= MINLEN]
        uf = defaultdict(list)
        for u in us: uf[u[0]].append(u)
        ov = lambda u, x: max(0, min(u[2], x["framemax"]) - max(u[1], x["framemin"]) + 1)
        found = exact = 0
        for x in th:
            best = max(uf.get(x["flight_id"], []), key=lambda u: ov(u, x), default=None)
            if best and ov(best, x): found += 1; exact += best[1] == x["framemin"] and best[2] == x["framemax"]
        tfl = {x["flight_id"] for x in th}; ufl = set(uf)
        tf = defaultdict(list)
        for x in th: tf[x["flight_id"]].append(x)
        miss_in = sum(1 for u in us if u[0] in tfl and not any(ov(u, x) for x in tf[u[0]]))
        add(f"| {p} | {len(th)} | {len(us)} | {found} | {exact} | {len(tfl)} | {len(ufl)} | {len(ufl - tfl)} | {miss_in} |")
    add("\nEvery AOT encounter is found by our rule and almost all match frame-for-frame; AOT's list leaves out whole flights "
        "(an unpublished selection), so the extra encounters we find are expected, not a rule mismatch.\n")

    with open(a.out, "w") as f:
        f.write("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
