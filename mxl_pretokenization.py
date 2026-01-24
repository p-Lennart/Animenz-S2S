# v24.0 - Tenuto Protection & Refined Rules
# 1. Adds _precalculate_tenuto_counts to map Lower Staff tenutos per measure.
# 2. Implements "Partial Protection": If Lower Staff has dense tenutos, those specific notes are added to the draft
#    even if Safe Mode selected the Upper Staff. This preserves melody without swapping staff entirely.

import music21
import copy
import os
import glob
import statistics
from pathlib import Path
from pretoken_melody_reduction import MelodyReducer

# ==========================================
# CONFIGURATION
# ==========================================
INPUT_DIR = "./test"
STAGING_DIR = "./staging"
DEBUG = True

AMBIGUITY_LOOKAHEAD = 2
ENABLE_DYNAMIC_EXCLUSION = True
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

        self.log_data = []

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
                print(f"Running Melody Reducer for {self.score_name}...")
                reducer = MelodyReducer(melody_midi, self.log_data)
                reducer.process()

                out_path = os.path.join(
                    STAGING_DIR, f"{self.score_name}_MELODY_DRAFT.mid"
                )
                melody_midi.write("midi", fp=out_path)

                self._write_changelog()

        except Exception as e:
            import traceback

            traceback.print_exc()
            print(f"ERROR processing MELODY MIDI for {self.score_name}: {e}")

    def _write_changelog(self):
        log_path = os.path.join(STAGING_DIR, f"{self.score_name}_CHANGELOG.txt")
        self.log_data.sort(key=lambda x: x["start_m"])
        with open(log_path, "w") as f:
            f.write(f"Melody Extraction Changelog: {self.score_name}\n")
            f.write("=" * 80 + "\n\n")
            for entry in self.log_data:
                s = entry["start_m"]
                e = entry["end_m"]
                if s != e and e > 0:
                    m_str = f"[Mm. {s}-{e}]"
                else:
                    m_str = f"[M.  {s}]"
                p_str = f"[{entry['part']}]"
                line = f"{m_str:<15} {p_str:<5} {entry['text']}"
                f.write(line + "\n")

    # ... [CORE LOGIC & LITERAL MIDI (Identical to previous)] ...
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
            for p_idx, m_list in enumerate(all_measures_by_part):
                if i < len(m_list):
                    m = m_list[i]
                    if sys_m["number"] == 0:
                        sys_m["number"] = self._safe_measure_number(m.number)
                    if m.timeSignature:
                        current_ts = copy.deepcopy(m.timeSignature)
                    if m.keySignature:
                        current_ks = copy.deepcopy(m.keySignature)
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
                        sys_m["all_dynamics"].append((d, d.offset, s_id, placement))
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

    def _generate_literal_midi(self):
        output_score = music21.stream.Score()
        parts = {
            "S": music21.stream.Part(),
            "A": music21.stream.Part(),
            "T": music21.stream.Part(),
            "B": music21.stream.Part(),
        }
        for role, p in parts.items():
            p.id = role
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
        for role, p in parts.items():
            p.id = role

        system_measures = self._collect_grand_staff_measures(self.melody_stream)
        start_states = self._calculate_start_volumes(system_measures)
        tenuto_counts = self._precalculate_tenuto_counts(system_measures)  # NEW

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
        exclusion_start_measure = None
        exclusion_start_vals = (0, 0)
        has_seen_upper_dynamic = False

        for i, sys_m in enumerate(system_measures):
            # ... [Static Analysis Same as v22] ...
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
            current_medians = {}
            for vid in current_upper_voices:
                pitches = voice_pitches.get(vid, [])
                if pitches:
                    median = statistics.median(pitches)
                    current_medians[vid] = median
                    exclusion_state["pitch_history"][vid] = median
                else:
                    current_medians[vid] = exclusion_state["pitch_history"].get(vid, -1)
            target_voice_id = None
            target_median = -1
            sorted_by_pitch = sorted(
                current_medians.items(), key=lambda x: x[1], reverse=True
            )
            if sorted_by_pitch:
                target_voice_id = sorted_by_pitch[0][0]
                target_median = sorted_by_pitch[0][1]
            measure_has_upper_dyn = any(
                d_sid == 1 for _, _, d_sid, _ in sys_m["all_dynamics"]
            )

            # ... [Timeline Logic Same as v22] ...
            timeline = []
            for dyn, offset, s_id, placement in sys_m["all_dynamics"]:
                timeline.append((offset, 0, (dyn, s_id, placement)))
            for note, offset, s_id, v_id in sys_m["all_notes"]:
                timeline.append((offset, 1, (note, s_id, v_id)))
            timeline.sort(key=lambda x: (x[0], x[1]))
            curr_u, curr_l = start_states[i]
            processed_notes = []
            if current_upper_voices != exclusion_state["last_voice_set"]:
                if exclusion_state["active"]:
                    su, sl = exclusion_start_vals
                    msg = f"Dynamic Exclusion (U={su} < L={sl}) -> (Voice structure change)"
                    self.log_data.append(
                        {
                            "start_m": exclusion_start_measure,
                            "end_m": sys_m["number"] - 1,
                            "part": "S",
                            "text": msg,
                        }
                    )
                    exclusion_state["active"] = False
                    exclusion_state["voice_id"] = None
                    exclusion_state["excluded_voice_prev_median"] = None
                    exclusion_start_measure = None
            exclusion_state["last_voice_set"] = current_upper_voices
            for idx, event in enumerate(timeline):
                offset, etype, data = event
                if etype == 0:
                    dyn_obj, s_id, placement = data
                    val = DYNAMICS_MAP.get(dyn_obj.value, 64)
                    if s_id == 1:
                        has_seen_upper_dynamic = True
                        if placement == "above":
                            curr_u = val
                        else:
                            overtake_val = None
                            for k in range(idx + 1, len(timeline)):
                                f_off, f_type, f_data = timeline[k]
                                if f_off > offset + 0.25:
                                    break
                                if f_type == 0:
                                    _, f_sid, f_place = f_data
                                    if f_sid == 1 and f_place == "above":
                                        overtake_val = DYNAMICS_MAP.get(
                                            timeline[k][2][0].value, 64
                                        )
                                        break
                            curr_u = overtake_val if overtake_val else val
                            curr_l = val
                    elif s_id == 2:
                        curr_l = val
                        if not measure_has_upper_dyn:
                            curr_u = val
                    if exclusion_state["active"]:
                        ex_vid = str(exclusion_state["voice_id"])
                        curr_med_check = current_medians.get(ex_vid, -1)
                        prev_med_check = exclusion_state["excluded_voice_prev_median"]
                        if curr_med_check > -1 and prev_med_check is not None:
                            if abs(curr_med_check - prev_med_check) >= 12:
                                su, sl = exclusion_start_vals
                                msg = f"Dynamic Exclusion (U={su} < L={sl}) -> (Octave Jump)"
                                self.log_data.append(
                                    {
                                        "start_m": exclusion_start_measure,
                                        "end_m": sys_m["number"],
                                        "part": "S",
                                        "text": msg,
                                    }
                                )
                                exclusion_state["active"] = False
                                exclusion_state["voice_id"] = None
                                exclusion_start_measure = None
                    if ENABLE_DYNAMIC_EXCLUSION:
                        if curr_u >= curr_l and exclusion_state["active"]:
                            su, sl = exclusion_start_vals
                            msg = f"Dynamic Exclusion (U={su} < L={sl}) -> (Volumes Equalized)"
                            self.log_data.append(
                                {
                                    "start_m": exclusion_start_measure,
                                    "end_m": sys_m["number"],
                                    "part": "S",
                                    "text": msg,
                                }
                            )
                            exclusion_state["active"] = False
                            exclusion_state["voice_id"] = None
                            exclusion_start_measure = None
                        if curr_u < curr_l:
                            if (
                                not exclusion_state["active"]
                                and has_seen_upper_dynamic
                                and target_voice_id
                            ):
                                exclusion_state["active"] = True
                                exclusion_state["voice_id"] = target_voice_id
                                exclusion_state["excluded_voice_prev_median"] = (
                                    target_median
                                )
                                exclusion_start_measure = sys_m["number"]
                                exclusion_start_vals = (curr_u, curr_l)
                elif etype == 1:
                    note_obj, s_id, v_id = data
                    keep = True
                    if (
                        exclusion_state["active"]
                        and s_id == 1
                        and str(v_id) == str(exclusion_state["voice_id"])
                    ):
                        keep = False
                    if keep:
                        processed_notes.append((note_obj, offset, s_id, v_id))

            measure_content = {"S": [], "A": [], "T": [], "B": []}
            voice_map = self._map_system_notes(processed_notes, bake_tremolos=False)
            is_ambiguous = self._check_ambiguity_lookahead(system_measures, i)

            if is_ambiguous:
                marker = music21.expressions.TextExpression("CHECK HERE")
                measure_content["S"].append((marker, 0.0))
                for role, notes in voice_map.items():
                    measure_content[role].extend(notes)
            else:
                target_staff = 1
                if ENABLE_DYNAMIC_EXCLUSION and curr_u < curr_l:
                    target_staff = 2
                if target_staff == 1:
                    measure_content["S"] = voice_map["S"]
                else:
                    measure_content["T"] = voice_map["T"]

                # --- NEW: Tenuto Protection (Rule 3) ---
                # "Do not exclude lower staff if..." -> Merge lower tenutos into draft
                # Criteria: 3+ tenutos in current, or (>1 current and 3+ adjacent)
                count_curr = tenuto_counts[i]
                count_prev = tenuto_counts[i - 1] if i > 0 else 0
                count_next = tenuto_counts[i + 1] if i < len(tenuto_counts) - 1 else 0

                lower_protected = False
                if count_curr >= 3:
                    lower_protected = True
                elif count_curr > 1 and (count_prev >= 3 or count_next >= 3):
                    lower_protected = True

                if lower_protected and target_staff == 1:
                    # Inject Tenuto notes from Lower Main Voice
                    # We look at voice_map['T'] which contains candidates for T track
                    for note, off in voice_map["T"]:
                        if hasattr(note, "articulations"):
                            if any(
                                isinstance(a, music21.articulations.Tenuto)
                                for a in note.articulations
                            ):
                                measure_content["T"].append((note, off))
                    # Log it
                    self.log_data.append(
                        {
                            "start_m": sys_m["number"],
                            "end_m": sys_m["number"],
                            "part": "T",
                            "text": f"Safe Mode: Merged Lower Staff Tenutos (Count={count_curr})",
                        }
                    )

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

        if exclusion_state["active"]:
            su, sl = exclusion_start_vals
            msg = f"Dynamic Exclusion (U={su} < L={sl}) -> (End of Score)"
            self.log_data.append(
                {
                    "start_m": exclusion_start_measure,
                    "end_m": system_measures[-1]["number"],
                    "part": "S",
                    "text": msg,
                }
            )

        for role in ["S", "A", "T", "B"]:
            output_score.insert(0, parts[role])
        return output_score

    def _precalculate_tenuto_counts(self, system_measures):
        """Counts Tenuto articulations in the Lower Staff Main Voice (Staff 2, Voice 'Main') per measure."""
        counts = []
        for sys_m in system_measures:
            # We must simulate _map_system_notes logic to find Lower Main Voice
            # Logic: Group by Staff -> Sort Voices -> Pick First
            staff_2_notes = []
            staff_groups = {2: {}}
            for n, _, s_id, v_id in sys_m["all_notes"]:
                if s_id == 2:
                    if v_id not in staff_groups[2]:
                        staff_groups[2][v_id] = []
                    staff_groups[2][v_id].append(n)

            main_voice_notes = []
            if staff_groups[2]:
                # Sort keys similar to _map_system_notes
                v_ids = list(staff_groups[2].keys())

                def sort_key(k):
                    try:
                        return int("".join(filter(str.isdigit, str(k))) or 999)
                    except:
                        return 999

                v_ids.sort(key=sort_key)
                main_voice_notes = staff_groups[2][v_ids[0]]

            # Count Tenutos
            c = 0
            for n in main_voice_notes:
                if hasattr(n, "articulations"):
                    if any(
                        isinstance(a, music21.articulations.Tenuto)
                        for a in n.articulations
                    ):
                        c += 1
            counts.append(c)
        return counts

    # ... [OTHER HELPERS IDENTICAL TO v22.0] ...
    def _calculate_start_volumes(self, system_measures):
        start_states = []
        curr_u = 64
        curr_l = 64
        exclusion_counter = 0
        exclusion_active = False
        sim_has_seen_upper = False
        for sys_m in system_measures:
            start_states.append((curr_u, curr_l))
            sys_m["all_dynamics"].sort(key=lambda x: x[1])
            measure_has_upper = any(
                d_sid == 1 for _, _, d_sid, _ in sys_m["all_dynamics"]
            )
            for dyn, offset, s_id, placement in sys_m["all_dynamics"]:
                val = DYNAMICS_MAP.get(dyn.value, 64)
                if s_id == 1:
                    sim_has_seen_upper = True
                    if placement == "above":
                        curr_u = val
                    else:
                        curr_u = val
                        curr_l = val
                elif s_id == 2:
                    curr_l = val
                    if not measure_has_upper:
                        curr_u = val
            trigger = (curr_u < curr_l) and sim_has_seen_upper
            if trigger:
                exclusion_active = True
                exclusion_counter += 1
            else:
                exclusion_active = False
                exclusion_counter = 0
            if (
                ENABLE_DYNAMIC_EXCLUSION
                and exclusion_active
                and exclusion_counter >= DYNAMIC_EXCLUSION_LIMIT
            ):
                reset_val = min(curr_u, curr_l)
                curr_u = reset_val
                curr_l = reset_val
                exclusion_active = False
                exclusion_counter = 0
        return start_states

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
