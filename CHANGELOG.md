# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow [Semantic Versioning](https://semver.org/).

## [1.2.0] - 2026-10-17
### Added
- `extract_data.py` copies the extracted data into `BepInEx\plugins\MapHints\data` when the
  [Map Hints](https://github.com/PikselYT/belmonts-curse-maphints) mod is installed
  (`--no-plugin-export` turns it off). No more copying JSON files by hand.
- Issue template, changelog and a version-compatibility table in the README.
- Data format version is 3 (unchanged); Map Hints 0.2.0+ checks it.

## [1.1.0]
- Missed collectibles: chests, upgrades, books and experience sources.

## [1.0.0]
- Secret zone hints with three levels of detail.
