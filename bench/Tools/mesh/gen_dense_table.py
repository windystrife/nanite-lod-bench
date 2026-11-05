#!/usr/bin/env python3
"""gen_dense_table.py - procedural "scan-like" dense wooden table for the Nanite-vs-LOD bench.

Pure stdlib (run with: py -I gen_dense_table.py --out <dir>\\dense_table.obj).
Writes  <name>.obj  + <name>.mtl  + T_<name>_D.png / _N.png / _R.png (unless --tex 0).

Geometry (centimetres, Z-up, as UE's Interchange OBJ translator expects: it only flips Y,
InterchangeOBJTranslator.cpp:186-195, and reads vn/vt, :1282-1283):
  * top slab 200 x 100 x 6 cm, top surface at z = 80
  * 4 legs 8 x 8 x 75 cm (1 cm sunk into the slab), inset 12 cm
  * every part is a rounded box (bevel radius r) built on a uniform lattice whose spacing is
    solved from --tris, so triangle density is uniform like a photogrammetry scan
  * interior lattice points get tangential jitter (+-35 % of a cell) and every quad gets a random
    diagonal => irregular triangulation, no long straight edge loops for the simplifier to exploit
  * displacement along the (continuous) rounded-box normal: anisotropic wood grain + fBm bumps +
    fine scan noise + a few dents + 3 cracks on the top => auto-LOD has to fight real detail
  * one material slot, one OBJ group => one StaticMesh, one section (like a Megascans 3D asset)
  * single non-overlapping 0..1 UV atlas (shelf-packed faces, uniform texel density)
  * smooth per-vertex normals (area weighted), written as vn aligned with v
Textures (UV-atlas space, grain follows each face's grain axis): albedo (sRGB wood), tangent-space
normal map (OpenGL/+Y convention; flip green in UE texture settings if it looks inverted), roughness.
"""
import argparse, math, os, random, struct, sys, time, zlib


# ----------------------------------------------------------------------------- noise
class Noise:
    def __init__(self, seed):
        rnd = random.Random(seed)
        p = list(range(256))
        rnd.shuffle(p)
        self.p = p + p
        self.v = [rnd.random() * 2.0 - 1.0 for _ in range(256)]

    def n3(self, x, y, z):
        xi, yi, zi = math.floor(x), math.floor(y), math.floor(z)
        xf, yf, zf = x - xi, y - yi, z - zi
        u = xf * xf * (3 - 2 * xf); w = yf * yf * (3 - 2 * yf); t = zf * zf * (3 - 2 * zf)
        X, Y, Z = xi & 255, yi & 255, zi & 255
        p, v = self.p, self.v
        a, b = p[X] + Y, p[X + 1] + Y
        aa, ab, ba, bb = p[a] + Z, p[a + 1] + Z, p[b] + Z, p[b + 1] + Z
        x00 = v[p[aa] & 255] + u * (v[p[ba] & 255] - v[p[aa] & 255])
        x10 = v[p[ab] & 255] + u * (v[p[bb] & 255] - v[p[ab] & 255])
        x01 = v[p[aa + 1] & 255] + u * (v[p[ba + 1] & 255] - v[p[aa + 1] & 255])
        x11 = v[p[ab + 1] & 255] + u * (v[p[bb + 1] & 255] - v[p[ab + 1] & 255])
        y0 = x00 + w * (x10 - x00)
        y1 = x01 + w * (x11 - x01)
        return y0 + t * (y1 - y0)

    def fbm(self, x, y, z, octaves=5, lac=2.03, gain=0.5):
        s, a, f = 0.0, 1.0, 1.0
        for _ in range(octaves):
            s += a * self.n3(x * f, y * f, z * f)
            f *= lac
            a *= gain
        return s


# ----------------------------------------------------------------------------- parts
class Part:
    """Rounded box. center/half in cm, grain = axis index (0=x,1=y,2=z) the wood grain runs along."""
    def __init__(self, name, center, half, r, grain):
        self.name, self.c, self.h, self.r, self.grain = name, center, half, r, grain

    def area(self):
        hx, hy, hz = self.h
        return 8.0 * (hx * hy + hy * hz + hx * hz)


