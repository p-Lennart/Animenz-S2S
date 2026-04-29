import os
import json
import symusic
from miditok import REMI, TokenizerConfig
from collections import defaultdict


def sanitize_chord(chord_str):
    """
    Cleans music21 chord strings into categorical tokens,
    and enforces strict enharmonic normalization.
    """
    if "Cannot Be Identified" in chord_str:
        return "[CHORD_NC]"

    # Remove whitespace and normalize music21's flat symbol '-' to 'b'
    c = chord_str.replace(" ", "").replace("-", "b")

    # Dictionary to force everything to a single canonical spelling
    enharmonics = {
        "C#": "Db",
        "D#": "Eb",
        "E#": "F",
        "F#": "Gb",
        "G#": "Ab",
        "A#": "Bb",
        "B#": "C",
        "Cb": "B",
        "Fb": "E",  # Catch edge-case extreme flats too
    }

    def normalize_pitch(pitch_str):
        # We only check the beginning of the string to avoid altering qualities
        for sharp, flat in enharmonics.items():
            if pitch_str.startswith(sharp):
                return flat + pitch_str[len(sharp) :]
        return pitch_str

    # Process root and bass independently if it's a slash chord
    if "/" in c:
        root, bass = c.split("/")
        c = f"{normalize_pitch(root)}/{normalize_pitch(bass)}"
    else:
        c = normalize_pitch(c)

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


def load_dictionary_entry(tsv_path):
    """
    Bypasses sanitization and offset math.
    Injects the exact master token string at the very first tick.
    """
    chords_by_tick = defaultdict(list)
    with open(tsv_path, "r", encoding="utf-8") as f:
        # Assuming line 0 is header, line 1 is the token: "[CHORD_A7/Db]"
        lines = f.readlines()
        if len(lines) > 1:
            exact_token = lines[1].strip()
            chords_by_tick[0].append(exact_token)
    return chords_by_tick


