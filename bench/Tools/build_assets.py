# Editor-side asset builder for the Nanite vs LOD bench (UE 5.8.4).
# Run (editor commandlet, no GPU needed):
#   UnrealEditor-Cmd.exe <NaniteLodBench.uproject> -run=pythonscript -script="<this file>" -unattended -nosplash
#       -nullrhi -SCCProvider=None -nop4 -stdout -abslog=<log>
#   Keep the DDC out of the shared engine tree: env UE-LocalDataCachePath=<dir> (or -LocalDataCachePath=<dir>) and
#   -ddc=NoZenLocalFallback as the Zone9 scripts do (scout s1, s3 L).
#   -nullrhi => GMaxRHIShaderPlatform = PCD3D_SM6 (GenericPlatformMisc.cpp:1446-1455, NullRHI.cpp:16), the bench's only
#   targeted format, so the material shaders compiled here are the ones the cook needs.
# Environment:
#   NLB_SRC       absolute path of the dense table OBJ (gen_dense_table.py: Z-up, cm, feet at Z=0, 200 x 100 x 81 cm)
#   NLB_TEX_DIR   folder holding the table's *_D.png / *_N.png / *_R.png (optional; default = folder of NLB_SRC)
#   NLB_ROCK      package path of an extra dense mesh already copied into Content (optional), e.g.
#                 /Game/PostABundle/Models/Nature/Rocks/SM_Rock_Boulder_10
# Creates under /Game/NLB (cooked: DefaultGame.ini DirectoriesToAlwaysCook=/Game/NLB), for each source X in {Table, Rock}:
#   SM_X_Nanite  full-detail mesh, Nanite on (KeepPercentTriangles 1, TrimRelativeError 0)  (article: Nanite variant)
#   SM_X_LOD     Nanite off, LOD0..3 = 5 % / 2.5 % / 1 % / 0.5 % of the RAW mesh, auto screen size (article: 4 auto LODs)
#   SM_X_Raw     Nanite off, 1 LOD at 100 %                                                (extra arm: "no Nanite, no LOD")
#   M_NLB_Table / M_NLB_Rock   bench-owned lit materials (used_with_nanite)
#   NLB_Bench    map: movable sun (CSM) + sky atmosphere + sky light + height fog + ground plane
# and the import/duplicate sources under /Game/NLBSrc (NOT cooked; nothing under /Game/NLB references them).
# Re-run: delete <proj>\Content\NLB and <proj>\Content\NLBSrc first (the script also deletes/reuses, best effort).
# Report: <ProjectSaved>/NLB/build_assets_report.json ; log lines "NLBPY ..." ("NLBPY CHECK PASS|FAIL|WARN ...") and a final
# "NLBPY_DONE ok=<bool>". ok=True only if every fatal CHECK passed (triangle counts of every variant are verified).
import json
import os
import traceback

import unreal

ROOT = "/Game/NLB"
SRC_ROOT = "/Game/NLBSrc"
REPORT = {"ok": False, "steps": [], "errors": [], "checks": [], "meshes": {}}
# Article: LOD0..3 = 5 / 2.5 / 1 / 0.5 % of the RAW mesh.
LOD_TARGETS = [0.049, 0.025, 0.01, 0.005]  # 11-05 owner: non-Nanite LOD0 must be < 100k tris (2M raw x 4.9 % = 98k)
TABLE_EXPECT = (200.0, 100.0, 81.0)  # generator: X 200, Y 100, Z 0..81 cm (bounds -100.25..100.39, -50.57..50.37, -0.39..80.60)
# 11-05 owner: use the real Megascans "Wooden Table" uc1kebzfa Raw FBX (2 000 017 tris, 78 x 79 x 52 cm per uc1kebzfa.json).
# NLB_TABLE_EXPECT="x,y,z" (cm) overrides the expected size; NLB_TABLE_FEET=0 skips the feet-at-Z=0 check (the bench
# offsets every copy by -bounds.min.z anyway); NLB_TEX_MAX caps imported texture size (0 = keep source).
if os.environ.get("NLB_TABLE_EXPECT"):
    TABLE_EXPECT = tuple(float(v) for v in os.environ["NLB_TABLE_EXPECT"].split(","))
