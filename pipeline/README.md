# Car Video Generator

Turn an Assetto Corsa car mod (`.zip`) into a cinematic studio video — automatically.

## One-click use

1. Put the car mod **`.zip`** in this folder (`D:\Car video generator`).
2. Double-click **`MAKE_CAR_VIDEO.bat`**.
3. Answer the menus:
   - **Skin** – every livery in the car's `skins/` folder (or the default)
   - **Scene** – `dark` · `white` · `sunset` · `neon` · `warehouse` · `gradient` · `nightcity` · `snow` · `tunnel` · `rooftop` · `forest` · `backrooms` · `cyberpunk`
   - **Aspect** – `vertical` (9:16 TikTok) · `landscape` (16:9) · `square` (1:1)
   - **Camera** – `montage` (all 15 shots, auto-cut) or a single shot
   - **Quality** – Final (128) / Good (96) / Draft (48 samples)
4. Choose whether to render now. Output lands in `output\<car>\`:
   - `<car>.blend` – the built scene (open it to tweak/preview)
   - `frames\` – rendered PNG sequence (resumable)
   - `<car>_video.mp4` – the final video

## What it does automatically

| Step | Detail |
|------|--------|
| Extract | unzips the mod into `work\` |
| Convert | runs `kn5conv.exe` on the main `.kn5` → FBX (+ textures) |
| Import | binary-FBX import, **auto-rescales** to real-world metres |
| **Auto-fix** | the usual KN5→FBX breakage: disconnects crash-**damage normal maps**, hides crash-**damage meshes**, rebuilds fake-alpha **glass** as real transmissive glass, adds a glossy **clearcoat** to paint, tightens **carbon** roughness |
| Livery | repoints textures to the chosen **skin** |
| Scene | builds the chosen environment + lights (+ smoke where fitting) |
| Cameras | 15-shot vertical montage, **auto-fit to the car's bounding box** |
| Render | Cycles GPU, resumable PNG sequence |
| Encode | ffmpeg → H.264 MP4 |

## Requirements / paths

Edit the paths at the top of `Make-CarVideo.ps1` if yours differ:

- **Blender** `C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe`
  (this build has **no ffmpeg**, so we encode separately)
- **kn5conv.exe** – ships in this folder
- **ffmpeg** `C:\Program Files\File Converter\ffmpeg.exe`

## Advanced / headless

Skip the menus and drive `build_car.py` directly with a JSON config:

```bat
blender -b -P pipeline\build_car.py -- myconfig.json
```

`config.json` keys: `fbx_path, car_dir, skin, scene, aspect, camera, samples, out_blend, frames_dir`.
See `Make-CarVideo.ps1` for how it assembles one.

## Notes / limits

- Camera framing assumes the converter's orientation (car faces **−Y**, wing at **+Y**).
  If a future car imports facing the wrong way, the shots will be reversed — tell me and
  I'll add an auto-orient / flip flag.
- The auto-fix heuristics match Assetto Corsa naming (`*damage*`, `*glass*`, `*carbon*`,
  paint names in `paint_name_hints`). A car with unusual material names may need a tweak
  to those hints in `build_car.py`.
- Scene lighting is studio-scale; very large/small vehicles (trucks, karts) may want a
  lighting scale pass.
