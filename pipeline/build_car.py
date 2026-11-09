"""
build_car.py  --  Reusable Assetto-Corsa-car -> Blender studio builder.

Run headless:
    blender -b -P build_car.py -- <config.json>

It:
  1. Imports the converted FBX
  2. Auto-fixes the common KN5->FBX import artifacts
       - crash-damage normal maps wired as the surface normal  (body looks dented)
       - crash-damage duplicate meshes left visible            (cracked glass etc.)
       - fake alpha "glass" with Transmission 0 / high roughness (milky windows)
       - matte paint (adds a glossy clearcoat)
       - flat-grey carbon (drops roughness)
  3. Applies a livery skin by repointing image textures
  4. Builds the chosen scene preset (dark / white / sunset / neon)
  5. Builds the 15-shot vertical camera package (markers = auto cuts)
  6. Sets Cycles GPU render config + resumable PNG output
  7. Saves the .blend  (does NOT render -- the launcher renders + encodes)

Everything it changes is printed to the console with a [FIX]/[SKIN]/... tag.
"""

import bpy, sys, os, json, math, bmesh
from mathutils import Vector, Matrix

# --------------------------------------------------------------------------- #
#  Config
# --------------------------------------------------------------------------- #
DEFAULTS = {
    "fbx_path":  "",             # required: path to the converted .fbx
    "car_dir":   "",             # car root folder (holds skins/, texture/)
    "skin":      "default",      # skin folder name under skins/, or "default"
    "wheel_fbx": "",             # optional per-skin wheel: converted *_tire FBX to mount on the hubs
    "scene":     "dark",         # dark | white | sunset | neon
    "aspect":    "vertical",     # vertical(9:16) | landscape(16:9) | square(1:1)
    "camera":    "montage",      # montage(all 15 w/ cuts) or a single shot key
    "samples":   128,
    "out_blend": "car_project.blend",
    "frames_dir": "renders/frames",
    # heuristics (rarely need changing)
    "paint_name_hints":  ["car_paint", "carpaint", "paint", "ext_skin", "bodypaint", "body"],
    "glass_name_hints":  ["glass", "window", "windshield", "windscreen"],
    "lens_exclude_hints":["red", "orange", "amber", "tail", "marker", "screen",
                          "gost", "ghost", "light", "led", "broken", "damage"],
}

def load_config():
    cfg = dict(DEFAULTS)
    argv = sys.argv
    if "--" in argv:
        extra = argv[argv.index("--") + 1:]
        if extra and os.path.isfile(extra[0]):
            with open(extra[0], "r", encoding="utf-8") as fh:
                cfg.update(json.load(fh))
    return cfg

CFG = load_config()
FLOOR_Z = -0.158  # recomputed after import from the actual mesh

# --- 60 fps switch -------------------------------------------------------- #
# The 15-shot montage below was hand-authored on a 24 fps timeline (frames
# 1-702 ~= 29 s).  To keep the SAME real duration at a higher fps we re-time
# every keyframe/marker by FPS/24.  T() wraps each authored frame number; the
# helper defs (KL/KR/M/add) call it, so the shot code stays readable.
FPS = 60
def T(f): return round(f * FPS / 24.0)

def log(tag, msg): print(f"[{tag}] {msg}")

# --------------------------------------------------------------------------- #
#  1. Import
# --------------------------------------------------------------------------- #
def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)

def _is_binary_fbx(path):
    try:
        with open(path, "rb") as fh:
            return fh.read(20).startswith(b"Kaydara FBX Binary")
    except Exception:
        return False

def _resolve_fbx(path):
    # Blender only imports BINARY fbx; kn5conv also writes a "<name>_binary.fbx".
    if _is_binary_fbx(path):
        return path
    root, _ = os.path.splitext(path)
    cand = root + "_binary.fbx"
    if os.path.isfile(cand) and _is_binary_fbx(cand):
        log("IMPORT", "ASCII FBX given -> using binary sibling")
        return cand
    d = os.path.dirname(path) or "."
    for f in os.listdir(d):
        if f.lower().endswith(".fbx") and _is_binary_fbx(os.path.join(d, f)):
            log("IMPORT", f"using binary FBX found in folder: {f}")
            return os.path.join(d, f)
    raise SystemExit(f"No BINARY FBX found (Blender can't read ASCII FBX): {path}")

def import_fbx(path):
    if not os.path.isfile(path):
        raise SystemExit(f"FBX not found: {path}")
    path = _resolve_fbx(path)
    bpy.ops.import_scene.fbx(filepath=path)
    meshes = [o for o in bpy.data.objects if o.type == 'MESH']
    log("IMPORT", f"{len(meshes)} meshes, {len(bpy.data.materials)} materials")
    # put the car in a 'Car' collection
    car = bpy.data.collections.get("Car") or bpy.data.collections.new("Car")
    if car.name not in [c.name for c in bpy.context.scene.collection.children]:
        bpy.context.scene.collection.children.link(car)
    for o in list(bpy.data.objects):
        for c in list(o.users_collection):
            c.objects.unlink(o)
        car.objects.link(o)
    return car

def ground_z():
    """Ground = the tyres' contact height, NOT the global lowest vertex.
    Low-hanging parts (exhaust, splitter, diffuser) can dangle below the
    wheels; using the global min drops the floor and floats the car (the
    Volvo 850R's exhaust hangs ~11cm below the tyres). Falls back to the
    global minimum only if no wheel/tyre meshes are found."""
    WHEEL = ('tyre', 'tire', 'wheel', 'rim')
    zw, zall = 1e18, 1e18
    for o in bpy.data.objects:
        if o.type == 'MESH' and len(o.data.vertices):
            mw = o.matrix_world
            is_wheel = any(k in o.name.lower() for k in WHEEL)
            for v in o.data.vertices:
                z = (mw @ v.co).z
                if z < zall: zall = z
                if is_wheel and z < zw: zw = z
    return zw if zw < 1e17 else zall

def normalize_scale():
    """kn5conv FBX often imports at 1/100 (cm). Rescale roots so the car is ~metres."""
    _, D = _car_bbox()
    maxd = max(D)
    factor = 100.0 if maxd < 1.0 else (0.01 if maxd > 60.0 else 1.0)
    if factor != 1.0:
        for o in bpy.data.objects:
            if o.parent is None:
                o.scale = tuple(s*factor for s in o.scale)
                o.location = tuple(c*factor for c in o.location)
        bpy.context.view_layer.update()
        _, D2 = _car_bbox()
        log("IMPORT", f"normalized scale x{factor:g}: dims now {tuple(round(x,2) for x in D2)}")

# --------------------------------------------------------------------------- #
#  2. Auto-fix import artifacts
# --------------------------------------------------------------------------- #
def _principled(mat):
    if not mat or not mat.use_nodes: return None
    return next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)

def fix_damage_normals():
    n = 0
    for m in bpy.data.materials:
        b = _principled(m)
        if not b: continue
        ninp = b.inputs.get('Normal')
        if not ninp or not ninp.links: continue
        src = ninp.links[0].from_node
        if src.type in ('NORMAL_MAP', 'BUMP'):
            col = src.inputs.get('Color') or src.inputs.get('Height')
            img = col.links[0].from_node if (col and col.links) else None
            if img and img.type == 'TEX_IMAGE' and img.image and 'damage' in img.image.name.lower():
                for l in list(ninp.links): m.node_tree.links.remove(l)
                n += 1
    log("FIX", f"disconnected {n} crash-damage normal map(s)")

def hide_damage_meshes():
    n = 0
    for o in bpy.data.objects:
        if o.type == 'MESH' and any(k in o.name.lower() for k in ('damage', 'broken', '_broke')):
            o.hide_render = o.hide_viewport = True; n += 1
    log("FIX", f"hid {n} crash-damage mesh(es)")

