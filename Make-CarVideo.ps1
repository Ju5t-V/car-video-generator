# =====================================================================
#  Make-CarVideo.ps1   --  one-stop Assetto-Corsa-car -> cinematic video
#
#  Drop a car mod .zip in this folder (or pass its path) and run.
#  It will: extract -> convert KN5 to FBX -> let you pick
#  skin / scene / camera / aspect / quality -> build the Blender scene
#  -> (optionally) render + encode an MP4.
#
#  Run:   right-click MAKE_CAR_VIDEO.bat  (or)  powershell -File Make-CarVideo.ps1
# =====================================================================

param(
    [string]$Zip = ""     # optional: path to the car .zip
)

$ErrorActionPreference = "Stop"
$ROOT     = Split-Path -Parent $MyInvocation.MyCommand.Definition
$BLENDER  = "C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe"
$KN5CONV  = Join-Path $ROOT "kn5conv.exe"
$FFMPEG   = "C:\Program Files\File Converter\ffmpeg.exe"
$PIPELINE = Join-Path $ROOT "pipeline\build_car.py"
$FORCEGPU = Join-Path $ROOT "force_gpu.py"
$WORK     = Join-Path $ROOT "work"        # extraction area
$OUTROOT  = Join-Path $ROOT "output"      # per-car .blend / frames / mp4

function Fail($m){ Write-Host "`nERROR: $m" -ForegroundColor Red; Read-Host "Press Enter to exit"; exit 1 }
function Info($m){ Write-Host $m -ForegroundColor Gray }
function Head($m){ Write-Host "`n=== $m ===" -ForegroundColor Cyan }

function Pick($title, $items) {
    Head $title
    for ($i=0; $i -lt $items.Count; $i++) { Write-Host ("  [{0}] {1}" -f ($i+1), $items[$i]) }
    do { $s = Read-Host "Choose 1-$($items.Count)" } until ($s -match '^\d+$' -and [int]$s -ge 1 -and [int]$s -le $items.Count)
    return $items[[int]$s - 1]
}

# --- sanity ---------------------------------------------------------
if (-not (Test-Path $BLENDER)) { Fail "Blender not found at $BLENDER (edit the path at the top of this script)." }
if (-not (Test-Path $KN5CONV)) { Fail "kn5conv.exe not found next to this script." }
if (-not (Test-Path $PIPELINE)){ Fail "pipeline\build_car.py not found." }
$haveFF = Test-Path $FFMPEG

# --- 1. locate the car zip -----------------------------------------
if (-not $Zip) {
    $zips = @(Get-ChildItem -Path $ROOT -Filter *.zip -File | Sort-Object LastWriteTime -Descending)
    if ($zips.Count -eq 0) { Fail "No .zip found. Put the car mod .zip in $ROOT or pass -Zip <path>." }
    elseif ($zips.Count -eq 1) { $Zip = $zips[0].FullName; Info "Using $($zips[0].Name)" }
    else { $Zip = (Pick "Which car .zip?" ($zips | ForEach-Object { $_.Name }) ); $Zip = Join-Path $ROOT $Zip }
}
if (-not (Test-Path $Zip)) { Fail "Zip not found: $Zip" }
$carName = [IO.Path]::GetFileNameWithoutExtension($Zip)

# --- 2. extract -----------------------------------------------------
Head "Extracting"
$dest = Join-Path $WORK $carName
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Expand-Archive -Path $Zip -DestinationPath $dest -Force
Info "-> $dest"

# --- 3. find the car folder + main .kn5 -----------------------------
$kn5s = @(Get-ChildItem -Path $dest -Recurse -Filter *.kn5 -File |
          Where-Object { $_.Name -notmatch '(?i)lod|collider' } |
          Sort-Object Length -Descending)
if ($kn5s.Count -eq 0) { Fail "No usable .kn5 found in the zip." }
$mainKn5 = $kn5s[0]
$carDir  = $mainKn5.DirectoryName
Info "Car folder: $carDir"
Info "Main model: $($mainKn5.Name)"

