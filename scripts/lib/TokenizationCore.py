import os
import json
import symusic
from miditok import REMI, TokenizerConfig, TokSequence
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


def chunk_token_sequence(tokens, chunk_size_bars=8, stride_bars=4):
    """
    Slices a continuous REMI token array into smaller chunks while
    flawlessly preserving Time, Key, Chord, and Control state across boundaries.
    """
    bars = []
    current_bar = []

    # 1. Smart Grouping (Binds leading metadata to the first bar)
    for token in tokens:
        if token.startswith("Bar_"):
            # Only start a new bar if the current one already has a Bar_ token
            if current_bar and any(t.startswith("Bar_") for t in current_bar):
                bars.append(current_bar)
                current_bar = [token]
            else:
                current_bar.append(token)
        else:
            current_bar.append(token)

    if current_bar:
        bars.append(current_bar)

    # 2. Pre-calculate the active state entering each bar
    active_ts = "TimeSig_4/4"
    active_ks = None
    active_chord = None
    active_control = None

    bar_states = []

    for bar in bars:
        bar_states.append(
            {
                "ts": active_ts,
                "ks": active_ks,
                "chord": active_chord,
                "control": active_control,
            }
        )

        # Update state for the next bar
        for t in bar:
            if t.startswith("TimeSig_"):
                active_ts = t
            elif t.startswith("KeySig_"):
                active_ks = t
            elif t.startswith("[CHORD_"):
                active_chord = t
            # Any bracketed token that isn't a chord is tracked as a Control Token
            elif t.startswith("[") and t.endswith("]") and not t.startswith("[CHORD_"):
                active_control = t

    # 3. Slide the window and generate the chunks
    chunks = []
    for i in range(0, len(bars), stride_bars):
        window_bars = bars[i : i + chunk_size_bars]

        if len(window_bars) < chunk_size_bars:
            if len(chunks) > 0:
                continue

        chunk_tokens = []
        first_bar = window_bars[0].copy()
        entering_state = bar_states[i]

        # --- A. INJECT CONTROL TOKEN ---
        if entering_state["control"]:
            # If the chunk doesn't already naturally start with the control token, prepend it
            if entering_state["control"] not in first_bar:
                first_bar.insert(0, entering_state["control"])

        # --- B. FIND BASE INSERT INDEX ---
        # Time/Key/Chords must go AFTER Bar_None
        try:
            base_insert_idx = first_bar.index("Bar_None") + 1
        except ValueError:
            # Edge case if a sequence is missing Bar_None
            base_insert_idx = 1 if entering_state["control"] in first_bar else 0

        # --- C. INJECT TIME/KEY SIGS ---
        if entering_state["ks"] and not any(t.startswith("KeySig_") for t in first_bar):
            first_bar.insert(base_insert_idx, entering_state["ks"])
        if entering_state["ts"] and not any(
            t.startswith("TimeSig_") for t in first_bar
        ):
            first_bar.insert(base_insert_idx, entering_state["ts"])

        # --- D. INJECT CHORD ---
        if entering_state["chord"]:
            # We must be careful not to overwrite a new chord that naturally starts at Position 0!
            pos_0_idx = -1
            for idx, t in enumerate(first_bar):
                if t == "Position_0":
                    pos_0_idx = idx
                    break

            has_pos_0_chord = False
            if pos_0_idx != -1 and pos_0_idx + 1 < len(first_bar):
                if first_bar[pos_0_idx + 1].startswith("[CHORD_"):
                    has_pos_0_chord = True

            if not has_pos_0_chord:
                if pos_0_idx != -1:
                    # Position_0 exists (e.g., for a melody note), just attach the chord!
                    first_bar.insert(pos_0_idx + 1, entering_state["chord"])
                else:
                    # Synthesize Position_0 and the Chord after TS/KS
                    ts_ks_count = sum(
                        1
                        for t in first_bar
                        if t.startswith("TimeSig_") or t.startswith("KeySig_")
                    )
                    chord_idx = base_insert_idx + ts_ks_count
                    first_bar.insert(chord_idx, entering_state["chord"])
                    first_bar.insert(chord_idx, "Position_0")

        # --- REASSEMBLE CHUNK ---
        chunk_tokens.extend(first_bar)
        for b in window_bars[1:]:
            chunk_tokens.extend(b)

        chunks.append(chunk_tokens)

    return chunks


def reinject_velocities(generated_tokens):
    """Satisfies MidiTok's strict grammar requirements before decoding."""
    repaired_sequence = []
    for token in generated_tokens:
        repaired_sequence.append(token)
        # The moment we see a Pitch, immediately satisfy MidiTok by faking the Velocity
        if token.startswith("Pitch_"):
            repaired_sequence.append("Velocity_127")

    return repaired_sequence


