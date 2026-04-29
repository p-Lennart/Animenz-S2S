import os
from lib.TokenizationCore import PianoArrangerTokenizer
from lib.file_utils import force_delete_dir

TARGET_IN_DIR = "../30_phase1_tokens"
TARGET_OUT_DIR = "../41_phase2_decoded"
CONFIG_PATH = "../30_phase1_tokens/tokenizer_config.json"


def run_phase2_decode():
    if not force_delete_dir(TARGET_OUT_DIR, refresh=True):
        print(f"⛔ Existing directory {TARGET_OUT_DIR} not cleared")
        return

    print("--- Starting Phase 2 Decoding ---")

    if not os.path.exists(CONFIG_PATH):
        print(f"CRITICAL ERROR: Could not find {CONFIG_PATH}.")
        return

    # 1. Initialize the Core object
    core = PianoArrangerTokenizer()

    # 2. Inherit the EXACT state from Phase 1
    core.load_existing(CONFIG_PATH)

    # 3. Process the Tokenized target dataset
    print(f"\nDecoding Tokens from {TARGET_IN_DIR}...")
    core.decode_directory(TARGET_IN_DIR, TARGET_OUT_DIR)

    print("\n--- Phase 2 Decoding Complete ---")


if __name__ == "__main__":
    run_phase2_decode()