# --- 4. convert KN5 -> FBX -----------------------------------------
Head "Converting KN5 -> FBX (this can take a minute)"
Push-Location $carDir
& $KN5CONV $mainKn5.FullName | Out-Null
Pop-Location
$fbx = Get-ChildItem -Path $carDir -Filter *.fbx -File | Sort-Object Length -Descending | Select-Object -First 1
if (-not $fbx) { Fail "Conversion produced no .fbx (check kn5conv output)." }
Info "-> $($fbx.Name)"

# --- 5. menus -------------------------------------------------------
$skinsDir = Join-Path $carDir "skins"
$skins = @("default (as-shipped)")
if (Test-Path $skinsDir) {
    $skins += @(Get-ChildItem -Path $skinsDir -Directory | ForEach-Object { $_.Name })
}
$skinPick = Pick "Choose a SKIN / livery" $skins
$skin = if ($skinPick -like "default*") { "default" } else { $skinPick }

$scene  = Pick "Choose a SCENE" @("dark", "white", "sunset", "neon", "warehouse", "gradient", "nightcity",
                                  "snow", "tunnel", "rooftop", "forest", "backrooms", "cyberpunk")
$aspect = Pick "Choose ASPECT" @("vertical", "landscape", "square")
$camPick= Pick "Choose CAMERA" @("montage (all 15 shots)","hero","tiltup","dutch","front","wheel",
                                 "orbit","side","intake","reartilt","wing","orbit2","exhaust","roof","headlight","finale")
$camera = if ($camPick -like "montage*") { "montage" } else { $camPick }
$qPick  = Pick "Choose QUALITY" @("Final (128 spp)","Good (96 spp)","Draft (48 spp)")
$samples= @{ "Final (128 spp)"=128; "Good (96 spp)"=96; "Draft (48 spp)"=48 }[$qPick]

# --- 6. write config + build ---------------------------------------
$outDir = Join-Path $OUTROOT $carName
New-Item -ItemType Directory -Force -Path (Join-Path $outDir "frames") | Out-Null
$blend  = Join-Path $outDir "$carName.blend"
$config = @{
    fbx_path   = $fbx.FullName.Replace("\","/")
    car_dir    = $carDir.Replace("\","/")
    skin       = $skin
    scene      = $scene
    aspect     = $aspect
    camera     = $camera
    samples    = $samples
    out_blend  = $blend.Replace("\","/")
    frames_dir = (Join-Path $outDir "frames").Replace("\","/")
} | ConvertTo-Json
$cfgPath = Join-Path $outDir "config.json"
$config | Set-Content -Path $cfgPath -Encoding UTF8

Head "Building Blender scene"
& $BLENDER -b -P $PIPELINE -- $cfgPath
if (-not (Test-Path $blend)) { Fail "Build failed - no .blend produced (see Blender output above)." }
Info "-> $blend"

# --- 7. render? -----------------------------------------------------
Head "Render now?"
Write-Host "  [1] Yes - render + encode MP4 now (long: minutes to hours)"
Write-Host "  [2] No  - just open/keep the .blend (render later)"
$go = Read-Host "Choose 1-2"
if ($go -ne "1") {
    Write-Host "`nDone. Scene saved: $blend" -ForegroundColor Green
    Write-Host "Open it in Blender, or re-run and choose Yes to render."
    Read-Host "Press Enter to exit"; exit 0
}

Head "Rendering frames (Cycles GPU)"
& $BLENDER -b $blend -P $FORCEGPU -a
$frames = @(Get-ChildItem -Path (Join-Path $outDir "frames") -Filter "RSR_*.png")
if ($frames.Count -eq 0) { Fail "No frames rendered." }
Info "$($frames.Count) frames rendered."

if ($haveFF) {
    Head "Encoding MP4"
    $mp4 = Join-Path $outDir "$carName`_video.mp4"
    & $FFMPEG -y -framerate 24 -start_number 1 -i (Join-Path $outDir "frames\RSR_%04d.png") `
        -c:v libx264 -preset slow -crf 16 -pix_fmt yuv420p -movflags +faststart $mp4
    Write-Host "`nDONE.  Video: $mp4" -ForegroundColor Green
} else {
    Write-Host "`nFrames done, but ffmpeg not found - encode them manually." -ForegroundColor Yellow
}
Read-Host "Press Enter to exit"
