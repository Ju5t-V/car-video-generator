# Car Video Generator

Turns an Assetto Corsa car mod (`.zip`) into a cinematic **9:16 / 16:9 / 1:1** showcase video in Blender, automatically.

**Pipeline:** extract mod → convert `.kn5` to FBX → import and rescale in Blender → auto-fix common conversion breakage (damage normal maps, fake-alpha glass, clearcoat paint, carbon roughness) → apply chosen livery → build one of 13 scene presets (studio, neon, night city, snow, tunnel, cyberpunk …) → 15-shot camera montage fitted to the car's bounding box → Cycles GPU render (resumable) → ffmpeg encode.

See [`pipeline/README.md`](pipeline/README.md) for full usage and options.

## Files
| File | Purpose |
|---|---|
| `MAKE_CAR_VIDEO.bat` / `Make-CarVideo.ps1` | Interactive one-click front end (skin / scene / aspect / camera / quality menus) |
| `pipeline/build_car.py` | Headless Blender build script |
| `force_gpu.py` | Enables Cycles HIP GPU rendering when running headless |
| `RENDER_OVERNIGHT.bat` | Overnight batch render of a saved scene |
| `examples/` | Example scripted render job (Volvo 850R cyberpunk scene) |
| `docs/ENGINEERING_NOTES.md` | Detailed engineering notes |

> Car models, scene asset packs and the `kn5conv` converter are third-party and **not included**. Supply your own.

## Status
🟡 Working pipeline with 13 scene presets. Still being polished.
