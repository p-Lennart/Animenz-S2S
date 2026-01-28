# v34.0 - Production Checkpoint (Commented for CLI)
# This file handles the primary pipeline:
# 1. Parsing MusicXML (with edge-case handling for Ghost/Split measures).
# 2. Generating LITERAL_MIDI (Structural/Repeats).
# 3. Generating MELODY_DRAFT (Grand Staff Reduction + Post-processing).

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
INPUT_DIR = "./xml"
STAGING_DIR = "./staging"
DEBUG = False

# --- Master Switch ---
ENABLE_MELODY_REDUCER = True     # If False, skips ostinato/run simplification (Raw Draft only)

# --- Melody Extraction Rules (Logic) ---
AMBIGUITY_LOOKAHEAD = 1          # (Legacy) Check ahead for voice ambiguity
ENABLE_DYNAMIC_EXCLUSION = True  # Enable Upper < Lower volume filtering
DYNAMIC_EXCLUSION_LIMIT = 8      # Max measures to exclude before forced reset
LOWER_TENUTO_THRESHOLD = 3       # Lower Staff Tenutos required to trigger "Safe Mode" protection
SAFE_MODE_PRESERVE_LOWER = False # If True, ALWAYS keep Lower Staff in safe mode (never discard)

# --- Run Simplification Rules (Passed to Reducer) ---
RUN_MIN_NOTES = 8                # Minimum length (notes) to detect a run
RUN_MIN_NOTES_PER_BEAT = 3.0     # Minimum density (notes/beat) to gate run detection
RUN_DURATION_TOLERANCE_RATIO = 2.5 # Max allowed duration difference (e.g. 16th vs dotted 8th)

# --- Selection Toggles (Granular) ---
RUN_INCLUDE_START_NOTE = False   # Keep the very first note?
RUN_INCLUDE_END_NOTE = False     # Keep the very last note?
RUN_INCLUDE_ARTICULATED = True   # Keep accents/tenutos?
RUN_BOUNDARY_SKIP = -1           # Beat boundary stride (-1 to disable)
RUN_LEAP_THRESHOLD = 99          # Semitones jump to keep (99 to disable)

# --- Ostinato Config ---
OSTINATO_ALWAYS_REPLACE = False  # If True, replace ostinato with high note instead of deleting

# Dynamics Map (Volume weights for Exclusion Logic)
DYNAMICS_MAP = {
    'pppp': 10, 'ppp': 20, 'pp': 30, 'p': 40, 'mp': 50,
    'mf': 60, 'f': 70, 'ff': 80, 'fff': 90, 'ffff': 100,
    'sf': 85, 'sfz': 85, 'fp': 50
}

def clean_filename(fname: str) -> str:
    """ASCII-Strict sanitization for cross-platform safety."""
    valid_chars = set(" -_")
    cleaned = "".join(c for c in fname if (c.isalnum() and c.isascii()) or c in valid_chars)
    return cleaned.strip()

def ensure_dir(d: str):
    os.makedirs(d, exist_ok=True)

