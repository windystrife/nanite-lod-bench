"""Summarise Nanite-vs-LOD bench flights.

usage: py -I analyze_nlb.py <results_root> [<out_prefix>]
<results_root> holds one sub-folder per flight (any depth) with <tag>_segments.csv, <tag>_summary.json and the
harness meta.json. Flights whose meta.json says valid=false are dropped; flights without meta.json are used with a warning.
Flights are grouped by ARM, not by bench mode: the flight campaign has L (LOD), Nz (Nanite, Zone9 knobs) and Ns (Nanite,
stock knobs); Nz and Ns are both mode "Nanite" in the CSV. Arm = meta.json "arm", else the tag suffix after the last "_"
when it is L/Nz/Ns, else the CSV mode. Asset = meta.json "asset", else the CSV "asset" column.
Per (asset, layout, count, bookmark, unlit) the median over the arm's flights is taken (ABBA => 2 flights per arm), and the
spread (max-min over flights) is kept so a delta can be compared with the run-to-run noise.
Output: <out_prefix>.md (tables), <out_prefix>.json (all numbers), <out_prefix>_pairs.csv (one row per segment key).
Stdlib only.
"""
import csv
import glob
import json
import math
import os
import statistics
import sys

METRICS = ["frame_ms_med", "gpu_ms_med", "render_ms_med", "game_ms_med", "rhi_ms_med", "drawcalls_med", "prims_med",
           "frame_ms_p95"]
ARM_ORDER = ["L", "LOD", "Nz", "Ns", "Nanite", "Raw"]
BASE_ARMS = ("L", "LOD")


def load_json(p):
    if p and os.path.isfile(p):
        with open(p, encoding="utf-8-sig") as f:
            return json.load(f)
    return {}


