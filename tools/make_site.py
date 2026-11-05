"""Build the static site (index.html + charts) of the Nanite-vs-LOD bench from analyze_nlb.py output. Stdlib only.

usage: py -I tools/make_site.py <main report.json> <appendix report.json or -> <notes.json> <site root> [<ism report.json>]
  main      = Megascans table campaign (arms M, Nz, Ns)
  appendix  = procedural-table campaign (arms L, Nz, Ns), optional
  notes     = {"verdict": [html paragraphs], "facts": {...}} written by hand after reading the numbers
"""
import html
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = {"M": "LOD (Megascans tiers)", "L": "LOD (auto, article)", "Nz": "Nanite, Zone9 knobs", "Ns": "Nanite, stock knobs",
        "Mi": "LOD, one ISM", "Mx": "LOD aggressive, one ISM", "Nzi": "Nanite, one ISM"}


def fmt(v, p=1):
    return ("%%.%df" % p) % v if isinstance(v, (int, float)) and v == v else "–"


def table(pairs, arms, count, unlit, base):
    rows = [r for r in pairs if r["count"] == count and r["unlit"] == unlit]
    rows.sort(key=lambda r: r["bookmark"])
    h = ['<div class="cmp"><table><thead><tr><th>View</th>']
    h += ['<th%s>%s FPS</th>' % (' class="w"' if a == base else "", html.escape(NAME.get(a, a))) for a in arms]
    h += ['<th>%s / %s</th>' % (html.escape(a), html.escape(base)) for a in arms if a != base]
    h += ['<th>GPU ms %s</th>' % html.escape("/".join(arms)), '<th>RT ms %s</th>' % html.escape("/".join(arms)),
          '<th>spread ms</th></tr></thead><tbody>']
    for r in rows:
        fb = r.get("fps_" + base)
        cells = ['<th>%s</th>' % html.escape(r["bookmark_name"])]
        best = max([r.get("fps_" + a) or 0 for a in arms])
        for a in arms:
            f = r.get("fps_" + a)
            cells.append('<td>%s%s%s</td>' % ("<b>" if f == best else "", fmt(f), "</b>" if f == best else ""))
        for a in arms:
            if a == base:
                continue
            f = r.get("fps_" + a)
            cells.append('<td>%s</td>' % (fmt(f / fb, 2) + "×" if fb and f == f and fb == fb else "–"))
        cells.append('<td>%s</td>' % " / ".join(fmt(r.get("%s_gpu_ms_med" % a), 2) for a in arms))
        cells.append('<td>%s</td>' % " / ".join(fmt(r.get("%s_render_ms_med" % a), 2) for a in arms))
        cells.append('<td>%s</td>' % " / ".join(fmt(r.get("%s_frame_ms_med_spread" % a), 2) for a in arms))
        h.append("<tr>" + "".join(cells) + "</tr>")
    h.append("</tbody></table></div>")
    return "\n".join(h)