TABLE_FEET = os.environ.get("NLB_TABLE_FEET", "1") != "0"
TEX_MAX = int(os.environ.get("NLB_TEX_MAX", "0") or 0)


def log(msg):
    unreal.log("NLBPY " + msg)
    REPORT["steps"].append(msg)


def check(name, ok, detail, fatal=True):
    REPORT["checks"].append({"name": name, "ok": bool(ok), "fatal": fatal, "detail": detail})
    log("CHECK %s %s %s" % ("PASS" if ok else ("FAIL" if fatal else "WARN"), name, detail))


def save_report():
    out_dir = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir()), "NLB")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "build_assets_report.json"), "w", encoding="utf-8") as f:
        json.dump(REPORT, f, indent=1)


EAL = unreal.EditorAssetLibrary
# In a -run=pythonscript commandlet UEditorEngine::LoadDefaultEditorModules never runs (InitEditor only), so the
# StaticMeshEditor module (home of UStaticMeshEditorSubsystem) is not loaded and get_editor_subsystem returns None
# (seen 11-05: "'NoneType' object has no attribute 'get_number_materials'"). Loading it makes FSubsystemModuleWatcher
# create the subsystem for the module (SubsystemCollection.cpp:641-647).
unreal.load_module("StaticMeshEditor")
SMES = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
TOOLS = unreal.AssetToolsHelpers.get_asset_tools()


def rot(roll=0.0, pitch=0.0, yaw=0.0):
    # Python's Rotator() is built from KismetMathLibrary.MakeRotator(Roll, Pitch, Yaw) (NoExportTypes.h:831 HasNativeMake,
    # KismetMathLibrary.h:2253-2256): POSITIONAL ORDER IS (roll, pitch, yaw). Always pass keywords.
    return unreal.Rotator(roll=roll, pitch=pitch, yaw=yaw)


def bbox(mesh):
    b = mesh.get_bounding_box()
    return (b.min.x, b.min.y, b.min.z), (b.max.x - b.min.x, b.max.y - b.min.y, b.max.z - b.min.z)


def delete_if_exists(path):
    if EAL.does_asset_exist(path) and not EAL.delete_asset(path):
        raise RuntimeError("could not delete existing asset %s (delete <proj>/Content/NLB* and re-run)" % path)


def save(asset):
    # bOnlyIfIsDirty=False: some edits (set_material) do not dirty the package (EditorAssetLibrary.h:262).
    if not EAL.save_loaded_asset(asset, False):
        raise RuntimeError("save failed: " + asset.get_path_name())


def import_obj(src, name, rotation=None, scale=1.0):
    """Legacy FBX-SDK import. Interchange is not enabled (DisableEnginePluginsByDefault), so the cvar
    Interchange.FeatureFlags.Import.OBJ does not exist and UFbxFactory lists .obj (FbxFactory.cpp:1207-1225).
    For OBJ the importer skips ConvertScene (FbxMainImport.cpp:1635-1638: "axis system is unknown for obj"): no axis and
    no unit conversion, only the RH->LH Y flip (FbxUtilsImport.cpp:63-71). A Z-up cm OBJ therefore lands upright, 1:1."""
    task = unreal.AssetImportTask()
    task.filename = src
    task.destination_path = SRC_ROOT
    task.destination_name = name
    task.automated = True
    task.replace_existing = True
    task.save = False
    opts = unreal.FbxImportUI()
    opts.automated_import_should_detect_type = False  # keep FBXIT_StaticMesh (FbxFactory.cpp:111,348)
    opts.import_mesh = True
    opts.import_as_skeletal = False
    opts.import_materials = False
    opts.import_textures = False
    opts.import_animations = False
    opts.mesh_type_to_import = unreal.FBXImportType.FBXIT_STATIC_MESH
    sm = opts.static_mesh_import_data
    sm.combine_meshes = True
    sm.generate_lightmap_u_vs = False
    sm.auto_generate_collision = False
    sm.remove_degenerates = False
    sm.build_nanite = False
    sm.import_uniform_scale = scale
    sm.import_translation = unreal.Vector(0.0, 0.0, 0.0)
    sm.import_rotation = rotation if rotation is not None else rot()
    task.options = opts
    TOOLS.import_asset_tasks([task])
    paths = list(task.imported_object_paths or [])
    mesh = None
    for p in paths:
        a = unreal.load_asset(p)
        if isinstance(a, unreal.StaticMesh):
            mesh = a
    if mesh is None:
        raise RuntimeError("OBJ import produced no StaticMesh (paths=%s)" % paths)
    mn, size = bbox(mesh)
    log("import %s rot=%s scale=%s -> min=%s size=%s tris0=%d" % (mesh.get_path_name(), rotation, scale,
                                                                 [round(v, 1) for v in mn], [round(v, 1) for v in size],
                                                                 mesh.get_num_triangles(0)))
    return mesh, mn, size


