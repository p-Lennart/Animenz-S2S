import os
import glob
import yaml
import shutil
import time

from lib.ScoreProcessorPop909 import ScoreProcessorPop909
from lib.file_utils import force_delete_dir

# --- PATHS ---
POP909_RAW_DIR = "./../10_POP909_raw"
POP909_PROCESSED_DIR = "./../11_POP909_processed"


def main():
    # Ensure dirs, wipe processed dir ahead of new generation
    os.makedirs(POP909_RAW_DIR, exist_ok=True)
    if not force_delete_dir(POP909_PROCESSED_DIR, refresh=True):
        print(f"⛔ Existing directory {POP909_PROCESSED_DIR} not cleared")
        return

    # Find all 3-digit folders in the POP909 root
    song_folders = sorted(glob.glob(os.path.join(POP909_RAW_DIR, "[0-9][0-9][0-9]")))

    if not song_folders:
        print(f"🔎 No XML files found in {POP909_RAW_DIR}")
        return
    print(f"🔎 Found {len(song_folders)} song folders in {POP909_RAW_DIR}")

    for folder in song_folders:
        processor = ScoreProcessorPop909(
            input_dir=folder, output_root=POP909_PROCESSED_DIR
        )
        try:
            processor.process()
        except Exception as e:
            import traceback

            print(f"❌ Failed to process {folder}: {e}")
            traceback.print_exc()

    print(f"🏁 Processed all songs in {POP909_RAW_DIR}")


if __name__ == "__main__":
    main()