def main():
    main_rep = json.load(open(sys.argv[1], encoding="utf-8"))
    app_rep = json.load(open(sys.argv[2], encoding="utf-8")) if sys.argv[2] != "-" else None
    notes = json.load(open(sys.argv[3], encoding="utf-8"))
    root = sys.argv[4]
    ism_rep = json.load(open(sys.argv[5], encoding="utf-8")) if len(sys.argv) > 5 else None
    charts = os.path.join(root, "charts")
    mk = os.path.join(HERE, "..", "bench", "Tools", "make_charts.py")
    subprocess.check_call([sys.executable, "-I", mk, sys.argv[1], os.path.join(charts, "megascans"), "M,Nz,Ns"])
    if app_rep:
        subprocess.check_call([sys.executable, "-I", mk, sys.argv[2], os.path.join(charts, "procedural"), "L,Nz,Ns"])
    if ism_rep:
        subprocess.check_call([sys.executable, "-I", mk, sys.argv[5], os.path.join(charts, "ism"), "M,Mi,Mx,Nzi"])
    arms = [a for a in ("M", "Nz", "Ns") if a in main_rep["arms"]]
    top = max(r["count"] for r in main_rep["pairs"])
    f = notes.get("facts", {})
    css = open(os.path.join(HERE, "site.css"), encoding="utf-8").read()
    p = []
    p.append('<!doctype html>\n<html lang="en">\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">')
    p.append('<title>Nanite vs LOD bench</title>')
    p.append('<link rel="preconnect" href="https://fonts.googleapis.com">')
    p.append('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Semi+Condensed:wght@600&family=Source+Sans+3:wght@400;600&family=IBM+Plex+Mono:wght@500&display=swap">')
    p.append("<style>\n" + css + "\n</style>\n<main>")
    p.append('<div class="eyebrow">Unreal Engine 5.8 · Radeon 680M handheld · 1280×800</div>')
    p.append('<h1>Nanite vs LOD</h1>')
    for para in notes.get("intro", []):
        p.append("<p>%s</p>" % para)
    p.append('<h2>Verdict</h2>')
    for para in notes.get("verdict", []):
        p.append("<p>%s</p>" % para)
    p.append('<h2>At a glance</h2><div class="charts">')
    p.append('<figure><a href="charts/megascans/fps-vs-copies.svg"><img src="charts/megascans/fps-vs-copies.svg" alt="FPS against copies per view, Megascans table"></a>'
             '<figcaption>Megascans table: FPS against the number of copies, lit, one panel per camera view (median of two flights per arm).</figcaption></figure>')
    p.append('<figure><a href="charts/megascans/frame-%d.svg"><img src="charts/megascans/frame-%d.svg" alt="Frame time at %d copies"></a>'
             '<figcaption>Frame time at %d copies, lit and unlit: bar = frame, white tick = GPU, ring = render thread.</figcaption></figure>' % (top, top, top, top))
    p.append("</div>")
    if notes.get("shots"):
        p.append('<h2>What the camera sees</h2><div class="shots">')
        for s in notes["shots"]:
            p.append('<figure><a href="%s"><img src="%s" alt="%s" loading="lazy"></a><figcaption>%s</figcaption></figure>' % (
                s["src"], s["src"], html.escape(s["caption"]), html.escape(s["caption"])))
        p.append("</div>")
    p.append('<h2 id="setup">Setup</h2>')
    p.append(notes.get("setup_html", ""))
    p.append('<h2 id="numbers">Numbers, %d copies, lit</h2>' % top)
    p.append(table(main_rep["pairs"], arms, top, 0, arms[0]))
    p.append('<h3>%d copies, unlit</h3>' % top)
    p.append(table(main_rep["pairs"], arms, top, 1, arms[0]))
    for c in sorted({r["count"] for r in main_rep["pairs"]}, reverse=True)[1:]:
        p.append('<h3>%d cop%s, lit</h3>' % (c, "y" if c == 1 else "ies"))
        p.append(table(main_rep["pairs"], arms, c, 0, arms[0]))
    p.append('<p class="note">FPS = 1000 / median frame time of the measured window (5 s per view and count). Two flights per arm, '
             'median of the two; "spread" = the difference between them, the run-to-run noise. GPU = last finished GPU frame, '
             'RT = render thread. Bold = fastest arm in the row.</p>')
    if ism_rep:
        p.append('<h2 id="ism">Instanced: one ISM component instead of 8192 actors</h2>')
        for para in notes.get("ism", []):
            p.append("<p>%s</p>" % para)
        p.append('<div class="charts"><figure><a href="charts/ism/fps-vs-copies.svg"><img src="charts/ism/fps-vs-copies.svg" alt="FPS against copies, ISM"></a>'
                 '<figcaption>LOD as separate actors (control flight) against one ISM component, LOD and Nanite meshes.</figcaption></figure></div>')
        it = max(r["count"] for r in ism_rep["pairs"])
        ia = [a for a in ("M", "Mi", "Mx", "Nzi") if a in ism_rep["arms"]]
        p.append('<h3>%d copies, lit</h3>' % it)
        p.append(table(ism_rep["pairs"], ia, it, 0, ia[0]))
        p.append('<h3>%d copies, unlit</h3>' % it)
        p.append(table(ism_rep["pairs"], ia, it, 1, ia[0]))
    if app_rep:
        p.append('<h2 id="appendix">Appendix: procedural table, auto LODs as in the article</h2>')
        for para in notes.get("appendix", []):
            p.append("<p>%s</p>" % para)
        p.append('<div class="charts"><figure><a href="charts/procedural/fps-vs-copies.svg"><img src="charts/procedural/fps-vs-copies.svg" alt="FPS against copies, procedural table"></a>'
                 '<figcaption>Procedural 249k-triangle table, one flight per arm.</figcaption></figure></div>')
        at = max(r["count"] for r in app_rep["pairs"])
        p.append(table(app_rep["pairs"], [a for a in ("L", "Nz", "Ns") if a in app_rep["arms"]], at, 0, "L"))
    p.append('<h2 id="method">Method</h2>')
    p.append(notes.get("method_html", ""))
    p.append('<p class="note">Source, configs, tools and every flight\'s CSV/JSON are in the <a href="https://github.com/windystrife/nanite-lod-bench">repository</a>. '
             'The Megascans asset itself is not redistributed.</p>')
    p.append("</main>\n</html>\n")
    with open(os.path.join(root, "index.html"), "w", encoding="utf-8", newline="\n") as fo:
        fo.write("\n".join(p))
    print("NLB_SITE ok ->", os.path.join(root, "index.html"))


if __name__ == "__main__":
    main()
