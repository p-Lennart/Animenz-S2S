import argparse
import shutil
import time
from pathlib import Path
from collections import defaultdict
from symusic import Score

# Configure your directories here
STAGING_DIR = Path("./../02_uniques_staging")
UNIQUES_DIR = Path("./../03_uniques")

def consolidate_tracks(score: Score) -> Score:
    """Helper to merge all tracks in a Score into a single track."""
    if len(score.tracks) <= 1:
        return score
        
    main_track = score.tracks[0]
    
    for track in score.tracks[1:]:
        main_track.notes.extend(track.notes)
        main_track.controls.extend(track.controls)
        main_track.pitch_bends.extend(track.pitch_bends)
        main_track.pedals.extend(track.pedals)
        
    main_track.notes.sort()
    main_track.controls.sort()
    main_track.pitch_bends.sort()
    main_track.pedals.sort()
    
    score.tracks = [main_track]
    return score

def find_all_overlaps(track) -> list[int]:
    """
    Scans a track for overlapping note durations.
    Returns a sorted list of unique start ticks where overlaps occur.
    """
    if not track.notes:
        return []
        
    notes = sorted(track.notes, key=lambda n: n.time)
    overlaps = set() 
    
    max_end = notes[0].time + notes[0].duration
    for i in range(1, len(notes)):
        current_note = notes[i]
        
        if current_note.time < max_end:
            overlaps.add(current_note.time)
            
        end_time = current_note.time + current_note.duration
        if end_time > max_end:
            max_end = end_time
            
    return sorted(list(overlaps))

def get_measure_index(tick: int, tpq: int, time_sigs) -> int:
    """Calculates the measure index of a given tick using the time signature map."""
    if not time_sigs:
        return tick // (4 * tpq) 
        
    time_sigs = sorted(time_sigs, key=lambda ts: ts.time)
    
    if time_sigs[0].time > tick:
        return tick // (4 * tpq)
        
    measure_count = 0
    current_tick = 0
    current_ts = time_sigs[0]
    
    for ts in time_sigs:
        if ts.time > tick:
            break
            
        tpm = int(current_ts.numerator * (4 / current_ts.denominator) * tpq)
        if tpm > 0:
            measure_count += (ts.time - current_tick) // tpm
            
        current_tick = ts.time
        current_ts = ts
        
    tpm = int(current_ts.numerator * (4 / current_ts.denominator) * tpq)
    if tpm > 0:
        measure_count += (tick - current_tick) // tpm
        
    return measure_count

