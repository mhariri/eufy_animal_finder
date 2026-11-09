# eufy_animal_finder

Download recordings directly from a Eufy HomeBase 3, find animal clips, and build
a dated collection with a Markdown index. This is a command-line application.

The default screening samples **one frame per second**, checks both halves of
dual-camera images and the full image, and excludes a clip when a person is
detected. MegaDetector v5a finds animals and people; CLIP ViT-B/32 suggests animal
names. Automatic labels are tentative and benefit from visual review.

The application reads HomeBase recordings. It does **not** delete recordings
from HomeBase. Rain-only results are review candidates, not deletion commands.

## Setup

Requirements: Python 3.13, Node.js 24.5 or later, Git, FFmpeg/ffprobe, and enough
local disk space for your recordings. Apple Silicon workers use PyTorch MPS;
local review also supports CPU inference.

```bash
git clone https://github.com/mhariri/eufy_animal_finder.git
cd eufy_animal_finder
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
python3 setup_sdk.py
.venv/bin/python prepare_models.py
python3 configure.py
```

The downloader currently depends on a pinned commit of the
[SDK fork](https://github.com/mhariri/eufy-sdk), which fixes assembly of the
400,000-byte archive chunks. `setup_sdk.py` builds that commit in ignored
`vendor/eufy-sdk/` and installs it through a relative file dependency. No SDK
source or dependency directory is vendored into this repository.

`configure.py` prompts for your Eufy credentials without echoing the password.
They remain in ignored `eufy-downloader/.eufy-state/credentials.json`, mode 0600.
Login flows requiring captcha or two-factor challenges currently stop with an
error; the unattended archive command does not implement an interactive challenge
flow. Account sessions and transfer keys remain private local state.

## Inventory and download

```bash
cd eufy-downloader
node homebase_archive.mjs inventory
node homebase_archive.mjs inventory-records
node homebase_archive.mjs download-batch 20
cd ..
.venv/bin/python classify_homebase.py --watch
```

All recording hours are included. Calendar and per-day inventories are
checkpointed. Optional environment variables:

- `EUFY_FROM_DATE` / `EUFY_TO_DATE`: inventory boundaries as `YYYYMMDD`. The
  default begins in 2015 and ends tomorrow; date-boundary behavior remains an
  investigation limit.
- `EUFY_STATION_SN`: select a particular station when the account has several.
- `EUFY_PRIORITY_ANIMALS=1`: prioritize recording metadata likely to contain
  animals; this is only scheduling, not proof an animal is present.
- `EUFY_ATTEMPT_LIMIT`: per-recording retry limit, default 3.

The downloader authenticates encrypted media, joins split pictures, verifies the
recorded picture count, and decodes the finished video before publishing it.
Incomplete or corrupt transfers are kept out of the reviewed collection.

## Three parallel Mac GPU workers

On the Mac, clone this repository, create its `.venv`, install the Python
requirements, and run `prepare_models.py`. The Mac needs only frame review;
do not configure Eufy credentials there.

On the downloader machine, configure key-based SSH access and run
`configure.py` with the worker's `user@host` and absolute checkout path. Verify
the Mac's SSH host key independently, then place the trusted known-host entry
in `eufy-downloader/.eufy-state/mac-known-hosts`.

```bash
.venv/bin/python homebase_pipeline.py
```

This resumes downloads locally and maintains three MPS processes on the Mac.
Only sampled JPEGs, opaque clip IDs, and review job data are sent to the worker.
Each worker claims a separate job with an exclusive lock. Results return to the
downloader for naming and indexing; no Eufy credentials or transfer keys are
sent to the Mac.

`homebase_pipeline.py` needs a worker configuration. For a single-machine
workflow, use the explicit download and `classify_homebase.py` commands above.
Stop with Ctrl-C; checkpoints allow resuming. For unattended operation, run the
coordinator in a terminal multiplexer or a service. Repeated zero-progress
download batches back off for 10–30 minutes and retry while classification can
continue. The coordinator reserves at least 30 GiB free disk space.

## Collection and visual corrections

```text
clips/homebase/
  INDEX.md
  RAIN_ONLY_CANDIDATES.md
  UNCERTAIN.md
  STATUS.md                  # written by the coordinator
  yyyy-mm-dd-hh-mm_unidentified-animal.mp4
  identified/
    yyyy-mm-dd-hh-mm_cat.mp4
    birds/
      yyyy-mm-dd-hh-mm_jay.mp4
```

Minute/topic collisions receive `-02`, `-03`, and so on. Person clips, rejected
false positives, and clips awaiting a recheck can live in private working
storage. Models, footage, sessions, indexes, and working state are ignored by
Git. The root directory can also contain newly downloaded, unreviewed clips.

`apply_visual_review.py decisions.json` applies a JSON object keyed by the
opaque local clip ID, for example:

```json
{
  "00000000000000000000": {
    "topic": "raccoon",
    "reason": "Masked face and ringed tail visible in the inspected frames."
  }
}
```

Use `person` to exclude a clip with a person, `no-animal` for an inspected false
positive, and `uncertain` or `unidentified-animal` when the evidence is unclear.
The command preserves the previous automatic report, updates filenames and
indexes, and records the manual decision. Never assign a species from the
model's label alone. `record_homebase_review.py` records deleted local review
files as manual no-animal decisions; use it only after deliberately removing
false positives from the visible collection.

## Limits and provenance

The HomeBase 3 retrieval path uses SDK transport internals and is experimental.
Eufy cloud failures, unresolved camera channels, camera-offline recordings, and
station session closures can interrupt retrieval. A completed batch is not
proof of complete archive coverage; inspect the inventory and status counts.
Days returning the 2,000-row query ceiling require further pagination work.
Sampled frames can miss brief people or animals. Species similarities are not
calibrated probabilities. Rain-only classification does not establish why a
recording was triggered.

See [HomeBase 3 protocol findings](docs/homebase3-protocol.md). These document
the observed Android request path with synthetic placeholders, without captures
or actual account/device identifiers. The application is ISC licensed; the
SDK and model packages/weights retain their own licenses and terms.

## Checks

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q . -x 'vendor|node_modules|\.venv'
node --check eufy-downloader/homebase_archive.mjs
node --check eufy-downloader/decode_homebase.mjs
```
