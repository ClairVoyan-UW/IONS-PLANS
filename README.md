# IONS-PLANS

Experiments for our IEEE/ION PLANS 2027 paper on radar–camera fusion for long-range detect-and-avoid.
Results freeze: **Dec 15, 2026**. Manuscript due: **Feb 1, 2027**. Tasks live on the Trello board "IONS/PLANS Conference".

## Data

Raw data is **not** committed to this repo. Each dataset has a folder with a README (source, citation,
contents, caveats) and a script that downloads it into an ignored `raw/` folder.

| Dataset | Role | Folder | Get it |
|---|---|---|---|
| AERPAW AADM (Fortem R20 radar, 33 flights, drone GPS truth) | Real radar for fitting and validating the radar emulator | [`data/aerpaw/`](data/aerpaw/README.md) | `bash scripts/get_aerpaw.sh` ([Dryad](https://doi.org/10.5061/dryad.7d7wm3898)) |
| AOT / PrimeAir video with true range | Real camera detections, 0.7–1.5 km | `data/aot/` (to add) | — |

## Rules

- Split data by **flight**, never by frame. Nobody opens held-out flights before Dec 1.
- Every number comes from a config + seed in the repo; every figure comes from a script.