def make_parts():
    L, W, H, T = 200.0, 100.0, 80.0, 6.0          # table length, width, height, slab thickness
    leg, inset, sink = 8.0, 12.0, 1.0
    parts = [Part("top", (0.0, 0.0, H - T / 2), (L / 2, W / 2, T / 2), 1.2, 0)]
    lh = (H - T + sink) / 2
    for sx in (-1, 1):
        for sy in (-1, 1):
            cx = sx * (L / 2 - inset - leg / 2)
            cy = sy * (W / 2 - inset - leg / 2)
            parts.append(Part("leg", (cx, cy, lh), (leg / 2, leg / 2, lh), 0.9, 2))
    return parts


def seg_counts(part, s):
    return [max(2, int(round(2.0 * hh / s))) for hh in part.h]


def tri_count(parts, s):
    t = 0
    for p in parts:
        nx, ny, nz = seg_counts(p, s)
        t += 4 * (nx * ny + ny * nz + nx * nz)
    return t


def solve_spacing(parts, target):
    area = sum(p.area() for p in parts)
    s = math.sqrt(area / (target / 2.0))
    for _ in range(30):
        t = tri_count(parts, s)
        if abs(t - target) / target < 0.004:
            break
        s *= math.sqrt(t / target)
    return s


# ----------------------------------------------------------------------------- displacement
def make_displace(noise, dents, cracks):
    def disp(px, py, pz, part, nx, ny, nz):
        g = part.grain
        # anisotropic coordinates: stretched along the grain axis
        q = [px, py, pz]
        st = [0.9, 0.9, 0.9]
        st[g] = 0.03
        gx, gy, gz = q[0] * st[0], q[1] * st[1], q[2] * st[2]
        warp = 0.6 * noise.n3(px * 0.02 + 11.1, py * 0.02, pz * 0.02)
        grain = math.sin((gx + gy + gz) * 6.0 + warp * 8.0 + noise.n3(gx * 2, gy * 2, gz * 2) * 3.0)
        d = 0.10 * grain * abs(grain)                                # rings / grain ridges
        d += 0.22 * noise.fbm(px / 7.0, py / 7.0, pz / 7.0, 4)      # medium scan bumps
        d += 0.05 * noise.n3(px / 0.8 + 5.3, py / 0.8, pz / 0.8)     # fine scan noise
        d += 0.35 * noise.n3(px / 60.0 + 3.7, py / 60.0, pz / 60.0)  # large warp / sag
        for (cx, cy, cz, rad, depth) in dents:
            dx, dy, dz = px - cx, py - cy, pz - cz
            dd = (dx * dx + dy * dy + dz * dz) / (rad * rad)
            if dd < 4.0:
                d -= depth * math.exp(-dd * 2.0)
        if part.name == "top" and nz > 0.7:
            for (y0, x0, x1, amp, width, depth) in cracks:
                if x0 < px < x1:
                    fade = min(1.0, (px - x0) / 15.0, (x1 - px) / 15.0)
                    yc = y0 + amp * math.sin(px * 0.045 + y0)
                    e = (py - yc) / width
                    if e * e < 9.0:
                        d -= depth * fade * math.exp(-e * e)
        return d
    return disp