def fix_glass():
    n = 0
    for m in bpy.data.materials:
        nm = m.name.lower()
        if not any(k in nm for k in CFG["glass_name_hints"]): continue
        if any(k in nm for k in CFG["lens_exclude_hints"]): continue   # keep colored lenses
        b = _principled(m)
        if not b: continue
        tw = b.inputs.get('Transmission Weight')
        if tw is None or tw.default_value > 0.001: continue            # already real glass
        for sock in ('Base Color', 'Alpha'):
            for l in list(b.inputs[sock].links): m.node_tree.links.remove(l)
        b.inputs['Base Color'].default_value = (0.80, 0.82, 0.82, 1)
        b.inputs['Alpha'].default_value = 1.0
        b.inputs['Roughness'].default_value = 0.04
        b.inputs['Metallic'].default_value = 0.0
        b.inputs['Transmission Weight'].default_value = 1.0
        if 'IOR' in b.inputs: b.inputs['IOR'].default_value = 1.45
        m.use_backface_culling = False
        n += 1
    log("FIX", f"converted {n} material(s) to transmissive glass")

def fix_paint_and_carbon():
    paint = carbon = 0
    for m in bpy.data.materials:
        nm = m.name.lower()
        b = _principled(m)
        if not b: continue
        if any(k in nm for k in CFG["paint_name_hints"]):
            if not b.inputs['Roughness'].links: b.inputs['Roughness'].default_value = 0.32
            b.inputs['Coat Weight'].default_value = 1.0
            b.inputs['Coat Roughness'].default_value = 0.04
            if 'Coat IOR' in b.inputs: b.inputs['Coat IOR'].default_value = 1.5
            paint += 1
        elif 'carbon' in nm:
            if not b.inputs['Roughness'].links: b.inputs['Roughness'].default_value = 0.28
            b.inputs['Coat Weight'].default_value = 0.9
            b.inputs['Coat Roughness'].default_value = 0.07
            carbon += 1
    log("FIX", f"glossed {paint} paint + {carbon} carbon material(s)")

def fix_tyres():
    """KN5->FBX often wires the tyre texture's ALPHA channel to the material,
    so in Cycles the tyre renders translucent (you see through to the rim).
    Force tyre/rubber materials fully opaque."""
    mats = set()
    for o in bpy.data.objects:
        if o.type == 'MESH' and any(k in o.name.lower() for k in ('tyre', 'tire')):
            for s in o.material_slots:
                if s.material: mats.add(s.material)
    for m in bpy.data.materials:
        if any(k in m.name.lower() for k in ('tyre', 'tire', 'tread', 'rubber')):
            mats.add(m)
    n = 0
    for m in mats:
        b = _principled(m)
        if not b: continue
        ai = b.inputs.get('Alpha')
        if ai:
            for l in list(ai.links): m.node_tree.links.remove(l)
            ai.default_value = 1.0
        met = b.inputs.get('Metallic')          # tyres are not metal
        if met: met.default_value = 0.0
        rgh = b.inputs.get('Roughness')          # matte rubber
        if rgh and not rgh.links: rgh.default_value = 0.9
        m.use_backface_culling = False
        n += 1
    log("FIX", f"forced {n} tyre/rubber material(s) opaque")

def fix_nan_meshes():
    """Some mod parts (alternate/parts-bin meshes) import with NaN/inf world
    transforms -> Cycles renders them as stray shards. Hide them from render."""
    n = 0
    for o in bpy.data.objects:
        if o.type != 'MESH': continue
        mw = o.matrix_world
        if any(math.isnan(mw[i][j]) or math.isinf(mw[i][j]) for i in range(4) for j in range(4)):
            o.hide_render = o.hide_viewport = True
            n += 1
    log("FIX", f"hid {n} mesh(es) with NaN/inf transforms")

def fix_rims():
    """Chrome rims render as a dull black hole when they're a BLURRY mirror
    (rough metal averages the dark surroundings) or when a dark detail texture
    tints the reflection. Make wheel/rim metals a clean, SHARP chrome: full
    metallic, low roughness, and a bright neutral base (unless the part is named
    'black') -> reads as polished chrome in daylight and catches crisp highlights
    at night."""
    mats = set()
    for o in bpy.data.objects:
        if o.type == 'MESH' and any(k in o.name.lower() for k in ('rim', 'wheel', 'spoke')):
            for s in o.material_slots:
                if s.material: mats.add(s.material)
    n = 0
    for m in mats:
        b = _principled(m)
        if not b: continue
        met = b.inputs.get('Metallic')
        if met and met.default_value > 0.85:
            met.default_value = 1.0
            rgh = b.inputs.get('Roughness')
            if rgh and not rgh.links: rgh.default_value = 0.10
            bc = b.inputs.get('Base Color')
            if bc and 'black' not in m.name.lower():
                for l in list(bc.links): m.node_tree.links.remove(l)
                bc.default_value = (0.82, 0.83, 0.85, 1.0)
            n += 1
    log("FIX", f"polished {n} chrome rim material(s)")

def fix_paint_diffuse():
    """kn5->FBX can SCRAMBLE the paint texture slots: the body/livery diffuse
    lands on Specular IOR Level while a small detail/metal map ends up on Base
    Color, so the car renders flat grey instead of its skin/livery. For each
    paint/body material, find the real livery diffuse (name hint
    skinbody/skin/diff/albedo/body, else the largest non-normal texture), wire it
    to Base Color, and unhook it from Specular."""
    n = 0
    for m in bpy.data.materials:
        if not any(k in m.name.lower() for k in CFG["paint_name_hints"]):
            continue
        b = _principled(m)
        if not b or not m.use_nodes:
            continue
        nt = m.node_tree
        imgs = [nd for nd in nt.nodes if nd.type == 'TEX_IMAGE' and nd.image]
        def is_nrm(nd):
            return any(k in nd.image.name.lower() for k in ('_nm', 'nm0', 'nm.', 'normal', 'flat_nm', '_n.'))
        cands = [nd for nd in imgs if not is_nrm(nd)]
        if not cands:
            continue
        def score(nd):
            inm = nd.image.name.lower()
            named = any(k in inm for k in ('skinbody', 'skin_body', 'livery', 'skin', 'diff', 'albedo', 'body'))
            return (1 if named else 0, (nd.image.size[0] or 0) * (nd.image.size[1] or 0))
        diff = max(cands, key=score)
        bc = b.inputs['Base Color']
        if bc.links and bc.links[0].from_node is diff:
            continue
        for l in list(bc.links): nt.links.remove(l)
        nt.links.new(diff.outputs['Color'], bc)
        spec = b.inputs.get('Specular IOR Level')
        if spec:
            for l in list(spec.links):
                if l.from_node is diff: nt.links.remove(l)
        n += 1
    log("FIX", f"rewired {n} paint diffuse(s) to Base Color")

def fix_scrambled_slots():
    """General form of the slot scramble (seen on kit + wheel materials, not just
    paint): a small '*detail*' map lands on Base Color, the REAL diffuse lands on
    Specular IOR Level, and the detail map's alpha gets wired to Alpha -> the part
    renders as speckled/translucent grey. Move the real diffuse to Base Color,
    clear Specular, and drop detail-driven Alpha links. Also zero Metallic on
    paint-named materials (metallic black paint renders as dark chrome)."""
    n = 0
    for m in bpy.data.materials:
        b = _principled(m)
        if not b: continue
        nt = m.node_tree
        bc = b.inputs.get('Base Color'); spec = b.inputs.get('Specular IOR Level'); ai = b.inputs.get('Alpha')
        base_img = bc.links[0].from_node if (bc and bc.links and bc.links[0].from_node.type == 'TEX_IMAGE') else None
        spec_img = spec.links[0].from_node if (spec and spec.links and spec.links[0].from_node.type == 'TEX_IMAGE') else None
        changed = False
        if (spec_img and spec_img.image and base_img and base_img.image
                and spec_img is not base_img and 'detail' in base_img.image.name.lower()):
            for l in list(bc.links): nt.links.remove(l)
            nt.links.new(spec_img.outputs['Color'], bc)
            for l in list(spec.links): nt.links.remove(l)
            spec.default_value = 0.5
            changed = True
        if ai and ai.links:
            src = ai.links[0].from_node
            if src.type == 'TEX_IMAGE' and src.image and 'detail' in src.image.name.lower():
                for l in list(ai.links): nt.links.remove(l)
                ai.default_value = 1.0
                changed = True
        if changed:
            if any(k in m.name.lower() for k in CFG["paint_name_hints"]):
                b.inputs['Metallic'].default_value = 0.0
            n += 1
    log("FIX", f"unscrambled {n} texture-slot material(s)")

