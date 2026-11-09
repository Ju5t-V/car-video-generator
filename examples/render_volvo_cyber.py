import bpy

sc = bpy.data.scenes["13_Cyberpunk"]
try:
    p = bpy.context.preferences.addons['cycles'].preferences
    p.compute_device_type = 'HIP'
    for d in p.devices:
        d.use = (d.type != 'CPU')
    print("GPU devices:", [(d.name, d.use) for d in p.devices])
except Exception as e:
    print("gpu pref warn:", e)

sc.render.engine = 'CYCLES'
sc.cycles.device = 'GPU'
sc.cycles.samples = 128
sc.cycles.use_denoising = True
sc.cycles.use_adaptive_sampling = True
sc.cycles.adaptive_threshold = 0.01

r = sc.render
r.resolution_x, r.resolution_y = 1080, 1920
r.resolution_percentage = 100
r.fps = 60
r.image_settings.file_format = 'PNG'
r.image_settings.color_mode = 'RGB'
r.filepath = r"D:/Car video generator/renders/volvo_cyber/VC_"
r.use_overwrite = False       # resumable: skip frames already on disk
r.use_placeholder = True      # write a 0-byte placeholder so parallel/resumed runs skip in-progress frames

sc.frame_start = 1
sc.frame_end = 2955

print("VOLVO_CYBER_RENDER_START frames", sc.frame_start, "-", sc.frame_end,
      "samples", sc.cycles.samples, "res", r.resolution_x, "x", r.resolution_y)
with bpy.context.temp_override(scene=sc):
    bpy.ops.render.render(animation=True)
print("VOLVO_CYBER_RENDER_DONE")
