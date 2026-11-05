"""Build the static site of the Nanite-vs-LOD bench from analyze_nlb.py output, in English or Vietnamese. Stdlib only.

usage: py -I tools/make_site.py <main report.json> <appendix report.json or -> <notes.json> <site root> [<ism report.json>]
                                [lang=en|vi] [out=<html path>] [base=<prefix for charts/ and img/>] [artifact=1]
  main      = Megascans table campaign (arms M, Nz, Ns)
  appendix  = procedural-table campaign (arms L, Nz, Ns), optional
  notes     = page text (intro, verdict, ism, appendix, setup_html, method_html, compare), one file per language
  lang=vi   = Vietnamese labels; charts go to charts/vi/... (chart text is localised too)
  out       = output file (default <site root>/index.html; e.g. <site root>/vi/index.html with base=../)
  artifact=1 = no <!doctype>/<html>/<head> wrapper (claude.ai Artifact skeleton), safe-area side gutter
"""
import html
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

T = {
    "en": {
        "names": {"M": "LOD (Megascans tiers)", "L": "LOD (auto, article)", "Nz": "Nanite, Zone9 knobs", "Ns": "Nanite, stock knobs",
                  "Mi": "LOD, one ISM", "Mx": "LOD aggressive, one ISM", "Mimp": "LOD + impostor, one ISM", "Nzi": "Nanite, one ISM"},
        "title": "Nanite vs LOD bench", "eyebrow": "Unreal Engine 5.8 · Radeon 680M handheld · 1280×800", "h1": "Nanite vs LOD",
        "verdict": "Verdict", "glance": "At a glance", "compare": "Compare", "shots": "What the camera sees", "setup": "Setup",
        "numbers": "Numbers, %d copies, lit", "unlit": "%d copies, unlit", "lit": "%d copies, lit", "lit1": "1 copy, lit",
        "cap_fps": "Megascans table: FPS against the number of copies, lit, one panel per camera view (median of two flights per arm).",
        "cap_frame": "Frame time at %d copies, lit and unlit: bar = frame, white tick = GPU, ring = render thread.",
        "tnote": 'FPS = 1000 / median frame time of the measured window (5 s per view and count). Two flights per arm, median of the two; "spread" = the difference between them, the run-to-run noise. GPU = last finished GPU frame, RT = render thread. Bold = fastest arm in the row.',
        "ism_h": "Instanced: one ISM component instead of 8192 actors",
        "ism_cap": "LOD as separate actors (control flight) against one ISM component, LOD and Nanite meshes.",
        "app_h": "Appendix: procedural table, auto LODs as in the article", "app_cap": "Procedural 249k-triangle table, one flight per arm.",
        "method": "Method",
        "foot": 'Source, configs, tools and every flight\'s CSV/JSON are in the <a href="https://github.com/windystrife/nanite-lod-bench">repository</a>. The Megascans asset itself is not redistributed.',
        "th_view": "View", "th_fps": "%s FPS", "th_gpu": "GPU ms %s", "th_rt": "RT ms %s", "th_spread": "spread ms",
        "left": "Left", "right": "Right", "view": "View", "nodata": "no data", "divider": "Comparison divider",
        "vnote": "Drag across the picture. 8192 copies on the Radeon 680M at 1280×800; FPS and GPU time are the medians of the flights of that arm at that view.",
        "other": ("Tiếng Việt", "vi/"),
    },
    "vi": {
        "names": {"M": "LOD (tier Megascans)", "L": "LOD (auto, như bài báo)", "Nz": "Nanite, cấu hình Zone9", "Ns": "Nanite, mặc định engine",
                  "Mi": "LOD, một ISM", "Mx": "LOD gắt, một ISM", "Mimp": "LOD + impostor, một ISM", "Nzi": "Nanite, một ISM"},
        "title": "Nanite vs LOD bench", "eyebrow": "Unreal Engine 5.8 · máy cầm tay Radeon 680M · 1280×800", "h1": "Nanite vs LOD",
        "verdict": "Kết luận", "glance": "Nhìn nhanh", "compare": "So sánh", "shots": "Camera thấy gì", "setup": "Cấu hình đo",
        "numbers": "Số liệu, %d bàn, có chiếu sáng", "unlit": "%d bàn, không chiếu sáng", "lit": "%d bàn, có chiếu sáng", "lit1": "1 bàn, có chiếu sáng",
        "cap_fps": "Bàn Megascans: FPS theo số bàn, có chiếu sáng, mỗi ô một góc camera (trung vị hai lượt bay mỗi arm).",
        "cap_frame": "Thời gian frame ở %d bàn, có và không chiếu sáng: thanh = frame, vạch trắng = GPU, vòng tròn = render thread.",
        "tnote": 'FPS = 1000 / trung vị thời gian frame trong cửa sổ đo (5 giây mỗi góc và mỗi mức số bàn). Hai lượt bay mỗi arm, lấy trung vị; "spread" = chênh lệch giữa hai lượt, tức độ nhiễu. GPU = frame GPU vừa xong, RT = render thread. In đậm = arm nhanh nhất trong dòng.',
        "ism_h": "Gộp instance: một ISM thay cho 8192 actor",
        "ism_cap": "LOD dạng actor riêng (lượt đối chứng) so với một component ISM, mesh LOD và mesh Nanite.",
        "app_h": "Phụ lục: bàn tự sinh, LOD tự động như bài báo", "app_cap": "Bàn tự sinh 249k tam giác, một lượt bay mỗi arm.",
        "method": "Cách đo",
        "foot": 'Mã nguồn, cấu hình, công cụ và CSV/JSON của từng lượt bay có trong <a href="https://github.com/windystrife/nanite-lod-bench">repository</a>. Asset Megascans không được phát lại.',
        "th_view": "Góc", "th_fps": "%s FPS", "th_gpu": "GPU ms %s", "th_rt": "RT ms %s", "th_spread": "nhiễu ms",
        "left": "Trái", "right": "Phải", "view": "Góc", "nodata": "không có số", "divider": "Thanh chia so sánh",
        "vnote": "Kéo qua lại trên ảnh. 8192 bàn trên Radeon 680M ở 1280×800; FPS và thời gian GPU là trung vị các lượt bay của arm đó ở góc đó.",
        "other": ("English", "../"),
    },
}