def audit_fix():
    fix_damage_normals(); hide_damage_meshes(); fix_glass(); fix_paint_and_carbon(); fix_paint_diffuse()
    fix_scrambled_slots(); fix_tyres(); fix_rims(); fix_nan_meshes()

# --------------------------------------------------------------------------- #
#  3. Apply skin
# --------------------------------------------------------------------------- #
def apply_skin(car_dir, skin):
    if not skin or skin.lower() in ("default", "none", ""):
        log("SKIN", "using default (baked) skin"); return
    sdir = os.path.join(car_dir, "skins", skin)
    if not os.path.isdir(sdir):
        log("SKIN", f"skin folder not found, keeping default: {sdir}"); return
    files = {f.lower(): f for f in os.listdir(sdir) if os.path.isfile(os.path.join(sdir, f))}
    n = 0
    for img in bpy.data.images:
        if not img.filepath: continue
        key = os.path.basename(img.filepath).lower()
        if key in files:
            img.filepath = os.path.join(sdir, files[key])
            try: img.reload()
            except Exception: pass
            n += 1
    log("SKIN", f"applied '{skin}' ({n} textures swapped)")

# --------------------------------------------------------------------------- #
#  3b. Per-skin wheel swap
# --------------------------------------------------------------------------- #
def swap_wheels(fbx_path):
    """Many AC skins ship their own wheel as a separate *_tire.kn5. Given that
    wheel converted to FBX, import it, join it into one wheel, auto-scale it to
    the car's base tyre diameter, and mount a copy on every hub (dish flipped on
    the -X side so it faces outward), then hide the car's base rims + tyres.
    Brake discs/calipers stay so they show behind the spokes."""
    if not fbx_path or not os.path.isfile(fbx_path):
        log("WHEEL", f"wheel fbx not found, skipping: {fbx_path}"); return
    fbx = _resolve_fbx(fbx_path)

    def wbb(o):
        cs = [o.matrix_world @ Vector(c) for c in o.bound_box]
        mn = Vector((min(c[i] for c in cs) for i in range(3)))
        mx = Vector((max(c[i] for c in cs) for i in range(3)))
        return (mn + mx) / 2, (mx - mn)

    tyres = [o for o in bpy.data.objects if o.type == 'MESH' and o.name.upper().startswith('TYRE')]
    if not tyres:
        log("WHEEL", "no base TYRE_* meshes found, skipping wheel swap"); return
    # per-corner hub (XY) + tyre diameter. Static AC poses often have unequal
    # axle heights (suspension rest pose), so do NOT trust hub Z -- each wheel
    # is grounded to FLOOR_Z instead (bottom of tyre = floor).
    hubs = []
    for o in tyres:
        c, d = wbb(o); hubs.append((c.copy(), max(d.y, d.z)))
    base_dia = sum(d for _, d in hubs) / len(hubs)

    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=fbx)
    new = [o for o in bpy.data.objects if o not in before]
    newm = [o for o in new if o.type == 'MESH']
    newe = [o for o in new if o.type != 'MESH']
    if not newm:
        log("WHEEL", "wheel fbx had no meshes, skipping"); return
    bpy.ops.object.select_all(action='DESELECT')
    for o in newm: o.select_set(True)
    bpy.context.view_layer.objects.active = newm[0]
    bpy.ops.object.parent_clear(type='CLEAR_KEEP_TRANSFORM')
    bpy.ops.object.join()
    master = bpy.context.active_object
    for o in list(newe):
        try: bpy.data.objects.remove(o, do_unlink=True)
        except Exception: pass

    # bake the import transform into the mesh, then centre geometry on the origin
    me = master.data; mw = master.matrix_world.copy()
    for v in me.vertices: v.co = mw @ v.co
    master.matrix_world = Matrix.Identity(4)
    xs = [v.co.x for v in me.vertices]; ys = [v.co.y for v in me.vertices]; zs = [v.co.z for v in me.vertices]
    cen = Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2))
    for v in me.vertices: v.co -= cen
    me.update()
    hre_dia = max(max(ys) - min(ys), max(zs) - min(zs))
    factor = base_dia / hre_dia if hre_dia else 1.0

    car = bpy.data.collections.get("Car")
    for i, (hub, dia) in enumerate(hubs):
        dup = master.copy(); dup.data = master.data.copy()
        (car or bpy.context.scene.collection).objects.link(dup)
        dup.name = f"SkinWheel_{i}"
        f_i = dia / hre_dia if hre_dia else 1.0   # per-corner size match
        z = FLOOR_Z + dia / 2.0                   # ground the tyre to the floor
        rot = Matrix.Rotation(math.pi, 4, 'Z') if hub.x < 0 else Matrix.Identity(4)
        dup.matrix_world = Matrix.Translation((hub.x, hub.y, z)) @ rot @ Matrix.Scale(f_i, 4)
    bpy.data.objects.remove(master, do_unlink=True)
    bpy.context.view_layer.update()   # refresh bounds so later _car_bbox() is correct

    n = 0
    for o in bpy.data.objects:
        nm = o.name.lower()
        if o.type == 'MESH' and not o.name.startswith('SkinWheel_') and \
           ('rim' in nm or nm.startswith('tyre') or nm.startswith('tire')):
            o.hide_render = o.hide_viewport = True; n += 1

    # the swapped wheel imports raw (audit_fix already ran) -> make its tyre opaque/matte
    for o in bpy.data.objects:
        if not o.name.startswith('SkinWheel_'):
            continue
        for s in o.material_slots:
            m = s.material; b = _principled(m) if m else None
            if b and any(k in m.name.lower() for k in ('tyre', 'tire', 'rubber')):
                ai = b.inputs.get('Alpha')
                if ai:
                    for l in list(ai.links): m.node_tree.links.remove(l)
                    ai.default_value = 1.0
                mt = b.inputs.get('Metallic')
                if mt: mt.default_value = 0.0
                rg = b.inputs.get('Roughness')
                if rg and not rg.links: rg.default_value = 0.9
    fix_scrambled_slots()   # the wheel imports raw, after the main audit pass
    log("WHEEL", f"mounted skin wheel on {len(hubs)} hubs (x{factor:.1f}), hid {n} base rim/tyre mesh(es)")

# --------------------------------------------------------------------------- #
#  4. Scene presets
# --------------------------------------------------------------------------- #
def _env_collection():
    env = bpy.data.collections.get("ENV") or bpy.data.collections.new("ENV")
    if env.name not in [c.name for c in bpy.context.scene.collection.children]:
        bpy.context.scene.collection.children.link(env)
    for o in list(env.objects): bpy.data.objects.remove(o, do_unlink=True)
    return env

def _put(o, env):
    for c in list(o.users_collection): c.objects.unlink(o)
    env.objects.link(o)

def _aim(o, t): o.rotation_euler = (Vector(t) - o.location).to_track_quat('-Z', 'Y').to_euler()

def _area(env, name, loc, power, size, color=(1,1,1), sy=None, aimat=(0,0,0.6)):
    ld = bpy.data.lights.new(name, 'AREA'); ld.energy = power; ld.size = size; ld.color = color
    if sy: ld.shape = 'RECTANGLE'; ld.size_y = sy
    o = bpy.data.objects.new(name, ld); o.location = loc; env.objects.link(o)
    if aimat: _aim(o, aimat)
    return o

