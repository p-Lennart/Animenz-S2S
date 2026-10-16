# VERSION = 2.0 (Intensity & Density Analytics with Method A Discretization)
import os
import statistics
import lib.stream_utils as stream_utils


def discretize_intensity_records(records):
    """
    Discretizes continuous measure-level intensity metrics into discrete levels [0..4]
    using Method A (Hybrid Continuous Scoring with Acoustic Floor/Ceiling Safeguards).

    Parameters:
      records (list of dict): Each dict contains measure metrics with keys:
        - 'note_density_eps' or 'density_eps': float
        - 'bass_median_pitch': float, None, or 'N/A'
        - 'pitch_diff' or 'pitch_difference': float, None, or 'N/A'

    Returns:
      list of int: Discrete intensity levels [0, 1, 2, 3, 4] for each measure.
    """
    n = len(records)
    if n == 0:
        return []

    densities = []
    basses = []
    diffs = []
    for r in records:
        r_lower = {str(k).lower(): v for k, v in r.items()}
        dens = r_lower.get("density_eps", r_lower.get("note_density_eps", 0.0))
        try:
            densities.append(float(dens))
        except (ValueError, TypeError):
            densities.append(0.0)

        b = r_lower.get("bass_median_pitch", "N/A")
        try:
            if b in (None, "N/A", "", "nan") or (isinstance(b, float) and (b != b)):
                basses.append(None)
            else:
                basses.append(float(b))
        except (ValueError, TypeError):
            basses.append(None)

        d = r_lower.get("pitch_difference", r_lower.get("pitch_diff", "N/A"))
        try:
            if d in (None, "N/A", "", "nan") or (isinstance(d, float) and (d != d)):
                diffs.append(None)
            else:
                diffs.append(float(d))
        except (ValueError, TypeError):
            diffs.append(None)

    # Compute song-relative density percentile rank [0..1]
    if n > 1:
        sorted_indices = sorted(range(n), key=lambda i: densities[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and densities[sorted_indices[j + 1]] == densities[sorted_indices[i]]:
                j += 1
            avg_rank = (i + 1 + j + 1) / (2.0 * n)
            for k in range(i, j + 1):
                ranks[sorted_indices[k]] = avg_rank
            i = j + 1
        dens_pcts = ranks
    else:
        dens_pcts = [0.5]

    levels = []
    for dens, ep_pct, b, d in zip(densities, dens_pcts, basses, diffs):
        is_na = (b is None)
        b_clean = 72.0 if is_na else b
        d_clean = 0.0 if (d is None) else d
        bass_depth = max(0.0, 60.0 - b_clean)

        score = (
            0.10
            + 2.60 * ep_pct
            + 0.045 * bass_depth
            + 0.015 * d_clean
            - (1.50 if is_na else 0.0)
        )
        lvl = int(round(score))
        lvl = max(0, min(lvl, 4))

        # Acoustic safeguards:
        # 1. Level 4 requires true sub-bass (<= 36.0) and substantial activity
        if lvl == 4 and (b_clean > 36.0 or (dens < 13.0 and ep_pct < 0.90)):
            lvl = 3
        # 2. Treble register cap:
        if b_clean >= 60.0:
            if dens < 5.0 or ep_pct < 0.25:
                lvl = 0
            elif lvl > 1:
                lvl = 1
        # 3. Missing bass cap: melody solo/silence cannot exceed Level 1
        if is_na and lvl > 1:
            lvl = 0

        levels.append(lvl)

    return levels


def discretize_intensity_tsv(tsv_content_or_path):
    """
    Parses a TSV string or file path, enriches with discretized 'Intensity' column,
    and returns the Rich Schema TSV string:
    Timestamp\tMIDI_Offset\tIntensity\tDensity_EPS\tBass_Median_Pitch\tPitch_Difference
    """
    if os.path.exists(tsv_content_or_path):
        with open(tsv_content_or_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    else:
        lines = tsv_content_or_path.strip().splitlines()

    if not lines:
        return ""

    header_cols = [c.strip().lower() for c in lines[0].strip().split("\t")]
    rows = []
    for line in lines[1:]:
        parts = line.strip().split("\t")
        if len(parts) >= len(header_cols):
            rows.append({header_cols[i]: parts[i] for i in range(len(header_cols))})

    levels = discretize_intensity_records(rows)

    out_headers = [
        "Timestamp",
        "MIDI_Offset",
        "Intensity",
        "Density_EPS",
        "Bass_Median_Pitch",
        "Pitch_Difference",
    ]
    out_lines = ["\t".join(out_headers)]

    for r, lvl in zip(rows, levels):
        r_lower = {str(k).lower(): v for k, v in r.items()}
        ts = r_lower.get("timestamp", "[00:00.000]")
        off = r_lower.get("midi_offset", r_lower.get("offset", "0.0"))
        dens = r_lower.get("density_eps", r_lower.get("note_density_eps", "0.0"))
        bass = r_lower.get("bass_median_pitch", "N/A")
        diff = r_lower.get("pitch_difference", r_lower.get("pitch_diff", "N/A"))
        out_lines.append(f"{ts}\t{off}\t{lvl}\t{dens}\t{bass}\t{diff}")

    return "\n".join(out_lines)


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

        # 7. Discretize into discrete levels [0..4] using Method A
        levels = discretize_intensity_records(self.extracted_data)
        for row, lvl in zip(self.extracted_data, levels):
            row["intensity"] = lvl

    def to_tsv(self):
        """
        Generates a Rich Schema TSV string of the extracted intensity data.
        Columns: Timestamp, MIDI_Offset, Intensity, Density_EPS, Bass_Median_Pitch, Pitch_Difference
        """
        headers = [
            "Timestamp",
            "MIDI_Offset",
            "Intensity",
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
                        str(row.get("intensity", 0)),
                        str(row["note_density_eps"]),
                        str(row["bass_median_pitch"]),
                        str(row["pitch_diff"]),
                    ]
                )
            )

        return "\n".join(lines)
