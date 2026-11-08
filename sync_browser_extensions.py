import os
import shutil

src_root = "browser_extension"
dist_root = "StrictRoutines-Browser-Extensions"
browsers = ["Arc", "Brave", "Chrome", "Comet", "Edge", "Opera", "Vivaldi"]

check_files = [
    "global_video_engine.js",
    "yt_main_world.js",
    "content.js",
    "service_worker.js",
    "targets.json",
    "manifest.json"
]

for b in browsers:
    target_dir = os.path.join(dist_root, b, "StrictRoutines")
    if os.path.isdir(target_dir):
        for fname in check_files:
            src_file = os.path.join(src_root, fname)
            dst_file = os.path.join(target_dir, fname)
            if os.path.exists(src_file):
                shutil.copy2(src_file, dst_file)
                print(f"Copied {fname} -> {dst_file}")

print("Browser extensions synchronized across all 7 browsers successfully.")