def _cyc(env, name, radius, base_color, rough=0.6, emissive=None):
    bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=radius, depth=44, location=(0,0,FLOOR_Z+22))
    o = bpy.context.active_object; o.name = name; _put(o, env)
    me = o.data; bm = bmesh.new(); bm.from_mesh(me)
    for f in bm.faces: f.normal_flip()
    bm.to_mesh(me); bm.free()
    m = bpy.data.materials.new("M_" + name); m.use_nodes = True; nt = m.node_tree; nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    if emissive:
        e = nt.nodes.new('ShaderNodeEmission'); e.inputs['Color'].default_value = (*emissive[:3], 1)
        e.inputs['Strength'].default_value = emissive[3]; nt.links.new(e.outputs['Emission'], out.inputs['Surface'])
    else:
        b = nt.nodes.new('ShaderNodeBsdfPrincipled'); b.inputs['Base Color'].default_value = (*base_color, 1)
        b.inputs['Roughness'].default_value = rough; nt.links.new(b.outputs['BSDF'], out.inputs['Surface'])
    o.data.materials.append(m); return o

def _floor(env, name, base_color, rough):
    bpy.ops.mesh.primitive_plane_add(size=200, location=(0,0,FLOOR_Z))
    o = bpy.context.active_object; o.name = name; _put(o, env)
    m = bpy.data.materials.new("M_" + name); m.use_nodes = True
    b = next(n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    b.inputs['Base Color'].default_value = (*base_color, 1); b.inputs['Roughness'].default_value = rough
    o.data.materials.append(m); return o

def _world(color, strength):
    w = bpy.data.worlds.new("W"); w.use_nodes = True; bpy.context.scene.world = w
    bg = w.node_tree.nodes.get('Background')
    bg.inputs['Color'].default_value = (*color, 1); bg.inputs['Strength'].default_value = strength
    return w

def scene_dark(env):
    _world((0.006, 0.007, 0.009), 1.0)
    _floor(env, "Floor", (0.012, 0.012, 0.014), 0.14)
    _cyc(env, "Cyc", 48, (0.02, 0.021, 0.024), rough=0.6)
    _area(env, "KEY",  (6, -6.5, 6.5), 2600, 7.0, (1.0,0.97,0.92))
    _area(env, "FILL", (-5, -7.5, 3.2), 450, 9.0, (0.93,0.96,1.0))
    _area(env, "RIM_L",(7.5, 6, 3.6), 2100, 4.0, (0.82,0.9,1.0))
    _area(env, "RIM_R",(-7.5, 6, 3.6), 1900, 4.0, (0.82,0.9,1.0))
    _area(env, "BACK", (0, 9, 2.2), 800, 6.0, (0.7,0.82,1.0))
    for x, p in ((1.6,1550), (-1.6,1550), (0,800)):
        s = _area(env, f"STRIP{x}", (x,0,5.2), p, 0.45, (1,0.98,0.95), sy=9.5, aimat=None); s.rotation_euler=(0,0,0)
    _smoke(env)

def scene_white(env):
    _world((0.62, 0.63, 0.66), 0.5)
    _floor(env, "Floor", (0.80, 0.80, 0.82), 0.22)
    _cyc(env, "Cyc", 46, (1,1,1), emissive=(1,1,1,1.35))
    _area(env, "WKey", (6,-6,7), 2600, 8)
    _area(env, "WFillL",(-7,-4,4), 1400, 7)
    _area(env, "WFillR",(7,3,4), 1400, 7)
    _area(env, "WTop", (0,0,8), 1800, 10)
    bpy.context.scene.view_settings.exposure = 0.4

def scene_sunset(env):
    w = bpy.data.worlds.new("W_Sunset"); w.use_nodes = True; bpy.context.scene.world = w
    nt = w.node_tree; nt.nodes.clear()
    sky = nt.nodes.new('ShaderNodeTexSky'); bg = nt.nodes.new('ShaderNodeBackground'); out = nt.nodes.new('ShaderNodeOutputWorld')
    try: sky.sky_type = 'MULTIPLE_SCATTERING'
    except Exception:
        try: sky.sky_type = 'NISHITA'
        except Exception: pass
    for a, v in [('sun_elevation', math.radians(6)), ('sun_rotation', math.radians(115)),
                 ('sun_intensity', 0.6), ('dust_density', 3.0), ('air_density', 1.2),
                 ('ozone_density', 1.0), ('sun_size', math.radians(1.6))]:
        try: setattr(sky, a, v)
        except Exception: pass
    nt.links.new(sky.outputs['Color'], bg.inputs['Color']); nt.links.new(bg.outputs['Background'], out.inputs['Surface'])
    el, az = math.radians(8), math.radians(115)
    d = Vector((math.cos(el)*math.sin(az), math.cos(el)*math.cos(az), math.sin(el)))
    sd = bpy.data.lights.new("SunKey", 'SUN'); sd.energy = 4.5; sd.color = (1.0,0.6,0.34); sd.angle = math.radians(2)
    so = bpy.data.objects.new("SunKey", sd); so.location = d*60; env.objects.link(so)
    so.rotation_euler = (-d).to_track_quat('-Z','Y').to_euler()
    _area(env, "SkyFill", (-6,5,6), 500, 12, (0.55,0.68,0.95))
    _floor(env, "Ground", (0.035,0.033,0.032), 0.45)

def scene_neon(env):
    _world((0.004, 0.004, 0.007), 1.0)
    _floor(env, "Floor", (0.01,0.01,0.014), 0.08)
    MAG, CYAN = (1.0,0.08,0.55), (0.05,0.7,1.0)
    def bar(name, loc, col, p, aimat): return _area(env, name, loc, p, 0.35, col, sy=6.0, aimat=aimat)
    bar("N_M_F", (4.6,-2.6,1.3), MAG, 650, (1,-1.6,0.6))
    bar("N_M_R", (4.6,2.6,1.3),  MAG, 650, (1,1.6,0.6))
    bar("N_C_F", (-4.6,-2.6,1.3),CYAN,650, (-1,-1.6,0.6))
    bar("N_C_R", (-4.6,2.6,1.3), CYAN,650, (-1,1.6,0.6))
    t = _area(env, "N_Top", (0,0,5), 500, 0.4, (0.9,0.95,1.0), sy=9, aimat=None); t.rotation_euler=(0,0,0)
    _area(env, "N_Back", (0,7.5,2), 700, 3, (0.5,0.2,1.0), sy=2, aimat=(0,2,0.8))
    _smoke(env, density=0.006)

def _smoke(env, density=0.012):
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0,0,FLOOR_Z+4))
    box = bpy.context.active_object; box.name = "Atmo"; box.dimensions = (26,30,8); box.display_type='WIRE'; _put(box, env)
    m = bpy.data.materials.new("M_Atmo"); m.use_nodes = True; nt = m.node_tree; nt.nodes.clear()
    pv = nt.nodes.new('ShaderNodeVolumePrincipled'); out = nt.nodes.new('ShaderNodeOutputMaterial')
    pv.inputs['Density'].default_value = density; pv.inputs['Anisotropy'].default_value = 0.15
    nt.links.new(pv.outputs['Volume'], out.inputs['Volume']); box.data.materials.append(m)

def _cyc_custom(env, name, radius, setup):
    bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=radius, depth=48, location=(0,0,FLOOR_Z+24))
    o = bpy.context.active_object; o.name = name; _put(o, env)
    me = o.data; bm = bmesh.new(); bm.from_mesh(me)
    for f in bm.faces: f.normal_flip()
    bm.to_mesh(me); bm.free()
    m = bpy.data.materials.new("M_" + name); m.use_nodes = True; nt = m.node_tree; nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial'); setup(nt, out)
    o.data.materials.append(m); return o

