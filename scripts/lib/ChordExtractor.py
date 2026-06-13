# VERSION = 1.10 (Fully Abstracted Stream Utils)
import music21
import copy
import stream_utils


class ChordExtractor:
    def __init__(self, literal_stream, time_scale=0.5):
        self.stream = literal_stream
        self.time_scale = time_scale
        self.extracted_data = []
        self._process_stream()

    def get_chord_events(self):
        """
        Returns the parsed chord data.
        Format: [( "[MM:SS.mmm]", "ChordName", absolute_quarter_offset ), ...]
        """
        return self.extracted_data

    def _process_stream(self):
        if not self.stream:
            return

        part_map, ref_part = stream_utils.map_parts(self.stream)
        if not part_map and not ref_part:
            return

        last_chord_str = None
        previous_window_notes = []

        for m in ref_part.getElementsByClass("Measure"):
            measure_start = m.offset
            measure_end = m.offset + m.duration.quarterLength

            bass_candidate_notes = stream_utils.get_bass_candidates_for_measure(
                measure_start, measure_end, part_map
            )

            if not bass_candidate_notes:
                continue

            # Fully abstracted call
            bass_pitch_names = stream_utils.define_bass_subset(bass_candidate_notes)

            bass_attacks = []
            for n_obj, offset in bass_candidate_notes:
                if n_obj.pitch.nameWithOctave in bass_pitch_names:
                    if not bass_attacks:
                        bass_attacks.append((offset, n_obj.pitch.pitchClass))
                    else:
                        if abs(bass_attacks[-1][0] - offset) > 0.001:
                            bass_attacks.append((offset, n_obj.pitch.pitchClass))

            bass_attacks.sort(key=lambda x: x[0])

            merged_boundaries = []
            if bass_attacks:
                merged_boundaries.append(bass_attacks[0][0])
                current_pc = bass_attacks[0][1]
                for i in range(1, len(bass_attacks)):
                    off, pc = bass_attacks[i]
                    if pc != current_pc:
                        merged_boundaries.append(off)
                        current_pc = pc

            merged_boundaries.append(measure_end)

            for i in range(len(merged_boundaries) - 1):
                w_start = merged_boundaries[i]
                w_end = merged_boundaries[i + 1]

                window_notes = stream_utils.get_all_notes_in_range(
                    self.stream, w_start, w_end
                )
                if not window_notes:
                    continue

                current_bass_note = stream_utils.find_lowest_at_offset(
                    window_notes, w_start
                )
                if not current_bass_note:
                    continue

                unique_pitches = set(n.pitch.nameWithOctave for n, o in window_notes)
                notes_pool = window_notes
                if len(unique_pitches) <= 3 and previous_window_notes:
                    notes_pool = window_notes + previous_window_notes

                scaled_timestamp = w_start * self.time_scale
                chord_symbol = self._construct_chord_iteratively(
                    current_bass_note, notes_pool, scaled_timestamp
                )

                if chord_symbol and chord_symbol != last_chord_str:
                    mins = int(scaled_timestamp // 60)
                    secs = scaled_timestamp % 60
                    formatted_time = f"[{mins:02d}:{secs:06.3f}]"
                    self.extracted_data.append(
                        (formatted_time, chord_symbol, float(w_start))
                    )
                    last_chord_str = chord_symbol

                previous_window_notes = window_notes

    def _get_quality_rank(self, symbol, pitch_count):
        if pitch_count == 1:
            return 1
        if symbol == "Chord Symbol" or symbol == "Chord Symbol Cannot Be Identified":
            return -1
        if any(x in symbol for x in ["9", "11", "13", "add"]):
            return 0
        if symbol == "pedal":
            return 1
        if symbol == "power":
            return 2
        return 3

    def _construct_chord_iteratively(self, bass_note, notes_pool, timestamp):
        pc_stats = {}
        max_dur = 0.0
        for n, off in notes_pool:
            pc = n.pitch.pitchClass
            if pc not in pc_stats:
                pc_stats[pc] = {"dur": 0.0, "min_ps": 1000}
            pc_stats[pc]["dur"] += n.duration.quarterLength
            if n.pitch.ps < pc_stats[pc]["min_ps"]:
                pc_stats[pc]["min_ps"] = n.pitch.ps
            if pc_stats[pc]["dur"] > max_dur:
                max_dur = pc_stats[pc]["dur"]

        if max_dur == 0:
            max_dur = 1.0
        priority_list = []
        for pc, stats in pc_stats.items():
            dur_ratio = stats["dur"] / max_dur
            bonus = dur_ratio * 4.9
            score = stats["min_ps"] - bonus
            priority_list.append((pc, score))
        priority_list.sort(key=lambda x: x[1])
        priority_pcs = [x[0] for x in priority_list]

        bass_pc = bass_note.pitch.pitchClass
        if bass_pc in priority_pcs:
            priority_pcs.remove(bass_pc)

        best_symbol = f"{bass_note.pitch.name} pedal"
        best_rank = 1
        current_notes = [bass_note]

        # mins = int(timestamp // 60)
        # secs = timestamp % 60
        # print(
        #     f"[DEBUG] T: {mins:02d}:{secs:06.3f} | Bass: {bass_note.pitch.nameWithOctave}"
        # )

        limit = min(len(priority_pcs) + 1, 7)

        for i in range(limit):
            if i > 0:
                pc_to_add = priority_pcs[i - 1]
                new_n = music21.note.Note()
                new_n.pitch.ps = pc_stats[pc_to_add]["min_ps"]
                current_notes.append(new_n)

            test_chord = music21.chord.Chord(current_notes)

            try:
                symbol = music21.harmony.chordSymbolFigureFromChord(test_chord)
            except:
                symbol = "Chord Symbol Cannot Be Identified"

            if symbol == "Chord Symbol Cannot Be Identified":
                temp_chord = copy.deepcopy(test_chord)
                temp_chord.simplifyEnharmonics(inPlace=True)
                try:
                    symbol = music21.harmony.chordSymbolFigureFromChord(temp_chord)
                except:
                    pass

            unique_pcs = len(set(n.pitch.pitchClass for n in current_notes))

            if symbol == "Chord Symbol Cannot Be Identified" and unique_pcs == 2:
                bass_p = current_notes[0].pitch
                added_p = current_notes[-1].pitch
                diff = (added_p.pitchClass - bass_p.pitchClass) % 12
                if diff == 3:
                    symbol = f"{bass_p.name}m"
                elif diff == 4:
                    symbol = f"{bass_p.name}"
                elif diff == 7:
                    symbol = f"{bass_p.name}power"

            rank = self._get_quality_rank(symbol, unique_pcs)

            # note_names = [n.pitch.name for n in current_notes]
            # print(f"   + Try {note_names} -> '{symbol}' (Rank {rank})")

            if rank == 0:
                break
            if rank == -1:
                continue
            if rank >= best_rank:
                best_symbol = symbol
                best_rank = rank

        # print(f"   => MATCH: {best_symbol}")
        return best_symbol
