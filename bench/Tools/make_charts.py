"""Make the SVG charts of the Nanite-vs-LOD site from analyze_nlb.py output (stdlib only).

usage: py -I make_charts.py <report.json from analyze_nlb.py> <out dir> [arm order, e.g. M,L,Nz,Ns]
Writes <out>/fps-vs-copies.svg (lit, one panel per camera view), <out>/frame-8192.svg (frame / GPU / render-thread ms per
arm at 8192 copies, lit and unlit) and prints NLB_CHARTS ... .
"""
import json
import os
import sys

COL = {"M": "#f0a640", "L": "#98a2ab", "Nz": "#4fa3e0", "Ns": "#5cc8a8", "LOD": "#98a2ab", "Nanite": "#4fa3e0", "Mi": "#e06a4f", "Mx": "#ffd166", "Nzi": "#a78bfa"}
NAME = {"M": "LOD, Megascans tiers", "L": "LOD, auto (article)", "Nz": "Nanite, Zone9 knobs", "Ns": "Nanite, stock knobs", "Mi": "LOD, one ISM", "Mx": "LOD aggressive, one ISM", "Nzi": "Nanite, one ISM"}
INK, MUTED, LINE, PANEL = "#e7eaec", "#98a2ab", "#2a3139", "#171c21"
FONT = 'font-family="Source Sans 3, Segoe UI, system-ui, sans-serif"'


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def num(v):
    return isinstance(v, (int, float)) and v == v