# ----------------------------------------------------------------------------- build mesh
def build(parts, s, seed):
    rnd = random.Random(seed + 1)
    noise = Noise(seed)
    top = parts[0]
    dents = []
    for _ in range(40):
        dents.append((rnd.uniform(-100, 100), rnd.uniform(-50, 50), top.c[2] + top.h[2] * rnd.choice((-1, 1)),
                      rnd.uniform(0.6, 3.0), rnd.uniform(0.08, 0.45)))
    for _ in range(25):
        lg = rnd.choice(parts[1:])
        dents.append((lg.c[0] + rnd.uniform(-4, 4), lg.c[1] + rnd.uniform(-4, 4), rnd.uniform(2, 72),
                      rnd.uniform(0.4, 1.5), rnd.uniform(0.05, 0.25)))
    cracks = [(rnd.uniform(-40, 40), rnd.uniform(-95, -20), rnd.uniform(20, 95), rnd.uniform(0.5, 2.5),
               rnd.uniform(0.45, 0.9), rnd.uniform(0.2, 0.45)) for _ in range(3)]   # width >= ~1 lattice cell
    disp = make_displace(noise, dents, cracks)

    P = []            # positions (x,y,z)
    pidx = {}         # (part, i, j, k) -> index
    UV = []           # (u, v) atlas-local, filled later
    faces = []        # list per face: dict(rect info, tris)
    tris = []         # (p0,p1,p2, t0,t1,t2)

    def position(pi, part, n3c, i, j, k):
        key = (pi, i, j, k)
        got = pidx.get(key)
        if got is not None:
            return got
        idx3 = (i, j, k)
        base = []
        for a in range(3):
            n = n3c[a]
            hh = part.h[a]
            cell = 2.0 * hh / n
            x = -hh + cell * idx3[a]
            if 0 < idx3[a] < n:
                x += rnd.uniform(-0.35, 0.35) * cell
            base.append(x)
        r = part.r
        inner = [max(-part.h[a] + r, min(part.h[a] - r, base[a])) for a in range(3)]
        dv = [base[a] - inner[a] for a in range(3)]
        ln = math.sqrt(dv[0] ** 2 + dv[1] ** 2 + dv[2] ** 2) or 1e-9
        nrm = [dv[a] / ln for a in range(3)]
        p = [part.c[a] + inner[a] + nrm[a] * r for a in range(3)]
        d = disp(p[0], p[1], p[2], part, nrm[0], nrm[1], nrm[2])
        p = [p[a] + nrm[a] * d for a in range(3)]
        P.append((p[0], p[1], p[2], base))
        pidx[key] = len(P) - 1
        return len(P) - 1

    for pi, part in enumerate(parts):
        n3c = seg_counts(part, s)
        for axis in range(3):
            for side in (0, 1):
                ua, va = [a for a in range(3) if a != axis]
                if va == part.grain:               # put the grain axis on U where possible
                    ua, va = va, ua
                nu, nv = n3c[ua], n3c[va]
                fixed = n3c[axis] * side
                face = {"w": 2 * part.h[ua], "h": 2 * part.h[va], "ua": ua, "va": va, "vt": {}, "part": pi}
                grid = [[0] * (nv + 1) for _ in range(nu + 1)]
                tgrid = [[0] * (nv + 1) for _ in range(nu + 1)]
                for a in range(nu + 1):
                    for b in range(nv + 1):
                        ijk = [0, 0, 0]
                        ijk[axis], ijk[ua], ijk[va] = fixed, a, b
                        pid = position(pi, part, n3c, ijk[0], ijk[1], ijk[2])
                        grid[a][b] = pid
                        base = P[pid][3]
                        UV.append((base[ua] + part.h[ua], base[va] + part.h[va], len(faces)))
                        tgrid[a][b] = len(UV) - 1
                # outward winding: normal of (u x v) must match outward sign of this face
                cross_sign = 1 if ((ua + 1) % 3 == va) else -1
                outward = 1 if side == 1 else -1
                flip = (cross_sign * outward) < 0
                for a in range(nu):
                    for b in range(nv):
                        q = (grid[a][b], grid[a + 1][b], grid[a + 1][b + 1], grid[a][b + 1])
                        t = (tgrid[a][b], tgrid[a + 1][b], tgrid[a + 1][b + 1], tgrid[a][b + 1])
                        if rnd.random() < 0.5:
                            tri_ix = ((0, 1, 2), (0, 2, 3))
                        else:
                            tri_ix = ((0, 1, 3), (1, 2, 3))
                        for (x, y, z) in tri_ix:
                            if flip:
                                y, z = z, y
                            tris.append((q[x], q[y], q[z], t[x], t[y], t[z]))
                faces.append(face)
    return P, UV, faces, tris


