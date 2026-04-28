import os
import argparse
import mido
from music21 import converter

# Import custom classes
from lib.ChordExtractor import ChordExtractor
from lib.MidiAligner import MidiAligner


class ScoreProcessorPop909:
    def __init__(self, input_dir, output_root):
        """
        Initializes the processor for a single POP909 song folder.

        Args:
            input_dir (str): Path to the specific song folder (e.g., 'dataset/001').
            output_root (str): The root directory where the new song folder will be created.
        """
        self.input_dir = os.path.normpath(input_dir)
        self.output_root = os.path.normpath(output_root)

        # Extract the 3-digit code from the folder name
        self.score_name = os.path.basename(self.input_dir)

        # Define the target output directory for this specific song
        self.output_dir = os.path.join(self.output_root, self.score_name)

    def _flatten_and_rechannel(self, tracks):
        """
        Merges a list of tracks into one and forces all channel-specific
        messages (like note_on, note_off) to channel 0.
        """
        merged_track = mido.merge_tracks(tracks)
        final_track = mido.MidiTrack()

        for msg in merged_track:
            # If the message has a channel attribute, force it to 0
            if hasattr(msg, "channel"):
                msg = msg.copy(channel=0)
            final_track.append(msg)

        return final_track

    def process(self):
        """Executes the conversion and extraction process."""
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
            raw_mid = aligner.align_midi(input_midi_path, beat_txt_path)
        else:
            raw_mid = mido.MidiFile(input_midi_path)
            print(
                f"[Warning] No beat_midi.txt found for {self.score_name}. Using raw MIDI."
            )

        # 2.5 Clean the MIDI file: Strip program & control changes while preserving delta timing
        original_mid = mido.MidiFile(ticks_per_beat=raw_mid.ticks_per_beat)

        for track in raw_mid.tracks:
            new_track = mido.MidiTrack()
            if hasattr(track, "name"):
                new_track.name = track.name

            accumulated_time = 0
            for msg in track:
                # Add this event's time to our running total
                accumulated_time += msg.time

                # If it's a keeper, give it the accumulated time and reset the counter
                if msg.type not in ["program_change", "control_change"]:
                    new_msg = msg.copy(time=accumulated_time)
                    new_track.append(new_msg)
                    accumulated_time = 0  # Reset for the next event

            original_mid.tracks.append(new_track)

        # 3. Track parsing and distribution
        meta_tracks = []
        melody_tracks = []
        piano_tracks = []

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

        # 4. Build and Save initial MIDI files

        # MELODY.mid: Merge, re-channel to 0, and save
        melody_mid = mido.MidiFile(ticks_per_beat=original_mid.ticks_per_beat)
        melody_mid.tracks.append(
            self._flatten_and_rechannel(meta_tracks + melody_tracks)
        )

        melody_midi_path = os.path.join(
            self.output_dir, f"{self.score_name}_MELODY.mid"
        )
        melody_mid.save(melody_midi_path)

        # LITERAL.mid: Save temporarily as multi-track so music21 can map them correctly
        literal_mid = mido.MidiFile(ticks_per_beat=original_mid.ticks_per_beat)
        literal_mid.tracks.extend(meta_tracks + melody_tracks + piano_tracks)

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

        # 6. Flatten LITERAL.mid into a single track (with channel=0) and overwrite
        flat_literal_mid = mido.MidiFile(ticks_per_beat=original_mid.ticks_per_beat)
        flat_literal_mid.tracks.append(
            self._flatten_and_rechannel(meta_tracks + melody_tracks + piano_tracks)
        )

        # Overwrite the multi-track version with the single-track, re-channeled version
        flat_literal_mid.save(literal_midi_path)

        print(f"Successfully processed, cleaned, & aligned: {self.score_name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Process a POP909 song folder into custom formats."
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
    processor = ScoreProcessorPop909(
        input_dir=args.input_dir, output_root=args.output_dir
    )
    processor.process()