def fmt(v, p=1):
    return ("%%.%df" % p) % v if isinstance(v, (int, float)) and v == v else "–"


def table(L, pairs, arms, count, unlit, base):
    rows = sorted([r for r in pairs if r["count"] == count and r["unlit"] == unlit], key=lambda r: r["bookmark"])
    h = ['<div class="cmp"><table><thead><tr><th>%s</th>' % L["th_view"]]
    h += ['<th%s>%s</th>' % (' class="w"' if a == base else "", html.escape(L["th_fps"] % L["names"].get(a, a))) for a in arms]
    h += ['<th>%s / %s</th>' % (html.escape(a), html.escape(base)) for a in arms if a != base]
    h += ['<th>%s</th>' % html.escape(L["th_gpu"] % "/".join(arms)), '<th>%s</th>' % html.escape(L["th_rt"] % "/".join(arms)),
          '<th>%s</th></tr></thead><tbody>' % L["th_spread"]]
    for r in rows:
        fb = r.get("fps_" + base)
        cells = ['<th>%s</th>' % html.escape(r["bookmark_name"])]
        best = max([r.get("fps_" + a) or 0 for a in arms])
        for a in arms:
            f = r.get("fps_" + a)
            cells.append('<td>%s%s%s</td>' % ("<b>" if f == best else "", fmt(f), "</b>" if f == best else ""))
        for a in arms:
            if a != base:
                f = r.get("fps_" + a)
                cells.append('<td>%s</td>' % (fmt(f / fb, 2) + "×" if fb and f == f and fb == fb else "–"))
        cells.append('<td>%s</td>' % " / ".join(fmt(r.get("%s_gpu_ms_med" % a), 2) for a in arms))
        cells.append('<td>%s</td>' % " / ".join(fmt(r.get("%s_render_ms_med" % a), 2) for a in arms))
        cells.append('<td>%s</td>' % " / ".join(fmt(r.get("%s_frame_ms_med_spread" % a), 2) for a in arms))
        h.append("<tr>" + "".join(cells) + "</tr>")
    h.append("</tbody></table></div>")
    return "\n".join(h)


