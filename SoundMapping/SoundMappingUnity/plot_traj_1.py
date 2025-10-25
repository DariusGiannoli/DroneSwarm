# Re-run the plotting with the most recent JSON, same as above.
import json, math, glob, os
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt

REF_SCALE = 0.3
REF_STEPS = [
    (0, 140), (-140, 0), (0, 100), (100, 0),
    (0, 160), (-100, 0), (0, 100), (-140, 0),
    (0, -160), (-200, 0), (0, 100), (100, 0),
]
OUT_TRAJ_PNG = "outputs/one_script_trajectories.png"
OUT_ERR_PNG  = "outputs/one_script_centroid_error.png"

candidates = sorted(glob.glob("/Users/chenyang/ToGoogleDrive/Gitchen/UnityMIT/SoundMapping/SoundMappingUnity/Assets/Data/default/Trajectories_1022/Scene_Selector_H_NO_20251025_164237_traj.json"), key=os.path.getmtime, reverse=True)
if not candidates:
    raise FileNotFoundError("No JSON files found in /mnt/data. Please upload the recorded JSON.")
INPUT_JSON = Path(candidates[0])

with INPUT_JSON.open("r") as f:
    data = json.load(f)

scene = data.get("scene", data.get("level", "Unknown Scene"))

drone_tracks = {}
if "trajectories" in data:
    for i, traj in enumerate(data["trajectories"]):
        name = traj.get("name", f"id:{traj.get('id', i)}")
        frames = traj.get("frames", [])
        if not frames: continue
        t_arr = [fr.get("t", None) for fr in frames]
        x_arr = [fr.get("x", 0.0) for fr in frames]
        z_arr = [fr.get("z", 0.0) for fr in frames]
        g_arr = [fr.get("g", None) for fr in frames]
        drone_tracks[name] = {
            "t": np.array(t_arr, dtype=float) if (t_arr and t_arr[0] is not None) else None,
            "x": np.array(x_arr, dtype=float),
            "z": np.array(z_arr, dtype=float),
            "g": np.array(g_arr, dtype=float) if any(v is not None for v in g_arr) else None,
        }
elif "swarmState" in data:
    top_time = data.get("time", None)
    top_time = np.array(top_time, dtype=float) if isinstance(top_time, list) else None
    for entry in data["swarmState"]:
        name = str(entry.get("droneId", f"d{len(drone_tracks)}"))
        pos = entry.get("droneState", {}).get("position", [])
        if not pos: continue
        x_arr = [p.get("x", 0.0) for p in pos]
        z_arr = [p.get("z", 0.0) for p in pos]
        g_arr = [p.get("g", None) for p in pos]
        t_here = top_time if (top_time is not None and len(top_time) == len(x_arr)) else None
        drone_tracks[name] = {
            "t": t_here,
            "x": np.array(x_arr, dtype=float),
            "z": np.array(z_arr, dtype=float),
            "g": np.array(g_arr, dtype=float) if any(v is not None for v in g_arr) else None,
        }
else:
    raise ValueError("Unrecognized JSON layout (expected 'trajectories' or 'swarmState').")

if not drone_tracks:
    raise ValueError("No drone trajectories found.")

pts = [(0.0, 0.0)]
x, z = 0.0, 0.0
for dx, dz in REF_STEPS:
    x += dx; z += dz
    pts.append((x, z))
ref_poly = np.array(pts, dtype=float) * REF_SCALE

def closest_point_on_segment(p, a, b):
    ap = p - a
    ab = b - a
    ab2 = float(ab[0]*ab[0] + ab[1]*ab[1])
    if ab2 == 0.0:
        return a, float((p[0]-a[0])**2 + (p[1]-a[1])**2)
    t = (ap[0]*ab[0] + ap[1]*ab[1]) / ab2
    t = max(0.0, min(1.0, t))
    q = a + t*ab
    d2 = float((p[0]-q[0])**2 + (p[1]-q[1])**2)
    return q, d2

