"""Independent read-back check of an OBJ: counts, index ranges, manifold edges, signed volume (winding),
degenerate tris, UV range, normal/face agreement. Usage: py -I verify_obj.py file.obj"""
import math, sys
from collections import Counter
V, VT, VN, F = [], [], [], []
with open(sys.argv[1], "r", encoding="ascii") as f:
    for line in f:
        if line.startswith("v "):
            V.append(tuple(map(float, line.split()[1:4])))
        elif line.startswith("vt "):
            VT.append(tuple(map(float, line.split()[1:3])))
        elif line.startswith("vn "):
            VN.append(tuple(map(float, line.split()[1:4])))
        elif line.startswith("f "):
            F.append([tuple(int(x) - 1 for x in c.split("/")) for c in line.split()[1:]])
print(f"v={len(V)} vt={len(VT)} vn={len(VN)} f={len(F)} nonTri={sum(1 for x in F if len(x)!=3)}")
bad = sum(1 for fc in F for (a, b, c) in fc if not (0 <= a < len(V) and 0 <= b < len(VT) and 0 <= c < len(VN)))
print("out_of_range_refs", bad)
edges = Counter()
vol = 0.0
degen = 0
disagree = 0
minA = 1e9
for fc in F:
    a, b, c = fc[0][0], fc[1][0], fc[2][0]
    for e in ((a, b), (b, c), (c, a)):
        edges[(min(e), max(e))] += 1
    A, B, C = V[a], V[b], V[c]
    ux, uy, uz = B[0]-A[0], B[1]-A[1], B[2]-A[2]
    vx, vy, vz = C[0]-A[0], C[1]-A[1], C[2]-A[2]
    nx, ny, nz = uy*vz-uz*vy, uz*vx-ux*vz, ux*vy-uy*vx
    ar = 0.5 * math.sqrt(nx*nx+ny*ny+nz*nz)
    minA = min(minA, ar)
    if ar < 1e-6:
        degen += 1
    vol += (A[0]*(B[1]*C[2]-B[2]*C[1]) - A[1]*(B[0]*C[2]-B[2]*C[0]) + A[2]*(B[0]*C[1]-B[1]*C[0])) / 6.0
    N = VN[fc[0][2]]
    if nx*N[0]+ny*N[1]+nz*N[2] < 0:
        disagree += 1
ec = Counter(edges.values())
print("edge_use_histogram", dict(ec), "(2 = closed manifold)")
print(f"signed_volume_cm3={vol:.0f} (positive = outward CCW)  degenerate={degen} minArea_cm2={minA:.5f}")
print(f"faces_whose_geo_normal_opposes_vn={disagree}")
us = [t[0] for t in VT]; vs = [t[1] for t in VT]
print(f"uv_range u=[{min(us):.4f},{max(us):.4f}] v=[{min(vs):.4f},{max(vs):.4f}]")
mn = [min(p[i] for p in V) for i in range(3)]; mx = [max(p[i] for p in V) for i in range(3)]
print("bounds", [round(x, 2) for x in mn], [round(x, 2) for x in mx])