class PianoArrangerTokenizer:
    def __init__(self, positions_per_beat=8):
        self.positions_per_beat = positions_per_beat
        self.tokenizer = None

    def initialize_new(self, chord_vocab):
        """Used by Phase 1 to create the canonical tokenizer."""
        # Automatically register the Separator Token
        if "[SEP]" not in chord_vocab:
            chord_vocab.append("[SEP]")

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

    def process_directory(
        self,
        input_dir,
        output_dir,
        chunk_size=8,
        stride=4,
        control_token=None,
        use_stitching=True,
        use_chunking=True,
    ):
        """Scans, injects, strips velocity, chunks, stitches, and tokenizes."""
        os.makedirs(output_dir, exist_ok=True)
        processed_count = 0

        for root, _, files in os.walk(input_dir):
            melody = literal = chords = None
            is_dict_entry = False

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
                score_name = os.path.basename(melody).replace("_MELODY.mid", "")

                try:
                    sym_melody = symusic.Score(melody)
                    sym_literal = symusic.Score(literal)
                    ticks_per_quarter = sym_melody.ticks_per_quarter

                    assert (
                        len(sym_melody.tracks) <= 1
                    ), f"Expected <1 melody track, got {len(sym_melody.tracks)}"
                    assert (
                        len(sym_literal.tracks) == 1
                    ), f"Expected 1 literal track, got {len(sym_literal.tracks)}"

                    if is_dict_entry:
                        chords_data = load_dictionary_entry(chords)
                    else:
                        chords_data = load_and_map_chords(chords, ticks_per_quarter)

                    melody_tok_seqs = self.tokenizer(sym_melody)
                    literal_tok_seqs = self.tokenizer(sym_literal)

                    raw_melody_strings = melody_tok_seqs[0].tokens
                    raw_literal_strings = literal_tok_seqs[0].tokens

                    injected_melody_strings = self._inject_chords_into_remi(
                        raw_melody_strings, chords_data, ticks_per_quarter
                    )

                    final_melody_strings = [
                        t
                        for t in injected_melody_strings
                        if not t.startswith("Velocity_")
                    ]
                    final_literal_strings = [
                        t for t in raw_literal_strings if not t.startswith("Velocity_")
                    ]

                    # --- CHUNKING BYPASS ---
                    if use_chunking:
                        melody_chunks = chunk_token_sequence(
                            final_melody_strings, chunk_size, stride
                        )
                        literal_chunks = chunk_token_sequence(
                            final_literal_strings, chunk_size, stride
                        )
                    else:
                        melody_chunks = [final_melody_strings]
                        literal_chunks = [final_literal_strings]

                    num_chunks = min(len(melody_chunks), len(literal_chunks))

                    # --- STITCHING & SAVING ---
                    for i in range(num_chunks):
                        current_melody = melody_chunks[i]
                        current_literal = literal_chunks[i]

                        # Apply Control Token if provided
                        if control_token and (
                            not current_melody or current_melody[0] != control_token
                        ):
                            current_melody = [control_token] + current_melody

                        # Apply Context Stitching
                        if use_stitching:
                            if i == 0:
                                encoder_input_strings = ["[SEP]"] + current_melody
                            else:
                                previous_literal = literal_chunks[i - 1]
                                encoder_input_strings = (
                                    previous_literal + ["[SEP]"] + current_melody
                                )
                        else:
                            encoder_input_strings = current_melody

                        decoder_target_strings = current_literal

                        # Convert to IDs
                        melody_ids = [
                            self.tokenizer.vocab.get(t, 0)
                            for t in encoder_input_strings
                        ]  # 0 fallback
                        literal_ids = [
                            self.tokenizer.vocab.get(t, 0)
                            for t in decoder_target_strings
                        ]

                        # Save Numbered Chunks
                        out_melody = os.path.join(
                            output_dir, f"{score_name}_chunk{i:03d}_MELODY_CHORDS.json"
                        )
                        out_literal = os.path.join(
                            output_dir, f"{score_name}_chunk{i:03d}_LITERAL.json"
                        )

                        with open(out_melody, "w") as f:
                            json.dump(
                                {"ids": melody_ids, "tokens": encoder_input_strings}, f
                            )
                        with open(out_literal, "w") as f:
                            json.dump(
                                {"ids": literal_ids, "tokens": decoder_target_strings},
                                f,
                            )

                    processed_count += 1

                except Exception as e:
                    import traceback

                    traceback.print_exc()
                    print(f"Failed processing {score_name}: {e}")

        print(
            f"[Core] Successfully processed {processed_count} songs into chunks from {input_dir} to {output_dir}"
        )

    def decode_directory(self, input_dir, output_dir):
        """Decodes JSON token sequences back into MIDI and TSV."""
        os.makedirs(output_dir, exist_ok=True)
        decoded_count = 0

        # Dynamically discover the default velocity token from the vocabulary
        try:
            vel_tok = next(
                k for k in self.tokenizer.vocab.keys() if k.startswith("Velocity_")
            )
        except StopIteration:
            vel_tok = "Velocity_127"

        for root, _, files in os.walk(input_dir):
            for f in files:
                if not f.endswith(".json") or f == "tokenizer_config.json":
                    continue

                json_path = os.path.join(root, f)
                score_name = f.replace("_LITERAL.json", "").replace(
                    "_MELODY_CHORDS.json", ""
                )
                song_out_dir = os.path.join(output_dir, score_name)
                os.makedirs(song_out_dir, exist_ok=True)

                try:
                    with open(json_path, "r", encoding="utf-8") as jf:
                        data = json.load(jf)
                        ids = data.get("ids", [])

                    if not ids:
                        continue

                    # 1. LITERAL DECODING
                    if "LITERAL" in f:
                        seq = TokSequence(ids=ids)
                        self.tokenizer.complete_sequence(seq)

                        # --- VELOCITY RESTORATION ---
                        restored_tokens = []
                        for t in seq.tokens:
                            restored_tokens.append(t)
                            if t.startswith("Pitch_"):
                                restored_tokens.append(vel_tok)

                        restored_ids = [
                            self.tokenizer.vocab[t] for t in restored_tokens
                        ]
                        restored_seq = TokSequence(ids=restored_ids)

                        decoded_score = self.tokenizer.decode([restored_seq])
                        out_midi_path = os.path.join(
                            song_out_dir, f"{score_name}_LITERAL.mid"
                        )
                        decoded_score.dump_midi(out_midi_path)

                    # 2. MELODY + CHORD DECODING
                    elif "MELODY_CHORDS" in f:
                        seq = TokSequence(ids=ids)
                        self.tokenizer.complete_sequence(seq)

                        clean_tokens = []
                        recovered_tsv_data = []

                        bar_start_quarter = 0.0
                        current_quarter = 0.0
                        beats_per_bar = 4.0
                        has_seen_bar = False

                        # If [SEP] is present, slice the list so we ONLY decode the current melody!
                        # Otherwise, the output MIDI will contain the previous literal overlapping the melody.
                        working_tokens = seq.tokens
                        if "[SEP]" in working_tokens:
                            sep_idx = working_tokens.index("[SEP]")
                            working_tokens = working_tokens[sep_idx + 1 :]

                        for token_str in seq.tokens:
                            # A. Extract Chords
                            if token_str.startswith("[CHORD_"):
                                chord_name = token_str[7:-1]
                                recovered_tsv_data.append((chord_name, current_quarter))
                                continue

                            # Bypass any other special bracket tokens ([SECTION_], etc.)
                            if token_str.startswith("[") and token_str.endswith("]"):
                                continue

                            # --- VELOCITY RESTORATION ---
                            clean_tokens.append(token_str)
                            if token_str.startswith("Pitch_"):
                                clean_tokens.append(vel_tok)

                            # B. Track Absolute Time
                            if token_str.startswith("TimeSig_"):
                                num, den = token_str.split("_")[1].split("/")
                                beats_per_bar = int(num) * (4.0 / int(den))

                            elif token_str.startswith("Bar_"):
                                if has_seen_bar:
                                    bar_start_quarter += beats_per_bar
                                has_seen_bar = True

                            elif token_str.startswith("Position_"):
                                pos_idx = int(token_str.split("_")[1])
                                current_quarter = bar_start_quarter + (
                                    pos_idx / self.positions_per_beat
                                )

                        # Convert clean tokens back to IDs for decoding
                        clean_ids = [self.tokenizer.vocab[t] for t in clean_tokens]

                        # C. Save the Recovered TSV
                        tsv_path = os.path.join(
                            song_out_dir, f"{score_name}_CHORDS_RECOVERED.tsv"
                        )
                        with open(tsv_path, "w", encoding="utf-8") as tf:
                            tf.write("Chord\tOffset_Quarter\n")
                            for chord, offset in recovered_tsv_data:
                                tf.write(f"{chord}\t{offset:.3f}\n")

                        # D. Save the Cleaned Melody MIDI
                        clean_seq = TokSequence(ids=clean_ids)
                        decoded_melody = self.tokenizer.decode([clean_seq])
                        out_melody_path = os.path.join(
                            song_out_dir, f"{score_name}_MELODY.mid"
                        )
                        decoded_melody.dump_midi(out_melody_path)

                    decoded_count += 1

                except Exception as e:
                    import traceback

                    traceback.print_exc()
                    print(f"Failed decoding {f}: {e}")

        print(f"[Core] Successfully decoded {decoded_count} files to {output_dir}")
