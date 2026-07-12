# VERSION = 1.0 (Intensity & Density Analytics)
import statistics
import lib.stream_utils as stream_utils


class IntensityExtractor:
    def __init__(self, literal_stream, time_scale=0.5):
        """
        Ingests a music21 stream and calculates intensity metrics per measure.
        time_scale: seconds per quarter note (e.g., 0.5 = 120 BPM)
        """
        self.stream = literal_stream
        self.time_scale = time_scale
        self.extracted_data = []
        self._process_stream()

    def get_intensity_events(self):
        """Returns the raw dictionary of extracted metrics per measure."""
        return self.extracted_data

    def _process_stream(self):
        if not self.stream:
            return

        part_map, bass_ref_part = stream_utils.map_parts(self.stream)
        if not part_map and not bass_ref_part:
            return

        for m in bass_ref_part.getElementsByClass("Measure"):
            measure_start = m.offset
            measure_end = m.offset + m.duration.quarterLength
            duration_quarters = m.duration.quarterLength
            duration_seconds = duration_quarters * self.time_scale

            # Prevent division by zero for anomalous empty measures
            if duration_seconds <= 0:
                continue

            # 1. Grab all note data for the current measure
            window_notes = stream_utils.get_all_notes_in_range(
                self.stream, measure_start, measure_end
            )

            if not window_notes:
                continue

            # 2. Calculate Density (Events per Second)
            # window_notes flattens chords, so this reflects total note-on density
            note_on_density = len(window_notes) / duration_seconds

            # 3. Calculate Overall Median Pitch
            overall_pitches = [n.pitch.ps for n, off in window_notes]
            overall_median = (
                statistics.median(overall_pitches) if overall_pitches else None
            )

            # 4. Extract Bassline & Calculate Bass Median Pitch
            bass_candidate_notes = stream_utils.get_bass_candidates_for_measure(
                measure_start, measure_end, part_map
            )
            bass_pitch_names = stream_utils.define_bass_subset(bass_candidate_notes)

            bass_pitches = []
            for n_obj, offset in bass_candidate_notes:
                if n_obj.pitch.nameWithOctave in bass_pitch_names:
                    bass_pitches.append(n_obj.pitch.ps)

            bass_median = statistics.median(bass_pitches) if bass_pitches else None

            # 5. Calculate Pitch Difference
            pitch_diff = None
            if overall_median is not None and bass_median is not None:
                pitch_diff = overall_median - bass_median

            # 6. Format Timestamp
            scaled_timestamp = float(measure_start) * self.time_scale
            mins = int(scaled_timestamp // 60)
            secs = scaled_timestamp % 60
            formatted_time = f"[{mins:02d}:{secs:06.3f}]"

            self.extracted_data.append(
                {
                    "timestamp": formatted_time,
                    "midi_offset": float(measure_start),
                    "note_density_eps": round(note_on_density, 3),
                    "bass_median_pitch": (
                        round(bass_median, 3) if bass_median is not None else "N/A"
                    ),
                    "pitch_diff": (
                        round(pitch_diff, 3) if pitch_diff is not None else "N/A"
                    ),
                }
            )

    def to_tsv(self):
        """
        Generates a TSV formatted string of the extracted intensity data.
        Columns: Timestamp, MIDI_Offset, Density_EPS, Bass_Median_Pitch, Pitch_Difference
        """
        headers = [
            "Timestamp",
            "MIDI_Offset",
            "Density_EPS",
            "Bass_Median_Pitch",
            "Pitch_Difference",
        ]
        lines = ["\t".join(headers)]

        for row in self.extracted_data:
            lines.append(
                "\t".join(
                    [
                        str(row["timestamp"]),
                        str(row["midi_offset"]),
                        str(row["note_density_eps"]),
                        str(row["bass_median_pitch"]),
                        str(row["pitch_diff"]),
                    ]
                )
            )

        return "\n".join(lines)
