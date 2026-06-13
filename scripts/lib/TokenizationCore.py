import os
import json
import symusic
from miditok import REMI, TokenizerConfig, TokSequence
from collections import defaultdict

# --- SYSTEM CONSTANTS ---
SYSTEM_TOKENS = {
    "chunk_separator": "[SEP]",
    "control_intensities": [
        "[INTENSITY_LOWEST]",
        "[INTENSITY_LOW]",
        "[INTENSITY_MEDIUM]",
        "[INTENSITY_HIGH]",
        "[INTENSITY_HIGHEST]",
    ],
}


def sanitize_chord(chord_str):
    if "Cannot Be Identified" in chord_str:
        return "[CHORD_NC]"
    c = chord_str.replace(" ", "").replace("-", "b")
    enharmonics = {
        "C#": "Db",
        "D#": "Eb",
        "E#": "F",
        "F#": "Gb",
        "G#": "Ab",
        "A#": "Bb",
        "B#": "C",
        "Cb": "B",
        "Fb": "E",
    }

    def normalize_pitch(pitch_str):
        for sharp, flat in enharmonics.items():
            if pitch_str.startswith(sharp):
                return flat + pitch_str[len(sharp) :]
        return pitch_str

    if "/" in c:
        root, bass = c.split("/")
        c = f"{normalize_pitch(root)}/{normalize_pitch(bass)}"
    else:
        c = normalize_pitch(c)
    return f"[CHORD_{c}]"


def load_and_map_chords(tsv_path, ticks_per_quarter):
    chords_by_tick = defaultdict(list)
    with open(tsv_path, "r", encoding="utf-8") as f:
        lines = f.readlines()[1:]
        for line in lines:
            parts = line.strip().split("\t")
            if len(parts) >= 3:
                chord_name = sanitize_chord(parts[1])
                offset_q = float(parts[2])
                tick = int(offset_q * ticks_per_quarter)
                chords_by_tick[tick].append(chord_name)
    return chords_by_tick


