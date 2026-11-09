@echo off
setlocal
set "BLENDER=C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe"
set "BLEND=D:\Car video generator\Volvo_Cyberpunk_ONLY.blend"
set "FFMPEG=C:\Program Files\File Converter\ffmpeg.exe"
set "FRAMES=D:\Car video generator\renders\volvo_cyber"
set "OUT=D:\Car video generator\renders\Volvo_Cyberpunk_reveal.mp4"
set "LOG=D:\Car video generator\renders\volvo_cyber_log.txt"

if not exist "%FRAMES%" mkdir "%FRAMES%"

echo ============================================ >> "%LOG%"
echo [%date% %time%] START Volvo cyberpunk montage (2955 frames @ 1080x1920 / 128 spp / 60fps, Cycles GPU) >> "%LOG%"
echo Rendering the 20-shot cyberpunk montage. Resumable (skips frames already on disk). >> "%LOG%"

"%BLENDER%" -b "%BLEND%" --python "%~dp0render_volvo_cyber.py" >> "%LOG%" 2>&1

echo [%date% %time%] Frames complete. Encoding MP4 @ 60fps... >> "%LOG%"
"%FFMPEG%" -y -framerate 60 -start_number 1 -i "%FRAMES%\VC_%%04d.png" -c:v libx264 -preset slow -crf 16 -pix_fmt yuv420p -movflags +faststart "%OUT%" >> "%LOG%" 2>&1

echo [%date% %time%] DONE -^> %OUT% >> "%LOG%"
endlocal
