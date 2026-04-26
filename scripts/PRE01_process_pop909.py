import os
import glob
import yaml
import shutil
from pathlib import Path
import time
import re

from lib.ScoreProcessorPop909 import ScoreProcessorPop909

# --- PATHS ---
POP909_RAW_DIR = Path("./../10_POP909_raw")
POP909_PROCESSED_DIR = Path("./../11_POP909_processed")


def force_delete_dir(target_dir: Path):
    """Helper to safely delete a directory, blocking if locked by Windows."""
    lock_notified = False
    while target_dir.exists():
        try:
            shutil.rmtree(target_dir)
            break
        except PermissionError:
            if not lock_notified:
                print(
                    f"⏳ [LOCKED] '{target_dir.name}' is open in your editor. Waiting for it to close",
                    end="",
                    flush=True,
                )
                lock_notified = True
            time.sleep(1.5)
            print(".", end="", flush=True)
        except FileNotFoundError:
            break  # Safe to break if something else deleted it

    if lock_notified:
        print()  # Print a new line once the dots stop


def main():
    # Ensure dirs, wipe processed dir ahead of new generation
    os.makedirs(POP909_RAW_DIR, exist_ok=True)
    force_delete_dir(POP909_PROCESSED_DIR)
    os.makedirs(POP909_PROCESSED_DIR, exist_ok=True)

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
