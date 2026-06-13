import music21
from collections import defaultdict

# --- Configuration ---
# Start Pitch -> (Max Accept Jump UP, Max Anchor Move UP)
BASS_TIERS = {
    "C0": (12, 7),  # Deep Bass: Octave up allowed, Anchor moves on 5th
    "C2": (6, 5),  # Mid Bass: <5th up allowed, Anchor moves on 4th
    "A2": (2, 2),  # High Bass: Stepwise only (filters ostinatos)
}

# Pre-compile on import
SORTED_BASS_TIERS = []
for _p_name, _rules in BASS_TIERS.items():
    _ps = music21.pitch.Pitch(_p_name).ps
    SORTED_BASS_TIERS.append((_ps, _rules))
SORTED_BASS_TIERS.sort(key=lambda x: x[0], reverse=True)


def map_parts(stream):
    """
    Identifies the Tenor and Bass parts in a polyphonic stream.
    Returns: (part_map, reference_part)
    """
    part_map = {}
    try:
        if isinstance(stream, music21.stream.Score):
            for p in stream.parts:
                if p.id in ["T", "B"]:
                    part_map[p.id] = p
            if not part_map and len(stream.parts) >= 4:
                part_map["T"] = stream.parts[2]
                part_map["B"] = stream.parts[3]
        elif isinstance(stream, music21.stream.Part):
            part_map["B"] = stream

        ref_part = part_map.get("B") or part_map.get("T") or stream.parts[0]
        return part_map, ref_part
    except Exception as e:
        print(f"[stream_utils] Error mapping parts: {e}")
        return {}, stream.parts[0] if hasattr(stream, "parts") else stream


def get_all_notes_in_range(stream, start_offset, end_offset):
    """Global scan of the stream to extract all notes within a time window."""
    notes = []
    for el in stream.flat.getElementsByClass(["Note", "Chord"]):
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


def get_bass_candidates_for_measure(start, end, part_map):
    """Collects notes from B. Falls back to T if empty."""
    candidates = []

    def extract(p):
        res = []
        for el in p.flat.getElementsByClass(["Note", "Chord"]):
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

    if "B" in part_map:
        candidates = extract(part_map["B"])
    if not candidates and "T" in part_map:
        candidates = extract(part_map["T"])
    return candidates


def define_bass_subset(notes):
    """
    Returns a SET of nameWithOctave strings representing the true bassline.
    Relies on the module-level SORTED_BASS_TIERS configuration.
    """
    events = defaultdict(list)
    for n, off in notes:
        events[off].append(n)

    timeline = []
    for off in sorted(events.keys()):
        vertical = sorted(events[off], key=lambda x: x.pitch.ps)
        lowest_note = vertical[0]
        timeline.append(lowest_note)

    if not timeline:
        return set()

    bass_subset = set()
    current_anchor = timeline[0]
    bass_subset.add(current_anchor.pitch.nameWithOctave)

    for i in range(1, len(timeline)):
        candidate = timeline[i]

        anchor_ps = current_anchor.pitch.ps
        cand_ps = candidate.pitch.ps
        interval = cand_ps - anchor_ps

        if interval <= 0:
            bass_subset.add(candidate.pitch.nameWithOctave)
            current_anchor = candidate
            continue

        max_accept = 12
        max_anchor_move = 7

        # Check against pre-compiled tiers
        for tier_ps, (acc, move) in SORTED_BASS_TIERS:
            if anchor_ps >= tier_ps:
                max_accept = acc
                max_anchor_move = move
                break

        if interval <= max_accept:
            bass_subset.add(candidate.pitch.nameWithOctave)
            if interval <= max_anchor_move:
                current_anchor = candidate

    return bass_subset


def find_lowest_at_offset(window_notes, target_offset):
    """Finds the specific bass note object at the exact start of the window."""
    candidates = [n for n, off in window_notes if abs(off - target_offset) < 0.001]
    if not candidates:
        return None
    candidates.sort(key=lambda x: x.pitch.ps)
    return candidates[0]