def scene_warehouse(env):
    _world((0.012, 0.012, 0.014), 1.0)
    _floor(env, "Concrete", (0.035, 0.034, 0.032), 0.28)
    _cyc(env, "Walls", 40, (0.04, 0.039, 0.037), rough=0.8)
    for yy in (-4, 0, 4):
        s = _area(env, f"Prac{yy}", (0, yy, 7.5), 950, 0.6, (1.0,0.9,0.72), sy=4.0, aimat=None); s.rotation_euler=(0,0,0)
    _area(env, "Shaft_L", (8, -3, 6), 4200, 2.0, (1.0,0.92,0.8))
    _area(env, "Shaft_R", (-8, 4, 6), 1300, 2.0, (0.8,0.86,1.0))
    _smoke(env, density=0.013)

def scene_gradient(env):
    _world((0.01, 0.01, 0.012), 1.0)
    _floor(env, "Floor", (0.02, 0.02, 0.025), 0.10)
    def g(nt, out):
        tc = nt.nodes.new('ShaderNodeTexCoord'); sep = nt.nodes.new('ShaderNodeSeparateXYZ')
        ramp = nt.nodes.new('ShaderNodeValToRGB'); em = nt.nodes.new('ShaderNodeEmission')
        nt.links.new(tc.outputs['Generated'], sep.inputs['Vector']); nt.links.new(sep.outputs['Z'], ramp.inputs['Fac'])
        ramp.color_ramp.elements[0].position = 0.15; ramp.color_ramp.elements[0].color = (0.55,0.08,0.55,1)
        ramp.color_ramp.elements[1].position = 0.85; ramp.color_ramp.elements[1].color = (0.06,0.10,0.55,1)
        em.inputs['Strength'].default_value = 1.6
        nt.links.new(ramp.outputs['Color'], em.inputs['Color']); nt.links.new(em.outputs['Emission'], out.inputs['Surface'])
    _cyc_custom(env, "Grad", 30, g)
    _area(env, "GKey", (5, -6, 6), 1400, 7)
    _area(env, "GFillL", (-6, -3, 3.5), 700, 7, (0.9,0.9,1.0))
    _area(env, "GTop", (0, 0, 6), 700, 8)

def _city_facade_mat(name):
    """Emissive night-tower facade: a GRID of small warm windows built from
    world-space coords -- floor rows from Z, window columns from the horizontal
    axis chosen by the face normal (so every facade grids correctly, not just
    one) -- on a black emission body, with noise-gated dark 'floors'. Consistent
    window size, per-building variation. Procedural, no external assets."""
    m = bpy.data.materials.new(name); m.use_nodes = True; nt = m.node_tree; nt.nodes.clear()
    N, LK = nt.nodes, nt.links
    def mm(op, i0=None, i1=None):
        n = N.new('ShaderNodeMath'); n.operation = op
        if i0 is not None:
            LK.new(i0, n.inputs[0]) if not isinstance(i0, (int, float)) else setattr(n.inputs[0], 'default_value', i0)
        if i1 is not None:
            LK.new(i1, n.inputs[1]) if not isinstance(i1, (int, float)) else setattr(n.inputs[1], 'default_value', i1)
        return n
    out = N.new('ShaderNodeOutputMaterial')
    geo = N.new('ShaderNodeNewGeometry')
    sp = N.new('ShaderNodeSeparateXYZ'); LK.new(geo.outputs['Position'], sp.inputs['Vector'])
    sn = N.new('ShaderNodeSeparateXYZ'); LK.new(geo.outputs['Normal'], sn.inputs['Vector'])
    # face-relative horizontal coord: x-facing -> use Y, else use X
    isx = mm('GREATER_THAN', mm('ABSOLUTE', sn.outputs['X']).outputs[0], mm('ABSOLUTE', sn.outputs['Y']).outputs[0])
    inv = mm('SUBTRACT', 1.0, isx.outputs[0])
    horiz = mm('ADD', mm('MULTIPLY', sp.outputs['X'], inv.outputs[0]).outputs[0],
                       mm('MULTIPLY', sp.outputs['Y'], isx.outputs[0]).outputs[0])
    # window columns (~2m) and floor rows (~1.6m) -> hard 0/1 grid
    col = mm('GREATER_THAN', mm('FRACT', mm('MULTIPLY', horiz.outputs[0], 0.5).outputs[0]).outputs[0], 0.58)
    row = mm('GREATER_THAN', mm('FRACT', mm('MULTIPLY', sp.outputs['Z'], 0.62).outputs[0]).outputs[0], 0.55)
    win = mm('MULTIPLY', col.outputs[0], row.outputs[0])
    # noise gates whole regions dark (unlit floors)
    nz = N.new('ShaderNodeTexNoise'); nz.inputs['Scale'].default_value = 0.15
    LK.new(geo.outputs['Position'], nz.inputs['Vector'])
    region = mm('GREATER_THAN', nz.outputs['Fac'], 0.48)
    lit = mm('MULTIPLY', win.outputs[0], region.outputs[0])
    strn = mm('MULTIPLY', lit.outputs[0], 3.0)
    em = N.new('ShaderNodeEmission'); em.inputs['Color'].default_value = (1.0, 0.85, 0.60, 1)
    LK.new(strn.outputs[0], em.inputs['Strength'])
    LK.new(em.outputs['Emission'], out.inputs['Surface'])
    return m