def pack_atlas(faces, pad_frac=0.002):
    order = sorted(range(len(faces)), key=lambda f: -faces[f]["h"])
    area = sum(f["w"] * f["h"] for f in faces)
    S = math.sqrt(area) * 1.05
    while True:
        pad = S * pad_frac
        x = y = shelf = 0.0
        ok = True
        pos = {}
        for f in order:
            w, h = faces[f]["w"], faces[f]["h"]
            if w + 2 * pad > S:                        # cannot fit even alone -> grow
                ok = False
                break
            if x + w + 2 * pad > S:
                x, y = 0.0, y + shelf
                shelf = 0.0
            if y + h + 2 * pad > S:
                ok = False
                break
            pos[f] = (x + pad, y + pad)
            x += w + 2 * pad
            shelf = max(shelf, h + 2 * pad)
        if ok:
            return S, pos
        S *= 1.04


def smooth_normals(P, tris):
    N = [[0.0, 0.0, 0.0] for _ in P]
    for (a, b, c, _, _, _) in tris:
        ax, ay, az = P[a][0], P[a][1], P[a][2]
        ux, uy, uz = P[b][0] - ax, P[b][1] - ay, P[b][2] - az
        vx, vy, vz = P[c][0] - ax, P[c][1] - ay, P[c][2] - az
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        for i in (a, b, c):
            n = N[i]
            n[0] += nx; n[1] += ny; n[2] += nz
    out = []
    for n in N:
        l = math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2]) or 1.0
        out.append((n[0] / l, n[1] / l, n[2] / l))
    return out


# ----------------------------------------------------------------------------- textures
def write_png(path, w, h, rows, channels):
    raw = bytearray()
    for r in rows:
        raw.append(0)
        raw += r
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    ctype = {1: 0, 3: 2, 4: 6}[channels]
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(data)


