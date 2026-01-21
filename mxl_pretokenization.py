# v12.1
# "Stamp & Flatten" Strategy
# Solves the Time/Class Catch-22 by stamping metadata onto notes before flattening.
# Propagates Time Signatures forward to ensure every measure has a reference.
# Forces explicit .duration on ALL measures (including empty ones) to prevent 0-length collapse.

import music21
import copy
import os
import glob
from pathlib import Path

# ==========================================
# CONFIGURATION
# ==========================================
INPUT_DIR = "./test"
STAGING_DIR = "./staging"
DEBUG = False

DYNAMICS_MAP = {
    "pppp": 10,
    "ppp": 20,
    "pp": 30,
    "p": 40,
    "mp": 50,
    "mf": 60,
    "f": 70,
    "ff": 80,
    "fff": 90,
    "ffff": 100,
    "sf": 85,
    "sfz": 85,
    "fp": 50,
}


def clean_filename(fname: str) -> str:
    return "".join(c for c in fname if c.isalnum() or c in (" ", "_", "-")).strip()


def ensure_dir(d: str):
    os.makedirs(d, exist_ok=True)


class MxlPretokenizer:
    def __init__(self, xml_path):
        self.xml_path = xml_path
        try:
            self.score = music21.converter.parse(xml_path, format="musicxml")
        except Exception as e:
            print(f"CRITICAL: Could not parse {xml_path}: {e}")
            self.score = music21.stream.Score()

        raw_name = Path(xml_path).stem
        self.score_name = clean_filename(raw_name)

        try:
            self.score = self.score.expandRepeats()
        except Exception as e:
            print(
                f"WARNING: Could not expand repeats for {self.score_name}. Processing linear score."
            )

        self.literal_stream = copy.deepcopy(self.score)
        self.melody_stream = copy.deepcopy(self.score)

    def process(self):
        if not self.literal_stream.parts:
            print(f"SKIPPING {self.score_name}: No parts found.")
            return

        print(f"Processing: {self.score_name}")

        try:
            literal_midi = self._generate_literal_midi()
            if literal_midi:
                out_path = os.path.join(STAGING_DIR, f"{self.score_name}_LITERAL.mid")
                literal_midi.write("midi", fp=out_path)
        except Exception as e:
            import traceback

            traceback.print_exc()
            print(f"ERROR processing LITERAL MIDI for {self.score_name}: {e}")

        try:
            melody_midi = self._generate_melody_draft()
            if melody_midi:
                out_path = os.path.join(
                    STAGING_DIR, f"{self.score_name}_MELODY_DRAFT.mid"
                )
                melody_midi.write("midi", fp=out_path)
        except Exception as e:
            import traceback

            traceback.print_exc()
            print(f"ERROR processing MELODY MIDI for {self.score_name}: {e}")

    # =========================================================================
    # CORE LOGIC
    # =========================================================================
    def _collect_grand_staff_measures(self, stream_obj):
        all_measures_by_part = []
        for p in stream_obj.parts:
            all_measures_by_part.append(list(p.getElementsByClass("Measure")))

        if not all_measures_by_part:
            return []
        max_len = max(len(m_list) for m_list in all_measures_by_part)

        system_measures = []

        # State tracking for propagation
        current_ts = None
        current_ks = None

        for i in range(max_len):
            sys_m = {
                "index": i,
                "number": 0,
                "time_sig": None,
                "key_sig": None,
                "duration": 0.0,
                "all_notes": [],
            }

            # First pass: Metadata collection and Note Stamping
            for p_idx, m_list in enumerate(all_measures_by_part):
                if i < len(m_list):
                    m = m_list[i]

                    if sys_m["number"] == 0:
                        sys_m["number"] = self._safe_measure_number(m.number)

                    # Capture Metadata
                    if m.timeSignature:
                        current_ts = copy.deepcopy(m.timeSignature)
                    if m.keySignature:
                        current_ks = copy.deepcopy(m.keySignature)

                    # --- STAMPING ---
                    for v in m.voices:
                        v_id = v.id
                        for n in v.notesAndRests:
                            s_id = self._detect_staff_context(n, v_id, p_idx)
                            n.q_staff_id = s_id
                            n.q_voice_id = str(v_id)

                    for el in m.elements:
                        if isinstance(
                            el, (music21.note.GeneralNote, music21.note.Rest)
                        ):
                            if not hasattr(el, "q_voice_id"):
                                v_id_fallback = "1"
                                if hasattr(el, "voice"):
                                    val = (
                                        el.voice.id
                                        if hasattr(el.voice, "id")
                                        else el.voice
                                    )
                                    if val:
                                        v_id_fallback = str(val)

                                s_id = self._detect_staff_context(
                                    el, v_id_fallback, p_idx
                                )
                                el.q_staff_id = s_id
                                el.q_voice_id = v_id_fallback

            # Assign Propagated Metadata
            if current_ts:
                sys_m["time_sig"] = current_ts
                sys_m["duration"] = current_ts.barDuration.quarterLength

            if current_ks:
                sys_m["key_sig"] = current_ks

            # Second pass: Flattening
            for p_idx, m_list in enumerate(all_measures_by_part):
                if i < len(m_list):
                    m = m_list[i]
                    flat_stream = m.flatten()
                    flat_notes = flat_stream.notesAndRests

                    for n in flat_notes:
                        s_id = getattr(n, "q_staff_id", 1)
                        v_id = getattr(n, "q_voice_id", "1")
                        sys_m["all_notes"].append((n, n.offset, s_id, v_id))

            system_measures.append(sys_m)

        return system_measures

    def _detect_staff_context(self, note, voice_id, part_index):
        if hasattr(note, "style") and note.style:
            try:
                if note.style.staff:
                    return int(note.style.staff)
            except:
                pass
        if hasattr(note, "staff") and note.staff:
            try:
                return int(note.staff)
            except:
                pass

        digits = "".join(filter(str.isdigit, str(voice_id)))
        if digits and int(digits) >= 5:
            return 2

        if part_index == 1:
            return 2
        return 1

    # =========================================================================
    # PIPELINE 1: LITERAL MIDI
    # =========================================================================
    def _generate_literal_midi(self):
        output_score = music21.stream.Score()
        parts = {
            "S": music21.stream.Part(),
            "A": music21.stream.Part(),
            "T": music21.stream.Part(),
            "B": music21.stream.Part(),
        }

        system_measures = self._collect_grand_staff_measures(self.literal_stream)
        if not system_measures:
            return None

        padding_duration = self._calculate_padding(self.literal_stream)
        measure_num_shift = 1 if padding_duration > 0 else 0

        for sys_m in system_measures:
            voice_map = self._map_system_notes(sys_m["all_notes"], bake_tremolos=True)

            for role, note_data in voice_map.items():
                new_measure = music21.stream.Measure()
                new_measure.number = sys_m["number"] + measure_num_shift

                if sys_m["time_sig"]:
                    new_measure.timeSignature = copy.deepcopy(sys_m["time_sig"])
                    # FIX: Wrap float in Duration object
                    new_measure.duration = music21.duration.Duration(sys_m["duration"])

                if sys_m["key_sig"]:
                    ks = copy.deepcopy(sys_m["key_sig"])
                    if ks.sharps is None:
                        ks.sharps = 0
                    new_measure.keySignature = ks

                # Padding Logic
                note_shift = 0.0
                if sys_m["index"] == 0 and padding_duration > 0:
                    pad_rest = music21.note.Rest()
                    pad_rest.duration.quarterLength = padding_duration
                    new_measure.insert(0.0, pad_rest)
                    note_shift = padding_duration

                    if new_measure.timeSignature:
                        new_measure.duration = music21.duration.Duration(
                            new_measure.timeSignature.barDuration.quarterLength
                        )

                for note, original_offset in note_data:
                    if note.duration.isGrace:
                        continue
                    final_note = copy.deepcopy(note)
                    new_measure.insert(original_offset + note_shift, final_note)

                parts[role].append(new_measure)

        for role in ["S", "A", "T", "B"]:
            output_score.insert(0, parts[role])

        return output_score

    # =========================================================================
    # PIPELINE 2: MELODY DRAFT
    # =========================================================================
    def _generate_melody_draft(self):
        output_score = music21.stream.Score()
        parts = {
            "S": music21.stream.Part(),
            "A": music21.stream.Part(),
            "T": music21.stream.Part(),
            "B": music21.stream.Part(),
        }

        system_measures = self._collect_grand_staff_measures(self.melody_stream)
        padding_duration = self._calculate_padding(self.melody_stream)
        measure_num_shift = 1 if padding_duration > 0 else 0

        for i, sys_m in enumerate(system_measures):
            measure_content = {"S": [], "A": [], "T": [], "B": []}

            voice_map = self._map_system_notes(sys_m["all_notes"], bake_tremolos=False)
            is_ambiguous = self._check_ambiguity_system(sys_m["all_notes"])

            if is_ambiguous:
                marker = music21.expressions.TextExpression("CHECK HERE")
                measure_content["S"].append((marker, 0.0))
                for role, notes in voice_map.items():
                    measure_content[role].extend(notes)
            else:
                target_staff = 1
                if target_staff == 1:
                    measure_content["S"] = voice_map["S"]
                else:
                    measure_content["T"] = voice_map["T"]

            for role in ["S", "A", "T", "B"]:
                new_measure = music21.stream.Measure()
                new_measure.number = sys_m["number"] + measure_num_shift

                # FIX: Wrap float in Duration object
                if sys_m["time_sig"]:
                    new_measure.timeSignature = copy.deepcopy(sys_m["time_sig"])
                    new_measure.duration = music21.duration.Duration(sys_m["duration"])

                if sys_m["key_sig"]:
                    ks = copy.deepcopy(sys_m["key_sig"])
                    if ks.sharps is None:
                        ks.sharps = 0
                    new_measure.keySignature = ks

                note_shift = 0.0
                if i == 0 and padding_duration > 0:
                    pad_rest = music21.note.Rest()
                    pad_rest.duration.quarterLength = padding_duration
                    new_measure.insert(0.0, pad_rest)
                    note_shift = padding_duration

                    if new_measure.timeSignature:
                        new_measure.duration = music21.duration.Duration(
                            new_measure.timeSignature.barDuration.quarterLength
                        )

                for item, original_offset in measure_content[role]:
                    if isinstance(item, music21.expressions.TextExpression):
                        new_measure.insert(original_offset + note_shift, item)
                        continue

                    if item.duration.isGrace:
                        continue

                    cleaned = self._clean_melody_note(item)
                    new_measure.insert(original_offset + note_shift, cleaned)

                parts[role].append(new_measure)

        for role in ["S", "A", "T", "B"]:
            output_score.insert(0, parts[role])

        return output_score

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _map_system_notes(self, all_notes, bake_tremolos=False):
        staff_groups = {1: {}, 2: {}}

        for n, offset, s_id, v_id in all_notes:
            if s_id not in staff_groups:
                s_id = 1
            if v_id not in staff_groups[s_id]:
                staff_groups[s_id][v_id] = []
            staff_groups[s_id][v_id].append((n, offset))

        output = {"S": [], "A": [], "T": [], "B": []}

        for s_num, role_main, role_extra in [(1, "S", "A"), (2, "T", "B")]:
            v_ids = list(staff_groups[s_num].keys())
            if not v_ids:
                continue

            def sort_key(k):
                try:
                    digits = "".join(filter(str.isdigit, str(k)))
                    return int(digits) if digits else 999
                except:
                    return 999

            v_ids.sort(key=sort_key)
            main_id = v_ids[0]

            output[role_main].extend(staff_groups[s_num][main_id])
            for extra_id in v_ids[1:]:
                output[role_extra].extend(staff_groups[s_num][extra_id])

        if bake_tremolos:
            for key in output:
                raw_tuples = output[key]
                if not raw_tuples:
                    continue

                temp_stream = music21.stream.Stream()
                for n, off in raw_tuples:
                    temp_stream.insert(off, n)
                try:
                    expanded = temp_stream.expandTremolos()
                    new_tuples = []
                    for n in expanded.notesAndRests:
                        new_tuples.append((n, n.offset))
                    output[key] = new_tuples
                except:
                    output[key] = raw_tuples

        return output

    def _check_ambiguity_system(self, all_notes):
        staff_voices = {1: set(), 2: set()}
        for _, _, s_id, v_id in all_notes:
            staff_voices[s_id].add(v_id)
        if len(staff_voices[1]) > 1 or len(staff_voices[2]) > 1:
            return True
        return False

    def _safe_measure_number(self, val):
        if val is None:
            return 0
        try:
            return int(val)
        except:
            digits = "".join(filter(str.isdigit, str(val)))
            return int(digits) if digits else 0

    def _clean_melody_note(self, element):
        new_note = None
        if element.isChord:
            element.sortAscending(inPlace=True)
            top_pitch = element.pitches[-1]
            new_note = music21.note.Note(top_pitch)
            new_note.duration = element.duration
        elif isinstance(element, music21.note.Note):
            new_note = copy.deepcopy(element)
        else:
            return copy.deepcopy(element)

        if hasattr(element, "tie") and element.tie is not None:
            new_note.tie = copy.deepcopy(element.tie)
        return new_note

    def _calculate_padding(self, stream):
        try:
            parts = stream.parts
            if not parts:
                return 0.0
            measures = parts[0].getElementsByClass("Measure")
            if not measures:
                return 0.0
            m1 = measures[0]
            ts = (
                m1.timeSignature
                or stream.recurse().getElementsByClass("TimeSignature")[0]
            )
            if not ts or not m1.duration:
                return 0.0
            bar_dur = ts.barDuration.quarterLength
            current_dur = m1.duration.quarterLength
            if bar_dur is None or current_dur is None:
                return 0.0
            if current_dur < bar_dur and current_dur > 0:
                return float(bar_dur - current_dur)
            return 0.0
        except:
            return 0.0


def main():
    ensure_dir(STAGING_DIR)
    files = glob.glob(os.path.join(INPUT_DIR, "*.musicxml")) + glob.glob(
        os.path.join(INPUT_DIR, "*.mxl")
    )
    print(f"Found {len(files)} files in {INPUT_DIR}")
    for xml_file in files:
        try:
            processor = MxlPretokenizer(xml_file)
            processor.process()
        except Exception as e:
            import traceback

            traceback.print_exc()
            print(f"FAILED to process {xml_file}: {e}")


if __name__ == "__main__":
    main()