def scene_nightcity(env):
    """Procedural night city, AUTO-FIT to the car's bbox (reuses _car_bbox like
    the camera rig): a ring of emissive-window skyscrapers around the car, a wet
    reflective street, warm/cool practical lights, haze. No external assets."""
    import random
    C, D = _car_bbox()
    L = max(D.x, D.y); fz = FLOOR_Z
    _world((0.004, 0.006, 0.012), 1.0)
    # wet reflective street (subtle ripple)
    o = _floor(env, "WetStreet", (0.008, 0.009, 0.013), 0.10)
    nt = o.data.materials[0].node_tree
    b = next(n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED')
    nz = nt.nodes.new('ShaderNodeTexNoise'); nz.inputs['Scale'].default_value = 2.5
    bmp = nt.nodes.new('ShaderNodeBump'); bmp.inputs['Strength'].default_value = 0.05
    nt.links.new(nz.outputs['Fac'], bmp.inputs['Height']); nt.links.new(bmp.outputs['Normal'], b.inputs['Normal'])
    # ring of emissive-window buildings surrounding the car (distances/heights scale with car size)
    citymat = _city_facade_mat("M_CityFacade")
    random.seed(11)
    rin, rout = 6.0 * L, 18.0 * L
    for i in range(80):
        ang = random.uniform(0, 2 * math.pi); rad = random.uniform(rin, rout)
        bx, by = C.x + math.cos(ang) * rad, C.y + math.sin(ang) * rad
        w = random.uniform(1.0 * L, 3.0 * L); dp = random.uniform(1.0 * L, 3.0 * L)
        h = random.uniform(3.0 * L, 12.0 * L)
        bpy.ops.mesh.primitive_cube_add(size=1, location=(bx, by, fz + h / 2))
        bld = bpy.context.active_object; bld.name = f"Bldg_{i:02d}"; bld.scale = (w, dp, h)
        _put(bld, env); bld.data.materials.append(citymat)
    # practical colored lights on the car (auto-fit)
    _area(env, "C_warm", (C.x + 0.9 * D.x, C.y - 0.8 * D.y, fz + 1.2 * D.z), 500, 0.5 * L,
          (1.0, 0.55, 0.25), sy=0.35 * L, aimat=(C.x, C.y - 0.3 * D.y, fz + 0.4 * D.z))
    _area(env, "C_cool", (C.x - 0.9 * D.x, C.y + 0.8 * D.y, fz + 1.2 * D.z), 500, 0.5 * L,
          (0.3, 0.55, 1.0), sy=0.35 * L, aimat=(C.x, C.y + 0.3 * D.y, fz + 0.4 * D.z))
    _area(env, "C_moon", (C.x, C.y, fz + 3.2 * D.z), 220, 1.4 * L, (0.5, 0.62, 0.95), aimat=None)
    _area(env, "C_kick", (C.x + 1.3 * D.x, C.y - 1.1 * D.y, fz + 0.35 * D.z), 320, 0.4 * L,
          (1.0, 0.88, 0.72), aimat=(C.x, C.y - 0.6 * D.y, fz + 0.22 * D.z))
    _smoke(env, density=0.006)
    sc = bpy.context.scene
    sc.view_settings.exposure = -0.55
    try: sc.cycles.sample_clamp_indirect = 8.0
    except Exception: pass

def _sky_world(elev, rot, dust=2.0, inten=0.6, strength=1.0):
    sc = bpy.context.scene
    w = bpy.data.worlds.new("W_Sky"); w.use_nodes = True; sc.world = w
    nt = w.node_tree; nt.nodes.clear()
    sky = nt.nodes.new('ShaderNodeTexSky'); bg = nt.nodes.new('ShaderNodeBackground'); out = nt.nodes.new('ShaderNodeOutputWorld')
    try: sky.sky_type = 'MULTIPLE_SCATTERING'
    except Exception:
        try: sky.sky_type = 'NISHITA'
        except Exception: pass
    for a, v in [('sun_elevation', math.radians(elev)), ('sun_rotation', math.radians(rot)),
                 ('sun_intensity', inten), ('dust_density', dust), ('air_density', 1.0), ('sun_size', math.radians(1.5))]:
        try: setattr(sky, a, v)
        except Exception: pass
    bg.inputs['Strength'].default_value = strength
    nt.links.new(sky.outputs['Color'], bg.inputs['Color']); nt.links.new(bg.outputs['Background'], out.inputs['Surface'])

def _sun(env, name, elev, az, energy, color, angle=2.0):
    el, a = math.radians(elev), math.radians(az)
    d = Vector((math.cos(el)*math.sin(a), math.cos(el)*math.cos(a), math.sin(el)))
    sd = bpy.data.lights.new(name, 'SUN'); sd.energy = energy; sd.color = color; sd.angle = math.radians(angle)
    o = bpy.data.objects.new(name, sd); o.location = d*80; env.objects.link(o)
    o.rotation_euler = (-d).to_track_quat('-Z', 'Y').to_euler()
    return o, d

def scene_snow(env):
    _sky_world(22, 40, dust=1.2, inten=0.5, strength=0.8)
    _sun(env, "SnowSun", 24, 45, 2.0, (0.95,0.97,1.0), angle=3)
    o = _floor(env, "Snow", (0.9,0.92,0.97), 0.5)
    nt = o.data.materials[0].node_tree; b = next(n for n in nt.nodes if n.type=='BSDF_PRINCIPLED')
    tex = nt.nodes.new('ShaderNodeTexNoise'); tex.inputs['Scale'].default_value = 28
    bump = nt.nodes.new('ShaderNodeBump'); bump.inputs['Strength'].default_value = 0.12
    nt.links.new(tex.outputs['Fac'], bump.inputs['Height']); nt.links.new(bump.outputs['Normal'], b.inputs['Normal'])
    _area(env, "SnowFill", (-6,4,7), 700, 12, (0.8,0.86,1.0))
    _smoke(env, density=0.006)
    bpy.context.scene.view_settings.exposure = -1.35

def scene_tunnel(env):
    _world((0.005,0.005,0.007), 1.0)
    bpy.ops.mesh.primitive_cylinder_add(vertices=48, radius=5.2, depth=48, location=(0,0,FLOOR_Z+2.4), rotation=(math.radians(90),0,0))
    t = bpy.context.active_object; t.name = "Tube"; _put(t, env)
    me = t.data; bm = bmesh.new(); bm.from_mesh(me)
    for fc in bm.faces: fc.normal_flip()
    bm.to_mesh(me); bm.free()
    m = bpy.data.materials.new("M_Tube"); m.use_nodes = True
    b = next(n for n in m.node_tree.nodes if n.type=='BSDF_PRINCIPLED'); b.inputs['Base Color'].default_value=(0.03,0.03,0.035,1); b.inputs['Roughness'].default_value=0.5
    t.data.materials.append(m)
    _floor(env, "Road", (0.02,0.02,0.024), 0.12)
    for yy in (-12,-6,0,6,12):
        s = _area(env, f"TL{yy}", (0,yy,FLOOR_Z+5.6), 1600, 0.5, (1.0,0.85,0.6), sy=2.2, aimat=None); s.rotation_euler=(0,0,0)
    for yy, col in ((-23.5,(0.7,0.8,1.0)), (23.5,(1.0,0.9,0.75))):
        ld = bpy.data.lights.new(f"End{yy}",'AREA'); ld.energy=2500; ld.size=6; ld.color=col
        o = bpy.data.objects.new(f"End{yy}",ld); o.location=(0,yy,FLOOR_Z+2.4); env.objects.link(o); o.rotation_euler=(math.radians(90 if yy<0 else -90),0,0)
    _smoke(env, density=0.018, )

def scene_rooftop(env):
    _sky_world(52, 60, dust=1.6, inten=0.9, strength=0.85)
    _sun(env, "MiddaySun", 52, 60, 3.0, (1.0,0.96,0.9), angle=1.0)
    _floor(env, "Deck", (0.16,0.16,0.17), 0.42)
    _area(env, "SkyFill", (-6,5,6), 400, 12, (0.6,0.72,1.0))
    bpy.context.scene.view_settings.exposure = -1.4

def scene_forest(env):
    _sky_world(30, 110, dust=2.5, inten=0.7, strength=0.5)
    _, d = _sun(env, "ForestSun", 30, 110, 8.0, (1.0,0.8,0.45), angle=0.5)
    _floor(env, "ForestFloor", (0.03,0.05,0.02), 0.8)
    _area(env, "GreenBounce", (-5,3,3), 350, 10, (0.25,0.5,0.2))
    bpy.ops.mesh.primitive_plane_add(size=26, location=(d.x*4.5, d.y*4.5, FLOOR_Z+5.5))
    can = bpy.context.active_object; can.name = "Canopy"; _put(can, env)
    can.rotation_euler = (-d).to_track_quat('-Z','Y').to_euler()
    m = bpy.data.materials.new("M_Canopy"); m.use_nodes = True; nt = m.node_tree
    b = next(n for n in nt.nodes if n.type=='BSDF_PRINCIPLED'); b.inputs['Base Color'].default_value=(0.02,0.05,0.015,1)
    nz = nt.nodes.new('ShaderNodeTexNoise'); nz.inputs['Scale'].default_value=6.0; nz.inputs['Detail'].default_value=6
    rmp = nt.nodes.new('ShaderNodeValToRGB'); rmp.color_ramp.elements[0].position=0.40; rmp.color_ramp.elements[1].position=0.52
    nt.links.new(nz.outputs['Fac'], rmp.inputs['Fac']); nt.links.new(rmp.outputs['Color'], b.inputs['Alpha'])
    can.data.materials.append(m)
    _smoke(env, density=0.008)
    bpy.context.scene.view_settings.exposure = -0.6

def scene_backrooms(env):
    _world((0.02,0.02,0.015), 1.0)
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0,0,FLOOR_Z+2.7))
    room = bpy.context.active_object; room.name="Room"; room.dimensions=(28,28,5.6); _put(room, env)
    bpy.context.view_layer.update()
    me = room.data; bm = bmesh.new(); bm.from_mesh(me)
    for fc in bm.faces: fc.normal_flip()
    bm.to_mesh(me); bm.free()
    m = bpy.data.materials.new("M_Wall"); m.use_nodes = True
    b = next(n for n in m.node_tree.nodes if n.type=='BSDF_PRINCIPLED'); b.inputs['Base Color'].default_value=(0.60,0.50,0.20,1); b.inputs['Roughness'].default_value=0.62
    room.data.materials.append(m)
    _floor(env, "Carpet", (0.42,0.35,0.14), 0.95)
    for x in (-7,0,7):
        for y in (-7,0,7):
            p = _area(env, f"Fl{x}_{y}", (x,y,FLOOR_Z+5.0), 260, 1.8, (0.96,1.0,0.90), sy=0.5, aimat=None); p.rotation_euler=(0,0,0)

