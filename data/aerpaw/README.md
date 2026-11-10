# AERPAW AADM dataset (real radar, Fortem R20)

**Source:** https://datadryad.org/dataset/doi:10.5061/dryad.7d7wm3898
**DOI:** https://doi.org/10.5061/dryad.7d7wm3898
**Size:** one zip, `AADM2025Dryad.zip`, 7.70 GB (March 12, 2026 version), plus a 13.69 KB README

> The data is **not** stored in this repo (GitHub's file limit is 100 MB, and the license is still being confirmed).
> Download it locally with `scripts/get_aerpaw.sh`. Everything under `data/aerpaw/raw/` is ignored by git.

## Citation

Hossen, Md Sharif; Dickerson, Cole; Ozdemir, Ozgur; Sichitiu, Mihail; Guvenc, Ismail (2026).
*UAV-based wireless multi-modal measurements from AERPAW autonomous data mule (AADM) challenge in
digital twin and real-world environments* [Dataset]. Dryad. https://doi.org/10.5061/dryad.7d7wm3898

## What we use it for

Real radar measurements to fit and validate the radar emulator (Trello cards E0, E3 and E4, owned by Pair B).

## Contents relevant to us

| Item | File(s) | Fields |
|---|---|---|
| Radar tracks (Fortem R20) | `radar_data_N.json` | track ID, range, azimuth, elevation, RCS (dBsm), radial velocity, lat/lon/alt |
| Ground truth (UAV telemetry) | `vehicleOut.txt` per flight | position, altitude, orientation, velocity |
| Flights with radar | 33 competition testbed flights + 3 localization flights | |

Other modalities (Keysight RF sensor, USRP links, LoRa) are in the zip but not used in this study.

## Caveats to state in the paper

- The targets are drones, not crewed aircraft, so the RCS has to be scaled (see E3).
- The radar is probably ground-based, not airborne; confirm its location from the metadata.
- License: the Dryad page does not name one. Confirm reuse terms with the NC State team before publishing results.

## Data card (fill in during E0)

- Flights with usable radar + GPS: ___
- Range span covered: ___ to ___ m
- Radar scan rate: ___ Hz; GPS rate: ~1 Hz
- Radar location (lat/lon/alt): ___
- Split file: `data/aerpaw/splits.csv` (by flight: ~60% fit / 20% tune / 20% held-out)
