@echo off
setlocal
set "BLENDER=C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe"
set "BLEND=D:\Car video generator\Agera_RSR_studio.blend"
set "FFMPEG=C:\Program Files\File Converter\ffmpeg.exe"
set "FRAMES=D:\Car video generator\renders\frames"
set "OUT=D:\Car video generator\renders\Agera_RSR_reveal.mp4"
set "LOG=D:\Car video generator\renders\render_log.txt"

echo ============================================ >> "%LOG%"
echo [%date% %time%] START render (active scene full range @ 1080p / 128 spp, Cycles GPU, 60fps) >> "%LOG%"
echo.
echo Rendering the active scene's full frame range @ 1080p / 128 spp on the GPU (60fps montage = ~2175 frames, ~22 hrs).
echo Progress is appended to: %LOG%
"%BLENDER%" -b "%BLEND%" --python "D:\Car video generator\force_gpu.py" -a >> "%LOG%" 2>&1

echo [%date% %time%] Frames complete. Encoding MP4 with ffmpeg... >> "%LOG%"
echo.
echo Frames done. Encoding MP4...
"%FFMPEG%" -y -framerate 60 -start_number 1 -i "%FRAMES%\RSR_%%04d.png" -c:v libx264 -preset slow -crf 16 -pix_fmt yuv420p -movflags +faststart "%OUT%" >> "%LOG%" 2>&1

echo [%date% %time%] DONE -^> %OUT% >> "%LOG%"
echo.
echo ================================================================
echo   DONE.  Video: %OUT%
echo ================================================================
endlocal