def tick_to_mmss(tick: int, tpq: int, tempos) -> str:
    """Converts a MIDI tick to a formatted MM:SS.xx string based on the tempo map."""
    tempos = sorted(tempos, key=lambda t: t.time)
    current_time_sec = 0.0
    current_tick = 0
    current_mspq = 500000 
    
    for t in tempos:
        if t.time > tick:
            break
        delta_ticks = t.time - current_tick
        current_time_sec += (delta_ticks / tpq) * (current_mspq / 1000000.0)
        current_tick = t.time
        
        if hasattr(t, 'mspq'):
            current_mspq = t.mspq
        elif hasattr(t, 'qpm'):
            current_mspq = 60000000 / t.qpm
            
    delta_ticks = tick - current_tick
    current_time_sec += (delta_ticks / tpq) * (current_mspq / 1000000.0)
    
    minutes = int(current_time_sec // 60)
    seconds = int(current_time_sec % 60)
    hundredths = int((current_time_sec * 100) % 100)
    return f"{minutes:02d}:{seconds:02d}.{hundredths:02d}"

def process_music_staging():
    """Main pipeline function to move and process files from staging to uniques."""
    UNIQUES_DIR.mkdir(parents=True, exist_ok=True)

    if not STAGING_DIR.exists():
        print(f"Error: {STAGING_DIR} does not exist.")
        return

    num_moved = 0
    num_passed = 0

    for song_dir in STAGING_DIR.iterdir():
        if not song_dir.is_dir():
            continue
            
        literal_files = list(song_dir.glob("*_LITERAL.mid"))
        melody_files = list(song_dir.glob("*_MELODY_DRAFT.mid"))
        chords_files = list(song_dir.glob("*_CHORDS.tsv"))
        
        if not (literal_files and melody_files and chords_files):
            num_passed += 1
            continue
            
        literal_path = literal_files[0]
        melody_path = melody_files[0]
        chords_path = chords_files[0]
        
        try:
            literal_score = Score(literal_path)
            melody_score = Score(melody_path)
            
            if len(melody_score.tracks) == 1:
                melody_track = melody_score.tracks[0]
                overlap_ticks = find_all_overlaps(melody_track)
                
                if overlap_ticks:
                    measure_logs = defaultdict(list)
                    for tick in overlap_ticks:
                        m_idx = get_measure_index(tick, melody_score.ticks_per_quarter, melody_score.time_signatures)
                        if len(measure_logs[m_idx]) < 3:
                            measure_logs[m_idx].append(tick)
                            
                    limited_ticks = sorted([tick for ticks in measure_logs.values() for tick in ticks])
                    
                    for tick in limited_ticks:
                        timestamp = tick_to_mmss(tick, melody_score.ticks_per_quarter, melody_score.tempos)
                        print(f"❌ [{timestamp}] {song_dir.name}")
                        
                    num_passed += 1
                    continue

                dest_dir = UNIQUES_DIR / song_dir.name
                dest_dir.mkdir(parents=True, exist_ok=True)
                
                consolidate_tracks(literal_score)
                literal_dest = dest_dir / literal_path.name
                literal_score.dump_midi(str(literal_dest))
                
                new_melody_name = melody_path.name.replace("_MELODY_DRAFT.mid", "_MELODY.mid")
                
                shutil.copy2(melody_path, dest_dir / new_melody_name)
                shutil.copy2(chords_path, dest_dir / chords_path.name)
                
                # --- NEW BLOCKING WAIT LOGIC ---
                lock_notified = False
                while song_dir.exists():
                    try:
                        shutil.rmtree(song_dir)
                        break
                    except PermissionError:
                        if not lock_notified:
                            print(f"⏳ [LOCKED] '{song_dir.name}' is open in your editor. Waiting for it to close", end="", flush=True)
                            lock_notified = True
                        time.sleep(1.5)
                        print(".", end="", flush=True)
                    except FileNotFoundError:
                        break # Safe to break if something else somehow deleted it
                
                if lock_notified:
                    print() # Print a new line once the dots stop
                # -------------------------------
                
                print(f"✅ {song_dir.name}")
                num_moved += 1
            else:
                print(f"❌ [{len(melody_score.tracks)} tracks] {song_dir.name}")
                num_passed += 1
                
        except Exception as e:
            print(f"❌ [Error: {e}] {song_dir.name}")
            num_passed += 1

    print(f"\n{num_moved} songs moved, {num_passed} songs passed over")

def retroactive_overlap_check():
    """Temporary helper to scan existing files in UNIQUES_DIR for overlaps."""
    if not UNIQUES_DIR.exists():
        print(f"Error: {UNIQUES_DIR} does not exist.")
        return

    print(f"--- Running Retroactive Overlap Check on {UNIQUES_DIR.name} ---\n")
    num_scanned = 0
    num_flagged = 0

    for song_dir in UNIQUES_DIR.iterdir():
        if not song_dir.is_dir():
            continue
            
        melody_files = list(song_dir.glob("*_MELODY.mid"))
        
        if not melody_files:
            continue
            
        melody_path = melody_files[0]
        num_scanned += 1
        
        try:
            melody_score = Score(melody_path)
            
            if len(melody_score.tracks) == 1:
                melody_track = melody_score.tracks[0]
                overlap_ticks = find_all_overlaps(melody_track)
                
                if overlap_ticks:
                    measure_logs = defaultdict(list)
                    for tick in overlap_ticks:
                        m_idx = get_measure_index(tick, melody_score.ticks_per_quarter, melody_score.time_signatures)
                        if len(measure_logs[m_idx]) < 3:
                            measure_logs[m_idx].append(tick)
                            
                    limited_ticks = sorted([tick for ticks in measure_logs.values() for tick in ticks])
                    
                    for tick in limited_ticks:
                        timestamp = tick_to_mmss(tick, melody_score.ticks_per_quarter, melody_score.tempos)
                        print(f"❌ [{timestamp}] {song_dir.name}")
                        
                    num_flagged += 1
            else:
                print(f"❌ [{len(melody_score.tracks)} tracks] {song_dir.name}")
                num_flagged += 1
                
        except Exception as e:
            print(f"❌ [Error: {e}] {song_dir.name}")
            num_flagged += 1

    print(f"\nRetroactive check complete: {num_flagged} songs flagged out of {num_scanned} scanned.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process staging music files for the ML Piano Arranging Project.")
    parser.add_argument(
        "-r", "--retroactive",
        action="store_true",
        help="Run the retroactive overlap check on UNIQUES_DIR instead of the main staging process."
    )
    
    args = parser.parse_args()
    
    if args.retroactive:
        retroactive_overlap_check()
    else:
        process_music_staging()