def table_fits(size, mn):
    tol = [max(6.0, 0.08 * e) for e in TABLE_EXPECT]
    # A round table is ~square in XY: accept X/Y swapped (the yaw does not matter), never a swapped height.
    xy_ok = ((abs(size[0] - TABLE_EXPECT[0]) < tol[0] and abs(size[1] - TABLE_EXPECT[1]) < tol[1]) or
             (abs(size[0] - TABLE_EXPECT[1]) < tol[1] and abs(size[1] - TABLE_EXPECT[0]) < tol[0]))
    return (xy_ok and abs(size[2] - TABLE_EXPECT[2]) < tol[2] and (not TABLE_FEET or abs(mn[2]) < 3.0))


def import_table(src):
    """Import and require 200 x 100 x 81 cm, Z up, feet at Z=0 (footprint checked too, so a yaw-swapped or tipped table is
    never accepted). Expected to pass on the first import (see import_obj). Fallback: a bounded list of re-imports, each into
    a FRESH package name (no delete/re-create of a live asset in this session); the accepted one becomes the source."""
    delete_if_exists(SRC_ROOT + "/SM_Table_Src")
    mesh, mn, size = import_obj(src, "SM_Table_Src")
    if table_fits(size, mn):
        REPORT["table_import"] = {"rotation": "none", "scale": 1.0, "size": size, "min": mn}
        return mesh
    longest = max(size)
    want = max(TABLE_EXPECT)
    scale = want / longest if longest > 0 and not (0.7 * want < longest < 1.4 * want) else 1.0
    candidates = [None, rot(roll=90), rot(roll=-90), rot(yaw=90), rot(roll=180), rot(roll=90, yaw=90),
                  rot(roll=-90, yaw=90), rot(pitch=90), rot(pitch=-90)]
    for i, r in enumerate(candidates):
        if r is None and scale == 1.0:
            continue  # identical to the first import
        mesh, mn, size = import_obj(src, "SM_Table_Src_try%d" % i, rotation=r, scale=scale)
        if table_fits(size, mn):
            REPORT["table_import"] = {"rotation": str(r), "scale": scale, "size": size, "min": mn}
            log("WARN table needed import rotation=%s scale=%s; source = %s" % (r, scale, mesh.get_path_name()))
            return mesh
    raise RuntimeError("could not orient the table (last size=%s min=%s)" % (size, mn))


def import_texture(path, kind):
    t = unreal.AssetImportTask()
    t.filename = path
    t.destination_path = ROOT + "/Tex"
    t.automated = True
    t.replace_existing = True
    t.save = False
    TOOLS.import_asset_tasks([t])
    if not t.imported_object_paths:
        raise RuntimeError("texture import failed: " + path)
    tex = unreal.load_asset(t.imported_object_paths[0])
    if kind == "normal":
        tex.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_NORMALMAP)
        tex.set_editor_property("srgb", False)
        # gen_dense_table.py writes an OpenGL (+Y) normal map (gen_dense_table.py:21,324); UE wants DirectX (-Y).
        tex.set_editor_property("flip_green_channel", True)
    elif kind == "roughness":
        tex.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_MASKS)
        tex.set_editor_property("srgb", False)
    if TEX_MAX > 0:
        tex.set_editor_property("max_texture_size", TEX_MAX)
    save(tex)
    return tex