def load_dictionary_entry(tsv_path):
    chords_by_tick = defaultdict(list)
    with open(tsv_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        if len(lines) > 1:
            exact_token = lines[1].strip()
            chords_by_tick[0].append(exact_token)
    return chords_by_tick


def load_and_map_intensities(tsv_path, ticks_per_quarter):
    """Maps TSV integer intensities to their global token strings."""
    intensities_by_tick = defaultdict(list)
    with open(tsv_path, "r", encoding="utf-8") as f:
        lines = f.readlines()[1:]
        for line in lines:
            parts = line.strip().split("\t")
            if len(parts) >= 3:
                try:
                    val = int(parts[1])
                    val = max(0, 4)  # Clamp to [0, 4]
                    token_str = SYSTEM_TOKENS["control_intensities"][val]
                    offset_q = float(parts[2])
                    tick = int(offset_q * ticks_per_quarter)
                    intensities_by_tick[tick].append(token_str)
                except ValueError:
                    pass
    return intensities_by_tick


def chunk_token_sequence(tokens, chunk_size_bars=8, stride_bars=4):
    """Slices sequence while preserving Time, Key, Chord, and Intensity state."""
    bars = []
    current_bar = []

    # 1. Smart Grouping
    for token in tokens:
        if token.startswith("Bar_"):
            if current_bar and any(t.startswith("Bar_") for t in current_bar):
                bars.append(current_bar)
                current_bar = [token]
            else:
                current_bar.append(token)
        else:
            current_bar.append(token)

    if current_bar:
        bars.append(current_bar)

    # 2. State Tracking
    active_ts = "TimeSig_4/4"
    active_ks = None
    active_chord = None
    active_intensity = None

    bar_states = []

    for bar in bars:
        bar_states.append(
            {
                "ts": active_ts,
                "ks": active_ks,
                "chord": active_chord,
                "intensity": active_intensity,
            }
        )
        for t in bar:
            if t.startswith("TimeSig_"):
                active_ts = t
            elif t.startswith("KeySig_"):
                active_ks = t
            elif t.startswith("[CHORD_"):
                active_chord = t
            elif t in SYSTEM_TOKENS["control_intensities"]:
                active_intensity = t

    # 3. Slide Window
    chunks = []
    for i in range(0, len(bars), stride_bars):
        window_bars = bars[i : i + chunk_size_bars]

        if len(window_bars) < chunk_size_bars:
            if len(chunks) > 0:
                continue

        chunk_tokens = []
        first_bar = window_bars[0].copy()
        entering_state = bar_states[i]

        try:
            base_insert_idx = first_bar.index("Bar_None") + 1
        except ValueError:
            base_insert_idx = 0

        # --- A. INJECT TIME/KEY SIGS ---
        if entering_state["ks"] and not any(t.startswith("KeySig_") for t in first_bar):
            first_bar.insert(base_insert_idx, entering_state["ks"])
        if entering_state["ts"] and not any(
            t.startswith("TimeSig_") for t in first_bar
        ):
            first_bar.insert(base_insert_idx, entering_state["ts"])

        # --- B. INJECT POSITION-BASED STATES (CHORD & INTENSITY) ---
        needs_pos_0 = False
        insert_chord = entering_state["chord"]
        insert_intensity = entering_state["intensity"]

        pos_0_idx = -1
        for idx, t in enumerate(first_bar):
            if t == "Position_0":
                pos_0_idx = idx
                break

        if pos_0_idx != -1:
            # Check what's already mapped to Position 0 to prevent duplicates
            for t in first_bar[pos_0_idx + 1 :]:
                if t.startswith("Position_"):
                    break
                if t.startswith("[CHORD_"):
                    insert_chord = None
                if t in SYSTEM_TOKENS["control_intensities"]:
                    insert_intensity = None
        else:
            if insert_chord or insert_intensity:
                needs_pos_0 = True

        if needs_pos_0:
            ts_ks_count = sum(
                1
                for t in first_bar
                if t.startswith("TimeSig_") or t.startswith("KeySig_")
            )
            pos_0_idx = base_insert_idx + ts_ks_count
            first_bar.insert(pos_0_idx, "Position_0")

        # Insert them correctly after Position_0
        if insert_chord:
            first_bar.insert(pos_0_idx + 1, insert_chord)
        if insert_intensity:
            first_bar.insert(pos_0_idx + 1, insert_intensity)

        # --- REASSEMBLE ---
        chunk_tokens.extend(first_bar)
        for b in window_bars[1:]:
            chunk_tokens.extend(b)

        chunks.append(chunk_tokens)

    return chunks


class PianoArrangerTokenizer:
    def __init__(self, positions_per_beat=8, use_control_tokens=False):
        self.positions_per_beat = positions_per_beat
        self.use_control_tokens = use_control_tokens
        self.tokenizer = None

    def initialize_new(self, chord_vocab):
        # Register System Tokens safely
        if SYSTEM_TOKENS["chunk_separator"] not in chord_vocab:
            chord_vocab.append(SYSTEM_TOKENS["chunk_separator"])

        for t in SYSTEM_TOKENS["control_intensities"]:
            if t not in chord_vocab:
                chord_vocab.append(t)

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
        self.tokenizer = REMI(params=config_path)
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

    def _inject_events_into_remi(self, remi_tokens, events_by_tick, ticks_per_quarter):
        injected_sequence = []
        current_bar_tick = 0
        ticks_per_pos = ticks_per_quarter / self.positions_per_beat
        beats_per_bar = 4.0
        ticks_per_bar = int(beats_per_bar * ticks_per_quarter)
        pending_ticks = sorted(events_by_tick.keys())

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
                        pos_idx = int(round((t - current_bar_tick) / ticks_per_pos))
                        injected_sequence.append(f"Position_{pos_idx}")
                        injected_sequence.extend(events_by_tick[t])
                    current_bar_tick = next_bar_tick
                injected_sequence.append(token)

            elif token.startswith("Position_"):
                pos_idx = int(token.split("_")[1])
                current_tick = current_bar_tick + int(round(pos_idx * ticks_per_pos))

                while pending_ticks and pending_ticks[0] < current_tick:
                    t = pending_ticks.pop(0)
                    pos = int(round((t - current_bar_tick) / ticks_per_pos))
                    injected_sequence.append(f"Position_{pos}")
                    injected_sequence.extend(events_by_tick[t])

                injected_sequence.append(token)

                while pending_ticks and pending_ticks[0] == current_tick:
                    t = pending_ticks.pop(0)
                    injected_sequence.extend(events_by_tick[t])

            else:
                injected_sequence.append(token)

        while pending_ticks:
            t = pending_ticks.pop(0)
            while t >= current_bar_tick + ticks_per_bar:
                injected_sequence.append("Bar_None")
                current_bar_tick += ticks_per_bar

            pos_idx = int(round((t - current_bar_tick) / ticks_per_pos))
            positions_per_bar = int(round(ticks_per_bar / ticks_per_pos))
            if pos_idx >= positions_per_bar:
                pos_idx = positions_per_bar - 1

            injected_sequence.append(f"Position_{pos_idx}")
            injected_sequence.extend(events_by_tick[t])

        return injected_sequence

    def process_directory(
        self,
        input_dir,
        output_dir,
        chunk_size=8,
        stride=4,
        use_stitching=True,
        use_chunking=True,
    ):
        os.makedirs(output_dir, exist_ok=True)
        processed_count = 0

        for root, _, files in os.walk(input_dir):
            melody = literal = chords = intensity = None
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
                elif f.endswith("_INTENSITY.tsv"):
                    intensity = os.path.join(root, f)

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
                        events_data = load_dictionary_entry(chords)
                    else:
                        events_data = load_and_map_chords(chords, ticks_per_quarter)

                    # --- TSV INTENSITY INJECTION ---
                    if self.use_control_tokens and intensity is not None:
                        intensity_data = load_and_map_intensities(
                            intensity, ticks_per_quarter
                        )
                        for t, toks in intensity_data.items():
                            # Merge them so they inject at the same Position_X
                            events_data[t] = toks + events_data[t]

                    melody_tok_seqs = self.tokenizer(sym_melody)
                    literal_tok_seqs = self.tokenizer(sym_literal)

                    raw_melody_strings = melody_tok_seqs[0].tokens
                    raw_literal_strings = literal_tok_seqs[0].tokens

                    injected_melody_strings = self._inject_events_into_remi(
                        raw_melody_strings, events_data, ticks_per_quarter
                    )

                    final_melody_strings = [
                        t
                        for t in injected_melody_strings
                        if not t.startswith("Velocity_")
                    ]
                    final_literal_strings = [
                        t for t in raw_literal_strings if not t.startswith("Velocity_")
                    ]

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
                    sep_tok = SYSTEM_TOKENS["chunk_separator"]

                    for i in range(num_chunks):
                        current_melody = melody_chunks[i]
                        current_literal = literal_chunks[i]

                        if use_stitching:
                            if i == 0:
                                encoder_input_strings = [sep_tok] + current_melody
                            else:
                                previous_literal = literal_chunks[i - 1]
                                encoder_input_strings = (
                                    previous_literal + [sep_tok] + current_melody
                                )
                        else:
                            encoder_input_strings = current_melody

                        decoder_target_strings = current_literal

                        melody_ids = [
                            self.tokenizer.vocab.get(t, 0)
                            for t in encoder_input_strings
                        ]
                        literal_ids = [
                            self.tokenizer.vocab.get(t, 0)
                            for t in decoder_target_strings
                        ]

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
            f"[Core] Successfully processed {processed_count} songs into chunks in {input_dir}"
        )

    def decode_directory(self, input_dir, output_dir):
        os.makedirs(output_dir, exist_ok=True)
        decoded_count = 0

        try:
            vel_tok = next(
                k for k in self.tokenizer.vocab.keys() if k.startswith("Velocity_")
            )
        except StopIteration:
            vel_tok = "Velocity_127"

        sep_tok = SYSTEM_TOKENS["chunk_separator"]

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

                    if "LITERAL" in f:
                        seq = TokSequence(ids=ids)
                        self.tokenizer.complete_sequence(seq)

                        restored_tokens = []
                        for t in seq.tokens:
                            restored_tokens.append(t)
                            if t.startswith("Pitch_"):
                                restored_tokens.append(vel_tok)

                        restored_ids = [
                            self.tokenizer.vocab.get(t, 0) for t in restored_tokens
                        ]
                        decoded_score = self.tokenizer.decode(
                            [TokSequence(ids=restored_ids)]
                        )
                        decoded_score.dump_midi(
                            os.path.join(song_out_dir, f"{score_name}_LITERAL.mid")
                        )

                    elif "MELODY_CHORDS" in f:
                        seq = TokSequence(ids=ids)
                        self.tokenizer.complete_sequence(seq)

                        working_tokens = seq.tokens
                        if sep_tok in working_tokens:
                            sep_idx = working_tokens.index(sep_tok)
                            working_tokens = working_tokens[sep_idx + 1 :]

                        clean_tokens = []
                        recovered_tsv_data = []

                        bar_start_quarter = 0.0
                        current_quarter = 0.0
                        beats_per_bar = 4.0
                        has_seen_bar = False

                        for token_str in working_tokens:
                            if token_str.startswith("[CHORD_"):
                                chord_name = token_str[7:-1]
                                recovered_tsv_data.append((chord_name, current_quarter))
                                continue

                            # Safely bypass Intensity and any other special bracket states
                            if token_str.startswith("[") and token_str.endswith("]"):
                                continue

                            clean_tokens.append(token_str)
                            if token_str.startswith("Pitch_"):
                                clean_tokens.append(vel_tok)

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

                        clean_ids = [
                            self.tokenizer.vocab.get(t, 0) for t in clean_tokens
                        ]

                        tsv_path = os.path.join(
                            song_out_dir, f"{score_name}_CHORDS_RECOVERED.tsv"
                        )
                        with open(tsv_path, "w", encoding="utf-8") as tf:
                            tf.write("Chord\tOffset_Quarter\n")
                            for chord, offset in recovered_tsv_data:
                                tf.write(f"{chord}\t{offset:.3f}\n")

                        decoded_melody = self.tokenizer.decode(
                            [TokSequence(ids=clean_ids)]
                        )
                        decoded_melody.dump_midi(
                            os.path.join(song_out_dir, f"{score_name}_MELODY.mid")
                        )

                    decoded_count += 1
                except Exception as e:
                    print(f"Failed decoding {f}: {e}")

        print(f"[Core] Successfully decoded {decoded_count} files to {output_dir}")
