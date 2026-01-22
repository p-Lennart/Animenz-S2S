# v15.1 - Final + Toggle
# 1. Added ENABLE_DYNAMIC_EXCLUSION flag.
#    - True: Run exclusion logic (remove voices based on dynamics).
#    - False: Skip exclusion logic entirely (safest for poorly marked scores).
# 2. Dynamic tracking still runs to help choose S vs T in "Safe Mode", but won't delete notes.

import music21
import copy
import os
import glob
import statistics
from pathlib import Path

# ==========================================
# CONFIGURATION
# ==========================================
INPUT_DIR = "./xml"
STAGING_DIR = "./staging"
DEBUG = False

# Logic Toggles
AMBIGUITY_LOOKAHEAD = 1
ENABLE_DYNAMIC_EXCLUSION = True  # Set False to disable voice removal
DYNAMIC_EXCLUSION_LIMIT = 8

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
    # CORE LOGIC: GRAND STAFF + STAMP & FLATTEN
    # =========================================================================
    def _collect_grand_staff_measures(self, stream_obj):
        all_measures_by_part = []
        for p in stream_obj.parts:
            all_measures_by_part.append(list(p.getElementsByClass("Measure")))

        if not all_measures_by_part:
            return []
        max_len = max(len(m_list) for m_list in all_measures_by_part)

        system_measures = []
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
                "all_dynamics": [],
            }

            # 1. Metadata & Stamping
            for p_idx, m_list in enumerate(all_measures_by_part):
                if i < len(m_list):
                    m = m_list[i]
                    if sys_m["number"] == 0:
                        sys_m["number"] = self._safe_measure_number(m.number)
                    if m.timeSignature:
                        current_ts = copy.deepcopy(m.timeSignature)
                    if m.keySignature:
                        current_ks = copy.deepcopy(m.keySignature)

                    # Notes
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

                        # Dynamics
                        if isinstance(el, music21.dynamics.Dynamic):
                            s_id = self._detect_staff_context(el, "1", p_idx)
                            el.q_staff_id = s_id
                            el.q_placement = "unknown"
                            if hasattr(el, "style") and el.style:
                                try:
                                    if getattr(el.style, "placement", None):
                                        el.q_placement = el.style.placement
                                except:
                                    pass
                            if el.q_placement == "unknown" and hasattr(el, "placement"):
                                el.q_placement = el.placement

            if current_ts:
                sys_m["time_sig"] = current_ts
                sys_m["duration"] = current_ts.barDuration.quarterLength
            if current_ks:
                sys_m["key_sig"] = current_ks

            # 2. Flattening
            for p_idx, m_list in enumerate(all_measures_by_part):
                if i < len(m_list):
                    m = m_list[i]
                    flat_stream = m.flatten()

                    for n in flat_stream.notesAndRests:
                        s_id = getattr(n, "q_staff_id", 1)
                        v_id = getattr(n, "q_voice_id", "1")
                        sys_m["all_notes"].append((n, n.offset, s_id, v_id))

                    for d in flat_stream.getElementsByClass("Dynamic"):
                        s_id = getattr(d, "q_staff_id", 1)
                        placement = getattr(d, "q_placement", "unknown")
                        sys_m["all_dynamics"].append((d, s_id, placement))

            system_measures.append(sys_m)

        return system_measures

    def _detect_staff_context(self, el, voice_id, part_index):
        if hasattr(el, "style") and el.style:
            try:
                if el.style.staff:
                    return int(el.style.staff)
            except:
                pass
        if hasattr(el, "staff") and el.staff:
            try:
                return int(el.staff)
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
                    new_measure.duration = music21.duration.Duration(sys_m["duration"])
                if sys_m["key_sig"]:
                    ks = copy.deepcopy(sys_m["key_sig"])
                    if ks.sharps is None:
                        ks.sharps = 0
                    new_measure.keySignature = ks

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
        dynamic_map = self._calculate_dynamic_map(system_measures)
        padding_duration = self._calculate_padding(self.melody_stream)
        measure_num_shift = 1 if padding_duration > 0 else 0

        exclusion_state = {
            "active": False,
            "voice_id": None,
            "counter": 0,
            "last_voice_set": set(),
            "pitch_history": {},
            "excluded_voice_prev_median": None,
        }

        for i, sys_m in enumerate(system_measures):
            # 1. Determine Voice Structure & Pitches
            current_upper_voices = set()
            voice_pitches = {}

            for n, _, s_id, v_id in sys_m["all_notes"]:
                if s_id == 1:
                    current_upper_voices.add(str(v_id))
                    if str(v_id) not in voice_pitches:
                        voice_pitches[str(v_id)] = []
                    if n.isChord:
                        voice_pitches[str(v_id)].append(max(p.ps for p in n.pitches))
                    elif n.isNote:
                        voice_pitches[str(v_id)].append(n.pitch.ps)

            # Calculate Medians (with History)
            current_medians = {}
            for vid in current_upper_voices:
                pitches = voice_pitches.get(vid, [])
                if pitches:
                    median = statistics.median(pitches)
                    current_medians[vid] = median
                    exclusion_state["pitch_history"][vid] = median
                else:
                    current_medians[vid] = exclusion_state["pitch_history"].get(vid, -1)

            vol_upper, vol_lower = dynamic_map[i] if i < len(dynamic_map) else (64, 64)

            # --- EXCLUSION LOGIC START ---
            if ENABLE_DYNAMIC_EXCLUSION:
                # 2. Check Reset Triggers
                reset_reason = None

                # Rule A: Volume Equalization
                if vol_upper >= vol_lower:
                    reset_reason = "Vol Upper >= Lower"
                # Rule B: Structure Change
                elif current_upper_voices != exclusion_state["last_voice_set"]:
                    reset_reason = "Voice Structure Changed"
                # Rule C: Octave Jump (Difference > 12)
                elif exclusion_state["active"]:
                    ex_vid = str(exclusion_state["voice_id"])
                    curr_med = current_medians.get(ex_vid, -1)
                    prev_med = exclusion_state["excluded_voice_prev_median"]
                    if curr_med > -1 and prev_med is not None and prev_med > -1:
                        if abs(curr_med - prev_med) >= 12:
                            reset_reason = (
                                f"Octave Jump ({prev_med:.1f} -> {curr_med:.1f})"
                            )

                # Rule D: Time Limit (Hard Reset)
                if (
                    not reset_reason
                    and exclusion_state["counter"] >= DYNAMIC_EXCLUSION_LIMIT
                ):
                    reset_reason = "Time Limit"
                    new_vol = min(vol_upper, vol_lower)
                    vol_upper = new_vol
                    vol_lower = new_vol
                    state_to_replace = dynamic_map[i]
                    for k in range(i, len(dynamic_map)):
                        if dynamic_map[k] == state_to_replace:
                            dynamic_map[k] = (new_vol, new_vol)
                        else:
                            break

                if exclusion_state["active"] and reset_reason:
                    print(
                        f"[Dynamic Decision] Measure {sys_m['number']}: Stop excluding Voice {exclusion_state['voice_id']} ({reset_reason})"
                    )
                    exclusion_state["active"] = False
                    exclusion_state["voice_id"] = None
                    exclusion_state["counter"] = 0
                    exclusion_state["excluded_voice_prev_median"] = None

                # 3. Check Activation Trigger
                if not exclusion_state["active"] and vol_upper < vol_lower:
                    sorted_by_pitch = sorted(
                        current_medians.items(), key=lambda x: x[1], reverse=True
                    )
                    if sorted_by_pitch:
                        target_voice_id = sorted_by_pitch[0][0]
                        target_median = sorted_by_pitch[0][1]
                        if target_median > -1:
                            exclusion_state["active"] = True
                            exclusion_state["voice_id"] = target_voice_id
                            exclusion_state["counter"] = 0
                            exclusion_state["excluded_voice_prev_median"] = (
                                target_median
                            )
                            print(
                                f"[Dynamic Decision] Measure {sys_m['number']}: Excluding Voice {target_voice_id} (Median={target_median:.1f}, Vol Upper={vol_upper} < Lower={vol_lower})"
                            )

                # Update Octave Jump Tracking
                if exclusion_state["active"]:
                    ex_vid = str(exclusion_state["voice_id"])
                    if ex_vid in current_medians:
                        exclusion_state["excluded_voice_prev_median"] = current_medians[
                            ex_vid
                        ]

                # 4. Apply Filter
                if exclusion_state["active"]:
                    filtered_notes = []
                    for n, off, s_id, v_id in sys_m["all_notes"]:
                        if s_id == 1 and str(v_id) == str(exclusion_state["voice_id"]):
                            continue
                        filtered_notes.append((n, off, s_id, v_id))
                    sys_m["all_notes"] = filtered_notes
                    exclusion_state["counter"] += 1

                exclusion_state["last_voice_set"] = current_upper_voices
            # --- EXCLUSION LOGIC END ---

            # 5. Process Measure
            measure_content = {"S": [], "A": [], "T": [], "B": []}
            voice_map = self._map_system_notes(sys_m["all_notes"], bake_tremolos=False)
            is_ambiguous = self._check_ambiguity_lookahead(system_measures, i)

            if is_ambiguous:
                marker = music21.expressions.TextExpression("CHECK HERE")
                measure_content["S"].append((marker, 0.0))
                for role, notes in voice_map.items():
                    measure_content[role].extend(notes)
            else:
                target_staff = 1
                if vol_upper < vol_lower:
                    target_staff = 2

                if target_staff == 1:
                    measure_content["S"] = voice_map["S"]
                else:
                    measure_content["T"] = voice_map["T"]

            for role in ["S", "A", "T", "B"]:
                new_measure = music21.stream.Measure()
                new_measure.number = sys_m["number"] + measure_num_shift

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

    def _calculate_dynamic_map(self, system_measures):
        current_upper = 64
        current_lower = 64
        dynamic_map = []
        for sys_m in system_measures:
            for dyn, s_id, placement in sys_m["all_dynamics"]:
                val = 64
                if hasattr(dyn, "value"):
                    val = DYNAMICS_MAP.get(dyn.value, 64)
                if s_id == 1:
                    if placement == "above":
                        current_upper = val
                    else:
                        current_upper = val
                        current_lower = val
                elif s_id == 2:
                    current_lower = val
            dynamic_map.append((current_upper, current_lower))
        return dynamic_map

    def _check_ambiguity_lookahead(self, system_measures, current_idx):
        end_idx = min(current_idx + AMBIGUITY_LOOKAHEAD + 1, len(system_measures))
        window = system_measures[current_idx:end_idx]
        for sys_m in window:
            staff_voices = {1: set(), 2: set()}
            for _, _, s_id, v_id in sys_m["all_notes"]:
                staff_voices[s_id].add(v_id)
            if len(staff_voices[1]) > 1 or len(staff_voices[2]) > 1:
                return True
        return False

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
                    return int("".join(filter(str.isdigit, str(k))) or 999)
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

    def _safe_measure_number(self, val):
        if val is None:
            return 0
        try:
            return int(val)
        except:
            return int("".join(filter(str.isdigit, str(val))) or 0)

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