def make_material(name, textures, base_color):
    mel = unreal.MaterialEditingLibrary
    path = ROOT + "/" + name
    mat = unreal.load_asset(path) if EAL.does_asset_exist(path) else None
    if isinstance(mat, unreal.Material):
        mel.delete_all_material_expressions(mat)  # re-run: keep the asset (and any reference to it), rebuild the graph
    else:
        delete_if_exists(path)
        mat = TOOLS.create_asset(name, ROOT, unreal.Material, unreal.MaterialFactoryNew())
        if mat is None:
            raise RuntimeError("create_asset failed: " + path)
    # Usage flags first, on the empty graph: each set_editor_property runs UMaterial::PostEditChangeProperty (a recompile).
    # bUsedWithNanite / bUsedWithInstancedStaticMeshes are UE_DEPRECATED(5.8) for C++ only; still EditAnywhere UPROPERTYs and
    # still what GetUsageByFlag reads (Material.h:793-795,860-863; Material.cpp:1563,1571).
    mat.set_editor_property("used_with_nanite", True)
    mat.set_editor_property("used_with_instanced_static_meshes", True)
    links = []
    y = 0
    if "albedo" in textures:
        n = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSample, -500, y)
        n.set_editor_property("texture", textures["albedo"])
        links.append(mel.connect_material_property(n, "RGB", unreal.MaterialProperty.MP_BASE_COLOR))
    else:
        n = mel.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -500, y)
        n.set_editor_property("constant", base_color)
        links.append(mel.connect_material_property(n, "", unreal.MaterialProperty.MP_BASE_COLOR))
    y += 250
    if "normal" in textures:
        n = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSample, -500, y)
        n.set_editor_property("texture", textures["normal"])
        n.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
        links.append(mel.connect_material_property(n, "RGB", unreal.MaterialProperty.MP_NORMAL))
    y += 250
    if "roughness" in textures:
        n = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSample, -500, y)
        n.set_editor_property("texture", textures["roughness"])
        n.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_MASKS)
        links.append(mel.connect_material_property(n, "R", unreal.MaterialProperty.MP_ROUGHNESS))
    else:
        n = mel.create_material_expression(mat, unreal.MaterialExpressionConstant, -500, y)
        n.set_editor_property("r", 0.7)
        links.append(mel.connect_material_property(n, "", unreal.MaterialProperty.MP_ROUGHNESS))
    if not all(links):
        raise RuntimeError("material %s: connect_material_property failed %s" % (name, links))
    errs = list(mel.recompile_material(mat) or [])  # 5.8: TArray<FString> compile errors (MaterialEditingLibrary.h:262-267)
    if errs:
        raise RuntimeError("material %s compile errors: %s" % (name, errs))
    save(mat)
    log("material %s textures=%s compile_errors=0" % (name, sorted(textures.keys())))
    return mat


def nanite_enabled(mesh):
    return bool(SMES.get_nanite_settings(mesh).enabled)


def set_nanite(mesh, enabled):
    ns = SMES.get_nanite_settings(mesh)
    ns.enabled = enabled
    if enabled:
        ns.keep_percent_triangles = 1.0  # full source detail (EngineTypes.h FMeshNaniteSettings defaults 1.0 / 0.0)
        ns.trim_relative_error = 0.0
    SMES.set_nanite_settings(mesh, ns, apply_changes=True)


def duplicate(src_mesh, dst_root, name):
    dst = dst_root + "/" + name
    delete_if_exists(dst)
    a = EAL.duplicate_asset(src_mesh.get_path_name().split(".")[0], dst)
    if a is None:
        raise RuntimeError("duplicate failed: " + dst)
    return a


