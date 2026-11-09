# Car Video Generator — Project Context (handoff)

_Last updated: 2026-07-09. Written for the next Claude session (previous one ran low on context)._

## What this project is
Turn Assetto Corsa car mods (and now other cars) into cinematic **vertical 9:16** studio/scene videos in Blender, with a **reusable pipeline** and a **library of scene presets**. Primary demo car: **Koenigsegg Agera RSR** (teal skin #122).

## Machine / tools (verify paths still valid)
- **Blender**: `C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe` (reports v5.1, EEVEE/Cycles). Connected to Claude via the **Blender MCP addon** on `localhost:9876` — Blender must be OPEN with the addon server started for MCP tools to work.
- **GPU**: AMD RX 9070 XT, Cycles via **HIP** (`force_gpu.py` enables it headless).
- **ffmpeg**: `C:\Program Files\File Converter\ffmpeg.exe` (gyan.dev 8.0.1, libx264). **This Blender build has NO ffmpeg output** — must encode PNG frames with this external ffmpeg. Not on PATH.
- **kn5conv.exe** + `FBXConverter.dll` + `libfbxsdk.dll` in project root — converts AC `.kn5` → FBX (writes both ASCII `.fbx` and binary `*_binary.fbx`; **Blender only imports the BINARY one**).

## The main file
`D:\Car video generator\Agera_RSR_studio.blend` — **WARNING: currently 71 MB** (was ~11 MB clean). It likely got the **broken horror-forest FBX saved into it**. There is a backup `Agera_RSR_studio.blend1` that is probably the clean pre-forest save. **First thing next session: open the file, check for objects whose name starts with `S_` (the broken forest) — if present, delete them all (they have wild transforms, objects flung to Z=666, and bog Blender down). If the file is corrupt/too heavy, fall back to `.blend1`.**

Contains (when clean): **13 scenes**, one shared car (`Collection`), one shared `CAMERAS` collection (15-shot vertical montage), per-scene `ENV_*` collections.

## The 13 scenes (Blender Scene datablocks, pick via scene dropdown)
`01_Dark_Studio, 02_White_Studio, 03_Sunset_Outdoor, 04_Neon_Night, 05_Warehouse, 06_Gradient, 07_Night_City, 08_Snow, 09_Tunnel, 10_Rooftop_Day, 11_Forest, 12_Backrooms, 13_Cyberpunk`

All share the car + the 15 cameras (markers bind cameras across frames 1–702 @24fps ≈ 29 s). Each scene has its own `ENV_<name>` collection + world + render settings (Cycles GPU, 1080×1920, 128 spp, AgX, resumable PNG output to `renders/frames/RSR_`).

Preview stills of all 13: `renders/previews/01_Dark_Studio.png` … `13_Cyberpunk.png`, plus `renders/previews/SCENES_ALL13.png` contact sheet.

## The 15-shot vertical camera montage (shared, auto-cut via timeline markers)
hero(24mm) · tiltup(35) · dutch(28) · front-fascia(45) · wheel(85) · orbit1(35) · side-mirror(85) · intake(85) · rear-tilt(50) · wing(35) · orbit2(40) · exhaust(100) · roof(50) · headlight-rack(105) · finale(24). Cameras named `V1_Hero24`…`V15_Finale` in `CAMERAS`. Frame 25 is the hero shot (used for all preview stills).

## Reusable pipeline (files in project root + pipeline/)
- **`MAKE_CAR_VIDEO.bat`** → `Make-CarVideo.ps1` — interactive: extract car zip → kn5conv → menus (skin/scene/aspect/camera/quality) → build → optional render+encode. Output in `output/<car>/`.
- **`pipeline/build_car.py`** — headless builder: `blender -b -P build_car.py -- config.json`. Does import → **scale-normalize** (kn5 FBX imports at 1/100; auto ×100 to metres) → auto-fixes → skin → one scene preset → 15-cam package **auto-fit to car bbox** → render config → save .blend. Has all **13 scene builders** (functions `scene_dark`…`scene_cyberpunk`, dict `SCENES`).
- **`pipeline/README.md`**, **`force_gpu.py`**, **`RENDER_OVERNIGHT.bat`** (renders active scene's frames then ffmpeg-encodes; glob-based).

## Auto-fixes discovered (all in build_car.py `audit_fix()` and applied to the live file)
KN5→FBX imports break these — the fixes:
1. **Damage normal maps** (`*damage*` image wired to Principled Normal) → disconnect (body looked crumpled/dented). 4 materials on the Agera.
2. **Crash-damage meshes** (name contains `damage`/`broken`) → hide from render (were z-fighting cracked glass over windows).
3. **Fake-alpha glass** (Transmission 0, rough 0.55, alpha-hashed, name has glass/window, NOT a colored lens) → rebuild as real transmissive glass (Transmission 1, rough 0.03, IOR 1.45, light tint). Milky windows → clear.
4. **Matte paint** (name has carpaint/paint/ext_skin) → add glossy clearcoat + roughness 0.32.
5. **Flat carbon** (name has carbon) → roughness 0.28 + coat.
6. **Livery/skin**: NOT a wiring bug — default baked skin is blank; apply a skin by repointing image datablock filepaths to `skins/<name>/*` and reloading. Agera uses teal **#122**.

## Scene-build gotchas (learned the hard way)
- **Reflective-floor "box" artifact**: backdrop **cylinder end-caps sitting at floor level z-fight the reflective floor**. Fix = delete cap faces (`abs(face.normal.z)>0.85`) from cyc cylinders (`Cyc/Walls/Grad/City/White_Cyc/Studio_Cyc`).
- **Visible haze "box"**: the `Atmo` volume domain box was too small (26–30 u) so its edges showed. Fix = enlarge domain XY to ~72×72.
- **Sky scenes blow out**: sky-texture world + a strong Sun lamp = clipped whites. Fix = lower sun energy + `view_settings.exposure` negative (snow −1.35→ overcast, rooftop −1.4, sunset −0.2, forest −0.6). Blender 5 Sky node type is `MULTIPLE_SCATTERING` (NOT `NISHITA`).
- **Sunset** now real golden hour: sun_elevation ~2°, dust_density 6.5, warm sun color, warm ground haze.
- **Backrooms** walls/carpet now have procedural mottled/damp texture + bump.
- **Snow particles STILL NOT WORKING** — a particle system (emitter `SnowEmitter` + instance `Flake`) was added but flakes don't render (white-on-bright, and possibly the sim isn't evaluating in headless). NEEDS FIXING (see prompt.md). Switched snow to overcast grey world so flakes *could* show.

## Night City = the proven asset-integration recipe (WORKS)
Integrated **Downtown LA photogrammetry** (`work/la-night-city/source/model/Dtla_Orbit.fbx`, UDIM diffuse textures, imported fine). Recipe that worked — **use this for every environment asset**:
1. Import model into the target scene's ENV collection; move the imported mesh(es) into `ENV_<scene>`.
2. **Scale up + push far behind the car as a backdrop** (LA: scale 5, location (0, 80, −13)). Do NOT center a dense environment on the car — the car gets buried and the render goes black.
3. **Carve a clearing**: delete env objects whose world bbox-centre is within ~5 m of origin (car sits at origin). Add a **wet reflective floor plane** at z=FLOOR_Z (≈ −0.158) under the car.
4. **Make windows glow**: feed the diffuse texture into Principled **Emission Color** (strength ~0.9) so the daytime scan reads as a night skyline.
5. **Light the car** with neon/practical area lights + a soft cool moon key (photogrammetry ambient won't light the car).
6. Result render: `renders/previews/CITY_look3.png` (great — glowing LA skyline, wet street reflection). **This night-city LA setup may or may not be in the current .blend depending on save state — REDO it if missing (user explicitly asked to redo night city).**

## Assets the user provided (in project root) — for the NEXT session
| File | ~Size | Format | Use for |
|---|---|---|---|
| `horror_forest_and_graveyard_glb.zip` | 636 MB | **glb** inside: `Horror Forest and Graveyard GLB/Horror Forest and Graveyard GLB.glb` | **Forest scene (11)** — REDO with this clean glb (the FBX version was broken; glb has correct scale + auto PBR) |
| `modular_city.glb` | 94 MB | glb (ready) | Add modular city to a scene (night city / new modular scene) |
| `city.zip` | 1.6 GB | glTF pieces: `City_Exports/*/(Grid System/Parking/Road/Sidewalk/Towers/Green Area).gltf` (+ .bin) | Big modular city (buildings, roads, towers) — alt for night city / modular scene |
| `postwar-city-exterior-scene.zip` | 122 MB | `.blend`: `source/d77c0cc629164202b994da7e203115fc.blend.blend` | **NEW "post-war" scene** (append/link) — user wants a post-war variant alongside cyberpunk |
| `neon_descent_pack.blend` | 122 MB | `.blend` (ready to append/link) | Cyberpunk enhancement (neon assets) |
| `bdb_volvo_850r.rar` | 166 MB | AC car (rar) | Test the pipeline on a 2nd car later |

**glTF/glb import tips**: `bpy.ops.import_scene.gltf(filepath=...)`. glb is in metres with PBR auto-wired — much cleaner than the FBX. Still apply the Night-City recipe (scale/position as backdrop, carve clearing, light car). Append from a `.blend`: `bpy.ops.wm.append(directory=path+"/Object/", ...)` or link a Scene/Collection.

## Render facts
Full quality per frame ≈ 37 s (1080×1920, 128 spp) on the 9070 XT → 702 frames ≈ **~7 hours**. User wants final render **only on explicit command** (overnight). Launch: `RENDER_OVERNIGHT.bat` (renders active scene). Resumable PNG frames → ffmpeg MP4.

## Memory files (also loaded automatically)
`agera-rsr-studio-render.md`, `car-video-generator-pipeline.md`, `blender-no-ffmpeg-use-fileconverter.md` in the Claude memory dir summarize the above.