class PianoArrangerTokenizer:
    def __init__(self, positions_per_beat=8):
        self.positions_per_beat = positions_per_beat
        self.tokenizer = None

    def initialize_new(self, chord_vocab):
        """Used by Phase 1 to create the canonical tokenizer."""
        config = TokenizerConfig(
            beat_res={(0, 4): self.positions_per_beat, (4, 12): 8},
            use_time_signatures=True,
            use_tempos=False,
            use_key_signatures=True,
            pitch_range=(21, 108),
            num_velocities=1,
            use_programs=False,
            use_chords=False,
            special_tokens=chord_vocab,
        )
        self.tokenizer = REMI(config)
        print(
            f"[Core] Initialized NEW Tokenizer. Vocab size: {len(self.tokenizer.vocab)}"
        )

    def load_existing(self, config_path):
        """Used by Phase 2 to perfectly inherit Phase 1's vocabulary."""
        self.tokenizer = REMI(params=config_path)

        # Dynamically recover POSITIONS_PER_BEAT from the loaded config
        base_beat_range = next(
            key for key in self.tokenizer.config.beat_res.keys() if key[0] == 0
        )
        self.positions_per_beat = self.tokenizer.config.beat_res[base_beat_range]
        print(
            f"[Core] Loaded EXISTING Tokenizer from {config_path}. Vocab size: {len(self.tokenizer.vocab)}"
        )

    def save_config(self, out_path):
        if self.tokenizer:
            self.tokenizer.save_params(out_path)
            print(f"[Core] Saved tokenizer config to {out_path}")

    def _inject_chords_into_remi(self, remi_tokens, chords_by_tick, ticks_per_quarter):
        """The core chronological lookahead injection logic."""
        injected_sequence = []
        current_bar_tick = 0
        ticks_per_pos = ticks_per_quarter / self.positions_per_beat
        beats_per_bar = 4.0
        ticks_per_bar = int(beats_per_bar * ticks_per_quarter)
        pending_ticks = sorted(chords_by_tick.keys())

        for token in remi_tokens:
            if token.startswith("TimeSig_"):
                try:
                    num_str, den_str = token.split("_")[1].split("/")
                    beats_per_bar = int(num_str) * (4.0 / int(den_str))
                    ticks_per_bar = int(beats_per_bar * ticks_per_quarter)
                except Exception:
                    pass
                injected_sequence.append(token)

            elif token.startswith("Bar_"):
                if any(t.startswith("Bar_") for t in injected_sequence):
                    next_bar_tick = current_bar_tick + ticks_per_bar
                    while pending_ticks and pending_ticks[0] < next_bar_tick:
                        t = pending_ticks.pop(0)
                        chord_pos_idx = int(
                            round((t - current_bar_tick) / ticks_per_pos)
                        )
                        injected_sequence.append(f"Position_{chord_pos_idx}")
                        injected_sequence.extend(chords_by_tick[t])
                    current_bar_tick = next_bar_tick
                injected_sequence.append(token)

            elif token.startswith("Position_"):
                pos_idx = int(token.split("_")[1])
                current_tick = current_bar_tick + int(round(pos_idx * ticks_per_pos))

                while pending_ticks and pending_ticks[0] < current_tick:
                    t = pending_ticks.pop(0)
                    chord_pos_idx = int(round((t - current_bar_tick) / ticks_per_pos))
                    injected_sequence.append(f"Position_{chord_pos_idx}")
                    injected_sequence.extend(chords_by_tick[t])

                injected_sequence.append(token)

                while pending_ticks and pending_ticks[0] == current_tick:
                    t = pending_ticks.pop(0)
                    injected_sequence.extend(chords_by_tick[t])

            else:
                injected_sequence.append(token)

        # 5. Catch any lingering trailing chords in the final measure (The Outro Fix)
        while pending_ticks:
            t = pending_ticks.pop(0)

            # If the chord happens in a future measure (because the melody track ended),
            # synthesize the missing Bar tokens to keep the timeline advancing!
            while t >= current_bar_tick + ticks_per_bar:
                injected_sequence.append("Bar_None")
                current_bar_tick += ticks_per_bar

            chord_pos_idx = int(round((t - current_bar_tick) / ticks_per_pos))

            # Failsafe clamp to prevent floating-point precision from pushing it out of bounds
            positions_per_bar = int(round(ticks_per_bar / ticks_per_pos))
            if chord_pos_idx >= positions_per_bar:
                chord_pos_idx = positions_per_bar - 1

            injected_sequence.append(f"Position_{chord_pos_idx}")
            injected_sequence.extend(chords_by_tick[t])

        return injected_sequence

    def process_directory(self, input_dir, output_dir):
        """Scans a directory for matching file triplets and tokenizes them."""
        os.makedirs(output_dir, exist_ok=True)
        processed_count = 0

        for root, _, files in os.walk(input_dir):
            melody = literal = chords = None
            is_dict_entry = False  # Track which TSV type we found

            for f in files:
                if f.endswith("_MELODY.mid"):
                    melody = os.path.join(root, f)
                elif f.endswith("_LITERAL.mid"):
                    literal = os.path.join(root, f)
                elif f.endswith("_CHORDS.tsv"):
                    chords = os.path.join(root, f)
                elif f.endswith("_CHORD_ENTRY.tsv"):
                    chords = os.path.join(root, f)
                    is_dict_entry = True

            if melody and literal and chords:
                # Strip the correct suffix for the score name
                suffix_to_remove = "_MELODY.mid"
                score_name = os.path.basename(melody).replace(suffix_to_remove, "")

                try:
                    sym_melody = symusic.Score(melody)
                    sym_literal = symusic.Score(literal)

                    assert (
                        len(sym_melody.tracks) <= 1
                    ), f"Expected <1 melody track, got {len(sym_melody.tracks)}"
                    assert (
                        len(sym_literal.tracks) == 1
                    ), f"Expected 1 literal track, got {len(sym_literal.tracks)}"

                    ticks_per_quarter = sym_melody.ticks_per_quarter
                    # --- THE ROUTER ---
                    if is_dict_entry:
                        chords_data = load_dictionary_entry(chords)
                    else:
                        chords_data = load_and_map_chords(chords, ticks_per_quarter)

                    melody_tok_seqs = self.tokenizer(sym_melody)
                    literal_tok_seqs = self.tokenizer(sym_literal)

                    raw_melody_strings = melody_tok_seqs[0].tokens
                    literal_ids = literal_tok_seqs[0].ids

                    injected_melody_strings = self._inject_chords_into_remi(
                        raw_melody_strings, chords_data, ticks_per_quarter
                    )

                    melody_ids = []
                    for tok in injected_melody_strings:
                        if tok in self.tokenizer.vocab:
                            melody_ids.append(self.tokenizer.vocab[tok])
                        else:
                            print(
                                f"  [WARNING] {score_name}: Dropping unknown token: {tok}"
                            )

                    out_melody = os.path.join(
                        output_dir, f"{score_name}_MELODY_CHORDS.json"
                    )
                    out_literal = os.path.join(output_dir, f"{score_name}_LITERAL.json")

                    with open(out_melody, "w") as f:
                        json.dump(
                            {"ids": melody_ids, "tokens": injected_melody_strings}, f
                        )
                    with open(out_literal, "w") as f:
                        json.dump(
                            {"ids": literal_ids, "tokens": literal_tok_seqs[0].tokens},
                            f,
                        )

                    processed_count += 1

                except AssertionError as ae:
                    print(f"SKIPPING {score_name}: {ae}")
                except Exception as e:
                    print(f"Failed processing {score_name}: {e}")

        print(f"[Core] Successfully processed {processed_count} songs in {input_dir}")