def lod_reduction_percents(targets):
    """Percentages for set_lods() so that LOD i ends at targets[i] of the RAW mesh.
    set_lods writes ReductionSettings[0] into LOD0 itself (StaticMeshEditorSubsystem.cpp:396-400) and copies LOD0's
    BaseLODModel (0) to every new LOD (:409-410). The builder reduces LOD0 IN PLACE (StaticMeshBuilder.cpp:1040-1045) and
    every generated LOD i>0 from MeshDescriptions[BaseLODModel=0] = the already-reduced LOD0 (:945,1049); Quadric takes
    PercentTriangles of its INPUT (QuadricMeshReduction.cpp:258-267). Passing [5, 2.5, 1, 0.5] % would give LOD1..3 =
    0.125 / 0.05 / 0.025 % of raw (clamped to 64 tris, :278). So: LOD0 = t0, LOD i = t_i / t0."""
    base = targets[0]
    return [base] + [round(t / base, 6) for t in targets[1:]]  # [0.05, 0.5, 0.2, 0.1]


def build_lods(mesh):
    opts = unreal.StaticMeshReductionOptions()
    opts.auto_compute_lod_screen_size = True
    settings = []
    for p in lod_reduction_percents(LOD_TARGETS):
        s = unreal.StaticMeshReductionSettings()
        s.percent_triangles = p
        s.screen_size = 0.0  # ignored: auto_compute_lod_screen_size
        settings.append(s)
    opts.reduction_settings = settings
    n = SMES.set_lods(mesh, opts)
    if n != len(LOD_TARGETS):
        raise RuntimeError("set_lods(%s) returned %s" % (mesh.get_name(), n))
    return n


def describe(mesh):
    n = SMES.get_lod_count(mesh)
    mn, size = bbox(mesh)
    return {
        "path": mesh.get_path_name(),
        "lods": n,
        "tris": [mesh.get_num_triangles(i) for i in range(n)],  # Nanite mesh: fallback-mesh tris
        "verts": [SMES.get_number_verts(mesh, i) for i in range(n)],
        "materials": SMES.get_number_materials(mesh),
        "nanite": nanite_enabled(mesh),
        "nanite_tris": mesh.get_num_nanite_triangles(),  # Nanite input tris (StaticMesh.cpp GetNumNaniteTriangles)
        "size_cm": [round(v, 1) for v in size],
        "min_cm": [round(v, 1) for v in mn],
    }


def make_variants(src, tag, mat):
    out = {}
    for i in range(max(1, SMES.get_number_materials(src))):
        src.set_material(i, mat)
    if nanite_enabled(src):
        set_nanite(src, False)
    save(src)
    raw_tris = src.get_num_triangles(0)
    REPORT.setdefault("raw_tris", {})[tag] = raw_tris
    check("%s_raw_tris" % tag, raw_tris > 1000, "source LOD0 tris=%d" % raw_tris)
    # NLB_TABLE_VARIANTS="Nanite" (11-05 owner): only the Nanite mesh comes from the raw scan; the non-Nanite one is LODMS.
    vlist = ("Nanite", "LOD", "Raw") if tag != "Table" else tuple(
        v for v in os.environ.get("NLB_TABLE_VARIANTS", "Nanite,LOD,Raw").split(",") if v)
    for variant in vlist:
        m = duplicate(src, ROOT, "SM_%s_%s" % (tag, variant))
        if variant == "Nanite":
            set_nanite(m, True)
        elif variant == "LOD":
            build_lods(m)
        save(m)
        d = describe(m)
        out[m.get_name()] = d
        log("variant %s %s" % (m.get_name(), json.dumps(d)))
        if variant == "Nanite":
            check("%s_Nanite_tris" % tag, d["nanite"] and abs(d["nanite_tris"] - raw_tris) <= 0.05 * raw_tris,
                  "nanite=%s nanite_tris=%d raw=%d (+-5%%)" % (d["nanite"], d["nanite_tris"], raw_tris))
        elif variant == "LOD":
            exp = [max(64, int(round(raw_tris * t))) for t in LOD_TARGETS]
            ok = (not d["nanite"]) and d["lods"] == len(LOD_TARGETS) and all(
                0.7 * e <= t <= 1.3 * e for t, e in zip(d["tris"], exp))
            check("%s_LOD_tris" % tag, ok, "tris=%s expected~%s (+-30%%) raw=%d" % (d["tris"], exp, raw_tris))
        else:
            check("%s_Raw_tris" % tag, (not d["nanite"]) and d["lods"] == 1 and d["tris"][0] == raw_tris,
                  "lods=%d tris=%s raw=%d" % (d["lods"], d["tris"], raw_tris))
    return out