def compare_viewer(L, cfg, reports, base):
    """Drag-to-compare stage (windyGI style): two stacked images, the top one clipped at the divider."""
    views = cfg["views"]
    src = {}
    for s in cfg["sources"]:
        rep = reports.get(s["report"])
        fps = {}
        for v, _label in views:
            bm = v.split("_", 1)[1]
            cnt = int(v[1:6])
            unlit = 1 if bm.endswith("_unlit") else 0
            bm = bm[:-6] if unlit else bm
            row = next((r for r in rep["pairs"] if r["count"] == cnt and r["bookmark_name"] == bm and r["unlit"] == unlit), None) if rep else None
            f = row.get("fps_" + s["arm"]) if row else None
            g = row.get("%s_gpu_ms_med" % s["arm"]) if row else None
            fps[v] = [round(f, 1) if isinstance(f, (int, float)) and f == f else None,
                      round(g, 2) if isinstance(g, (int, float)) and g == g else None]
        src[s["id"]] = {"label": s["label"], "sub": s.get("sub", ""), "img": base + s["img"], "fps": fps}
    data = json.dumps({"views": views, "src": src, "left": cfg["left"], "right": cfg["right"], "nodata": L["nodata"]}, ensure_ascii=False)
    opts = "".join('<option value="%s">%s</option>' % (html.escape(k), html.escape(v["label"])) for k, v in src.items())
    vopts = "".join('<option value="%s">%s</option>' % (html.escape(v), html.escape(l)) for v, l in views)
    return """<section class="viewer" aria-label="%s">
  <div class="controls">
    <label class="pick">%s <select id="cmpL">%s</select></label>
    <label class="pick">%s <select id="cmpR">%s</select></label>
    <label class="pick">%s <select id="cmpV">%s</select></label>
  </div>
  <div class="stage" id="stage">
    <img id="imgL" alt="">
    <img id="imgR" class="top" alt="">
    <div class="handle"></div>
    <div class="tag l" id="tagL"></div>
    <div class="tag r" id="tagR"></div>
    <input type="range" id="cut" min="0" max="100" value="50" step="0.1" aria-label="%s">
  </div>
  <p class="note">%s</p>
</section>
<script>
(() => {
  const D = %s;
  const $ = id => document.getElementById(id);
  const L = $('cmpL'), R = $('cmpR'), V = $('cmpV'), st = $('stage'), cut = $('cut');
  L.value = D.left; R.value = D.right; V.value = D.views[0][0];
  const tag = (s, v) => { const f = s.fps[v] || [];
    return '<div class="t1">' + s.label + '</div><div class="t2">' + (f[0] != null ? '<b>' + f[0].toFixed(1) + ' FPS</b> · ' + (1000 / f[0]).toFixed(2) + ' ms' : D.nodata) + '</div>' +
      (f[1] != null ? '<div class="t3">GPU ' + f[1].toFixed(2) + ' ms' + (s.sub ? ' · ' + s.sub : '') + '</div>' : ''); };
  const render = () => { const v = V.value, a = D.src[L.value], b = D.src[R.value];
    $('imgL').src = a.img.replace('{v}', v); $('imgR').src = b.img.replace('{v}', v);
    $('imgL').alt = a.label; $('imgR').alt = b.label;
    $('tagL').innerHTML = tag(a, v); $('tagR').innerHTML = tag(b, v); };
  cut.addEventListener('input', () => st.style.setProperty('--cut', cut.value + '%%'));
  [L, R, V].forEach(e => e.addEventListener('change', render));
  render();
})();
</script>""" % (L["compare"], L["left"], opts, L["right"], opts, L["view"], vopts, L["divider"], L["vnote"], data)


