import os
import re
import json
import symusic
from collections import defaultdict
from pathlib import Path
from miditok import REMI, TokenizerConfig

# --- CONFIGURATION ---
FINAL_DIR = "./../final"
OUTPUT_DIR = "./../tokenized_dataset"
POSITIONS_PER_BEAT = 8  # Granularity of the REMI grid (8 positions per quarter note)


def sanitize_chord(chord_str):
    """
    Cleans music21 chord strings into categorical tokens.
    Preserves quality numbers (e.g., C7, D-9) and maps music21 flats ('-') to 'b'.

    Examples:
    'E-m7/G-' -> '[CHORD_Ebm7/Gb]'
    'A-power' -> '[CHORD_Abpower]'
    'F7'      -> '[CHORD_F7]'
    """
    if "Cannot Be Identified" in chord_str:
        return "[CHORD_NC]"

    # Remove whitespace
    c = chord_str.replace(" ", "")

    # Convert music21's flat symbol '-' to 'b'
    # (e.g., E-m7 -> Ebm7)
    c = c.replace("-", "b")

    return f"[CHORD_{c}]"


def load_and_map_chords(tsv_path, ticks_per_quarter):
    """
    Reads TSV and uses Field 3 (Absolute Quarter Offset) to calculate the exact
    MIDI tick. Returns a dictionary mapping ticks to their respective chords.
    """
    chords_by_tick = defaultdict(list)
    with open(tsv_path, "r", encoding="utf-8") as f:
        lines = f.readlines()[1:]  # Skip header
        for line in lines:
            parts = line.strip().split("\t")
            if len(parts) >= 3:
                chord_name = sanitize_chord(parts[1])

                # FIELD 3: Absolute Quarter Offset (e.g. 370.0, 373.5)
                offset_q = float(parts[2])

                # Convert exact offset to exact MIDI tick
                tick = int(offset_q * ticks_per_quarter)
                chords_by_tick[tick].append(chord_name)

    return chords_by_tick


def inject_chords_into_remi(remi_tokens, chords_by_tick, ticks_per_quarter):
    """
    Chronologically injects chords. If the melody skips a beat where a chord
    occurs (due to a rest or tied note), it synthesizes the missing Position token.
    """
    injected_sequence = []
    current_bar_tick = 0
    ticks_per_pos = ticks_per_quarter / POSITIONS_PER_BEAT

    # Defaults
    beats_per_bar = 4.0
    ticks_per_bar = int(beats_per_bar * ticks_per_quarter)

    pending_ticks = sorted(chords_by_tick.keys())

    for token in remi_tokens:

        # 1. Update Grid Dimensions
        if token.startswith("TimeSig_"):
            try:
                num_str, den_str = token.split("_")[1].split("/")
                beats_per_bar = int(num_str) * (4.0 / int(den_str))
                ticks_per_bar = int(beats_per_bar * ticks_per_quarter)
            except Exception:
                pass
            injected_sequence.append(token)

        # 2. Advance Timeline by One Full Bar
        elif token.startswith("Bar_"):
            has_seen_bar = any(t.startswith("Bar_") for t in injected_sequence)

            if has_seen_bar:
                next_bar_tick = current_bar_tick + ticks_per_bar

                # Flush any chords that happened during a rest at the end of the measure
                # (or during a completely empty measure)
                while pending_ticks and pending_ticks[0] < next_bar_tick:
                    t = pending_ticks.pop(0)
                    chord_pos_idx = int(round((t - current_bar_tick) / ticks_per_pos))
                    injected_sequence.append(f"Position_{chord_pos_idx}")
                    injected_sequence.extend(chords_by_tick[t])

                current_bar_tick = next_bar_tick

            injected_sequence.append(token)

        # 3. Calculate Absolute Tick & Inject Chords
        elif token.startswith("Position_"):
            pos_idx = int(token.split("_")[1])
            # Use round() to prevent floating point drift on tuplets
            current_tick = current_bar_tick + int(round(pos_idx * ticks_per_pos))

            # A. Flush chords that were SKIPPED by the melody
            while pending_ticks and pending_ticks[0] < current_tick:
                t = pending_ticks.pop(0)
                chord_pos_idx = int(round((t - current_bar_tick) / ticks_per_pos))
                injected_sequence.append(f"Position_{chord_pos_idx}")
                injected_sequence.extend(chords_by_tick[t])

            # B. Append the actual melody position token
            injected_sequence.append(token)

            # C. Flush chords that happen EXACTLY on this melody note
            # (Piggybacking on the existing Position token we just placed)
            while pending_ticks and pending_ticks[0] == current_tick:
                t = pending_ticks.pop(0)
                injected_sequence.extend(chords_by_tick[t])

        # 4. Standard Note Data
        else:
            injected_sequence.append(token)

    # 5. Catch any lingering trailing chords in the final measure
    while pending_ticks:
        t = pending_ticks.pop(0)
        chord_pos_idx = int(round((t - current_bar_tick) / ticks_per_pos))
        injected_sequence.append(f"Position_{chord_pos_idx}")
        injected_sequence.extend(chords_by_tick[t])

    return injected_sequence