def scene_cyberpunk(env):
    _world((0.004, 0.004, 0.008), 1.0)
    _floor(env, "WetStreet", (0.006, 0.006, 0.01), 0.05)
    def bb(nm, loc, yaw, w_, h_, col, strg):
        bpy.ops.mesh.primitive_plane_add(size=1, location=loc, rotation=(math.radians(90),0,math.radians(yaw)))
        p = bpy.context.active_object; p.name = nm; p.scale = (w_, h_, 1); _put(p, env)
        mt = bpy.data.materials.new("M_" + nm); mt.use_nodes = True; nt = mt.node_tree; nt.nodes.clear()
        em = nt.nodes.new('ShaderNodeEmission'); out = nt.nodes.new('ShaderNodeOutputMaterial')
        em.inputs['Color'].default_value = (*col, 1); em.inputs['Strength'].default_value = strg
        nt.links.new(em.outputs['Emission'], out.inputs['Surface']); p.data.materials.append(mt)
    MAG=(1.0,0.05,0.5); CY=(0.05,0.8,1.0); PUR=(0.5,0.1,1.0); AMB=(1.0,0.5,0.1); GRN=(0.2,1.0,0.4); PINK=(1.0,0.2,0.6)
    bb("BB_mag",(11,-6,2.6),100,3.5,4.5,MAG,6);  bb("BB_cy",(-11,3,3.2),-70,2.5,5.5,CY,6)
    bb("BB_pur",(6,12,3.0),150,5.0,4.0,PUR,5);   bb("BB_amb",(-8,-9,1.8),40,3.0,2.2,AMB,5)
    bb("BB_grn",(12,5,2.2),115,1.6,3.2,GRN,7);   bb("BB_pink",(-12,-3,3.6),-95,2.0,4.5,PINK,6)
    bb("BB_cy2",(3,-13,2.4),175,4.5,3.0,CY,4)
    _area(env, "N_mag", (4.5,-3,1.6), 600, 0.3, MAG, sy=5, aimat=(1,-1.6,0.6))
    _area(env, "N_cy",  (-4.5,3,1.6), 600, 0.3, CY,  sy=5, aimat=(-1,1.6,0.6))
    _area(env, "N_pur", (0,6.5,2.2),  700, 0.3, PUR, sy=3, aimat=(0,2,0.8))
    _area(env, "N_amb", (3.5,4,1.2),  350, 0.3, AMB, sy=3, aimat=(1,1.5,0.5))
    _smoke(env, density=0.03)

SCENES = {"dark": scene_dark, "white": scene_white, "sunset": scene_sunset, "neon": scene_neon,
          "warehouse": scene_warehouse, "gradient": scene_gradient, "nightcity": scene_nightcity,
          "snow": scene_snow, "tunnel": scene_tunnel, "rooftop": scene_rooftop,
          "forest": scene_forest, "backrooms": scene_backrooms, "cyberpunk": scene_cyberpunk}

# --------------------------------------------------------------------------- #
#  5. Camera package  (15-shot vertical montage)
# --------------------------------------------------------------------------- #
# (frame_start, cam_name, lens, fstop, key0(loc), key1(loc), target, [target_key0,target_key1])
def _car_bbox():
    mn = [1e18]*3; mx = [-1e18]*3
    for o in bpy.data.objects:
        if o.type == 'MESH' and len(o.data.vertices):
            mw = o.matrix_world
            for corner in o.bound_box:
                w = mw @ Vector(corner)
                for i in range(3):
                    if w[i] < mn[i]: mn[i] = w[i]
                    if w[i] > mx[i]: mx[i] = w[i]
    C = Vector(((mn[0]+mx[0])/2, (mn[1]+mx[1])/2, (mn[2]+mx[2])/2))
    D = Vector((mx[0]-mn[0], mx[1]-mn[1], mx[2]-mn[2]))
    return C, D

# Reference bbox the 15-shot rig was hand-tuned against (Koenigsegg Agera RSR).
_REF_C = Vector((0.0, -0.093, 0.6495)); _REF_D = Vector((3.072, 6.088, 1.615))

