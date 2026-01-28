import music21
import math
import copy

class MelodyReducer:
    """
    Post-processing module to simplify MELODY_DRAFT.
    Removes texture (ostinatos) and virtuoso runs to reveal the structural melody.
    """
    def __init__(self, score, log_data):
        self.score = score
        self.log_data = log_data  # Reference to shared list for Changelog generation
        
        # --- OSTINATO CONFIGURATION ---
        self.OSTINATO_MIN_REPS = 2
        self.OSTINATO_PROTECTION_MEASURES = 8  # Do not touch intros
        self.OSTINATO_ALWAYS_REPLACE = False   # If True, never delete, always put high note
        
        # --- RUN CONFIGURATION ---
        self.RUN_MIN_NOTES = 8                 # Minimum detected notes to qualify as a run
        self.RUN_MIN_NOTES_PER_BEAT = 3.0      # Density Threshold (e.g. triplets or faster)
        self.RUN_DURATION_TOLERANCE_RATIO = 2.5 # Max allowed ratio between adjacent notes in a run
        
        # --- RUN SELECTION TOGGLES ---
        # Determining which notes SURVIVE the simplification process
        self.RUN_INCLUDE_START_NOTE = True
        self.RUN_INCLUDE_END_NOTE = True
        self.RUN_INCLUDE_ARTICULATED = True
        self.RUN_BOUNDARY_SKIP = 0             # 0 = Keep all beat boundaries. -1 = Disable boundary checks.
        self.RUN_LEAP_THRESHOLD = 7            # Semitones. Keep note if jump >= this.
        self.RUN_AGGRESSIVE_MODE = False       # Legacy toggle (superseded by above flags)
        
        self.TOLERANCE = 0.05
        self.DEBUG = False 

    def process(self):
        """Main entry point. Iterates over Soprano (S) and Alto (A) parts."""
        for part_id in ['S', 'A']:
            part = self._get_part_by_id(part_id)
            if part:
                if self.DEBUG: print(f"[Reducer] Processing Part {part_id}")
                # Order matters: Remove rigid patterns first, then simplify fluid runs
                self._remove_texture_patterns(part, part_id)
                self._simplify_runs(part)

    def _get_part_by_id(self, part_id):
        for p in self.score.parts:
            if p.id == part_id: return p
        return None

    def _log_structured(self, start_offset, end_offset, part_id, text):
        """Appends a structured dictionary to the global log for final report generation."""
        part = self._get_part_by_id(part_id)
        start_m = "?"
        if part:
            # map global offset back to measure number
            m = self._get_context_at_offset(part, start_offset, 'Measure')
            if m: start_m = m.number
        
        end_m = start_m
        if part:
            m_end = self._get_context_at_offset(part, end_offset - 0.01, 'Measure')
            if m_end: end_m = m_end.number

        self.log_data.append({
            'start_m': start_m if isinstance(start_m, int) else 0,
            'end_m': end_m if isinstance(end_m, int) else 0,
            'part': part_id,
            'text': text
        })

    # =========================================================================
    # RULE 1: TEXTURE / OSTINATO REMOVAL
    # Detects repeating patterns (e.g., Alberti bass, arpeggios) and removes them.
    # =========================================================================
    def _remove_texture_patterns(self, part, part_id):
        # Flattening is necessary to see linear sequence of notes across measures
        flat_stream = part.flatten()
        elements = list(flat_stream.notesAndRests)
        if not elements: return

        i = 0
        while i < len(elements):
            matched_len = 0
            
            # Check for patterns of length 4, then length 3
            for p_size in [4, 3]:
                if i + p_size * self.OSTINATO_MIN_REPS > len(elements): continue
                
                seed = elements[i : i + p_size]
                # Fast Fail: Ostinatos must have uniform duration (approx)
                dur = seed[0].duration.quarterLength
                if not all(abs(el.duration.quarterLength - dur) < self.TOLERANCE for el in seed):
                    continue

                # Scan ahead for repetitions
                reps = 0
                curr_idx = i
                while curr_idx + p_size <= len(elements):
                    candidate = elements[curr_idx : curr_idx + p_size]
                    if self._is_pattern_match(seed, candidate):
                        reps += 1
                        curr_idx += p_size
                    else: break
                
                if reps >= self.OSTINATO_MIN_REPS:
                    matched_len = reps * p_size
                    break
            
            if matched_len > 0:
                pattern_elements = elements[i : i + matched_len]
                start_offset = pattern_elements[0].offset
                
                # PROTECTION: Skip if in Intro
                m = self._get_context_at_offset(part, start_offset, 'Measure')
                if m and m.number <= self.OSTINATO_PROTECTION_MEASURES:
                    i += 1
                    continue

                end_offset = pattern_elements[-1].offset + pattern_elements[-1].duration.quarterLength
                
                # Pitch string for logging (e.g. "C-E-G")
                pitch_names = []
                for el in elements[i : i + p_size]: 
                    if el.isNote: pitch_names.append(el.pitch.name)
                    elif el.isChord: pitch_names.append(el.pitches[-1].name)
                    elif el.isRest: pitch_names.append("R")
                pitch_str = "-".join(pitch_names)
                total_beats = (end_offset - start_offset)
                
                # DECISION: Delete or Replace?
                # Check if other parts are playing at the same time
                is_supported = self._check_simultaneous_activity(start_offset, end_offset, exclude_part_id=part_id)
                should_delete = is_supported and not self.OSTINATO_ALWAYS_REPLACE
                
                if should_delete:
                    msg = f"Delete ostinato ({pitch_str}, Beats={total_beats:.2f}, Length={matched_len})"
                    self._log_structured(start_offset, end_offset, part_id, msg)
                    self._clear_range_in_part(part, start_offset, end_offset)
                else:
                    suffix = " (Forced)" if self.OSTINATO_ALWAYS_REPLACE and is_supported else ""
                    msg = f"Replace ostinato ({pitch_str}, Beats={total_beats:.2f}, Length={matched_len}) -> High Note{suffix}"
                    self._log_structured(start_offset, end_offset, part_id, msg)
                    self._replace_with_high_note(pattern_elements, start_offset, end_offset, part)
                
                i += matched_len
            else:
                i += 1

    def _is_pattern_match(self, seed, candidate):
        """Compares two segments for Pitch Class and Duration equality."""
        if len(seed) != len(candidate): return False
        for s, c in zip(seed, candidate):
            if abs(s.duration.quarterLength - c.duration.quarterLength) > self.TOLERANCE: return False
            if isinstance(s, music21.note.Rest) and isinstance(c, music21.note.Rest): continue
            if isinstance(s, music21.note.Rest) or isinstance(c, music21.note.Rest): return False
            # Compare sorted pitch classes (handles chords)
            s_pitches = tuple(sorted([p.ps for p in s.pitches])) if s.isChord else (s.pitch.ps,)
            c_pitches = tuple(sorted([p.ps for p in c.pitches])) if c.isChord else (c.pitch.ps,)
            if s_pitches != c_pitches: return False
        return True

    def _check_simultaneous_activity(self, start, end, exclude_part_id):
        """Returns True if any OTHER part has notes in this time range."""
        for p in self.score.parts:
            if p.id == exclude_part_id: continue
            flat = p.flatten().notes
            if not flat: continue
            for n in flat:
                n_end = n.offset + n.duration.quarterLength
                if n.offset < end and n_end > start: return True
                if n.offset >= end: break
        return False

    def _replace_with_high_note(self, pattern_elements, start, end, part):
        """Replaces a range with a single note matching the highest pitch found."""
        highest_ps = -1
        highest_pitch_obj = None
        for el in pattern_elements:
            if el.isNote:
                if el.pitch.ps > highest_ps:
                    highest_ps = el.pitch.ps
                    highest_pitch_obj = el.pitch
            elif el.isChord:
                top = max(el.pitches, key=lambda p: p.ps)
                if top.ps > highest_ps:
                    highest_ps = top.ps
                    highest_pitch_obj = top
        
        if highest_pitch_obj:
            new_note = music21.note.Note(highest_pitch_obj)
            new_note.duration = music21.duration.Duration(end - start)
            
            # Nuclear Option: Clear range first (handles lower octave removal)
            self._clear_range_in_part(part, start, end)
            
            # Insert into Measure
            target_measure = self._get_context_at_offset(part, start, 'Measure')
            if target_measure:
                local_offset = start - target_measure.offset
                target_measure.insert(local_offset, new_note)

    # =========================================================================
    # RULE 2: RUN SIMPLIFICATION
    # Detects fast scale-like runs and reduces them to key structural notes.
    # =========================================================================
    def _simplify_runs(self, part):
        # CRITICAL: Do NOT sort here. music21 flat() returns chronological order.
        # Sorting can pull "Ghost Notes" (offset 0.0 bugs) to the front.
        elements = list(part.flatten().notesAndRests)
        if not elements: return

        current_run = []
        # Calculate max duration allowed (e.g. 1/3 beat for triplets)
        duration_gate_threshold = 1.0 / self.RUN_MIN_NOTES_PER_BEAT
        
        # Chronological Safety: Track time to detect "Ghost Notes" (notes that rewind time)
        last_processed_offset = -1.0
        
        for el in elements:
            # 1. GHOST NOTE CHECK
            if el.offset < last_processed_offset - self.TOLERANCE:
                # Discard notes that appear earlier than current processing point
                if self.DEBUG: print(f"DEBUG [Skip Rewind]: Offset {el.offset} < {last_processed_offset}")
                continue
            
            if el.offset > last_processed_offset:
                last_processed_offset = el.offset

            # 2. FILTER: Ignore Grace Notes (Duration 0.0)
            if el.duration.isGrace or el.duration.quarterLength == 0.0:
                continue

            # 3. BREAK: Rests break runs
            if el.isRest:
                if current_run:
                    self._evaluate_and_process_run(current_run, part)
                    current_run = []
                continue
            
            n = el
            # 4. BREAK: Ties break runs (implies sustained melody)
            if hasattr(n, 'tie') and n.tie is not None:
                 if current_run:
                    self._evaluate_and_process_run(current_run, part)
                    current_run = []
                 continue
            
            # 5. FILTER: Strict Duration Gating
            if n.duration.quarterLength > (duration_gate_threshold + 0.01):
                if current_run:
                    self._evaluate_and_process_run(current_run, part)
                    current_run = []
                continue

            if not current_run:
                current_run.append(n)
            else:
                prev = current_run[-1]
                
                # 6. FILTER: Duplicates (Chord tones at same offset)
                if abs(n.offset - prev.offset) < self.TOLERANCE:
                    continue 
                
                prev_end = prev.offset + prev.duration.quarterLength
                is_continuous = abs(prev_end - n.offset) < self.TOLERANCE
                
                # 7. BREAK: Duration Ratio Check (Don't merge 32nds with 8ths)
                d1 = prev.duration.quarterLength
                d2 = n.duration.quarterLength
                if d1 > 0 and d2 > 0:
                    ratio = max(d1, d2) / min(d1, d2)
                    if ratio > self.RUN_DURATION_TOLERANCE_RATIO:
                        is_continuous = False
                
                if is_continuous: 
                    current_run.append(n)
                else:
                    self._evaluate_and_process_run(current_run, part)
                    current_run = [n]
        
        self._evaluate_and_process_run(current_run, part)

    def _evaluate_and_process_run(self, run, part):
        if not run: return
        
        # 8. THRESHOLD: Must be long enough (Note Count)
        if len(run) < self.RUN_MIN_NOTES: return
        
        start_offset = run[0].offset
        ts = self._get_context_at_offset(part, start_offset, 'TimeSignature')
        if not ts: beat_dur = 1.0 
        else: beat_dur = ts.beatDuration.quarterLength
        
        self._rewrite_run(run, ts, beat_dur, part)

    def _get_context_at_offset(self, part, offset, class_name):
        """Robustly finds the Measure or TimeSignature active at a global offset."""
        measures = part.getElementsByClass('Measure')
        target_m = None
        for m in measures:
            if m.offset <= offset < (m.offset + m.duration.quarterLength):
                target_m = m
                break
        # Edge case: Offset is exactly at end of score
        if not target_m and measures:
            if offset >= measures[-1].offset: target_m = measures[-1]
            
        if not target_m: return None

        if class_name == 'Measure':
            return target_m
        elif class_name == 'TimeSignature':
            ts = target_m.timeSignature
            if ts: return ts
            # Recurse backwards if not in current measure
            return part.recurse().getElementsByClass('TimeSignature').getElementAtOrBefore(offset)
        return None
    
    def _clear_range_in_part(self, part, start_offset, end_offset):
        """
        Removes ALL notes/rests within a global timeframe.
        Used for Nuclear Option replacement.
        """
        measures = part.getElementsByClass('Measure')
        for m in measures:
            m_start = m.offset
            m_end = m.offset + m.duration.quarterLength
            if m_end <= start_offset or m_start >= end_offset: continue
            
            remove_list = []
            for el in m.notesAndRests:
                el_global_start = m_start + el.offset
                # Use tolerance to handle float precision
                if el_global_start >= (start_offset - self.TOLERANCE) and el_global_start < (end_offset - self.TOLERANCE):
                    remove_list.append(el)
            
            for el in remove_list:
                try: m.remove(el)
                except: pass

    def _rewrite_run(self, run, ts, beat_dur, part):
        start_global = run[0].offset
        end_global = run[-1].offset + run[-1].duration.quarterLength
        beats = (end_global - start_global) / beat_dur
        
        # Debug Log for Pitch content
        pitch_list = []
        for n in run:
            if n.isNote: pitch_list.append(n.pitch.nameWithOctave)
            elif n.isChord: pitch_list.append(n.pitches[-1].nameWithOctave)
        p_str = ",".join(pitch_list[:5]) + ("..." if len(pitch_list)>5 else "")
        self._log_structured(start_global, end_global, part.id, f"Simplify run (Notes={len(run)}, Beats={beats:.1f}) [{p_str}]")

        raw_keepers = []
        
        # SELECTION LOGIC
        if self.RUN_INCLUDE_START_NOTE:
            raw_keepers.append(run[0])
        if self.RUN_INCLUDE_END_NOTE:
            raw_keepers.append(run[-1])
        
        boundary_hits = 0
        
        for i, n in enumerate(run):
            should_keep = False
            
            # 1. Articulation
            if self.RUN_INCLUDE_ARTICULATED:
                if hasattr(n, 'articulations') and n.articulations:
                    for art in n.articulations:
                        if isinstance(art, (music21.articulations.Accent, music21.articulations.Tenuto, 
                                      music21.articulations.StrongAccent, music21.articulations.Staccato)):
                            should_keep = True
            
            # 2. Boundary (Beat)
            if self.RUN_BOUNDARY_SKIP >= 0:
                orig_m = self._get_context_at_offset(part, n.offset, 'Measure')
                if orig_m: m_offset = n.offset - orig_m.offset
                else: m_offset = n.offset 
                is_boundary = abs(m_offset % beat_dur) < self.TOLERANCE
                
                if is_boundary:
                    if boundary_hits % (self.RUN_BOUNDARY_SKIP + 1) == 0: should_keep = True
                    boundary_hits += 1
            
            # 3. Leaps (Intervallic)
            if i > 0:
                prev = run[i-1]
                if n.isNote and prev.isNote:
                    if (n.pitch.ps - prev.pitch.ps) >= self.RUN_LEAP_THRESHOLD: should_keep = True
            
            if should_keep: raw_keepers.append(n)

        # De-duplicate Keepers (Set logic)
        seen_ids = set()
        sorted_keepers = []
        for k in sorted(raw_keepers, key=lambda x: x.offset):
            if id(k) not in seen_ids:
                sorted_keepers.append(k)
                seen_ids.add(id(k))

        # RECONSTRUCTION: Merge and Grid
        merged_keepers = []
        if sorted_keepers:
            curr = sorted_keepers[0]
            accum_note = copy.deepcopy(curr)
            accum_note.offset = curr.offset 
            accum_note.duration = music21.duration.Duration(curr.duration.quarterLength)
            
            for next_n in sorted_keepers[1:]:
                # Stop merging if target has articulation
                has_stop_articulation = False
                if hasattr(next_n, 'articulations'):
                    for art in next_n.articulations:
                        if isinstance(art, (music21.articulations.Tenuto, music21.articulations.Staccato)):
                            has_stop_articulation = True
                            break
                
                # Merge consecutive Pitch Classes
                if (accum_note.isNote and next_n.isNote and 
                    accum_note.pitch.pitchClass == next_n.pitch.pitchClass and
                    not has_stop_articulation): 
                    pass # Absorb (duration will extend implicitly by gap filling)
                else:
                    merged_keepers.append(accum_note)
                    accum_note = copy.deepcopy(next_n)
                    accum_note.offset = next_n.offset 
                    accum_note.duration = music21.duration.Duration(next_n.duration.quarterLength)
            merged_keepers.append(accum_note)

        # Place on Grid (Powers of 2)
        note_specs = [] 
        for n in merged_keepers:
            b_idx = math.floor(n.offset / beat_dur)
            # Density based on ORIGINAL keepers in this beat
            orig_count = sum(1 for k in sorted_keepers if math.floor(k.offset/beat_dur) == b_idx)
            x = 2 ** math.ceil(math.log2(orig_count)) if orig_count > 0 else 4
            if x < 4: x = 4
            
            slot_dur = beat_dur / x
            rel_off = n.offset - (b_idx * beat_dur)
            slot_idx = round(rel_off / slot_dur)
            new_offset = (b_idx * beat_dur) + (slot_idx * slot_dur)
            note_specs.append((n, new_offset))

        # Apply Changes
        self._clear_range_in_part(part, start_global, end_global)

        for i, (n, start) in enumerate(note_specs):
            # Extend note to meet next note (Legato)
            if i < len(note_specs) - 1: end = note_specs[i+1][1]
            else: end = run[-1].offset + run[-1].duration.quarterLength
            
            dur = end - start
            if dur <= 0: dur = slot_dur
            
            n.duration = music21.duration.Duration(dur)
            n.offset = start 
            
            target_m = self._get_context_at_offset(part, start, 'Measure')
            if target_m:
                local_offset = start - target_m.offset
                # Insert if fits in measure
                if 0 <= local_offset < target_m.duration.quarterLength:
                    target_m.insert(local_offset, n)
                else: 
                    # Fallback
                    target_m.insert(local_offset, n)