def discover_files(base_dir):
    """Yields tuples of (melody_mid, literal_mid, chords_tsv, score_name)"""
    for root, _, files in os.walk(base_dir):
        melody = literal = chords = None
        for f in files:
            if f.endswith("_MELODY.mid"):
                melody = os.path.join(root, f)
            elif f.endswith("_LITERAL.mid"):
                literal = os.path.join(root, f)
            elif f.endswith("_CHORDS.tsv"):
                chords = os.path.join(root, f)

        if melody and literal and chords:
            score_name = Path(melody).name.replace("_MELODY.mid", "")
            yield melody, literal, chords, score_name


def build_chord_vocabulary(base_dir):
    """Pass 1: Gather all unique sanitized chords to inject as special tokens."""
    print("Pass 1: Building Chord Vocabulary...")
    unique_chords = set()

    for _, _, chords_tsv, _ in discover_files(base_dir):
        print("[DEBUG] Discovered file", chords_tsv)
        with open(chords_tsv, "r", encoding="utf-8") as f:
            lines = f.readlines()[1:]  # Skip header
            for line in lines:
                parts = line.strip().split("\t")
                if len(parts) >= 3:
                    unique_chords.add(sanitize_chord(parts[1]))

    print(f"Discovered {len(unique_chords)} unique chords.")
    return list(unique_chords)


def process_dataset():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Pass 1: Setup Vocab & Tokenizer
    chord_vocab = build_chord_vocabulary(FINAL_DIR)

    # Lock beat_res to a flat layout so our math (ticks_per_pos) is predictable
    # Assuming POSITIONS_PER_BEAT is defined earlier (e.g., 8, 12, or 24 depending on tuplets)
    config = TokenizerConfig(
        # --- Rhythm & Time ---
        beat_res={(0, 4): POSITIONS_PER_BEAT, (4, 12): 8},
        use_time_signatures=True,  # Still required so REMI knows when to trigger Bar_None
        use_tempos=False,  # Explicitly ignoring tempo changes
        # --- Tonality ---
        use_key_signatures=True,  # Contextualizes the scale
        # --- Pitch & Dynamics ---
        pitch_range=(21, 108),  # Strict 88-key piano range (A0 to C8)
        num_velocities=1,  #
        # --- Structure & ML ---
        use_programs=False,  # Ignore track instruments (everything is piano)
        use_chords=False,  # We are doing this manually
        special_tokens=chord_vocab,
    )
    tokenizer = REMI(config)

    # Save the exact tokenizer state (vocab, beat_res, flags) for the decoder
    config_out_path = os.path.join(OUTPUT_DIR, "tokenizer_config.json")
    tokenizer.save_params(config_out_path)
    print(f"Saved tokenizer state to {config_out_path}")

    print("\nPass 2: Tokenizing and Injecting...")

    for melody_path, literal_path, chords_path, score_name in discover_files(FINAL_DIR):
        try:
            # Load Data
            sym_melody = symusic.Score(melody_path)
            sym_literal = symusic.Score(literal_path)

            # --- STRICT FORMAT ASSERTIONS ---
            # Fail early if the data doesn't perfectly match our 1-track expectation
            assert (
                len(sym_melody.tracks) == 1
            ), f"Format Error: {melody_path} has {len(sym_melody.tracks)} tracks (expected 1)."
            assert (
                len(sym_literal.tracks) == 1
            ), f"Format Error: {literal_path} has {len(sym_literal.tracks)} tracks (expected 1)."

            ticks_per_quarter = sym_melody.ticks_per_quarter
            chords_data = load_and_map_chords(chords_path, ticks_per_quarter)

            # Tokenize Raw (Returns list of TokSequences)
            melody_tok_seqs = tokenizer(sym_melody)
            literal_tok_seqs = tokenizer(sym_literal)

            # Grab the strings directly for the melody track
            raw_melody_strings = melody_tok_seqs[0].tokens

            # Grab the final IDs directly for the literal track
            literal_ids = literal_tok_seqs[0].ids

            # Inject Custom Chords into the string array
            injected_melody_strings = inject_chords_into_remi(
                raw_melody_strings, chords_data, ticks_per_quarter
            )

            # Convert Injected Strings back to IDs
            # (tokenizer.vocab is a dict of key=string -> value=id)
            melody_ids = []
            for tok in injected_melody_strings:
                if tok in tokenizer.vocab:
                    melody_ids.append(tokenizer.vocab[tok])
                else:
                    print(f"  [WARNING] Dropping unknown token: {tok}")

            # Save Output
            out_melody = os.path.join(OUTPUT_DIR, f"{score_name}_MELODY_CHORDS.json")
            out_literal = os.path.join(OUTPUT_DIR, f"{score_name}_LITERAL.json")

            with open(out_melody, "w") as f:
                json.dump({"ids": melody_ids, "tokens": injected_melody_strings}, f)

            with open(out_literal, "w") as f:
                # To save the literal tokens string array for debugging, we can just grab it from the TokSequence
                json.dump({"ids": literal_ids, "tokens": literal_tok_seqs[0].tokens}, f)

            print(f"Processed: {score_name}")

        except AssertionError as ae:
            print(f"SKIPPING {score_name}: {ae}")
        except Exception as e:
            import traceback

            traceback.print_exc()
            print(f"FAILED to process {score_name}: {e}")


if __name__ == "__main__":
    process_dataset()