def load_textures(tex_dir):
    textures = {}
    for fn in sorted(os.listdir(tex_dir)):
        low = fn.lower()
        stem, ext = os.path.splitext(low)
        if ext not in (".png", ".jpg", ".jpeg"):
            continue
        kind = ("albedo" if stem.endswith(("_d", "_basecolor", "_albedo")) else
                "normal" if stem.endswith(("_n", "_normal")) else
                "roughness" if stem.endswith(("_r", "_roughness")) else None)
        if kind and kind not in textures:
            textures[kind] = import_texture(os.path.join(tex_dir, fn), kind)
    return textures


# 11-05 owner: non-Nanite mesh whose LODs are the Megascans tiers (High/Mid/Low FBX) plus generated LODs up to LOD6
# (UE max 8 LODs). LOD0 must stay under 100k tris (High = 21 851).
# 11-05 owner: non-Nanite mesh = Megascans tiers as anchors, generated LODs in between (UE max 8 LODs):
#   LOD0 High, LOD1-2 from High; LOD3 Mid, LOD4-5 from Mid; LOD6 Low, LOD7 from Low. LOD0 must be < 100k tris (High 21 851).
# (lod index, base lod, percent of base) for the generated ones; the anchors are imported.
MS_PLAN = [("import", "high"), ("gen", 0, 0.80), ("gen", 0, 0.65), ("import", "mid"), ("gen", 3, 0.60), ("gen", 3, 0.40),
           ("import", "low"), ("gen", 6, 0.50)]
MS_SCREEN = [1.0, 0.6, 0.4, 0.28, 0.18, 0.12, 0.07, 0.04]


def make_ms_lod(high, mid, low, mat):
    files = {"high": high, "mid": mid, "low": low}
    src, mn, size = import_obj(high, "SM_TableMS_Src")
    dst = duplicate(src, ROOT, "SM_Table_LOD")  # named LOD: -NLBMode=LOD picks it
    if nanite_enabled(dst):
        set_nanite(dst, False)
    for i, step in enumerate(MS_PLAN):
        if i == 0:
            continue
        if step[0] == "import":
            r = SMES.import_lod(dst, i, files[step[1]])
            if r != i:
                raise RuntimeError("import_lod(%d, %s) returned %s" % (i, files[step[1]], r))
        else:
            dst.set_num_source_models(i + 1)
            rs = unreal.MeshReductionSettings()
            rs.set_editor_property("percent_triangles", step[2])
            rs.set_editor_property("base_lod_model", step[1])
            SMES.set_lod_reduction_settings(dst, i, rs)
        if SMES.get_lod_count(dst) != i + 1:
            raise RuntimeError("after LOD%d step %s the mesh has %d LODs" % (i, step, SMES.get_lod_count(dst)))
    for i in range(max(1, SMES.get_number_materials(dst))):
        dst.set_material(i, mat)
    if not SMES.set_lod_screen_sizes(dst, MS_SCREEN):
        raise RuntimeError("set_lod_screen_sizes failed")
    save(dst)
    d = describe(dst)
    d["screen_sizes"] = [round(s, 4) for s in SMES.get_lod_screen_sizes(dst)]
    d["plan"] = [list(s) for s in MS_PLAN]
    log("variant SM_Table_LOD (Megascans tiers) %s" % json.dumps(d))
    tr = d["tris"]
    ok = ((not d["nanite"]) and d["lods"] == 8 and tr[0] < 100000 and all(tr[i] > tr[i + 1] for i in range(7)))
    check("Table_LODMS_tris", ok, "lods=%d tris=%s (8 LODs, LOD0<100k, strictly decreasing)" % (d["lods"], tr))
    return {"SM_Table_LOD": d}


