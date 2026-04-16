import os
import json
from pathlib import Path
from miditok import REMI, TokSequence

# --- CONFIGURATION ---
TOKEN_DIR = "./../tokenized_dataset"
OUTPUT_DIR = "./../decoded_midi"


def decode_literal_dataset():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 1. Initialize tokenizer directly from the saved state!
    config_path = os.path.join(TOKEN_DIR, "tokenizer_config.json")
    if not os.path.exists(config_path):
        print(f"CRITICAL: Could not find {config_path}. Did you run the encoder?")
        return

    tokenizer = REMI(params=config_path)
    print(f"Loaded tokenizer state with vocab size: {len(tokenizer.vocab)}")
    print("Decoding Literal JSON files to MIDI...")

    for root, _, files in os.walk(TOKEN_DIR):
        for f in files:
            if f.endswith("_LITERAL.json"):
                json_path = os.path.join(root, f)
                score_name = f.replace("_LITERAL.json", "")

                try:
                    # 2. Load the IDs
                    with open(json_path, "r", encoding="utf-8") as jf:
                        data = json.load(jf)
                        ids = data.get("ids", [])

                    if not ids:
                        print(f"  [WARNING] No IDs found in {f}, skipping.")
                        continue

                    # 3. Decode directly back to a symusic.Score object
                    # Explicitly wrap the IDs to lock in the 1D dimensionality
                    seq = TokSequence(ids=ids)

                    # Pass it as a list (since a MIDI file is a list of tracks)
                    decoded_score = tokenizer.decode([seq])

                    # 4. Create specific output subdirectory
                    song_out_dir = os.path.join(OUTPUT_DIR, score_name)
                    os.makedirs(song_out_dir, exist_ok=True)

                    # 5. Save the MIDI
                    out_midi_path = os.path.join(
                        song_out_dir, f"{score_name}_LITERAL.mid"
                    )
                    decoded_score.dump_midi(out_midi_path)

                    print(f"Successfully decoded: {score_name}")

                except Exception as e:
                    import traceback

                    traceback.print_exc()
                    print(f"FAILED to process {score_name}: {e}")


if __name__ == "__main__":
    decode_literal_dataset()
