"""Copy the publishable part of the flight results into the site repo. Stdlib + ffmpeg (for .webp shots).

usage: py -I tools/export_data.py <runs dir of one campaign> <site root> <name> [shot names, comma separated]
Per valid flight (meta.json valid=true): <name>/<run>/{segments.csv, summary.json, meta.json(subset)}; frames.csv is gzip'd.
The huge CSV-profiler file, the game log and the harness process lists are not exported.
Shots: the first valid flight of each arm, the listed segment names, converted to webp q80 -> img/<name>/<arm>_<seg>.webp
"""
import gzip
import json
import os
import shutil
import subprocess
import sys

META_KEYS = ["run", "campaign", "arm", "asset", "valid", "nlb_args", "expect_counts", "expect_segments", "session",
             "vram_other_before_mb", "power_source", "battery_pct_start", "battery_pct_end", "exe_sha256", "fullscreen_mode",
             "display_before", "client_size", "listen_tcp", "start", "end", "minutes", "exit_code", "display_samples",
             "display_bad_samples", "power_off_samples", "vram_game_max_mb", "conflicts_during", "nlb_platform",
             "bench_rhi", "bench_adapter", "bench_driver", "bench_engine", "bench_viewport", "bench_nanite_proxies", "headline"]


def main():
    src, root, name = sys.argv[1], sys.argv[2], sys.argv[3]
    shots = [s for s in (sys.argv[4].split(",") if len(sys.argv) > 4 else []) if s]
    dst = os.path.join(root, "data", name)
    os.makedirs(dst, exist_ok=True)
    img = os.path.join(root, "img", name)
    os.makedirs(img, exist_ok=True)
    done_arm = set()
    n = 0
    for run in sorted(os.listdir(src)):
        d = os.path.join(src, run)
        mp = os.path.join(d, "meta.json")
        if run.startswith("_") or not os.path.isfile(mp):
            continue
        meta = json.load(open(mp, encoding="utf-8-sig"))
        if not meta.get("valid"):
            continue
        od = os.path.join(dst, run)
        os.makedirs(od, exist_ok=True)
        shutil.copyfile(os.path.join(d, run + "_segments.csv"), os.path.join(od, "segments.csv"))
        summ = json.load(open(os.path.join(d, run + "_summary.json"), encoding="utf-8-sig"))
        summ.pop("cmdline", None)  # holds the device-local -UserDir path; the bench args are in meta.nlb_args
        st = summ.get("settings", {})
        for k in ("out_dir", "csv_file"):
            if isinstance(st.get(k), str):
                st[k] = os.path.basename(st[k].replace("\\", "/").rstrip("/"))
        json.dump(summ, open(os.path.join(od, "summary.json"), "w", encoding="utf-8"), indent=1)
        json.dump({k: meta.get(k) for k in META_KEYS if k in meta}, open(os.path.join(od, "meta.json"), "w", encoding="utf-8"), indent=1)
        fr = os.path.join(d, run + "_frames.csv")
        if os.path.isfile(fr):
            with open(fr, "rb") as fi, gzip.open(os.path.join(od, "frames.csv.gz"), "wb", compresslevel=9) as fo:
                shutil.copyfileobj(fi, fo)
        arm = meta.get("arm")
        if shots and arm not in done_arm:
            for s in shots:
                p = os.path.join(d, run + "_shots", s + ".png")
                if os.path.isfile(p):
                    subprocess.check_call(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", p, "-c:v", "libwebp",
                                           "-quality", "80", os.path.join(img, "%s_%s.webp" % (arm, s))])
            done_arm.add(arm)
        n += 1
    print("NLB_EXPORT flights=%d -> %s (shots for arms %s)" % (n, dst, sorted(done_arm)))


if __name__ == "__main__":
    main()
