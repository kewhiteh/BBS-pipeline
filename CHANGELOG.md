# Changelog

All notable changes to this project are recorded here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

- Changed: `observations.source` defaults to `10_stop` (was `50_stop`). Profiles that
  omit it now read 10-stop data.
- Changed: the editor drops `50_stop` from the Source list when the years start before
  1997 and switches the draft to `10_stop` with a note, instead of a red error; the
  `stops` layout is likewise offered only with `50_stop`.
- Changelog started 2026-11-07; earlier history is in git log.

Tags v1.0.0 and v2.0.0-rc1 below are from the legacy (pre-v3) code, not the current tree (Kyle, 2026-11-07).

## [2.0.0-rc1] - 2026-09-23

- Tag message: "Release Candidate 1 for v2.0.0 Track B Additive Spatial Upgrade". Details: UNKNOWN (ask Kyle); see git log.

## [1.0.0] - 2026-09-23

- Tag message: "release: verified stable v1.0 baseline". Details: UNKNOWN (ask Kyle); see git log.
