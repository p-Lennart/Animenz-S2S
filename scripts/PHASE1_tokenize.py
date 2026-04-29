import os
import json
import shutil
import time
from lib.TokenizationCore import PianoArrangerTokenizer
from lib.file_utils import force_delete_dir

POP909_IN_DIR = "../11_POP909_processed"
DICT_IN_DIR = "../20_master_chord_vocab"
PHASE1_OUT_DIR = "../30_phase1_tokens"
MASTER_VOCAB_PATH = "../20_master_chord_vocab/master_vocab.json"


def run_phase1_tokenization():
    if not force_delete_dir(PHASE1_OUT_DIR, refresh=True):
        print(f"⛔ Existing directory {PHASE1_OUT_DIR} not cleared")
        return

    print("--- Starting Phase 1 Tokenization ---")

    # 1. Load the canonical target vocabulary
    with open(MASTER_VOCAB_PATH, "r", encoding="utf-8") as f:
        chord_vocab = json.load(f)

    # 2. Initialize the Core object
    core = PianoArrangerTokenizer(positions_per_beat=8)

    # 3. Setup the tokenizer entirely from scratch using the vocab
    core.initialize_new(chord_vocab)

    # 4. Save the config so Phase 2 and the Decoder can use it
    config_path = os.path.join(PHASE1_OUT_DIR, "tokenizer_config.json")
    os.makedirs(PHASE1_OUT_DIR, exist_ok=True)
    core.save_config(config_path)

    # 5. Process the Datasets
    print("\nProcessing Chord Dictionary...")
    core.process_directory(DICT_IN_DIR, PHASE1_OUT_DIR)

    print("\nProcessing POP909 Dataset...")
    core.process_directory(POP909_IN_DIR, PHASE1_OUT_DIR)

    print("\n--- Phase 1 Tokenization Complete ---")


if __name__ == "__main__":
    run_phase1_tokenization()
