import json
import os
import copy
import re
import warnings
import music21
import shutil
import time
from pathlib import Path

TOKENIZED_DIR = Path("./../05_tokenized")
CHORD_DICT_DIR = Path("./../13_chord_dictionary")

from lib.ChordExtractor import ChordExtractor


def force_delete_dir(target_dir: Path):
    """Helper to safely delete a directory, blocking if locked by Windows."""

    # Check if directory exists before prompting
    if not target_dir.exists():
        print(f"Directory '{target_dir}' does not exist.")
        return

    # Prompt for confirmation
    confirm = (
        input(f"⚠️ Are you sure you want to permanently delete '{target_dir}'? (y/n): ")
        .strip()
        .lower()
    )
    if confirm not in ("y", "yes"):
        print("Deletion cancelled.")
        return

    lock_notified = False
    while target_dir.exists():
        try:
            shutil.rmtree(target_dir)
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
            break  # Safe to break if something else deleted it

    if lock_notified:
        print()  # Print a new line once the dots stop


def translate_to_m21(chord_str):
    """
    Translates raw chord strings into music21-compatible strings.
    - Replaces 'b' with '-' for roots and bass notes
    - Converts 'power' to '5'
    - Converts 'ø7' to 'm7b5'
    - Detects 'pedal' and flags it for bass-only generation
    """
    is_pedal = False
    if "pedal" in chord_str.lower():
        is_pedal = True
        chord_str = re.sub(r"(?i)pedal", "", chord_str)

    parts = chord_str.split("/")
    new_parts = []

    for i, part in enumerate(parts):
        # Match the root/bass note (A-G), optional accidental (b or #), and the rest
        m = re.match(r"^([A-G])(b|#)?(.*)", part)
        if m:
            note = m.group(1)
            acc = m.group(2)
            rest = m.group(3)

            # music21 strictly prefers '-' for flats in roots/basses
            if acc == "b":
                acc = "-"
            elif acc is None:
                acc = ""

            # Only replace qualities on the root part
            if i == 0:
                rest = rest.replace("power", "5")
                rest = rest.replace("ø7", "m7b5")

            new_parts.append(f"{note}{acc}{rest}")
        else:
            new_parts.append(part)

    translated = "/".join(new_parts)
    return translated, is_pedal


def generate_chord_dictionary():
    force_delete_dir(CHORD_DICT_DIR)

    config_path = os.path.join(TOKENIZED_DIR, "tokenizer_config.json")

    with open(config_path, "r", encoding="utf-8") as f:
        config_data = json.load(f)

    special_tokens = config_data.get("config", {}).get("special_tokens", [])

    chord_tokens = [
        t for t in special_tokens if t.startswith("[CHORD_") and t != "[CHORD_NC]"
    ]

    for token in chord_tokens:
        chord_symbol_str = token.replace("[CHORD_", "").replace("]", "")
        cleaned_chord_name = chord_symbol_str.replace("/", "_over_")

        output_dir = os.path.join(CHORD_DICT_DIR, cleaned_chord_name)
        os.makedirs(output_dir, exist_ok=True)

        # 1. Translate to music21 syntax
        translated_str, is_pedal = translate_to_m21(chord_symbol_str)

        # 2. Parse the chord symbol
        try:
            sym = music21.harmony.ChordSymbol(translated_str)
        except Exception as e:
            # Fallback: if quality is completely unrecognizable, treat as pedal
            is_pedal = True
            m = re.match(r"^([A-G][\-#]?)", translated_str)
            if m:
                root_str = m.group(1)
                sym = music21.harmony.ChordSymbol(root_str)
            else:
                print(f"Skipping {chord_symbol_str}: Could not extract root note.")
                continue

        # ==========================================
        # Generate LITERAL.mid
        # ==========================================
        literal_score = music21.stream.Score()
        part = music21.stream.Part()
        part.id = "B"

        measure = music21.stream.Measure()
        measure.number = 1
        measure.append(music21.meter.TimeSignature("4/4"))

        # Bass Octave (Left Hand)
        bass_pitch_name = sym.bass().name
        bass_p2 = music21.pitch.Pitch(f"{bass_pitch_name}2")
        bass_p3 = music21.pitch.Pitch(f"{bass_pitch_name}3")

        bass_chord = music21.chord.Chord([bass_p2, bass_p3], quarterLength=4.0)
        measure.insert(0.0, bass_chord)

        # Upper Chord (Right Hand) - Skip if it's a pedal chord
        if not is_pedal:
            upper_pitches = []
            for p in sym.pitches:
                if p.pitchClass != bass_p2.pitchClass:
                    new_p = music21.pitch.Pitch(p.name)
                    new_p.octave = p.implicitOctave if p.implicitOctave else 4
                    upper_pitches.append(new_p)

            if upper_pitches:
                # Shift the entire upper chord up by octaves until its lowest note is >= bass_p3
                while min(p.ps for p in upper_pitches) < bass_p3.ps:
                    for up in upper_pitches:
                        up.octave += 1

                upper_chord = music21.chord.Chord(upper_pitches, quarterLength=4.0)
                measure.insert(0.0, upper_chord)

        part.append(measure)
        literal_score.insert(0.0, part)

        literal_path = os.path.join(output_dir, f"{cleaned_chord_name}_LITERAL.mid")
        literal_score.write("midi", literal_path)

        # ==========================================
        # Generate MELODY.mid
        # ==========================================
        melody_score = copy.deepcopy(literal_score)
        for element in melody_score.recurse().notes:
            element.activeSite.remove(element)

        melody_path = os.path.join(output_dir, f"{cleaned_chord_name}_MELODY.mid")
        melody_score.write("midi", melody_path)

        # ==========================================
        # Generate CHORDS.tsv
        # ==========================================
        try:
            ce = ChordExtractor(literal_score)
            chord_events = ce.get_chord_events()

            if chord_events:
                chords_path = os.path.join(
                    output_dir, f"{cleaned_chord_name}_CHORDS.tsv"
                )
                with open(chords_path, "w", encoding="utf-8") as f:
                    f.write("Timestamp\tChord\tOffset\n")
                    for timestamp, chord_name, offset in chord_events:
                        f.write(f"{timestamp}\t{chord_name}\t{offset}\n")
        except NameError:
            pass


if __name__ == "__main__":
    generate_chord_dictionary()
    print("Chord dictionary generation complete.")
