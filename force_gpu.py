import bpy
# Force Cycles onto the AMD GPU (HIP) for headless rendering.
p = bpy.context.preferences.addons['cycles'].preferences
p.compute_device_type = 'HIP'
try:
    p.refresh_devices()
except Exception:
    pass
for d in p.devices:
    d.use = (d.type == 'HIP')
bpy.context.scene.cycles.device = 'GPU'
print("GPU forced ->", [d.name for d in p.devices if d.use])
