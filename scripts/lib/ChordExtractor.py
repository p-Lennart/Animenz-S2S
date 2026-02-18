# VERSION = 1.7 (Chronological Contour & Running Anchor)
import os
import music21
import copy
from collections import defaultdict

class ChordExtractor:
    # Configuration: Start Pitch -> (Max Accept Jump UP, Max Anchor Move UP)
    # Downward movement is always accepted and always moves the anchor.
    BASS_TIERS = {
        'C0': (12, 7), # Deep Bass: Octave up allowed, Anchor moves on 5th
        'C2': (6, 5),  # Mid Bass: <5th up allowed, Anchor moves on 4th
        'A2': (2, 2),  # High Bass: Stepwise only (filters ostinatos)
    }

    def __init__(self, literal_stream, time_scale=0.5):
        self.stream = literal_stream
        self.time_scale = time_scale
        self.extracted_data = [] 
        
        # Pre-calculate Tier Pitch Spaces (ps) for efficiency
        # Sort DESCENDING so we find the "highest floor" first
        self.sorted_tiers = []
        for p_name, rules in self.BASS_TIERS.items():
            ps = music21.pitch.Pitch(p_name).ps
            self.sorted_tiers.append((ps, rules))
        self.sorted_tiers.sort(key=lambda x: x[0], reverse=True)

        self._process_stream()

    def _process_stream(self):
        if not self.stream:
            return

        part_map = {}
        try:
            if isinstance(self.stream, music21.stream.Score):
                for p in self.stream.parts:
                    if p.id in ['T', 'B']:
                        part_map[p.id] = p
                if not part_map and len(self.stream.parts) >= 4:
                    part_map['T'] = self.stream.parts[2]
                    part_map['B'] = self.stream.parts[3]
            elif isinstance(self.stream, music21.stream.Part):
                part_map['B'] = self.stream
        except Exception as e:
            print(f"[ChordExtractor] Error mapping parts: {e}")
            return

        last_chord_str = None
        previous_window_notes = []

        ref_part = part_map.get('B') or part_map.get('T') or self.stream.parts[0]
        
        for m in ref_part.getElementsByClass('Measure'):
            measure_start = m.offset
            measure_end = m.offset + m.duration.quarterLength
            
            bass_candidate_notes = self._get_bass_candidates_for_measure(measure_start, measure_end, part_map)
            
            if not bass_candidate_notes:
                continue

            # Step 1: Define Bass Subset using Chronological Contour
            bass_pitch_names = self._define_bass_subset(bass_candidate_notes)
            
            # Step 2: Identify Bass Attacks (Filter candidates by the subset)
            bass_attacks = []
            for n_obj, offset in bass_candidate_notes:
                if n_obj.pitch.nameWithOctave in bass_pitch_names:
                    if not bass_attacks:
                        bass_attacks.append((offset, n_obj.pitch.pitchClass))
                    else:
                        if abs(bass_attacks[-1][0] - offset) > 0.001:
                            bass_attacks.append((offset, n_obj.pitch.pitchClass))
            
            bass_attacks.sort(key=lambda x: x[0])

            # Step 3: Merge Consecutive Identical Bass PCs
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
            
            # Step 4: Process Windows
            for i in range(len(merged_boundaries) - 1):
                w_start = merged_boundaries[i]
                w_end = merged_boundaries[i+1]
                
                window_notes = self._get_all_notes_in_range(w_start, w_end)
                if not window_notes: continue

                current_bass_note = self._find_lowest_at_offset(window_notes, w_start)
                if not current_bass_note: continue

                unique_pitches = set(n.pitch.nameWithOctave for n, o in window_notes)
                notes_pool = window_notes
                if len(unique_pitches) <= 3 and previous_window_notes:
                    notes_pool = window_notes + previous_window_notes

                scaled_timestamp = w_start * self.time_scale

                chord_symbol = self._construct_chord_iteratively(current_bass_note, notes_pool, scaled_timestamp)

                if chord_symbol and chord_symbol != last_chord_str:
                    self.extracted_data.append((scaled_timestamp, chord_symbol))
                    last_chord_str = chord_symbol
                
                previous_window_notes = window_notes

    def _get_bass_candidates_for_measure(self, start, end, part_map):
        candidates = []
        def extract(p):
            res = []
            for el in p.flat.getElementsByClass(['Note', 'Chord']):
                off = float(el.offset)
                if start <= off < end:
                    if isinstance(el, music21.chord.Chord):
                        for p_name in el.pitches:
                            n = music21.note.Note(p_name)
                            n.duration = el.duration
                            res.append((n, off))
                    else:
                        res.append((el, off))
            return res

        if 'B' in part_map: candidates = extract(part_map['B'])
        if not candidates and 'T' in part_map: candidates = extract(part_map['T'])
        return candidates

    def _get_all_notes_in_range(self, start_offset, end_offset):
        notes = []
        for el in self.stream.flat.getElementsByClass(['Note', 'Chord']):
            off = float(el.offset)
            if start_offset <= off < end_offset:
                if isinstance(el, music21.chord.Chord):
                    for p_name in el.pitches:
                        n = music21.note.Note(p_name)
                        n.duration = el.duration
                        notes.append((n, off))
                else:
                    notes.append((el, off))
        return notes

    def _define_bass_subset(self, notes):
        """
        Returns a SET of nameWithOctave strings.
        Implements CHRONOLOGICAL CONTOUR Logic.
        1. Find lowest note at each timeline event.
        2. Walk forward in time, comparing against a running Anchor.
        """
        # 1. Group by Offset to find Chronological Contour
        events = defaultdict(list)
        for n, off in notes:
            events[off].append(n)
        
        # Create list of (offset, lowest_note) sorted by time
        timeline = []
        for off in sorted(events.keys()):
            vertical = sorted(events[off], key=lambda x: x.pitch.ps)
            lowest_note = vertical[0]
            timeline.append(lowest_note)
            
        if not timeline: return set()

        bass_subset = set()
        
        # 2. Chronological Walk
        # Initialize Anchor with the very first note of the measure
        current_anchor = timeline[0]
        bass_subset.add(current_anchor.pitch.nameWithOctave)
        
        for i in range(1, len(timeline)):
            candidate = timeline[i]
            
            anchor_ps = current_anchor.pitch.ps
            cand_ps = candidate.pitch.ps
            interval = cand_ps - anchor_ps
            
            # CASE A: Downward Movement (or repetition)
            # Always accept drops to find true deep bass.
            if interval <= 0:
                bass_subset.add(candidate.pitch.nameWithOctave)
                current_anchor = candidate # Anchor follows the drop
                continue

            # CASE B: Upward Movement
            # Check against Tiers based on CURRENT ANCHOR
            max_accept = 12 # Default fallback
            max_anchor_move = 7
            
            for tier_ps, (acc, move) in self.sorted_tiers:
                if anchor_ps >= tier_ps:
                    max_accept = acc
                    max_anchor_move = move
                    break
            
            if interval <= max_accept:
                bass_subset.add(candidate.pitch.nameWithOctave)
                
                # Only move anchor if jump is small enough (melodic)
                # If jump is large (e.g. octave stride), anchor stays low.
                if interval <= max_anchor_move:
                    current_anchor = candidate
            
            # If interval > max_accept, REJECT. 
            # Anchor remains where it was.
            
        return bass_subset

    def _find_lowest_at_offset(self, window_notes, target_offset):
        candidates = [n for n, off in window_notes if abs(off - target_offset) < 0.001]
        if not candidates: return None
        candidates.sort(key=lambda x: x.pitch.ps)
        return candidates[0]

    def _get_quality_rank(self, symbol, pitch_count):
        if pitch_count == 1: return 1 
        if symbol == 'Chord Symbol' or symbol == 'Chord Symbol Cannot Be Identified': return -1 
        if any(x in symbol for x in ['9', '11', '13', 'add']): return 0 
        if symbol == 'pedal': return 1
        if symbol == 'power': return 2
        return 3

    def _construct_chord_iteratively(self, bass_note, notes_pool, timestamp):
        pc_stats = {}
        max_dur = 0.0
        for n, off in notes_pool:
            pc = n.pitch.pitchClass
            if pc not in pc_stats: pc_stats[pc] = {'dur': 0.0, 'min_ps': 1000}
            pc_stats[pc]['dur'] += n.duration.quarterLength
            if n.pitch.ps < pc_stats[pc]['min_ps']: pc_stats[pc]['min_ps'] = n.pitch.ps
            if pc_stats[pc]['dur'] > max_dur: max_dur = pc_stats[pc]['dur']

        if max_dur == 0: max_dur = 1.0
        priority_list = []
        for pc, stats in pc_stats.items():
            dur_ratio = stats['dur'] / max_dur
            bonus = dur_ratio * 4.9 
            score = stats['min_ps'] - bonus
            priority_list.append((pc, score))
        priority_list.sort(key=lambda x: x[1])
        priority_pcs = [x[0] for x in priority_list]

        bass_pc = bass_note.pitch.pitchClass
        if bass_pc in priority_pcs: priority_pcs.remove(bass_pc)

        best_symbol = f"{bass_note.pitch.name} pedal"
        best_rank = 1 
        current_notes = [bass_note]
        
        mins = int(timestamp // 60)
        secs = timestamp % 60
        print(f"[DEBUG] T: {mins:02d}:{secs:06.3f} | Bass: {bass_note.pitch.nameWithOctave}")

        limit = min(len(priority_pcs) + 1, 7) 

        for i in range(limit):
            if i > 0:
                pc_to_add = priority_pcs[i-1]
                new_n = music21.note.Note()
                new_n.pitch.ps = pc_stats[pc_to_add]['min_ps']
                current_notes.append(new_n)

            test_chord = music21.chord.Chord(current_notes)
            
            try: symbol = music21.harmony.chordSymbolFigureFromChord(test_chord)
            except: symbol = 'Chord Symbol Cannot Be Identified'

            if symbol == 'Chord Symbol Cannot Be Identified':
                temp_chord = copy.deepcopy(test_chord)
                temp_chord.simplifyEnharmonics(inPlace=True)
                try: symbol = music21.harmony.chordSymbolFigureFromChord(temp_chord)
                except: pass
            
            unique_pcs = len(set(n.pitch.pitchClass for n in current_notes))
            
            if symbol == 'Chord Symbol Cannot Be Identified' and unique_pcs == 2:
                bass_p = current_notes[0].pitch
                added_p = current_notes[-1].pitch 
                diff = (added_p.pitchClass - bass_p.pitchClass) % 12
                if diff == 3: symbol = f"{bass_p.name}m"
                elif diff == 4: symbol = f"{bass_p.name}"
                elif diff == 7: symbol = f"{bass_p.name}power"

            rank = self._get_quality_rank(symbol, unique_pcs)
            
            note_names = [n.pitch.name for n in current_notes]
            print(f"   + Try {note_names} -> '{symbol}' (Rank {rank})")

            if rank == 0: break
            if rank == -1: continue
            if rank >= best_rank:
                best_symbol = symbol
                best_rank = rank
        
        print(f"   => MATCH: {best_symbol}")
        return best_symbol

    def save(self, directory):
        if not os.path.exists(directory):
            try: os.makedirs(directory)
            except OSError as e:
                print(f"[ChordExtractor] Could not create directory: {e}")
                return
        filepath = os.path.join(directory, 'extracted_chords.txt')
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                for timestamp, chord_name in self.extracted_data:
                    minutes = int(timestamp // 60)
                    seconds = timestamp % 60
                    f.write(f"[{minutes:02d}:{seconds:06.3f}] {chord_name}\n")
            print(f"[ChordExtractor] Saved {len(self.extracted_data)} lines to {filepath}")
        except IOError as e: print(f"[ChordExtractor] Error writing to file: {e}")