def build_cameras(single=None):
    sc = bpy.context.scene
    cams = bpy.data.collections.get("CAMERAS") or bpy.data.collections.new("CAMERAS")
    if cams.name not in [c.name for c in sc.collection.children]: sc.collection.children.link(cams)
    for o in list(cams.objects): bpy.data.objects.remove(o, do_unlink=True)
    for mk in list(sc.timeline_markers): sc.timeline_markers.remove(mk)
    prefs = bpy.context.preferences

    # Fit the tuned rig onto THIS car: match centre + per-axis scale.
    Cn, Dn = _car_bbox()
    kx, ky, kz = Dn.x/_REF_D.x, Dn.y/_REF_D.y, Dn.z/_REF_D.z
    def xf(p):   # absolute point (camera/target positions)
        return (Cn.x + kx*(p[0]-_REF_C.x), Cn.y + ky*(p[1]-_REF_C.y), Cn.z + kz*(p[2]-_REF_C.z))
    def off(v):  # relative offset (parented orbit cameras)
        return (v[0]*kx, v[1]*ky, v[2]*kz)
    log("CAM", f"fit rig -> centre {tuple(round(x,2) for x in Cn)} dims {tuple(round(x,2) for x in Dn)} k=({kx:.2f},{ky:.2f},{kz:.2f})")

    def E(name, loc):
        o = bpy.data.objects.new(name, None); o.location = xf(loc); cams.objects.link(o); return o
    def C(name, lens, f=None):
        cd = bpy.data.cameras.new(name); cd.lens = lens
        o = bpy.data.objects.new(name, cd); cams.objects.link(o)
        if f: cd.dof.use_dof = True; cd.dof.aperture_fstop = f
        return o
    def trk(c, t): k = c.constraints.new('TRACK_TO'); k.target = t; k.track_axis='TRACK_NEGATIVE_Z'; k.up_axis='UP_Y'
    def foc(c, t): c.data.dof.use_dof = True; c.data.dof.focus_object = t
    def KL(o, fr, l): o.location = xf(l); o.keyframe_insert('location', frame=T(fr))
    def KR(o, fr, r): o.rotation_euler = r; o.keyframe_insert('rotation_euler', frame=T(fr))
    def M(name, fr, c): mk = sc.timeline_markers.new(name, frame=T(fr)); mk.camera = c
    def lin(x): prefs.edit.keyframe_new_interpolation_type = x

    lin('BEZIER')
    C_ = E("T_center",(0,0,0.55)); BADGE=E("T_badge",(0,-2.55,0.62)); WF=E("T_wheelF",(1.19,-1.86,0.42))
    MIR=E("T_mirror",(1.40,-0.53,1.02)); SV=E("T_sidevent",(1.44,0.60,0.60)); WING=E("T_wing",(0,2.55,1.03))
    EXH=E("T_exhaust",(0,2.79,0.33)); HL=E("T_head",(0.98,-2.54,0.62)); t2=E("T_t2",(0,-2.4,0.15))
    t9=E("T_t9",(1.14,1.83,0.4)); rf=E("T_roof",(0,-0.8,1.2))

    shots = {}   # key -> (start,end)
    def add(key, start, end): shots[key] = (T(start), T(end))

    c=C("V1_Hero24",24,8.0); trk(c,C_); KL(c,1,(3.7,-5.2,0.5)); KL(c,52,(3.0,-4.5,0.78)); M("S01",1,c); add("hero",1,52)
    c=C("V2_TiltUp",35,4.0); trk(c,t2); foc(c,t2); KL(c,53,(0.9,-5.2,0.55)); KL(c,96,(0.9,-5.2,0.75))
    KL(t2,53,(0,-2.5,0.1)); KL(t2,96,(0,-1.2,1.34)); M("S02",53,c); add("tiltup",53,96)
    c=C("V3_Dutch",28,5.6); foc(c,C_)
    p0,p1,tg=(3.5,-4.6,0.55),(2.7,-3.9,0.7),(0,-0.2,0.8)
    KL(c,97,p0); KL(c,140,p1)
    Q = __import__('mathutils').Quaternion
    px0,px1,pxt=Vector(xf(p0)),Vector(xf(p1)),Vector(xf(tg))
    c.location=px0; KR(c,97,((pxt-px0).to_track_quat('-Z','Y')@Q((0,0,1),math.radians(8))).to_euler())
    c.location=px1; KR(c,140,((pxt-px1).to_track_quat('-Z','Y')@Q((0,0,1),math.radians(6))).to_euler())
    M("S03",97,c); add("dutch",97,140)
    c=C("V4_Front",45,5.0); trk(c,BADGE); foc(c,BADGE); KL(c,141,(0,-5.1,0.4)); KL(c,182,(0,-4.5,0.56)); M("S04",141,c); add("front",141,182)
    c=C("V5_Wheel",85,2.2); trk(c,WF); foc(c,WF); KL(c,183,(4.1,-2.7,0.5)); KL(c,224,(4.1,-1.2,0.62)); M("S05",183,c); add("wheel",183,224)
    piv=E("ORB1",(0,0,0.5)); c=C("V6_Orbit1",35,7.0); c.parent=piv; c.location=off((0,-6.4,1.25)); trk(c,C_)
    lin('LINEAR'); KR(piv,225,(0,0,0)); KR(piv,288,(0,0,math.radians(-105))); lin('BEZIER'); M("S06",225,c); add("orbit",225,288)
    c=C("V7_Side",85,2.5); trk(c,MIR); foc(c,MIR); KL(c,289,(4.2,-1.3,1.02)); KL(c,330,(4.2,-0.2,0.95))
    KL(MIR,289,(1.40,-0.53,1.02)); KL(MIR,330,(1.46,-0.10,0.80)); M("S07",289,c); add("side",289,330)
    c=C("V8_Intake",85,2.8); trk(c,SV); foc(c,SV); KL(c,331,(4.0,0.3,0.72)); KL(c,370,(3.7,0.95,0.66)); M("S08",331,c); add("intake",331,370)
    c=C("V9_RearTilt",50,3.5); trk(c,t9); foc(c,t9); KL(c,371,(3.4,3.1,0.5)); KL(c,414,(3.1,3.3,0.95))
    KL(t9,371,(1.14,1.83,0.38)); KL(t9,414,(0.2,2.55,1.05)); M("S09",371,c); add("reartilt",371,414)
    c=C("V10_Wing",35,5.6); trk(c,WING); foc(c,WING); KL(c,415,(2.0,6.0,0.5)); KL(c,456,(1.1,5.5,0.72)); M("S10",415,c); add("wing",415,456)
    piv2=E("ORB2",(0,0,0.5)); c=C("V11_Orbit2",40,7.0); c.parent=piv2; c.location=off((0,-6.2,1.5)); trk(c,C_)
    lin('LINEAR'); KR(piv2,457,(0,0,math.radians(200))); KR(piv2,518,(0,0,math.radians(285))); lin('BEZIER'); M("S11",457,c); add("orbit2",457,518)
    c=C("V12_Exhaust",100,2.2); trk(c,EXH); foc(c,EXH); KL(c,519,(0.8,4.7,0.45)); KL(c,558,(-0.6,4.7,0.5)); M("S12",519,c); add("exhaust",519,558)
    c=C("V13_Roof",50,4.0); trk(c,rf); foc(c,rf); KL(c,559,(0.7,-3.4,2.4)); KL(c,602,(0.5,-1.1,1.95))
    KL(rf,559,(0,-1.4,1.05)); KL(rf,602,(0,-0.4,1.28)); M("S13",559,c); add("roof",559,602)
    c=C("V14_Head",105,2.0); c.data.dof.use_dof=True; KL(c,603,(2.4,-3.9,0.75)); KL(c,642,(2.15,-3.65,0.72)); trk(c,HL)
    c.data.dof.focus_distance=3.1; c.data.dof.keyframe_insert('focus_distance',frame=T(603))
    c.data.dof.focus_distance=1.95; c.data.dof.keyframe_insert('focus_distance',frame=T(642)); M("S14",603,c); add("headlight",603,642)
    c=C("V15_Finale",24,6.3); trk(c,C_); KL(c,643,(2.8,-4.3,0.65)); KL(c,702,(3.9,-6.6,1.5)); M("S15",643,c); add("finale",643,702)

    sc.frame_start, sc.frame_end = 1, T(702)
    sc.camera = bpy.data.objects["V1_Hero24"]

    if single and single != "montage" and single in shots:
        s, e = shots[single]
        sc.frame_start, sc.frame_end = s, e
        cam = [mk.camera for mk in sc.timeline_markers if s <= mk.frame <= e]
        if cam: sc.camera = cam[0]
        log("CAM", f"single shot '{single}' -> frames {s}-{e}")
    else:
        log("CAM", f"full montage, 15 shots, frames 1-{T(702)} ({round(T(702)/FPS,1)}s @ {FPS}fps)")
    return shots

# --------------------------------------------------------------------------- #
#  6. Render config
# --------------------------------------------------------------------------- #
ASPECTS = {"vertical": (1080, 1920), "landscape": (1920, 1080), "square": (1080, 1080)}

def render_config():
    sc = bpy.context.scene; r = sc.render
    r.engine = 'CYCLES'; sc.cycles.device = 'GPU'
    try:
        p = bpy.context.preferences.addons['cycles'].preferences
        p.compute_device_type = 'HIP'
        for d in p.devices: d.use = (d.type != 'CPU')
    except Exception: pass
    sc.cycles.samples = int(CFG["samples"]); sc.cycles.use_denoising = True
    sc.cycles.use_adaptive_sampling = True; sc.cycles.adaptive_threshold = 0.01
    w, h = ASPECTS.get(CFG["aspect"], ASPECTS["vertical"])
    r.resolution_x, r.resolution_y = w, h; r.resolution_percentage = 100; r.fps = FPS
    r.use_motion_blur = False
    r.image_settings.file_format = 'PNG'
    r.filepath = os.path.join(CFG["frames_dir"], "RSR_") if CFG["frames_dir"] else "//frames/RSR_"
    r.use_overwrite = False; r.use_placeholder = True
    sc.view_settings.view_transform = 'AgX'
    for lk in ('AgX - Medium High Contrast', 'AgX - Base Contrast', 'None'):
        try: sc.view_settings.look = lk; break
        except Exception: continue
    log("RENDER", f"Cycles GPU, {CFG['aspect']} {w}x{h}, {CFG['samples']} spp")

# --------------------------------------------------------------------------- #
#  main
# --------------------------------------------------------------------------- #
def main():
    global FLOOR_Z
    log("CONFIG", json.dumps({k: CFG[k] for k in ('scene','skin','aspect','camera','samples')}))
    reset_scene()
    import_fbx(CFG["fbx_path"])
    normalize_scale()
    FLOOR_Z = ground_z(); log("IMPORT", f"ground Z = {FLOOR_Z:.3f}")
    audit_fix()
    apply_skin(CFG["car_dir"], CFG["skin"])
    build_cameras(CFG["camera"])   # fit the camera rig to the BASE car bbox first
    if CFG.get("wheel_fbx"):
        swap_wheels(CFG["wheel_fbx"])   # then swap wheels (same size/position, framing unaffected)
    env = _env_collection()
    SCENES.get(CFG["scene"], scene_dark)(env)
    log("SCENE", f"built '{CFG['scene']}'")
    render_config()
    out = bpy.path.abspath(CFG["out_blend"])
    bpy.ops.wm.save_as_mainfile(filepath=out)
    log("DONE", f"saved {out}")

if __name__ == "__main__":
    main()
