import os
import json
import shutil
import time
from lib.TokenizationCore import sanitize_chord
from lib.ChordDictGeneration import process_chord_token
from lib.file_utils import force_delete_dir

# --- CONFIGURATION ---
DATA_DIRS = ["../04_augmented", "../11_POP909_processed"]
MASTER_VOCAB_DIR = "../20_master_chord_vocab"


def build_master_vocabulary():
    # Clear master folder
    if not force_delete_dir(MASTER_VOCAB_DIR, refresh=True):
        print(f"⛔ Existing directory {MASTER_VOCAB_DIR} not cleared")
        return

    print("--- Starting Master Vocabulary Consolidation ---")

    unique_chords = set()

    # 1. Scrape all datasets
    for directory in DATA_DIRS:
        if not os.path.exists(directory):
            print(f"[WARNING] Directory not found: {directory}")
            continue

        print(f"Scanning {directory}...")
        for root, _, files in os.walk(directory):
            for f in files:
                if f.endswith("_CHORDS.tsv"):
                    tsv_path = os.path.join(root, f)
                    with open(tsv_path, "r", encoding="utf-8") as tsv:
                        lines = tsv.readlines()[1:]  # Skip header
                        for line in lines:
                            parts = line.strip().split("\t")
                            if len(parts) >= 2:
                                raw_chord = parts[1]
                                # CRITICAL: Sanitize BEFORE adding to the master set
                                clean_chord = sanitize_chord(raw_chord)
                                unique_chords.add(clean_chord)

    # Convert to sorted list for deterministic JSON output
    vocab_list = sorted(list(unique_chords))
    print(f"\nDiscovered {len(vocab_list)} unique sanitized chords.")

    # 2. Dump the Master JSON
    master_json_out = os.path.join(MASTER_VOCAB_DIR, "master_vocab.json")
    with open(master_json_out, "w", encoding="utf-8") as f:
        json.dump(vocab_list, f, indent=4)
    print(f"Saved master vocabulary to {master_json_out}")

    # 3. Generate the Dictionary MIDI Files
    print(f"\nGenerating 1-bar dictionary MIDIs into {MASTER_VOCAB_DIR}...")

    success_count = 0
    fail_count = 0

    for chord_token in vocab_list:
        try:
            process_chord_token(chord_token, MASTER_VOCAB_DIR)
            success_count += 1
        except Exception as e:
            print(f"  [ERROR] Failed to generate dict entry for {chord_token}: {e}")
            fail_count += 1

    print(
        f"\nDictionary Generation Complete: {success_count} Success, {fail_count} Failed."
    )
    print("--- Master Vocab Generation Complete ---")


if __name__ == "__main__":
    build_master_vocabulary()