def fps_panels(pairs, arms, path):
    lit = [r for r in pairs if r["unlit"] == 0]
    views = []
    for r in lit:
        if (r["bookmark"], r["bookmark_name"]) not in views:
            views.append((r["bookmark"], r["bookmark_name"]))
    views.sort()
    counts = sorted({r["count"] for r in lit})
    cols, pw, ph, gap, top = 4, 210, 170, 18, 64
    rows = (len(views) + cols - 1) // cols
    W = cols * pw + (cols - 1) * gap + 40
    H = top + rows * (ph + 44) + 20
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (W, H, W, H),
           '<rect width="100%%" height="100%%" fill="#101316"/>',
           '<text x="20" y="28" fill="%s" font-size="18" font-weight="600" %s>FPS against copies, lit (median over flights)</text>' % (INK, FONT)]
    lx = 20
    for a in arms:
        out.append('<rect x="%d" y="40" width="14" height="4" fill="%s"/><text x="%d" y="47" fill="%s" font-size="13" %s>%s</text>' % (
            lx, COL.get(a, INK), lx + 20, MUTED, FONT, esc(NAME.get(a, a))))
        lx += 30 + 7 * len(NAME.get(a, a))
    for i, (bid, bname) in enumerate(views):
        cx = 20 + (i % cols) * (pw + gap)
        cy = top + (i // cols) * (ph + 44)
        pts = {a: [] for a in arms}
        for r in lit:
            if r["bookmark"] != bid:
                continue
            for a in arms:
                f = r.get("fps_" + a)
                if num(f):
                    pts[a].append((r["count"], f))
        ymax = max([f for a in arms for _, f in pts[a]] + [1.0]) * 1.08
        out.append('<rect x="%d" y="%d" width="%d" height="%d" rx="6" fill="%s" stroke="%s"/>' % (cx, cy, pw, ph + 30, PANEL, LINE))
        out.append('<text x="%d" y="%d" fill="%s" font-size="13" font-weight="600" %s>%s</text>' % (cx + 10, cy + 18, INK, FONT, esc(bname)))
        x0, x1, y0, y1 = cx + 36, cx + pw - 10, cy + ph, cy + 30

        def X(c):
            return x0 + (counts.index(c) / max(1, len(counts) - 1)) * (x1 - x0)

        def Y(v):
            return y0 - (v / ymax) * (y0 - y1)
        for t in range(0, 5):
            v = ymax * t / 4
            out.append('<line x1="%d" x2="%d" y1="%.1f" y2="%.1f" stroke="%s"/>' % (x0, x1, Y(v), Y(v), LINE))
            out.append('<text x="%d" y="%.1f" fill="%s" font-size="10" text-anchor="end" %s>%d</text>' % (x0 - 4, Y(v) + 3, MUTED, FONT, v))
        for c in counts:
            out.append('<text x="%.1f" y="%d" fill="%s" font-size="10" text-anchor="middle" %s>%d</text>' % (X(c), y0 + 14, MUTED, FONT, c))
        for a in arms:
            p = sorted(pts[a])
            if not p:
                continue
            d = " ".join("%s%.1f,%.1f" % ("M" if k == 0 else "L", X(c), Y(f)) for k, (c, f) in enumerate(p))
            out.append('<path d="%s" fill="none" stroke="%s" stroke-width="2"/>' % (d, COL.get(a, INK)))
            for c, f in p:
                out.append('<circle cx="%.1f" cy="%.1f" r="2.6" fill="%s"><title>%s %d copies: %.1f fps</title></circle>' % (
                    X(c), Y(f), COL.get(a, INK), esc(NAME.get(a, a)), c, f))
    out.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def frame_bars(pairs, arms, count, path):
    rows = sorted([r for r in pairs if r["count"] == count], key=lambda r: (r["unlit"], r["bookmark"]))
    bar, gapa, gapv, left, top = 9, 2, 14, 190, 70
    per = len(arms) * (bar + gapa) + gapv
    W, H = 900, top + len(rows) * per + 40
    vmax = max([r.get("%s_frame_ms_med" % a, 0) for r in rows for a in arms if num(r.get("%s_frame_ms_med" % a))] + [1]) * 1.05
    sx = (W - left - 30) / vmax
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (W, H, W, H),
           '<rect width="100%%" height="100%%" fill="#101316"/>',
           '<text x="20" y="28" fill="%s" font-size="18" font-weight="600" %s>Frame time at %d copies (bar = frame ms; tick = GPU ms; ring = render thread ms)</text>' % (INK, FONT, count)]
    lx = 20
    for a in arms:
        out.append('<rect x="%d" y="40" width="14" height="8" fill="%s"/><text x="%d" y="48" fill="%s" font-size="13" %s>%s</text>' % (
            lx, COL.get(a, INK), lx + 20, MUTED, FONT, esc(NAME.get(a, a))))
        lx += 30 + 7 * len(NAME.get(a, a))
    for t in range(0, int(vmax) + 1, 5 if vmax > 25 else 2):
        x = left + t * sx
        out.append('<line x1="%.1f" x2="%.1f" y1="%d" y2="%d" stroke="%s"/>' % (x, x, top - 8, H - 30, LINE))
        out.append('<text x="%.1f" y="%d" fill="%s" font-size="10" text-anchor="middle" %s>%d ms</text>' % (x, H - 16, MUTED, FONT, t))
    y = top
    for r in rows:
        label = "%s%s" % (r["bookmark_name"], " (unlit)" if r["unlit"] else "")
        out.append('<text x="%d" y="%d" fill="%s" font-size="12" text-anchor="end" %s>%s</text>' % (left - 8, y + per / 2 - 4, INK, FONT, esc(label)))
        for k, a in enumerate(arms):
            fm, g, rt = (r.get("%s_%s" % (a, m)) for m in ("frame_ms_med", "gpu_ms_med", "render_ms_med"))
            yy = y + k * (bar + gapa)
            if num(fm):
                out.append('<rect x="%d" y="%d" width="%.1f" height="%d" fill="%s"><title>%s %s: frame %.2f ms (%.0f fps), GPU %.2f, RT %.2f, draws %s</title></rect>' % (
                    left, yy, fm * sx, bar, COL.get(a, INK), esc(NAME.get(a, a)), esc(label), fm, 1000 / fm if fm else 0,
                    g if num(g) else -1, rt if num(rt) else -1, r.get("%s_drawcalls_med" % a)))
                if num(g):
                    out.append('<rect x="%.1f" y="%d" width="2" height="%d" fill="%s"/>' % (left + g * sx - 1, yy - 1, bar + 2, INK))
                if num(rt):
                    out.append('<circle cx="%.1f" cy="%.1f" r="3" fill="none" stroke="%s"/>' % (left + rt * sx, yy + bar / 2, INK))
        y += per
    out.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def main():
    rep = json.load(open(sys.argv[1], encoding="utf-8"))
    out = sys.argv[2]
    os.makedirs(out, exist_ok=True)
    arms = sys.argv[3].split(",") if len(sys.argv) > 3 else rep["arms"]
    arms = [a for a in arms if a in rep["arms"]]
    pairs = rep["pairs"]
    fps_panels(pairs, arms, os.path.join(out, "fps-vs-copies.svg"))
    top = max(r["count"] for r in pairs)
    frame_bars(pairs, arms, top, os.path.join(out, "frame-%d.svg" % top))
    print("NLB_CHARTS arms=%s rows=%d -> %s" % (",".join(arms), len(pairs), out))


if __name__ == "__main__":
    main()
