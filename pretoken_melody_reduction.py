import music21
import math
import copy


class MelodyReducer:
    def __init__(self, score, changelog):
        self.score = score
        self.changelog = changelog  # Reference to the shared log list

        # Configuration Constants
        self.OSTINATO_MIN_REPS = 2
        self.RUN_MIN_NOTES = 7
        self.RUN_MIN_BEATS = 2
        self.TOLERANCE = 0.05
        self.DEBUG = False

    def process(self):
        """Main entry point. Applies reduction rules to Upper Staff tracks (S, A)."""
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

    def _log(self, message, offset, part_id):
        """Helper to format and append to the changelog."""
        # Find measure number for context
        # We can use any part to find the measure number since they align
        # But best to use the part we are processing
        p = self._get_part_by_id(part_id)
        m_num = "?"
        if p:
            m = self._get_context_at_offset(p, offset, "Measure")
            if m:
                m_num = str(m.number)

        entry = f"[Measure {m_num}] (Part {part_id}) {message}"
        self.changelog.append(entry)
        if self.DEBUG:
            print(f"[Reducer] {entry}")

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
                end_offset = (
                    pattern_elements[-1].offset
                    + pattern_elements[-1].duration.quarterLength
                )

                is_supported = self._check_simultaneous_activity(
                    start_offset, end_offset, exclude_part_id=part_id
                )

                if is_supported:
                    self._log(
                        f"Deleting ostinato pattern (Length={matched_len})",
                        start_offset,
                        part_id,
                    )
                    self._robust_remove_elements(pattern_elements, part)
                else:
                    self._log(
                        f"Replacing unsupported ostinato with high note",
                        start_offset,
                        part_id,
                    )
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

    def _robust_remove_elements(self, elements, part):
        for n in elements:
            removed = False
            target_n = n
            if n.derivation and n.derivation.origin:
                target_n = n.derivation.origin

            if target_n.sites:
                for site in target_n.sites.get():
                    if site is None:
                        continue
                    if not hasattr(site, "classes"):
                        continue
                    if "Measure" in site.classes:
                        try:
                            site.remove(target_n)
                            removed = True
                            break
                        except:
                            pass

            if not removed:
                m = self._get_context_at_offset(part, n.offset, "Measure")
                if m:
                    for el in list(m.elements):
                        if abs((el.offset + m.offset) - n.offset) < self.TOLERANCE:
                            if type(el) == type(n):
                                match = True
                                if hasattr(n, "pitch") and hasattr(el, "pitch"):
                                    if n.pitch != el.pitch:
                                        match = False
                                if match:
                                    m.remove(el)
                                    removed = True
                                    break

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

            first_el = pattern_elements[0]
            target_measure = self._get_context_at_offset(
                part, first_el.offset, "Measure"
            )

            self._robust_remove_elements(pattern_elements, part)

            if target_measure:
                local_offset = start - target_measure.offset
                target_measure.insert(local_offset, new_note)

    # =========================================================================
    # RULE 2: RUN SIMPLIFICATION
    # =========================================================================
    def _simplify_runs(self, part):
        flat_notes = list(part.flatten().notes)
        if not flat_notes:
            return

        runs = []
        current_run = [flat_notes[0]]

        for n in flat_notes[1:]:
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
        if len(run) < self.RUN_MIN_NOTES:
            return

        start_offset = run[0].offset
        end_offset = run[-1].offset + run[-1].duration.quarterLength
        total_len = end_offset - start_offset

        ts = self._get_context_at_offset(part, start_offset, "TimeSignature")
        if not ts:
            beat_dur = 1.0
        else:
            beat_dur = ts.beatDuration.quarterLength

        if total_len < self.RUN_MIN_BEATS * beat_dur:
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
        # Logging
        self._log(
            f"Simplifying run (Len={len(run)} notes, {run[-1].offset + run[-1].duration.quarterLength - run[0].offset:.2f} beats)",
            run[0].offset,
            part.id,
        )

        keepers = []
        for i, n in enumerate(run):
            keep = False
            orig_m = self._get_context_at_offset(part, n.offset, "Measure")
            if orig_m:
                m_offset = n.offset - orig_m.offset
            else:
                m_offset = n.offset

            if abs(m_offset % beat_dur) < self.TOLERANCE:
                keep = True

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
                        keep = True

            if i > 0:
                prev = run[i - 1]
                if n.isNote and prev.isNote:
                    if abs(n.pitch.ps - prev.pitch.ps) >= 7:
                        keep = True

            if keep:
                keepers.append(n)

        if not keepers:
            return

        sorted_keepers = sorted(keepers, key=lambda n: n.offset)
        merged_keepers = []
        if sorted_keepers:
            curr = sorted_keepers[0]
            accum_note = copy.deepcopy(curr)
            accum_note.offset = curr.offset
            accum_note.duration = music21.duration.Duration(curr.duration.quarterLength)

            for next_n in sorted_keepers[1:]:
                if (
                    accum_note.isNote
                    and next_n.isNote
                    and accum_note.pitch.pitchClass == next_n.pitch.pitchClass
                ):
                    pass
                else:
                    merged_keepers.append(accum_note)
                    accum_note = copy.deepcopy(next_n)
                    accum_note.offset = next_n.offset
                    accum_note.duration = music21.duration.Duration(
                        next_n.duration.quarterLength
                    )
            merged_keepers.append(accum_note)

        note_specs = []
        for n in merged_keepers:
            b_idx = math.floor(n.offset / beat_dur)
            orig_count = sum(
                1 for k in keepers if math.floor(k.offset / beat_dur) == b_idx
            )
            x = 2 ** math.ceil(math.log2(orig_count)) if orig_count > 0 else 4
            if x < 4:
                x = 4

            slot_dur = beat_dur / x
            rel_off = n.offset - (b_idx * beat_dur)
            slot_idx = round(rel_off / slot_dur)
            new_offset = (b_idx * beat_dur) + (slot_idx * slot_dur)
            note_specs.append((n, new_offset))

        start_global = run[0].offset
        end_global = run[-1].offset + run[-1].duration.quarterLength
        self._clear_range_in_part(part, start_global, end_global)

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