def dist_point_to_polyline(p, poly):
    best_d2 = float("inf")
    for i in range(len(poly)-1):
        _, d2 = closest_point_on_segment(p, poly[i], poly[i+1])
        if d2 < best_d2:
            best_d2 = d2
    return math.sqrt(best_d2)

use_time = any(drone_tracks[name]["t"] is not None for name in drone_tracks)

if use_time:
    bins = {}
    for name, d in drone_tracks.items():
        t = d["t"]; xarr = d["x"]; zarr = d["z"]; g = d.get("g", None)
        if t is None: continue
        for idx, (ti, xi, zi) in enumerate(zip(t, xarr, zarr)):
            if g is not None and not (g[idx] == 1):
                continue
            key = round(float(ti), 3)
            bins.setdefault(key, []).append((xi, zi))
    if not bins:
        for name, d in drone_tracks.items():
            t = d["t"]; xarr = d["x"]; zarr = d["z"]
            if t is None: continue
            for ti, xi, zi in zip(t, xarr, zarr):
                key = round(float(ti), 3)
                bins.setdefault(key, []).append((xi, zi))
    times = np.array(sorted(bins.keys()), dtype=float)
    centroid_x = np.array([np.mean([p[0] for p in bins[t]]) for t in times], dtype=float)
    centroid_z = np.array([np.mean([p[1] for p in bins[t]]) for t in times], dtype=float)
else:
    min_len = min(len(drone_tracks[name]["x"]) for name in drone_tracks)
    times = np.arange(min_len, dtype=float)
    xs, zs = [], []
    for f in range(min_len):
        pts = []
        for name, d in drone_tracks.items():
            xval = d["x"][f]; zval = d["z"][f]
            g = d.get("g", None)
            if g is not None and not (g[f] == 1):
                continue
            pts.append((xval, zval))
        if not pts:
            for name, d in drone_tracks.items():
                pts.append((d["x"][f], d["z"][f]))
        xs.append(np.mean([p[0] for p in pts]))
        zs.append(np.mean([p[1] for p in pts]))
    centroid_x = np.array(xs, dtype=float)
    centroid_z = np.array(zs, dtype=float)

centroid = np.column_stack([centroid_x, centroid_z])
centroid_err = np.array([dist_point_to_polyline(p, ref_poly) for p in centroid], dtype=float)

plt.figure(figsize=(8, 8))
for name, d in drone_tracks.items():
    plt.plot(d["x"], d["z"], alpha=0.25)
plt.plot(centroid_x, centroid_z, linewidth=3, label="Swarm centroid (main group)")
plt.scatter([centroid_x[0]],[centroid_z[0]], s=50, marker="o")
plt.scatter([centroid_x[-1]],[centroid_z[-1]], s=50, marker="x")
plt.plot(ref_poly[:,0], ref_poly[:,1], linewidth=3, linestyle="--", label=f"Reference (×{REF_SCALE})")
plt.scatter(ref_poly[:,0], ref_poly[:,1], s=20)
plt.gca().set_aspect("equal", adjustable="box")
plt.xlabel("X (m)"); plt.ylabel("Z (m)")
plt.title(f"Trajectories & Centroid vs Reference — {scene}\nFile: {INPUT_JSON.name}")
plt.grid(True, alpha=0.3); plt.legend(loc="best")
plt.tight_layout(); plt.savefig(OUT_TRAJ_PNG, dpi=150); plt.show()

plt.figure(figsize=(9, 5))
plt.plot(times, centroid_err)
plt.xlabel("Time (s)" if use_time else "Frame index")
plt.ylabel("Centroid cross-track error (m)")
plt.title("Centroid cross-track error vs time (main group only)")
plt.grid(True, alpha=0.3)
plt.tight_layout(); plt.savefig(OUT_ERR_PNG, dpi=150); plt.show()

OUT_TRAJ_PNG, OUT_ERR_PNG, str(INPUT_JSON)
