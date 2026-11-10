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
| Flights with radar | **17**: competition flights 1–14 + localization flights Opt1–Opt3 (flights 15–33 have no radar file; 16–18 also lack GPS truth) | |

Other modalities (Keysight RF sensor, USRP links, LoRa) are in the zip but not used in this study.

## Caveats to state in the paper

- The targets are drones, not crewed aircraft, so the RCS has to be scaled (see E3).
- The radar is probably ground-based, not airborne; confirm its location from the metadata.
- License: the Dryad page does not name one. Confirm reuse terms with the NC State team before publishing results.

## Data card (E0, partly filled from a first look on Nov 10)

- Radar files are under `RF Sensor and Radar/<team>vol<k>_flight<N>/radar_data_*.json` (one JSON message per line, ~860 MB total).
- Each message has `params` (radar pose: `lla`, `rpy`, `aboveGroundLevel` = 10 m, `globalTime`), `data` (detections) and `trackData` (tracks).
- Detection fields: range, azimuth, elevation (+ sigmas), radialVelocity, rcsDbsm, sinrDb, lla, relativePositionNed, isStationary, onGround.
- Radar is **fixed on a 10 m mast** (ground-based). Flight 1: ~3.9 detection messages/s over 940 s; detection ranges 11–920 m (all detections, not only the drone).
- Ground truth `*_vehicleOut.txt`: ~1 Hz CSV (index, lon, lat, alt, attitude tuple, velocity tuple, ..., local timestamp).
- **Yaw is not surveyed**, so radar tracks are rotated about the radar by a few degrees. Fix per flight: sort tracks by `timeUnderTrack`, find the drone's tracks among the longest 25–50, then fit the rotation to GPS truth (dataset README; example in `Opt2/radarmatoutput.m`: tracks 491 and 919, rotated 2° CCW).
- Range covered by the drone: ___ m (to compute; probably under ~1 km, so Pd beyond that is extrapolated through the RCS/r⁴ model).
- Split file: `data/aerpaw/splits.csv` (by flight: ~11 fit / 3 tune / 3 held-out).
- Questions on post-processing: Cole Dickerson (dataset author, email in the dataset README).
