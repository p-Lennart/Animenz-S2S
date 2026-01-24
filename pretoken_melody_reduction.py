import music21
import math
import copy


class MelodyReducer:
    def __init__(self, score, log_data):
        self.score = score
        self.log_data = log_data

        # Configuration Constants
        self.OSTINATO_MIN_REPS = 2
        self.OSTINATO_PROTECTION_MEASURES = 8  # New: Protect intro

        self.RUN_MIN_BEATS = 2.0
        self.RUN_MIN_NOTES_PER_BEAT = 3.0  # New: Density check
        self.RUN_BOUNDARY_SKIP = 0  # New: Skip N boundary notes

        self.TOLERANCE = 0.05
        self.DEBUG = False

    def process(self):
        """Main entry point."""
        for part_id in ["S", "A"]:
            part = self._get_part_by_id(part_id)
            if part:
                if self.DEBUG:
                    print(f"[Reducer] Processing Part {part_id}")
                self._remove_texture_patterns(part, part_id)
                self._simplify_runs(part)

    def _get_part_by_id(self, part_id):
        for p in self.score.parts:
            if p.id == part_id:
                return p
        return None

    def _log_structured(self, start_offset, end_offset, part_id, text):
        part = self._get_part_by_id(part_id)
        start_m = "?"
        if part:
            m = self._get_context_at_offset(part, start_offset, "Measure")
            if m:
                start_m = m.number

        end_m = start_m
        if part:
            m_end = self._get_context_at_offset(part, end_offset - 0.01, "Measure")
            if m_end:
                end_m = m_end.number

        self.log_data.append(
            {
                "start_m": start_m if isinstance(start_m, int) else 0,
                "end_m": end_m if isinstance(end_m, int) else 0,
                "part": part_id,
                "text": text,
            }
        )

    # =========================================================================
    # RULE 1: TEXTURE / OSTINATO REMOVAL
    # =========================================================================
    def _remove_texture_patterns(self, part, part_id):
        flat_stream = part.flatten()
        elements = list(flat_stream.notesAndRests)
        if not elements:
            return

        i = 0
        while i < len(elements):
            matched_len = 0

            for p_size in [4, 3]:
                if i + p_size * self.OSTINATO_MIN_REPS > len(elements):
                    continue

                seed = elements[i : i + p_size]
                dur = seed[0].duration.quarterLength
                if not all(
                    abs(el.duration.quarterLength - dur) < self.TOLERANCE for el in seed
                ):
                    continue

                reps = 0
                curr_idx = i
                while curr_idx + p_size <= len(elements):
                    candidate = elements[curr_idx : curr_idx + p_size]
                    if self._is_pattern_match(seed, candidate):
                        reps += 1
                        curr_idx += p_size
                    else:
                        break

                if reps >= self.OSTINATO_MIN_REPS:
                    matched_len = reps * p_size
                    break

            if matched_len > 0:
                pattern_elements = elements[i : i + matched_len]
                start_offset = pattern_elements[0].offset

                # Check Protection Buffer
                m = self._get_context_at_offset(part, start_offset, "Measure")
                if m and m.number <= self.OSTINATO_PROTECTION_MEASURES:
                    i += 1
                    continue

                end_offset = (
                    pattern_elements[-1].offset
                    + pattern_elements[-1].duration.quarterLength
                )

                pitch_names = []
                for el in elements[i : i + p_size]:
                    if el.isNote:
                        pitch_names.append(el.pitch.name)
                    elif el.isChord:
                        pitch_names.append(el.pitches[-1].name)
                    elif el.isRest:
                        pitch_names.append("R")
                pitch_str = "-".join(pitch_names)

                total_beats = end_offset - start_offset

                is_supported = self._check_simultaneous_activity(
                    start_offset, end_offset, exclude_part_id=part_id
                )

                if is_supported:
                    msg = f"Delete ostinato ({pitch_str}, Beats={total_beats:.2f}, Length={matched_len})"
                    self._log_structured(start_offset, end_offset, part_id, msg)
                    self._clear_range_in_part(
                        part, start_offset, end_offset
                    )  # Use Range Clearing
                else:
                    msg = f"Replace ostinato ({pitch_str}, Beats={total_beats:.2f}, Length={matched_len}) -> High Note"
                    self._log_structured(start_offset, end_offset, part_id, msg)
                    self._replace_with_high_note(
                        pattern_elements, start_offset, end_offset, part
                    )

                i += matched_len
            else:
                i += 1

    def _is_pattern_match(self, seed, candidate):
        if len(seed) != len(candidate):
            return False
        for s, c in zip(seed, candidate):
            if (
                abs(s.duration.quarterLength - c.duration.quarterLength)
                > self.TOLERANCE
            ):
                return False
            if isinstance(s, music21.note.Rest) and isinstance(c, music21.note.Rest):
                continue
            if isinstance(s, music21.note.Rest) or isinstance(c, music21.note.Rest):
                return False
            s_pitches = (
                tuple(sorted([p.ps for p in s.pitches])) if s.isChord else (s.pitch.ps,)
            )
            c_pitches = (
                tuple(sorted([p.ps for p in c.pitches])) if c.isChord else (c.pitch.ps,)
            )
            if s_pitches != c_pitches:
                return False
        return True

    def _check_simultaneous_activity(self, start, end, exclude_part_id):
        for p in self.score.parts:
            if p.id == exclude_part_id:
                continue
            flat = p.flatten().notes
            if not flat:
                continue
            for n in flat:
                n_end = n.offset + n.duration.quarterLength
                if n.offset < end and n_end > start:
                    return True
                if n.offset >= end:
                    break
        return False

    def _replace_with_high_note(self, pattern_elements, start, end, part):
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

            # Use Nuclear Option: Clear everything in range
            self._clear_range_in_part(part, start, end)

            # Insert new note
            target_measure = self._get_context_at_offset(part, start, "Measure")
            if target_measure:
                local_offset = start - target_measure.offset
                target_measure.insert(local_offset, new_note)

    # =========================================================================
    # RULE 2: RUN SIMPLIFICATION
    # =========================================================================
    def _simplify_runs(self, part):
        flat_notes = list(part.flatten().notes)  # Only notes, no rests
        # Need notesAndRests to detect breaks, but flat_notes used for runs
        elements = list(part.flatten().notesAndRests)
        if not elements:
            return

        current_run = []

        for el in elements:
            if el.isRest:
                # Break run on Rest
                if current_run:
                    self._evaluate_and_process_run(current_run, part)
                    current_run = []
                continue

            # It's a note/chord
            n = el
            # Break run on Tie
            if hasattr(n, "tie") and n.tie is not None:
                if current_run:
                    self._evaluate_and_process_run(current_run, part)
                    current_run = []
                continue

            # Check continuity with previous note in run
            if not current_run:
                current_run.append(n)
            else:
                prev = current_run[-1]
                prev_end = prev.offset + prev.duration.quarterLength

                is_continuous = abs(prev_end - n.offset) < self.TOLERANCE
                is_equal_dur = (
                    abs(prev.duration.quarterLength - n.duration.quarterLength)
                    < self.TOLERANCE
                )

                if is_continuous and is_equal_dur:
                    current_run.append(n)
                else:
                    self._evaluate_and_process_run(current_run, part)
                    current_run = [n]

        self._evaluate_and_process_run(current_run, part)

    def _evaluate_and_process_run(self, run, part):
        if not run:
            return

        start_offset = run[0].offset
        end_offset = run[-1].offset + run[-1].duration.quarterLength
        total_len_beats = 0

        ts = self._get_context_at_offset(part, start_offset, "TimeSignature")
        if not ts:
            beat_dur = 1.0
        else:
            beat_dur = ts.beatDuration.quarterLength

        total_len_beats = (end_offset - start_offset) / beat_dur

        if total_len_beats < self.RUN_MIN_BEATS:
            return

        # Density Check
        avg_density = len(run) / total_len_beats
        if avg_density < self.RUN_MIN_NOTES_PER_BEAT:
            if self.DEBUG:
                print(
                    f"[Reducer] Run Rejected: Density {avg_density:.2f} < {self.RUN_MIN_NOTES_PER_BEAT}"
                )
            return

        self._rewrite_run(run, ts, beat_dur, part)

    def _get_context_at_offset(self, part, offset, class_name):
        measures = part.getElementsByClass("Measure")
        target_m = None
        for m in measures:
            if m.offset <= offset < (m.offset + m.duration.quarterLength):
                target_m = m
                break
        if not target_m and measures:
            if offset >= measures[-1].offset:
                target_m = measures[-1]
        if not target_m:
            return None
        if class_name == "Measure":
            return target_m
        elif class_name == "TimeSignature":
            ts = target_m.timeSignature
            if ts:
                return ts
            return (
                part.recurse()
                .getElementsByClass("TimeSignature")
                .getElementAtOrBefore(offset)
            )
        return None

    def _clear_range_in_part(self, part, start_offset, end_offset):
        measures = part.getElementsByClass("Measure")
        for m in measures:
            m_start = m.offset
            m_end = m.offset + m.duration.quarterLength
            if m_end <= start_offset or m_start >= end_offset:
                continue
            remove_list = []
            for el in m.notesAndRests:
                el_global_start = m_start + el.offset
                if el_global_start >= (
                    start_offset - self.TOLERANCE
                ) and el_global_start < (end_offset - self.TOLERANCE):
                    remove_list.append(el)
            for el in remove_list:
                try:
                    m.remove(el)
                except:
                    pass

    def _rewrite_run(self, run, ts, beat_dur, part):
        start_global = run[0].offset
        end_global = run[-1].offset + run[-1].duration.quarterLength
        beats = (end_global - start_global) / beat_dur

        self._log_structured(
            start_global,
            end_global,
            part.id,
            f"Simplify run (Beats={beats:.1f}, Density={len(run)/beats:.1f})",
        )

        # 1. Collect Keepers (Anchors + Filtered)
        keepers_set = (
            set()
        )  # Store tuples (offset, pitch_ps) or just note objects if unique
        # We use a list and sort later to ensure stability
        raw_keepers = []

        # Always keep First and Last
        raw_keepers.append(run[0])
        raw_keepers.append(run[-1])

        boundary_hits = 0

        # Check others
        for i, n in enumerate(run):
            # Avoid dupes if 0 or -1 checked again (we dedup later)
            orig_m = self._get_context_at_offset(part, n.offset, "Measure")
            if orig_m:
                m_offset = n.offset - orig_m.offset
            else:
                m_offset = n.offset

            is_boundary = False
            if abs(m_offset % beat_dur) < self.TOLERANCE:
                is_boundary = True

            should_keep = False

            if is_boundary:
                if boundary_hits % (self.RUN_BOUNDARY_SKIP + 1) == 0:
                    should_keep = True
                boundary_hits += 1

            if hasattr(n, "articulations") and n.articulations:
                for art in n.articulations:
                    if isinstance(
                        art,
                        (
                            music21.articulations.Accent,
                            music21.articulations.Tenuto,
                            music21.articulations.StrongAccent,
                            music21.articulations.Staccato,
                        ),
                    ):
                        should_keep = True

            if i > 0:
                prev = run[i - 1]
                if n.isNote and prev.isNote:
                    if abs(n.pitch.ps - prev.pitch.ps) >= 7:
                        should_keep = True

            if should_keep:
                raw_keepers.append(n)

        # Dedup and Sort
        # Notes in 'run' are distinct objects.
        seen_ids = set()
        sorted_keepers = []
        for k in sorted(raw_keepers, key=lambda x: x.offset):
            if id(k) not in seen_ids:
                sorted_keepers.append(k)
                seen_ids.add(id(k))

        # 2. Merge Pitch Classes (PRE-GRID)
        merged_keepers = []
        if sorted_keepers:
            curr = sorted_keepers[0]
            accum_note = copy.deepcopy(curr)
            accum_note.offset = curr.offset
            accum_note.duration = music21.duration.Duration(curr.duration.quarterLength)

            for next_n in sorted_keepers[1:]:
                # Merge if Pitch Class matches (Octave insensitive)
                if (
                    accum_note.isNote
                    and next_n.isNote
                    and accum_note.pitch.pitchClass == next_n.pitch.pitchClass
                ):
                    pass  # Don't add next_n, accum effectively extends to it
                else:
                    merged_keepers.append(accum_note)
                    accum_note = copy.deepcopy(next_n)
                    accum_note.offset = next_n.offset
                    accum_note.duration = music21.duration.Duration(
                        next_n.duration.quarterLength
                    )
            merged_keepers.append(accum_note)

        # 3. Calculate New Offsets (Post-Merge Density)
        note_specs = []
        for n in merged_keepers:
            b_idx = math.floor(n.offset / beat_dur)

            # Density based on Merged Keepers
            count_in_beat = sum(
                1 for k in merged_keepers if math.floor(k.offset / beat_dur) == b_idx
            )
            x = 2 ** math.ceil(math.log2(count_in_beat)) if count_in_beat > 0 else 4
            if x < 4:
                x = 4

            slot_dur = beat_dur / x
            rel_off = n.offset - (b_idx * beat_dur)
            slot_idx = round(rel_off / slot_dur)
            new_offset = (b_idx * beat_dur) + (slot_idx * slot_dur)
            note_specs.append((n, new_offset))

        # 4. Remove Old
        self._clear_range_in_part(part, start_global, end_global)

        # 5. Insert New
        for i, (n, start) in enumerate(note_specs):
            if i < len(note_specs) - 1:
                end = note_specs[i + 1][1]
            else:
                end = run[-1].offset + run[-1].duration.quarterLength

            dur = end - start
            if dur <= 0:
                dur = slot_dur

            n.duration = music21.duration.Duration(dur)
            n.offset = start

            target_m = self._get_context_at_offset(part, start, "Measure")
            if target_m:
                local_offset = start - target_m.offset
                if 0 <= local_offset < target_m.duration.quarterLength:
                    target_m.insert(local_offset, n)
                else:
                    target_m.insert(local_offset, n)