def make_map():
    les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    map_path = ROOT + "/NLB_Bench"
    delete_if_exists(map_path)
    # new_level refuses an existing package and saves the empty map itself (LevelEditorSubsystem.cpp NewLevel).
    if not les.new_level(map_path, False):
        raise RuntimeError("new_level failed")

    sun = eas.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0, 0, 1000), rot(pitch=-45.0, yaw=35.0))
    sc = sun.get_component_by_class(unreal.DirectionalLightComponent)
    sc.set_editor_property("mobility", unreal.ComponentMobility.MOVABLE)
    sc.set_editor_property("intensity", 10.0)
    sc.set_editor_property("atmosphere_sun_light", True)
    sc.set_editor_property("cast_shadows", True)

    eas.spawn_actor_from_class(unreal.SkyAtmosphere, unreal.Vector(0, 0, 0), rot())
    sky = eas.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 500), rot())
    kc = sky.get_component_by_class(unreal.SkyLightComponent)
    kc.set_editor_property("mobility", unreal.ComponentMobility.MOVABLE)
    kc.set_editor_property("real_time_capture", True)
    eas.spawn_actor_from_class(unreal.ExponentialHeightFog, unreal.Vector(0, 0, 0), rot())

    plane = unreal.load_asset("/Engine/BasicShapes/Plane")
    if plane is None:
        raise RuntimeError("cannot load /Engine/BasicShapes/Plane")
    # spawn_actor_from_object returned None in the -run=pythonscript commandlet (11-05: no actor factory resolved for the
    # mesh there), so spawn the actor class and set the mesh on its component.
    ground = eas.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(0, 0, 0), rot())
    if ground is None:
        raise RuntimeError("spawn StaticMeshActor (ground) failed")
    gsmc = ground.static_mesh_component
    if not gsmc.set_static_mesh(plane):
        raise RuntimeError("ground set_static_mesh(Plane) failed")
    ground.set_actor_scale3d(unreal.Vector(20000, 20000, 1))  # 100 cm plane x 20000 = 20 km x 20 km, one primitive
    gm = unreal.load_asset("/Engine/BasicShapes/BasicShapeMaterial")
    if gm:
        gsmc.set_material(0, gm)
    for a, nm in ((sun, "sun"), (sky, "skylight")):
        if a is None:
            raise RuntimeError("spawn %s failed" % nm)

    if not les.save_current_level():
        raise RuntimeError("save_current_level failed")
    log("map %s saved" % map_path)


def prepare_rock(rock_path):
    rock = unreal.load_asset(rock_path)
    if not isinstance(rock, unreal.StaticMesh):
        raise RuntimeError("NLB_ROCK is not a StaticMesh: %r" % rock_path)
    REPORT["rock_source"] = describe(rock)  # its Zone9 materials are not copied: load warnings for them are expected
    before = rock.get_num_triangles(0)
    rock_src = duplicate(rock, SRC_ROOT, "SM_Rock_Src")
    # A full-detail LOD0 is needed for every variant: drop the asset's own LOD1..n (RemoveLods keeps source model 0,
    # StaticMeshEditorSubsystem.cpp:938-985) ...
    if SMES.get_lod_count(rock_src) > 1 and not SMES.remove_lods(rock_src):
        raise RuntimeError("remove_lods failed on " + rock_src.get_path_name())
    # ... and make sure LOD0 itself is not a reduction of a denser source (Quadric IsReductionActive:
    # QuadricMeshReduction.cpp:514-533). Otherwise Raw/LOD would derive from the reduced LOD0 and Nanite from the raw source.
    rs = SMES.get_lod_reduction_settings(rock_src, 0)
    REPORT["rock_lod0_reduction_before"] = {"percent_triangles": rs.percent_triangles,
                                            "percent_vertices": rs.percent_vertices,
                                            "termination": str(rs.termination_criterion)}
    SMES.set_lod_reduction_settings(rock_src, 0, unreal.MeshReductionSettings())  # defaults = no reduction
    after = rock_src.get_num_triangles(0)
    check("Rock_LOD0_is_source", before > 0 and abs(after - before) <= 0.05 * before,
          "LOD0 tris asset=%d after-reset=%d (reduction before=%s)" % (before, after, REPORT["rock_lod0_reduction_before"]),
          fatal=False)
    return rock_src


