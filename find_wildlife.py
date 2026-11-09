#!/usr/bin/env python3
"""
find_wildlife.py — scan a folder of eufy security clips and find the ones that
(a) were recorded inside a time-of-day window (default 12am-7am) and
(b) contain an animal, according to MegaDetector.

Setup (Python 3.9+, first run downloads the ~280MB model automatically):

    pip install megadetector opencv-python

Usage:

    # Just see which clips fall in the 12am-7am window (no ML, instant):
    python find_wildlife.py /path/to/clips --dry-run

    # Full run: filter by time window, then detect animals:
    python find_wildlife.py /path/to/clips --out ./wildlife_results

    # Custom window (e.g. 10pm-6am) and stricter confidence:
    python find_wildlife.py /path/to/clips --start-hour 22 --end-hour 6 --conf 0.4

Output:
    <out>/matches/          copies of every clip with an animal in it
    <out>/report.csv        one row per scanned clip: match, confidence, when
    <out>/frames/           the frame where the animal was spotted (for review)

Recording time is read from the filename when possible (eufy exports usually
embed a timestamp like 20260801_023015); otherwise the file's modification
time is used — pass --time-source mtime|filename to force one.
"""

import argparse
import csv
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".ts", ".h264", ".h265", ".dat"}

# animal=1, person=2, vehicle=3 in MegaDetector's category map
ANIMAL_CATEGORY = "1"

FILENAME_TS = re.compile(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})[T _\-]?(\d{2})[:\-_]?(\d{2})[:\-_]?(\d{2})")


def metadata_timestamp(path: Path):
    """Recording time from the container's creation_time tag (local time), via ffprobe."""
    import json
    import subprocess

    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_entries",
             "format_tags=creation_time,com.apple.quicktime.creationdate", str(path)],
            capture_output=True, text=True, timeout=30).stdout
        tags = json.loads(out or "{}").get("format", {}).get("tags", {})
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    raw = tags.get("com.apple.quicktime.creationdate") or tags.get("creation_time")
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.year < 2000:  # unset tag (1904/1970 epoch)
        return None
    return ts.astimezone().replace(tzinfo=None) if ts.tzinfo else ts


def clip_timestamp(path: Path, source: str) -> datetime:
    """Best-effort recording time of a clip: filename, then video metadata, then mtime."""
    if source in ("auto", "filename"):
        m = FILENAME_TS.search(path.stem)
        if m:
            try:
                return datetime(*(int(g) for g in m.groups()))
            except ValueError:
                pass
        if source == "filename":
            raise ValueError(f"no timestamp found in filename: {path.name}")
    if source in ("auto", "meta"):
        ts = metadata_timestamp(path)
        if ts:
            return ts
        if source == "meta":
            raise ValueError(f"no creation_time metadata in: {path.name}")
    return datetime.fromtimestamp(path.stat().st_mtime)


def in_window(ts: datetime, start_hour: int, end_hour: int) -> bool:
    """True if ts falls in [start_hour, end_hour), handling windows that wrap midnight."""
    if start_hour < end_hour:
        return start_hour <= ts.hour < end_hour
    return ts.hour >= start_hour or ts.hour < end_hour