def make_textures(outdir, name, res, seed, faces, S, pos):
    noise = Noise(seed + 7)
    G = 192                                   # coarse noise grid, bilinearly sampled per pixel
    coarse = [[noise.fbm(i / 24.0, j / 24.0, 0.5, 4) for j in range(G + 1)] for i in range(G + 1)]
    def cn(u, v):
        x, y = (u % 1.0) * G, (v % 1.0) * G
        i, j = int(x), int(y)
        fx, fy = x - i, y - j
        a = coarse[i][j] + fx * (coarse[i + 1][j] - coarse[i][j])
        b = coarse[i][j + 1] + fx * (coarse[i + 1][j + 1] - coarse[i][j + 1])
        return a + fy * (b - a)
    # height field in atlas space: grain runs along atlas U inside every face rect (see build())
    H = [[0.0] * res for _ in range(res)]
    for py in range(res):
        v = 1.0 - (py + 0.5) / res            # PNG row 0 = top = OBJ v 1 (UE flips v on import)
        row = H[py]
        for px in range(res):
            u = (px + 0.5) / res
            warp = cn(u * 0.7, v * 0.7)
            g = math.sin(v * 900.0 + warp * 14.0 + cn(u * 3.0, v * 9.0) * 4.0)
            row[px] = 0.55 * g * abs(g) + 0.35 * cn(u * 6.0, v * 6.0) + 0.10 * cn(u * 23.0, v * 31.0)
    alb, nrm, rgh = [], [], []
    dark, light = (78, 52, 33), (160, 118, 78)
    k = 2.5
    for py in range(res):
        ra, rn, rr = bytearray(), bytearray(), bytearray()
        Hm, H0, Hp = H[(py - 1) % res], H[py], H[(py + 1) % res]
        for px in range(res):
            h = H0[px]
            t = max(0.0, min(1.0, 0.5 + 0.5 * h))
            ra += bytes((int(dark[0] + t * (light[0] - dark[0])), int(dark[1] + t * (light[1] - dark[1])),
                         int(dark[2] + t * (light[2] - dark[2]))))
            dx = (H0[(px + 1) % res] - H0[(px - 1) % res]) * k
            dy = (Hm[px] - Hp[px]) * k          # image rows go down; +Y (OpenGL) normal = up in UV space
            l = math.sqrt(dx * dx + dy * dy + 1.0)
            rn += bytes((int((-dx / l * 0.5 + 0.5) * 255), int((-dy / l * 0.5 + 0.5) * 255), int((1.0 / l * 0.5 + 0.5) * 255)))
            rr.append(int(max(0.0, min(1.0, 0.62 - 0.18 * h)) * 255))
        alb.append(ra); nrm.append(rn); rgh.append(rr)
    files = {}
    for suffix, rows, ch in (("D", alb, 3), ("N", nrm, 3), ("R", rgh, 1)):
        fn = f"T_{name}_{suffix}.png"
        write_png(os.path.join(outdir, fn), res, res, rows, ch)
        files[suffix] = fn
    return files


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="output .obj path")
    ap.add_argument("--tris", type=int, default=250000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--tex", type=int, default=1024, help="texture resolution, 0 = no textures")
    a = ap.parse_args()
    t0 = time.time()
    outdir = os.path.dirname(os.path.abspath(a.out))
    name = os.path.splitext(os.path.basename(a.out))[0]
    os.makedirs(outdir, exist_ok=True)

    parts = make_parts()
    s = solve_spacing(parts, a.tris)
    P, UV, faces, tris = build(parts, s, a.seed)
    S, pos = pack_atlas(faces)
    N = smooth_normals(P, tris)
    t1 = time.time()

    tex = make_textures(outdir, name, a.tex, a.seed, faces, S, pos) if a.tex > 0 else {}
    t2 = time.time()

    mtl = os.path.join(outdir, name + ".mtl")
    with open(mtl, "w", encoding="ascii", newline="\n") as f:
        f.write(f"# gen_dense_table.py procedural material\nnewmtl M_{name}\nKd 1.0 1.0 1.0\nKs 0.0 0.0 0.0\n")
        if tex:
            f.write(f"map_Kd {tex['D']}\nnorm {tex['N']}\nmap_Pr {tex['R']}\n")

    mn = [min(p[i] for p in P) for i in range(3)]
    mx = [max(p[i] for p in P) for i in range(3)]
    with open(a.out, "w", encoding="ascii", newline="\n") as f:
        f.write(f"# gen_dense_table.py seed={a.seed} target_tris={a.tris} spacing_cm={s:.4f}\n")
        f.write(f"# units=cm up=+Z verts={len(P)} uvs={len(UV)} tris={len(tris)}\n")
        f.write(f"mtllib {name}.mtl\no {name}\ng {name}\n")
        buf = []
        for p in P:
            buf.append(f"v {p[0]:.4f} {p[1]:.4f} {p[2]:.4f}\n")
        f.write("".join(buf)); buf = []
        inv = 1.0 / S
        for (lu, lv, fi) in UV:
            ox, oy = pos[fi]
            buf.append(f"vt {(ox + lu) * inv:.6f} {(oy + lv) * inv:.6f}\n")
        f.write("".join(buf)); buf = []
        for n in N:
            buf.append(f"vn {n[0]:.4f} {n[1]:.4f} {n[2]:.4f}\n")
        f.write("".join(buf)); buf = []
        f.write(f"usemtl M_{name}\ns 1\n")
        for (a0, b0, c0, ta, tb, tc) in tris:
            buf.append(f"f {a0+1}/{ta+1}/{a0+1} {b0+1}/{tb+1}/{b0+1} {c0+1}/{tc+1}/{c0+1}\n")
            if len(buf) >= 50000:
                f.write("".join(buf)); buf = []
        f.write("".join(buf))
    t3 = time.time()
    size = os.path.getsize(a.out)
    print(f"spacing_cm={s:.4f} parts={len(parts)} verts={len(P)} uvs={len(UV)} tris={len(tris)}")
    print(f"bounds_cm min=({mn[0]:.2f},{mn[1]:.2f},{mn[2]:.2f}) max=({mx[0]:.2f},{mx[1]:.2f},{mx[2]:.2f})")
    print(f"obj={a.out} bytes={size} ({size/1048576:.2f} MB) atlas_side_cm={S:.1f}")
    for k_, v_ in tex.items():
        fp = os.path.join(outdir, v_)
        print(f"tex_{k_}={fp} bytes={os.path.getsize(fp)}")
    print(f"secs mesh={t1-t0:.1f} tex={t2-t1:.1f} write={t3-t2:.1f}")


if __name__ == "__main__":
    main()