def main():
    for nm, ss in (("StaticMeshEditorSubsystem", SMES),
                   ("LevelEditorSubsystem", unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)),
                   ("EditorActorSubsystem", unreal.get_editor_subsystem(unreal.EditorActorSubsystem))):
        if ss is None:
            raise RuntimeError("editor subsystem %s is None in this commandlet" % nm)
    src = os.environ.get("NLB_SRC", "")
    if not src or not os.path.isfile(src):
        raise RuntimeError("NLB_SRC not set or missing: %r" % src)
    tex_dir = os.environ.get("NLB_TEX_DIR", "") or os.path.dirname(src)
    log("LOD targets %s of raw -> set_lods percents %s" % (LOD_TARGETS, lod_reduction_percents(LOD_TARGETS)))

    textures = {}
    for fn in sorted(os.listdir(tex_dir)):
        low = fn.lower()
        stem, ext = os.path.splitext(low)
        if ext not in (".png", ".jpg", ".jpeg"):
            continue
        # gen_dense_table.py: *_D/_N/_R.png ; Megascans: *_8K_BaseColor/_Normal/_Roughness.jpg (AO/Cavity/Gloss/... unused)
        kind = ("albedo" if stem.endswith(("_d", "_basecolor", "_albedo")) else
                "normal" if stem.endswith(("_n", "_normal")) else
                "roughness" if stem.endswith(("_r", "_roughness")) else None)
        if kind and kind not in textures:
            textures[kind] = import_texture(os.path.join(tex_dir, fn), kind)
    log("table textures %s" % sorted(textures.keys()))

    table = import_table(src)
    mat_t = make_material("M_NLB_Table", textures, unreal.LinearColor(0.32, 0.2, 0.11, 1.0))
    REPORT["meshes"].update(make_variants(table, "Table", mat_t))

    ms = [os.environ.get("NLB_MS_" + k, "") for k in ("HIGH", "MID", "LOW")]
    if all(ms):
        for p in ms:
            if not os.path.isfile(p):
                raise RuntimeError("Megascans tier missing: %s" % p)
        tex_ms = load_textures(os.path.dirname(ms[0]))
        log("LODMS textures %s from %s" % (sorted(tex_ms.keys()), os.path.dirname(ms[0])))
        mat_ms = make_material("M_NLB_TableMS", tex_ms, unreal.LinearColor(0.32, 0.2, 0.11, 1.0))
        REPORT["meshes"].update(make_ms_lod(ms[0], ms[1], ms[2], mat_ms))

    rock_path = os.environ.get("NLB_ROCK", "")
    if rock_path:
        rock_src = prepare_rock(rock_path)
        mat_r = make_material("M_NLB_Rock", {}, unreal.LinearColor(0.35, 0.33, 0.3, 1.0))
        REPORT["meshes"].update(make_variants(rock_src, "Rock", mat_r))

    make_map()
    failed = [c["name"] for c in REPORT["checks"] if c["fatal"] and not c["ok"]]
    if failed:
        raise RuntimeError("fatal checks failed: %s" % failed)
    REPORT["ok"] = True


try:
    main()
except Exception as e:
    REPORT["errors"].append("%s\n%s" % (e, traceback.format_exc()))
    unreal.log_error("NLBPY_FATAL %s" % e)
finally:
    save_report()
    unreal.log("NLBPY_DONE ok=%s" % REPORT["ok"])