def iter_frames(path: Path, every_s: float):
    """Yield (seconds_into_clip, RGB ndarray) every `every_s` seconds."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        print(f"  ! could not open {path.name}, skipping", file=sys.stderr)
        return
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0 or fps > 240:
        fps = 15.0  # eufy cams are typically 15fps; also covers raw .h264 with no header fps
    step = max(1, round(fps * every_s))
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % step == 0:
            yield idx / fps, cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        idx += 1
    cap.release()


def main() -> int:
    ap = argparse.ArgumentParser(description="Find night-time eufy clips containing animals (MegaDetector).")
    ap.add_argument("clips_dir", type=Path, help="folder containing the video clips (searched recursively)")
    ap.add_argument("--out", type=Path, default=Path("wildlife_results"), help="output folder")
    ap.add_argument("--start-hour", type=int, default=0, help="window start hour, 0-23 (default 0 = midnight)")
    ap.add_argument("--end-hour", type=int, default=7, help="window end hour, exclusive (default 7 = 7am)")
    ap.add_argument("--conf", type=float, default=0.25, help="MegaDetector confidence threshold (default 0.25)")
    ap.add_argument("--every", type=float, default=1.0, help="seconds between sampled frames (default 1.0)")
    ap.add_argument("--time-source", choices=["auto", "filename", "mtime"], default="auto",
                    help="where to read the recording time from (default: filename, falling back to mtime)")
    ap.add_argument("--model", default="MDV5A", help="MegaDetector model to use (default MDV5A)")
    ap.add_argument("--scan-full", action="store_true",
                    help="keep scanning after the first animal hit to record the best confidence")
    ap.add_argument("--dry-run", action="store_true", help="only list clips inside the time window, no detection")
    args = ap.parse_args()

    clips = sorted(p for p in args.clips_dir.rglob("*") if p.suffix.lower() in VIDEO_EXTS)
    if not clips:
        print(f"No video files found under {args.clips_dir}", file=sys.stderr)
        return 1

    in_win = []
    for p in clips:
        ts = clip_timestamp(p, args.time_source)
        if in_window(ts, args.start_hour, args.end_hour):
            in_win.append((p, ts))

    print(f"{len(clips)} clips found, {len(in_win)} inside "
          f"{args.start_hour:02d}:00-{args.end_hour:02d}:00 window")
    if args.dry_run:
        for p, ts in in_win:
            print(f"  {ts:%Y-%m-%d %H:%M:%S}  {p}")
        return 0
    if not in_win:
        return 0

    print(f"Loading MegaDetector ({args.model})... (first run downloads the model)")
    from PIL import Image
    from megadetector.detection.run_detector import load_detector
    detector = load_detector(args.model)

    args.out.mkdir(parents=True, exist_ok=True)
    matches_dir = args.out / "matches"
    frames_dir = args.out / "frames"
    matches_dir.mkdir(exist_ok=True)
    frames_dir.mkdir(exist_ok=True)

    rows = []
    n_matches = 0
    for i, (p, ts) in enumerate(in_win, 1):
        print(f"[{i}/{len(in_win)}] {p.name} ({ts:%Y-%m-%d %H:%M}) ...", end=" ", flush=True)
        best_conf, best_t, best_frame = 0.0, None, None
        for t, rgb in iter_frames(p, args.every):
            img = Image.fromarray(rgb)
            result = detector.generate_detections_one_image(
                img, p.name, detection_threshold=args.conf)
            animal_confs = [d["conf"] for d in result.get("detections", [])
                            if d["category"] == ANIMAL_CATEGORY and d["conf"] >= args.conf]
            if animal_confs and max(animal_confs) > best_conf:
                best_conf, best_t, best_frame = max(animal_confs), t, img
                if not args.scan_full:
                    break
        if best_conf > 0:
            n_matches += 1
            shutil.copy2(p, matches_dir / p.name)
            best_frame.save(frames_dir / f"{p.stem}_t{best_t:.0f}s_conf{best_conf:.2f}.jpg")
            print(f"ANIMAL (conf {best_conf:.2f} at {best_t:.0f}s)")
        else:
            print("no animal")
        rows.append({"file": str(p), "recorded": ts.isoformat(sep=" "),
                     "animal": best_conf > 0, "confidence": round(best_conf, 3),
                     "seconds_into_clip": None if best_t is None else round(best_t, 1)})

    report = args.out / "report.csv"
    with open(report, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["file", "recorded", "animal", "confidence", "seconds_into_clip"])
        w.writeheader()
        w.writerows(rows)

    print(f"\nDone: {n_matches}/{len(in_win)} night clips contain an animal.")
    print(f"Matched clips: {matches_dir}\nDetection frames: {frames_dir}\nReport: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
