import os
import argparse
import mido
from music21 import converter

from lib.ChordExtractor import ChordExtractor
from lib.MidiAligner import MidiAligner


class ScoreProcessorPop909:
    def __init__(self, input_dir, output_root):
        self.input_dir = os.path.normpath(input_dir)
        self.output_root = os.path.normpath(output_root)
        self.score_name = os.path.basename(self.input_dir)
        self.output_dir = os.path.join(self.output_root, self.score_name)

    def process(self):
        input_midi_path = os.path.join(self.input_dir, f"{self.score_name}.mid")
        beat_txt_path = os.path.join(self.input_dir, "beat_midi.txt")

        if not os.path.exists(input_midi_path):
            raise FileNotFoundError(
                f"Could not find expected MIDI file: {input_midi_path}"
            )

        # 1. Create the output folder
        os.makedirs(self.output_dir, exist_ok=True)

        # 2. Load & Quantize the MIDI file
        aligner = MidiAligner()
        if os.path.exists(beat_txt_path):
            # Warps the timeline to strict beats if the beat map exists
            original_mid = aligner.align_midi(input_midi_path, beat_txt_path)
        else:
            # Fallback for unexpected missing text files
            original_mid = mido.MidiFile(input_midi_path)
            print(
                f"[Warning] No beat_midi.txt found for {self.score_name}. Using raw MIDI."
            )

        melody_mid = mido.MidiFile(ticks_per_beat=original_mid.ticks_per_beat)
        literal_mid = mido.MidiFile(ticks_per_beat=original_mid.ticks_per_beat)

        meta_tracks = []
        melody_tracks = []
        piano_tracks = []

        # 3. Track parsing and distribution
        for i, track in enumerate(original_mid.tracks):
            track_name = track.name.strip().upper()

            if "MELODY" in track_name:
                melody_tracks.append(track)
            elif "PIANO" in track_name:
                piano_tracks.append(track)
            elif "BRIDGE" in track_name:
                continue
            else:
                if not track_name:
                    if i == 1:
                        melody_tracks.append(track)
                    elif i == 2:
                        continue
                    elif i == 3:
                        piano_tracks.append(track)
                    else:
                        meta_tracks.append(track)
                else:
                    meta_tracks.append(track)

        melody_mid.tracks.extend(meta_tracks + melody_tracks)
        literal_mid.tracks.extend(meta_tracks + melody_tracks + piano_tracks)

        # 4. Save isolated MIDI files
        melody_midi_path = os.path.join(
            self.output_dir, f"{self.score_name}_MELODY.mid"
        )
        melody_mid.save(melody_midi_path)

        literal_midi_path = os.path.join(
            self.output_dir, f"{self.score_name}_LITERAL.mid"
        )
        literal_mid.save(literal_midi_path)

        # 5. Extract chords using literal_score
        literal_score = converter.parse(literal_midi_path)

        # Explicit mapping for ChordExtractor
        if len(literal_score.parts) >= 2:
            literal_score.parts[0].id = "T"
            literal_score.parts[-1].id = "B"
            for part in literal_score.parts:
                part_name = (part.partName or "").upper()
                if "MELODY" in part_name:
                    part.id = "T"
                elif "PIANO" in part_name:
                    part.id = "B"

        ce = ChordExtractor(literal_score)
        chord_events = ce.get_chord_events()

        if chord_events:
            chords_path = os.path.join(self.output_dir, f"{self.score_name}_CHORDS.tsv")
            with open(chords_path, "w", encoding="utf-8") as f:
                f.write("Timestamp\tChord\tOffset\n")
                for timestamp, chord_name, offset in chord_events:
                    f.write(f"{timestamp}\t{chord_name}\t{offset}\n")

        print(f"Successfully processed & aligned: {self.score_name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Process a POP909 song folder into custom custom formats."
    )
    parser.add_argument(
        "--input_dir",
        type=str,
        required=True,
        help="Path to the POP909 single song folder (e.g., ./POP909/001)",
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        required=True,
        help="Path to the root output directory where the formatted folder will be saved",
    )

    args = parser.parse_args()
    processor = Pop909ScoreProcessor(
        input_dir=args.input_dir, output_root=args.output_dir
    )
    processor.process()