class MxlPretokenizer:
    def __init__(self, xml_path):
        self.xml_path = xml_path
        # Music21 Parsing
        try:
            self.score = music21.converter.parse(xml_path, format='musicxml')
        except Exception as e:
            print(f"CRITICAL: Could not parse {xml_path}: {e}")
            self.score = music21.stream.Score()

        raw_name = Path(xml_path).stem
        self.score_name = clean_filename(raw_name)
        self.log_data = [] # Shared log for Changelog
        
        # Expand Repeats (Crucial for 1:1 Audio Alignment)
        try:
            self.score = self.score.expandRepeats()
        except Exception as e:
            print(f"WARNING: Could not expand repeats for {self.score_name}. Processing linear score.")
        
        # Deep copies for separate pipelines
        self.literal_stream = copy.deepcopy(self.score)
        self.melody_stream = copy.deepcopy(self.score)

    def process(self):
        if not self.literal_stream.parts:
            print(f"SKIPPING {self.score_name}: No parts found.")
            return

        print(f"Processing: {self.score_name}")
        
        # 1. LITERAL MIDI Pipeline
        try:
            literal_midi = self._generate_literal_midi()
            if literal_midi:
                out_path = os.path.join(STAGING_DIR, f"{self.score_name}_LITERAL.mid")
                literal_midi.write('midi', fp=out_path)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"ERROR processing LITERAL MIDI for {self.score_name}: {e}")
        
        # 2. MELODY DRAFT Pipeline
        try:
            melody_midi = self._generate_melody_draft()
            if melody_midi:
                if ENABLE_MELODY_REDUCER:
                    print(f"Running Melody Reducer for {self.score_name}...")
                    reducer = MelodyReducer(melody_midi, self.log_data)
                    
                    # Inject Configuration
                    reducer.RUN_MIN_NOTES = RUN_MIN_NOTES
                    reducer.RUN_MIN_NOTES_PER_BEAT = RUN_MIN_NOTES_PER_BEAT
                    reducer.RUN_DURATION_TOLERANCE_RATIO = RUN_DURATION_TOLERANCE_RATIO
                    reducer.RUN_INCLUDE_START_NOTE = RUN_INCLUDE_START_NOTE
                    reducer.RUN_INCLUDE_END_NOTE = RUN_INCLUDE_END_NOTE
                    reducer.RUN_INCLUDE_ARTICULATED = RUN_INCLUDE_ARTICULATED
                    reducer.RUN_BOUNDARY_SKIP = RUN_BOUNDARY_SKIP
                    reducer.RUN_LEAP_THRESHOLD = RUN_LEAP_THRESHOLD
                    reducer.OSTINATO_ALWAYS_REPLACE = OSTINATO_ALWAYS_REPLACE
                    
                    reducer.process()
                else:
                    print("Melody Reducer Disabled (Skipping Ostinato/Run pass)")
                
                out_path = os.path.join(STAGING_DIR, f"{self.score_name}_MELODY_DRAFT.mid")
                melody_midi.write('midi', fp=out_path)
                self._write_changelog()
        except Exception as e:
             import traceback
             traceback.print_exc()
             print(f"ERROR processing MELODY MIDI for {self.score_name}: {e}")

    def _write_changelog(self):
        """Generates the human-readable report."""
        log_path = os.path.join(STAGING_DIR, f"{self.score_name}_CHANGELOG.txt")
        self.log_data.sort(key=lambda x: x['start_m'])
        with open(log_path, "w") as f:
            f.write(f"Melody Extraction Changelog: {self.score_name}\n")
            f.write("="*80 + "\n\n")
            for entry in self.log_data:
                s = entry['start_m']
                e = entry['end_m']
                if s != e and e > 0: m_str = f"[Mm. {s}-{e}]"
                else: m_str = f"[M.  {s}]"
                p_str = f"[{entry['part']}]"
                line = f"{p_str:<5} {m_str:<15} {entry['text']}"
                f.write(line + "\n")

    # =========================================================================
    # CORE LOGIC: Parsing & Structure
    # =========================================================================
    def _collect_grand_staff_measures(self, stream_obj):
        """
        Parses the score into a list of 'System Measures'.
        Handles alignment of Upper/Lower staves and cleans up metadata.
        """
        all_measures_by_part = []
        for p in stream_obj.parts:
            all_measures_by_part.append(list(p.getElementsByClass('Measure')))
            
        if not all_measures_by_part: return []
        max_len = max(len(m_list) for m_list in all_measures_by_part)
        
        system_measures = []
        current_ts = None
        current_ks = None
        
        for i in range(max_len):
            sys_m = {
                'index': i,
                'number': 0,
                'time_sig': None,
                'key_sig': None,
                'duration': 0.0,
                'all_notes': [],
                'all_dynamics': [] 
            }
            
            current_measures_list = []
            
            # PASS 1: Metadata & Context (Scanning Measures)
            for p_idx, m_list in enumerate(all_measures_by_part):
                if i < len(m_list):
                    m = m_list[i]
                    current_measures_list.append(m)
                    
                    if sys_m['number'] == 0: sys_m['number'] = self._safe_measure_number(m.number)
                    if m.timeSignature: current_ts = copy.deepcopy(m.timeSignature)
                    if m.keySignature: current_ks = copy.deepcopy(m.keySignature)

                    # Context Detection (Staff & Voice ID)
                    for v in m.voices:
                        v_id = v.id
                        for n in v.notesAndRests:
                            s_id = self._detect_staff_context(n, v_id, p_idx)
                            n.q_staff_id = s_id
                            n.q_voice_id = str(v_id)
                    for el in m.elements:
                        if isinstance(el, (music21.note.GeneralNote, music21.note.Rest)):
                            if not hasattr(el, 'q_voice_id'):
                                v_id_fallback = "1"
                                if hasattr(el, 'voice'):
                                    val = el.voice.id if hasattr(el.voice, 'id') else el.voice
                                    if val: v_id_fallback = str(val)
                                s_id = self._detect_staff_context(el, v_id_fallback, p_idx)
                                el.q_staff_id = s_id
                                el.q_voice_id = v_id_fallback
                        
                        if isinstance(el, music21.dynamics.Dynamic):
                            s_id = self._detect_staff_context(el, "1", p_idx)
                            el.q_staff_id = s_id
                            # Detect Dynamic Placement (Above/Below)
                            el.q_placement = 'unknown'
                            if hasattr(el, 'style') and el.style:
                                try:
                                    if getattr(el.style, 'placement', None):
                                        el.q_placement = el.style.placement
                                except: pass
                            if el.q_placement == 'unknown' and hasattr(el, 'placement'):
                                el.q_placement = el.placement

            # PASS 2: Duration Logic (Content-Aware)
            if current_ts:
                sys_m['time_sig'] = current_ts
                ts_dur = current_ts.barDuration.quarterLength
                # Verify if measure is a "Ghost" or "Split" measure
                has_content, content_end = self._analyze_measure_content(current_measures_list)
                
                if not has_content:
                    sys_m['duration'] = 0.0 # Ghost Measure -> Skip
                elif content_end < (ts_dur - 0.125):
                    sys_m['duration'] = content_end # Split Measure -> Truncate
                else:
                    sys_m['duration'] = ts_dur # Standard

            if current_ks: sys_m['key_sig'] = current_ks

            # PASS 3: Flattening (Collecting Notes)
            for p_idx, m_list in enumerate(all_measures_by_part):
                if i < len(m_list):
                    m = m_list[i]
                    flat_stream = m.flatten() # Flattens Voices into a single timeline
                    
                    for n in flat_stream.notesAndRests:
                        s_id = getattr(n, 'q_staff_id', 1)
                        v_id = getattr(n, 'q_voice_id', "1")
                        sys_m['all_notes'].append((n, n.offset, s_id, v_id))
                    
                    for d in flat_stream.getElementsByClass('Dynamic'):
                        s_id = getattr(d, 'q_staff_id', 1)
                        placement = getattr(d, 'q_placement', 'unknown')
                        sys_m['all_dynamics'].append((d, d.offset, s_id, placement))

            system_measures.append(sys_m)
            
        return system_measures

    def _analyze_measure_content(self, measures_list):
        """
        Scans measure contents to find the 'Waterline' (last active beat).
        Uses .flatten() to ensure cross-staff notes inside Voices are counted.
        """
        max_end = 0.0
        has_printable_content = False
        
        for m in measures_list:
            for el in m.flatten().notesAndRests:
                # Ignore hidden objects (Ghost detection)
                is_hidden = False
                if hasattr(el, 'printObject') and el.printObject is False: is_hidden = True
                if hasattr(el, 'style') and getattr(el.style, 'hideObjectOnPrint', False): is_hidden = True
                
                if not is_hidden:
                    has_printable_content = True
                    end_pos = el.offset + el.duration.quarterLength
                    if end_pos > max_end: max_end = end_pos
        return has_printable_content, max_end

    def _detect_staff_context(self, el, voice_id, part_index):
        """Heuristic to determine if a note belongs to Upper (1) or Lower (2) staff."""
        if hasattr(el, 'style') and el.style:
            try: 
                if el.style.staff: return int(el.style.staff)
            except: pass
        if hasattr(el, 'staff') and el.staff:
            try: return int(el.staff)
            except: pass

        # Fallback: Voice 5+ usually implies left hand in some XML dialects
        digits = "".join(filter(str.isdigit, str(voice_id)))
        if digits and int(digits) >= 5: return 2
        # Fallback: Part Index 1 is usually Left Hand
        if part_index == 1: return 2
        return 1

    # --- LITERAL MIDI PIPELINE ---
    def _generate_literal_midi(self):
        output_score = music21.stream.Score()
        parts = {'S': music21.stream.Part(), 'A': music21.stream.Part(), 
                 'T': music21.stream.Part(), 'B': music21.stream.Part()}
        for role, p in parts.items(): p.id = role
        system_measures = self._collect_grand_staff_measures(self.literal_stream)
        if not system_measures: return None
        padding_duration = self._calculate_padding(self.literal_stream)
        measure_num_shift = 1 if padding_duration > 0 else 0
        
        for sys_m in system_measures:
            voice_map = self._map_system_notes(sys_m['all_notes'], bake_tremolos=True)
            for role, note_data in voice_map.items():
                new_measure = music21.stream.Measure()
                new_measure.number = sys_m['number'] + measure_num_shift
                if sys_m['time_sig']:
                    new_measure.timeSignature = copy.deepcopy(sys_m['time_sig'])
                    new_measure.duration = music21.duration.Duration(sys_m['duration'])
                if sys_m['key_sig']:
                    ks = copy.deepcopy(sys_m['key_sig'])
                    if ks.sharps is None: ks.sharps = 0
                    new_measure.keySignature = ks
                note_shift = 0.0
                if sys_m['index'] == 0 and padding_duration > 0:
                    pad_rest = music21.note.Rest()
                    pad_rest.duration.quarterLength = padding_duration
                    new_measure.insert(0.0, pad_rest)
                    note_shift = padding_duration
                    if new_measure.timeSignature:
                        new_measure.duration = music21.duration.Duration(new_measure.timeSignature.barDuration.quarterLength)
                for note, original_offset in note_data:
                    if note.duration.isGrace: continue
                    final_note = copy.deepcopy(note)
                    new_measure.insert(original_offset + note_shift, final_note)
                parts[role].append(new_measure)
        for role in ['S', 'A', 'T', 'B']:
            output_score.insert(0, parts[role])
        return output_score

    # --- MELODY DRAFT PIPELINE ---
    def _generate_melody_draft(self):
        output_score = music21.stream.Score()
        parts = {'S': music21.stream.Part(), 'A': music21.stream.Part(), 
                 'T': music21.stream.Part(), 'B': music21.stream.Part()}
        for role, p in parts.items(): p.id = role
        
        system_measures = self._collect_grand_staff_measures(self.melody_stream)
        start_states = self._calculate_start_volumes(system_measures)
        tenuto_counts = self._precalculate_tenuto_counts(system_measures)
        
        padding_duration = self._calculate_padding(self.melody_stream)
        measure_num_shift = 1 if padding_duration > 0 else 0

        # Exclusion Logic State
        exclusion_state = {'active': False, 'voice_id': None, 'counter': 0, 'last_voice_set': set(), 
                           'pitch_history': {}, 'excluded_voice_prev_median': None}
        exclusion_start_measure = None
        exclusion_start_vals = (0, 0)
        has_seen_upper_dynamic = False

        for i, sys_m in enumerate(system_measures):
            # [STATIC ANALYSIS] Identify Upper Voices and Pitches
            current_upper_voices = set()
            voice_pitches = {}
            for n, _, s_id, v_id in sys_m['all_notes']:
                if s_id == 1:
                    current_upper_voices.add(str(v_id))
                    if str(v_id) not in voice_pitches: voice_pitches[str(v_id)] = []
                    if n.isChord: voice_pitches[str(v_id)].append(max(p.ps for p in n.pitches))
                    elif n.isNote: voice_pitches[str(v_id)].append(n.pitch.ps)
            current_medians = {}
            for vid in current_upper_voices:
                pitches = voice_pitches.get(vid, [])
                if pitches:
                    median = statistics.median(pitches)
                    current_medians[vid] = median
                    exclusion_state['pitch_history'][vid] = median 
                else: current_medians[vid] = exclusion_state['pitch_history'].get(vid, -1)
            target_voice_id = None
            target_median = -1
            sorted_by_pitch = sorted(current_medians.items(), key=lambda x: x[1], reverse=True)
            if sorted_by_pitch:
                target_voice_id = sorted_by_pitch[0][0]
                target_median = sorted_by_pitch[0][1]
            measure_has_upper_dyn = any(d_sid == 1 for _, _, d_sid, _ in sys_m['all_dynamics'])

            # [TIMELINE CONSTRUCTION] Sort Events by Offset
            timeline = []
            for dyn, offset, s_id, placement in sys_m['all_dynamics']: timeline.append((offset, 0, (dyn, s_id, placement)))
            for note, offset, s_id, v_id in sys_m['all_notes']: timeline.append((offset, 1, (note, s_id, v_id)))
            timeline.sort(key=lambda x: (x[0], x[1]))
            
            # [INITIAL STATE]
            curr_u, curr_l = start_states[i]
            upper_ever_dominant = (curr_u >= curr_l) # Tracks if Upper was ever the loud one
            
            processed_notes = [] 
            
            # Reset Exclusion if Voice Structure Changes
            if current_upper_voices != exclusion_state['last_voice_set']:
                if exclusion_state['active']:
                    su, sl = exclusion_start_vals
                    msg = f"Dynamic Exclusion (U={su} < L={sl}) -> (Voice structure change)"
                    self.log_data.append({'start_m': exclusion_start_measure, 'end_m': sys_m['number']-1, 'part': 'S', 'text': msg})
                    exclusion_state['active'] = False
                    exclusion_state['voice_id'] = None
                    exclusion_state['excluded_voice_prev_median'] = None
                    exclusion_start_measure = None
            exclusion_state['last_voice_set'] = current_upper_voices
            
            # [PROCESS EVENTS]
            for idx, event in enumerate(timeline):
                offset, etype, data = event
                if etype == 0: # DYNAMIC
                    dyn_obj, s_id, placement = data
                    val = DYNAMICS_MAP.get(dyn_obj.value, 64)
                    if s_id == 1:
                        has_seen_upper_dynamic = True
                        if placement == 'above': curr_u = val
                        else: 
                            overtake_val = None
                            for k in range(idx + 1, len(timeline)):
                                f_off, f_type, f_data = timeline[k]
                                if f_off > offset + 0.25: break
                                if f_type == 0:
                                    _, f_sid, f_place = f_data
                                    if f_sid == 1 and f_place == 'above':
                                        overtake_val = DYNAMICS_MAP.get(timeline[k][2][0].value, 64)
                                        break
                            curr_u = overtake_val if overtake_val else val
                            curr_l = val 
                    elif s_id == 2:
                        curr_l = val
                        if not measure_has_upper_dyn: curr_u = val 
                    
                    if curr_u >= curr_l: upper_ever_dominant = True

                    # EXCLUSION STATE MACHINE
                    if exclusion_state['active']:
                        # Check for Octave Jumps (Reason to stop excluding)
                        ex_vid = str(exclusion_state['voice_id'])
                        curr_med_check = current_medians.get(ex_vid, -1)
                        prev_med_check = exclusion_state['excluded_voice_prev_median']
                        if curr_med_check > -1 and prev_med_check is not None:
                            if abs(curr_med_check - prev_med_check) >= 12:
                                su, sl = exclusion_start_vals
                                msg = f"Dynamic Exclusion (U={su} < L={sl}) -> (Octave Jump)"
                                self.log_data.append({'start_m': exclusion_start_measure, 'end_m': sys_m['number'], 'part': 'S', 'text': msg})
                                exclusion_state['active'] = False
                                exclusion_state['voice_id'] = None
                                exclusion_start_measure = None
                    
                    if ENABLE_DYNAMIC_EXCLUSION:
                        # Reset if volumes equalize
                        if curr_u >= curr_l and exclusion_state['active']:
                            su, sl = exclusion_start_vals
                            msg = f"Dynamic Exclusion (U={su} < L={sl}) -> (Volumes Equalized)"
                            self.log_data.append({'start_m': exclusion_start_measure, 'end_m': sys_m['number'], 'part': 'S', 'text': msg})
                            exclusion_state['active'] = False
                            exclusion_state['voice_id'] = None
                            exclusion_start_measure = None
                        
                        # Start Exclusion Trigger
                        if curr_u < curr_l:
                            if not exclusion_state['active'] and has_seen_upper_dynamic and target_voice_id:
                                exclusion_state['active'] = True
                                exclusion_state['voice_id'] = target_voice_id
                                exclusion_state['excluded_voice_prev_median'] = target_median
                                exclusion_start_measure = sys_m['number']
                                exclusion_start_vals = (curr_u, curr_l)
                                
                elif etype == 1: # NOTE
                    note_obj, s_id, v_id = data
                    keep = True
                    # Filter Excluded Voice
                    if exclusion_state['active'] and s_id == 1 and str(v_id) == str(exclusion_state['voice_id']): keep = False
                    if keep: processed_notes.append((note_obj, offset, s_id, v_id))

            # [DECISION TIME] Construct Measure Content
            measure_content = {'S': [], 'A': [], 'T': [], 'B': []}
            voice_map = self._map_system_notes(processed_notes, bake_tremolos=False)
            is_ambiguous = self._check_ambiguity_lookahead(system_measures, i)
            
            if is_ambiguous:
                marker = music21.expressions.TextExpression("CHECK HERE")
                measure_content['S'].append((marker, 0.0))
                for role, notes in voice_map.items(): measure_content[role].extend(notes)
            else:
                target_staff = 1
                should_swap = (curr_u < curr_l)
                # FIX: Only Safe-Swap if Upper was NEVER dominant
                if upper_ever_dominant: should_swap = False
                if ENABLE_DYNAMIC_EXCLUSION and should_swap: target_staff = 2
                
                if target_staff == 1: measure_content['S'] = voice_map['S']
                else: measure_content['T'] = voice_map['T']
                
                # SAFE MODE PRESERVATION / TENUTO PROTECTION
                count_curr = tenuto_counts[i]
                count_prev = tenuto_counts[i-1] if i > 0 else 0
                count_next = tenuto_counts[i+1] if i < len(tenuto_counts)-1 else 0
                
                lower_protected = False
                if count_curr >= LOWER_TENUTO_THRESHOLD: lower_protected = True
                elif count_curr > 1 and (count_prev >= LOWER_TENUTO_THRESHOLD or count_next >= LOWER_TENUTO_THRESHOLD): lower_protected = True
                
                if (lower_protected or SAFE_MODE_PRESERVE_LOWER) and target_staff == 1:
                    for note, off in voice_map['T']:
                        keep_note = False
                        if SAFE_MODE_PRESERVE_LOWER:
                            keep_note = True
                        elif hasattr(note, 'articulations'):
                            if any(isinstance(a, music21.articulations.Tenuto) for a in note.articulations):
                                keep_note = True
                        if keep_note:
                            measure_content['T'].append((note, off))
                    
                    if lower_protected and not SAFE_MODE_PRESERVE_LOWER:
                        self.log_data.append({'start_m': sys_m['number'], 'end_m': sys_m['number'], 'part': 'T', 'text': f"Safe Mode: Merged Lower Staff Tenutos (Count={count_curr})"})

            # [RECONSTRUCTION] Build Music21 Measure
            for role in ['S', 'A', 'T', 'B']:
                new_measure = music21.stream.Measure()
                new_measure.number = sys_m['number'] + measure_num_shift
                if sys_m['time_sig']:
                    new_measure.timeSignature = copy.deepcopy(sys_m['time_sig'])
                    new_measure.duration = music21.duration.Duration(sys_m['duration'])
                if sys_m['key_sig']:
                    ks = copy.deepcopy(sys_m['key_sig'])
                    if ks.sharps is None: ks.sharps = 0
                    new_measure.keySignature = ks
                note_shift = 0.0
                if i == 0 and padding_duration > 0:
                    pad_rest = music21.note.Rest()
                    pad_rest.duration.quarterLength = padding_duration
                    new_measure.insert(0.0, pad_rest)
                    note_shift = padding_duration
                    if new_measure.timeSignature:
                        new_measure.duration = music21.duration.Duration(new_measure.timeSignature.barDuration.quarterLength)
                for item, original_offset in measure_content[role]:
                    if isinstance(item, music21.expressions.TextExpression):
                         new_measure.insert(original_offset + note_shift, item)
                         continue
                    if item.duration.isGrace: continue
                    cleaned = self._clean_melody_note(item)
                    new_measure.insert(original_offset + note_shift, cleaned)
                parts[role].append(new_measure)
        
        if exclusion_state['active']:
             su, sl = exclusion_start_vals
             msg = f"Dynamic Exclusion (U={su} < L={sl}) -> (End of Score)"
             self.log_data.append({'start_m': exclusion_start_measure, 'end_m': system_measures[-1]['number'], 'part': 'S', 'text': msg})

        for role in ['S', 'A', 'T', 'B']:
            output_score.insert(0, parts[role])
        return output_score

    def _precalculate_tenuto_counts(self, system_measures):
        """Scans Lower Staff (Main Voice) for Tenuto articulations to inform 'Safe Mode' decisions."""
        counts = []
        for sys_m in system_measures:
            staff_groups = {2: {}}
            for n, _, s_id, v_id in sys_m['all_notes']:
                if s_id == 2:
                    if v_id not in staff_groups[2]: staff_groups[2][v_id] = []
                    staff_groups[2][v_id].append(n)
            main_voice_notes = []
            if staff_groups[2]:
                v_ids = list(staff_groups[2].keys())
                # Sort voices to find Main Voice (usually digit-less or low digit)
                def sort_key(k):
                    try: return int(''.join(filter(str.isdigit, str(k))) or 999)
                    except: return 999
                v_ids.sort(key=sort_key)
                main_voice_notes = staff_groups[2][v_ids[0]]
            c = 0
            for n in main_voice_notes:
                if hasattr(n, 'articulations'):
                    if any(isinstance(a, music21.articulations.Tenuto) for a in n.articulations):
                        c += 1
            counts.append(c)
        return counts

    def _calculate_start_volumes(self, system_measures):
        """Pre-calculates Volume State (Upper/Lower) at the start of every measure."""
        start_states = []
        curr_u = 64
        curr_l = 64
        exclusion_counter = 0
        exclusion_active = False
        sim_has_seen_upper = False
        for sys_m in system_measures:
            start_states.append((curr_u, curr_l))
            sys_m['all_dynamics'].sort(key=lambda x: x[1])
            measure_has_upper = any(d_sid == 1 for _, _, d_sid, _ in sys_m['all_dynamics'])
            for dyn, offset, s_id, placement in sys_m['all_dynamics']:
                val = DYNAMICS_MAP.get(dyn.value, 64)
                if s_id == 1:
                    sim_has_seen_upper = True
                    if placement == 'above': curr_u = val
                    else: 
                        curr_u = val
                        curr_l = val
                elif s_id == 2:
                    curr_l = val
                    if not measure_has_upper: curr_u = val 
            trigger = (curr_u < curr_l) and sim_has_seen_upper
            if trigger:
                 exclusion_active = True
                 exclusion_counter += 1
            else:
                 exclusion_active = False
                 exclusion_counter = 0
            if ENABLE_DYNAMIC_EXCLUSION and exclusion_active and exclusion_counter >= DYNAMIC_EXCLUSION_LIMIT:
                reset_val = min(curr_u, curr_l)
                curr_u = reset_val
                curr_l = reset_val
                exclusion_active = False
                exclusion_counter = 0
        return start_states

    def _check_ambiguity_lookahead(self, system_measures, current_idx):
        end_idx = min(current_idx + AMBIGUITY_LOOKAHEAD + 1, len(system_measures))
        window = system_measures[current_idx : end_idx]
        for sys_m in window:
            staff_voices = {1: set(), 2: set()}
            for _, _, s_id, v_id in sys_m['all_notes']:
                staff_voices[s_id].add(v_id)
            if len(staff_voices[1]) > 1 or len(staff_voices[2]) > 1:
                return True
        return False

    def _map_system_notes(self, all_notes, bake_tremolos=False):
        """Maps flat note list to Parts (S/A/T/B) based on Staff and Voice ID."""
        staff_groups = {1: {}, 2: {}}
        for n, offset, s_id, v_id in all_notes:
            if s_id not in staff_groups: s_id = 1
            if v_id not in staff_groups[s_id]: staff_groups[s_id][v_id] = []
            staff_groups[s_id][v_id].append((n, offset))
        output = {'S': [], 'A': [], 'T': [], 'B': []}
        for s_num, role_main, role_extra in [(1, 'S', 'A'), (2, 'T', 'B')]:
            v_ids = list(staff_groups[s_num].keys())
            if not v_ids: continue
            def sort_key(k):
                try: return int(''.join(filter(str.isdigit, str(k))) or 999)
                except: return 999
            v_ids.sort(key=sort_key)
            main_id = v_ids[0]
            output[role_main].extend(staff_groups[s_num][main_id])
            for extra_id in v_ids[1:]:
                output[role_extra].extend(staff_groups[s_num][extra_id])
        
        if bake_tremolos:
            for key in output:
                raw_tuples = output[key]
                raw_tuples.sort(key=lambda x: x[1]) 
                try:
                    temp_stream = music21.stream.Stream()
                    for n, off in raw_tuples: temp_stream.insert(off, n)
                    expanded = temp_stream.expandTremolos()
                    new_tuples = []
                    for n in expanded.notesAndRests: new_tuples.append((n, n.offset))
                    output[key] = new_tuples
                except: output[key] = raw_tuples
        return output

    def _safe_measure_number(self, val):
        if val is None: return 0
        try: return int(val)
        except: return int(''.join(filter(str.isdigit, str(val))) or 0)

    def _clean_melody_note(self, element):
        new_note = None
        if element.isChord:
            element.sortAscending(inPlace=True)
            top_pitch = element.pitches[-1]
            new_note = music21.note.Note(top_pitch)
            new_note.duration = element.duration
        elif isinstance(element, music21.note.Note):
            new_note = copy.deepcopy(element)
        else: return copy.deepcopy(element)
        if hasattr(element, 'tie') and element.tie is not None:
            new_note.tie = copy.deepcopy(element.tie)
        return new_note

    def _calculate_padding(self, stream):
        try:
            parts = stream.parts
            if not parts: return 0.0
            measures = parts[0].getElementsByClass('Measure')
            if not measures: return 0.0
            m1 = measures[0]
            ts = m1.timeSignature or stream.recurse().getElementsByClass('TimeSignature')[0]
            if not ts or not m1.duration: return 0.0
            bar_dur = ts.barDuration.quarterLength
            current_dur = m1.duration.quarterLength
            if bar_dur is None or current_dur is None: return 0.0
            if current_dur < bar_dur and current_dur > 0:
                return float(bar_dur - current_dur)
            return 0.0
        except: return 0.0

def main():
    ensure_dir(STAGING_DIR)
    files = glob.glob(os.path.join(INPUT_DIR, "*.musicxml")) + \
            glob.glob(os.path.join(INPUT_DIR, "*.mxl"))
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