def load_flights(root):
    flights = []
    for seg_path in sorted(glob.glob(os.path.join(root, "**", "*_segments.csv"), recursive=True)):
        d = os.path.dirname(seg_path)
        if os.sep + "_invalid" + os.sep in seg_path + os.sep:
            continue
        tag = os.path.basename(seg_path)[: -len("_segments.csv")]
        meta_path = next((c for c in (os.path.join(d, "meta.json"), os.path.join(os.path.dirname(d), "meta.json"))
                          if os.path.isfile(c)), None)
        meta = load_json(meta_path)
        summ = load_json(os.path.join(d, tag + "_summary.json"))
        with open(seg_path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        arm = meta.get("arm") or ""
        if not arm:
            suf = tag.rsplit("_", 1)[-1]
            arm = suf if suf in ("L", "Nz", "Ns") else (rows[0]["mode"] if rows else "?")
        flights.append({"dir": d, "tag": tag, "meta": meta, "summary": summ, "rows": rows, "arm": arm,
                        "valid": bool(meta.get("valid", True)) if meta else None})
    return flights


def fnum(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def med(v):
    return statistics.median(v) if v else float("nan")


def fmt(v, p=1):
    return ("%%.%df" % p) % v if isinstance(v, (int, float)) and v == v else "-"


def main():
    root = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(root, "nlb_report")
    flights = load_flights(root)
    used = [fl for fl in flights if fl["valid"] is not False]
    warn = [fl["dir"] for fl in flights if fl["valid"] is None]
    dropped = [fl["dir"] for fl in flights if fl["valid"] is False]

    # agg[arm][key][metric] -> list (one value per flight)
    agg = {}
    for fl in used:
        for r in fl["rows"]:
            asset = fl["meta"].get("asset") or r.get("asset") or fl["summary"].get("asset") or "?"
            k = (asset, r["layout"], int(r["count"]), int(r["bookmark"]), r["bookmark_name"], int(r["unlit"]))
            slot = agg.setdefault(fl["arm"], {}).setdefault(k, {mm: [] for mm in METRICS})
            for mm in METRICS:
                v = fnum(r.get(mm))
                if v is not None:
                    slot[mm].append(v)
            slot.setdefault("flights", []).append(fl["tag"])

    arms = sorted(agg.keys(), key=lambda a: (ARM_ORDER.index(a) if a in ARM_ORDER else 99, a))
    base = next((a for a in arms if a in BASE_ARMS), None)
    keys = sorted({k for a in arms for k in agg[a].keys()})
    pairs = []
    for k in keys:
        row = {"asset": k[0], "layout": k[1], "count": k[2], "bookmark": k[3], "bookmark_name": k[4], "unlit": k[5]}
        for a in arms:
            s = agg[a].get(k)
            for mm in METRICS:
                vals = s[mm] if s else []
                row["%s_%s" % (a, mm)] = med(vals)
                row["%s_%s_n" % (a, mm)] = len(vals)
                row["%s_%s_spread" % (a, mm)] = (max(vals) - min(vals)) if vals else float("nan")
            f = row["%s_frame_ms_med" % a]
            row["fps_%s" % a] = 1000.0 / f if f == f and f > 0 else float("nan")
        if base:
            for a in arms:
                if a == base:
                    continue
                for mm, short in (("frame_ms_med", "frame"), ("gpu_ms_med", "gpu"), ("render_ms_med", "rt"),
                                  ("game_ms_med", "game")):
                    row["%s_delta_ms_%s_minus_%s" % (short, a, base)] = row["%s_%s" % (a, mm)] - row["%s_%s" % (base, mm)]
                # noise floor of this delta: the larger of the two arms' flight-to-flight spreads
                row["frame_noise_ms_%s_vs_%s" % (a, base)] = max(row["%s_frame_ms_med_spread" % a],
                                                                  row["%s_frame_ms_med_spread" % base])
        pairs.append(row)

    if pairs:
        with open(out + "_pairs.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(pairs[0].keys()))
            w.writeheader()
            w.writerows(pairs)

    lines = ["# Nanite vs LOD bench - summary", "",
             "flights used: %d, dropped (invalid): %d, without meta.json: %d; arms: %s; baseline: %s" % (
                 len(used), len(dropped), len(warn), ", ".join(arms), base), ""]
    for fl in used:
        s = fl["summary"]
        lines.append("- `%s` arm=%s mode=%s layout=%s rhi=%s adapter=%s use_nanite=%s viewport=%s" % (
            fl["tag"], fl["arm"], s.get("mode"), s.get("layout"), s.get("rhi"), s.get("adapter"), s.get("use_nanite"),
            s.get("viewport")))
    for d in dropped:
        lines.append("- DROPPED (invalid): `%s`" % d)
    lines.append("")
    head = ["asset", "layout", "count", "bookmark", "unlit"]
    head += ["fps %s" % a for a in arms]
    head += ["frame ms %s" % a for a in arms]
    head += ["GPU ms %s" % a for a in arms]
    head += ["RT ms %s" % a for a in arms]
    head += ["GT ms %s" % a for a in arms]
    head += ["draws %s" % a for a in arms]
    head += ["spread ms %s" % a for a in arms]
    head += ["n %s" % "/".join(arms)]
    lines.append("| " + " | ".join(head) + " |")
    lines.append("|" + "|".join(["---"] * 2 + ["---:"] + ["---"] + ["---:"] * (len(head) - 4)) + "|")
    for r in pairs:
        cells = [r["asset"], r["layout"], str(r["count"]), r["bookmark_name"], str(r["unlit"])]
        cells += [fmt(r["fps_%s" % a]) for a in arms]
        cells += [fmt(r["%s_frame_ms_med" % a], 2) for a in arms]
        cells += [fmt(r["%s_gpu_ms_med" % a], 2) for a in arms]
        cells += [fmt(r["%s_render_ms_med" % a], 2) for a in arms]
        cells += [fmt(r["%s_game_ms_med" % a], 2) for a in arms]
        cells += [fmt(r["%s_drawcalls_med" % a], 0) for a in arms]
        cells += [fmt(r["%s_frame_ms_med_spread" % a], 2) for a in arms]
        cells += ["/".join(str(r["%s_frame_ms_med_n" % a]) for a in arms)]
        lines.append("| " + " | ".join(cells) + " |")
    with open(out + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(out + ".json", "w", encoding="utf-8") as f:
        json.dump({"used": [fl["dir"] for fl in used], "dropped": dropped, "no_meta": warn, "arms": arms,
                   "baseline": base, "pairs": pairs}, f, indent=1, allow_nan=True)
    print("NLB_ANALYZE flights=%d used=%d dropped=%d arms=%s rows=%d -> %s.md" % (
        len(flights), len(used), len(dropped), ",".join(arms), len(pairs), out))


if __name__ == "__main__":
    main()