def main():
    pos = [a for a in sys.argv[1:] if "=" not in a or a.startswith(("I:", "C:", "H:"))]
    kv = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a and not a.startswith(("I:", "C:", "H:")))
    lang = kv.get("lang", "en")
    L = T[lang]
    base = kv.get("base", "")
    artifact = kv.get("artifact") == "1"
    main_rep = json.load(open(pos[0], encoding="utf-8"))
    app_rep = json.load(open(pos[1], encoding="utf-8")) if pos[1] != "-" else None
    notes = json.load(open(pos[2], encoding="utf-8"))
    root = pos[3]
    ism_rep = json.load(open(pos[4], encoding="utf-8")) if len(pos) > 4 else None
    cdir = "charts" if lang == "en" else "charts/" + lang
    mk = os.path.join(HERE, "..", "bench", "Tools", "make_charts.py")
    for rep, sub, arms in ((pos[0], "megascans", "M,Nz,Ns"), (pos[1] if app_rep else None, "procedural", "L,Nz,Ns"),
                           (pos[4] if ism_rep else None, "ism", "M,Mi,Mx,Mimp,Nzi")):
        if rep:
            subprocess.check_call([sys.executable, "-I", mk, rep, os.path.join(root, cdir, sub), arms, lang])
    C = base + cdir
    arms = [a for a in ("M", "Nz", "Ns") if a in main_rep["arms"]]
    top = max(r["count"] for r in main_rep["pairs"])
    css = open(os.path.join(HERE, "site.css"), encoding="utf-8").read()
    p = []
    if not artifact:
        p.append('<!doctype html>\n<html lang="%s">\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">' % lang)
    p.append('<title>%s</title>' % L["title"])
    if artifact:
        p.append('<meta name="claude:viewport-fit-sides" content="cover">')
    p.append('<link rel="preconnect" href="https://fonts.googleapis.com">')
    p.append('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Semi+Condensed:wght@600&family=Source+Sans+3:wght@400;600&family=IBM+Plex+Mono:wght@500&display=swap">')
    extra = ""
    if artifact:
        extra = ("\n:root { color-scheme: dark; }\nmain { padding-left: max(16px, env(safe-area-inset-left, 0px)); "
                 "padding-right: max(16px, env(safe-area-inset-right, 0px)); }")
    p.append("<style>\n" + css + extra + "\n</style>\n<main>")
    other = '' if artifact else ' · <a href="%s">%s</a>' % (L["other"][1], L["other"][0])
    p.append('<div class="eyebrow">%s%s</div>' % (L["eyebrow"], other))
    p.append('<h1>%s</h1>' % L["h1"])
    for para in notes.get("intro", []):
        p.append("<p>%s</p>" % para)
    p.append('<h2>%s</h2>' % L["verdict"])
    for para in notes.get("verdict", []):
        p.append("<p>%s</p>" % para)
    p.append('<h2>%s</h2><div class="charts">' % L["glance"])
    p.append('<figure><a href="%s/megascans/fps-vs-copies.svg"><img src="%s/megascans/fps-vs-copies.svg" alt="%s"></a><figcaption>%s</figcaption></figure>'
             % (C, C, html.escape(L["cap_fps"]), L["cap_fps"]))
    p.append('<figure><a href="%s/megascans/frame-%d.svg"><img src="%s/megascans/frame-%d.svg" alt="%s"></a><figcaption>%s</figcaption></figure>'
             % (C, top, C, top, html.escape(L["cap_frame"] % top), L["cap_frame"] % top))
    p.append("</div>")
    if notes.get("compare"):
        p.append('<h2 id="compare">%s</h2>' % L["compare"])
        p.append(compare_viewer(L, notes["compare"], {"main": main_rep, "ism": ism_rep, "app": app_rep}, base))
    p.append('<h2 id="setup">%s</h2>' % L["setup"])
    p.append(notes.get("setup_html", ""))
    p.append('<h2 id="numbers">%s</h2>' % (L["numbers"] % top))
    p.append(table(L, main_rep["pairs"], arms, top, 0, arms[0]))
    p.append('<h3>%s</h3>' % (L["unlit"] % top))
    p.append(table(L, main_rep["pairs"], arms, top, 1, arms[0]))
    for c in sorted({r["count"] for r in main_rep["pairs"]}, reverse=True)[1:]:
        p.append('<h3>%s</h3>' % (L["lit1"] if c == 1 else L["lit"] % c))
        p.append(table(L, main_rep["pairs"], arms, c, 0, arms[0]))
    p.append('<p class="note">%s</p>' % L["tnote"])
    if ism_rep:
        p.append('<h2 id="ism">%s</h2>' % L["ism_h"])
        for para in notes.get("ism", []):
            p.append("<p>%s</p>" % para)
        p.append('<div class="charts"><figure><a href="%s/ism/fps-vs-copies.svg"><img src="%s/ism/fps-vs-copies.svg" alt="%s"></a><figcaption>%s</figcaption></figure></div>'
                 % (C, C, html.escape(L["ism_cap"]), L["ism_cap"]))
        it = max(r["count"] for r in ism_rep["pairs"])
        ia = [a for a in ("M", "Mi", "Mx", "Mimp", "Nzi") if a in ism_rep["arms"]]
        p.append('<h3>%s</h3>' % (L["lit"] % it))
        p.append(table(L, ism_rep["pairs"], ia, it, 0, ia[0]))
        p.append('<h3>%s</h3>' % (L["unlit"] % it))
        p.append(table(L, ism_rep["pairs"], ia, it, 1, ia[0]))
    if app_rep:
        p.append('<h2 id="appendix">%s</h2>' % L["app_h"])
        for para in notes.get("appendix", []):
            p.append("<p>%s</p>" % para)
        p.append('<div class="charts"><figure><a href="%s/procedural/fps-vs-copies.svg"><img src="%s/procedural/fps-vs-copies.svg" alt="%s"></a><figcaption>%s</figcaption></figure></div>'
                 % (C, C, html.escape(L["app_cap"]), L["app_cap"]))
        at = max(r["count"] for r in app_rep["pairs"])
        p.append(table(L, app_rep["pairs"], [a for a in ("L", "Nz", "Ns") if a in app_rep["arms"]], at, 0, "L"))
    p.append('<h2 id="method">%s</h2>' % L["method"])
    p.append(notes.get("method_html", ""))
    p.append('<p class="note">%s</p>' % L["foot"])
    p.append("</main>" + ("" if artifact else "\n</html>") + "\n")
    out = kv.get("out", os.path.join(root, "index.html"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as fo:
        fo.write("\n".join(p))
    print("NLB_SITE ok lang=%s artifact=%s -> %s" % (lang, artifact, out))


if __name__ == "__main__":
    main()
