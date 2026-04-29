import os
import shutil
import time
from pathlib import Path
from typing import Union

def force_delete_dir(target_dir: Union[str, Path], prompt: bool = True, refresh: bool = False) -> bool:
    """Helper to safely delete a directory, blocking if locked by Windows."""
    target_dir = Path(target_dir)

    if prompt:
        # Check if directory exists before prompting
        if not target_dir.exists():
            print(f"Directory '{target_dir}' does not exist.")
            if refresh:
                target_dir.mkdir(parents=True, exist_ok=True)
            return True

        # Prompt for confirmation
        confirm = (
            input(
                f"⚠️  Are you sure you want to permanently delete '{target_dir}'? (y/n): "
            )
            .strip()
            .lower()
        )
        if confirm not in ("y", "yes"):
            print("Deletion cancelled.")
            return False

    lock_notified = False
    while target_dir.exists():
        try:
            shutil.rmtree(target_dir)
            if prompt:
                print(f"✅ Deleted '{target_dir.name}'.")
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
            if prompt:
                if refresh:
                    target_dir.mkdir(parents=True, exist_ok=True)
                return True
            break  # Safe to break if something else somehow deleted it

    if lock_notified:
        print()  # Print a new line once the dots stop

    if refresh:
        target_dir.mkdir(parents=True, exist_ok=True)

    return True
