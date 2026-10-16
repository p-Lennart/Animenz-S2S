import os
import glob
import re
import sys
import shutil
import yaml
import time
from pathlib import Path

# Configure utf-8 console output for Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Add script lib to path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from lib.ScoreProcessor import ScoreProcessor

# --- DIRECTORY PATHS ---
INPUT_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "../01_xml_raw"))
STAGING_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "../02_uniques_staging"))
UNIQUES_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "../03_uniques"))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "ingest_defaults.yaml")
BACKUP_TARGETS_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "../tests/fixtures/intensity_targets"))


def load_config(path: str) -> dict:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def clean_filename(fname: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 _-]", "", fname)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.strip()
    cleaned = cleaned.rstrip(". ")
    return cleaned[:100]


def abbr_scorename(sname: str) -> str:
    return sname[:5]


def backup_intensity_targets():
    """Back up all Intensity_Targets.tsv files to tests/fixtures/ and 03_uniques/."""
    os.makedirs(BACKUP_TARGETS_DIR, exist_ok=True)
    targets = glob.glob(os.path.join(STAGING_DIR, "*", "Intensity_Targets.tsv"))
    print(f"[BACKUP] Backing up {len(targets)} Intensity_Targets.tsv files...")
    for src in targets:
        song_name = os.path.basename(os.path.dirname(src))
        
        # Backup to tests/fixtures/intensity_targets
        dest_fixture = os.path.join(BACKUP_TARGETS_DIR, song_name, "Intensity_Targets.tsv")
        os.makedirs(os.path.dirname(dest_fixture), exist_ok=True)
        shutil.copy2(src, dest_fixture)
        
        # Backup to 03_uniques
        dest_uniques = os.path.join(UNIQUES_DIR, song_name, "Intensity_Targets.tsv")
        if os.path.isdir(os.path.join(UNIQUES_DIR, song_name)):
            shutil.copy2(src, dest_uniques)
            
        print(f"  + Preserved: {song_name[:50]}...")


def get_midi_fingerprints(uniques_dir: str) -> dict:
    """Capture mtime and size of all LITERAL and MELODY midis in 03_uniques."""
    fingerprints = {}
    for root, _, files in os.walk(uniques_dir):
        for f in files:
            if f.endswith("_LITERAL.mid") or "_MELODY" in f:
                p = os.path.join(root, f)
                stat = os.stat(p)
                fingerprints[p] = (stat.st_mtime, stat.st_size)
    return fingerprints


def reprocess_and_promote():
    start_time = time.time()
    default_config = load_config(CONFIG_FILE)
    
    # 1. First backup intensity targets
    backup_intensity_targets()
    
    # 2. Capture baseline fingerprints of preserved MIDIs
    print("\n[GUARD] Recording fingerprints of existing LITERAL and MELODY files in 03_uniques...")
    initial_fingerprints = get_midi_fingerprints(UNIQUES_DIR)
    print(f"  Captured {len(initial_fingerprints)} MIDI files under preservation guard.")

    # 3. Locate XML files
    xml_files = sorted(
        glob.glob(os.path.join(INPUT_DIR, "*.musicxml"))
        + glob.glob(os.path.join(INPUT_DIR, "*.mxl"))
    )
    total_songs = len(xml_files)
    print(f"\n[INFO] Found {total_songs} XML files in {INPUT_DIR} to reprocess.\n")

    success_count = 0
    fail_count = 0
    total_chords = 0
    total_measures = 0
    intensity_distribution = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}

    for idx, xml_file in enumerate(xml_files, start=1):
        raw_name = os.path.splitext(os.path.basename(xml_file))[0]
        score_name = clean_filename(raw_name)
        score_name_abbr = abbr_scorename(score_name)
        
        staging_song_dir = os.path.join(STAGING_DIR, score_name)
        uniques_song_dir = os.path.join(UNIQUES_DIR, score_name)
        
        if not os.path.isdir(uniques_song_dir):
            print(f"[WARN] [{idx}/{total_songs}] Missing matching uniques dir: {uniques_song_dir}. Skipping.")
            fail_count += 1
            continue

        os.makedirs(staging_song_dir, exist_ok=True)
        
        # Load song-specific meta config if present, otherwise default
        meta_path = os.path.join(staging_song_dir, f"{score_name_abbr}_META.yaml")
        run_config = load_config(meta_path) if os.path.exists(meta_path) else default_config.copy()
        run_config["DEBUG"] = False

        song_t0 = time.time()
        try:
            # A. Process through ScoreProcessor
            processor = ScoreProcessor(xml_file, score_name_abbr, run_config)
            processor.process(output_dir=staging_song_dir, write_files=True)
            
            # Staging outputs
            staging_chords = os.path.join(staging_song_dir, f"{score_name_abbr}_CHORDS.tsv")
            staging_intensity = os.path.join(staging_song_dir, f"{score_name_abbr}_INTENSITY.tsv")
            
            if not (os.path.exists(staging_chords) and os.path.exists(staging_intensity)):
                raise RuntimeError("Failed to generate CHORDS or INTENSITY TSV in staging.")

            # B. Promote ONLY CHORDS.tsv and INTENSITY.tsv to 03_uniques
            dest_chords = os.path.join(uniques_song_dir, f"{score_name_abbr}_CHORDS.tsv")
            dest_intensity = os.path.join(uniques_song_dir, f"{score_name_abbr}_INTENSITY.tsv")
            
            shutil.copy2(staging_chords, dest_chords)
            shutil.copy2(staging_intensity, dest_intensity)
            
            # Collect metrics from generated files
            with open(dest_chords, "r", encoding="utf-8") as f:
                chord_lines = [l for l in f.read().splitlines() if l.strip()][1:]
            with open(dest_intensity, "r", encoding="utf-8") as f:
                intensity_lines = [l for l in f.read().splitlines() if l.strip()][1:]
                
            n_chords = len(chord_lines)
            n_measures = len(intensity_lines)
            total_chords += n_chords
            total_measures += n_measures
            
            for line in intensity_lines:
                parts = line.split("\t")
                if len(parts) >= 3:
                    try:
                        lvl = int(parts[2])
                        intensity_distribution[lvl] = intensity_distribution.get(lvl, 0) + 1
                    except ValueError:
                        pass
            
            elapsed = time.time() - song_t0
            print(f"[{idx:02d}/{total_songs}] OK {score_name_abbr} ({score_name[:35]:<35}) "
                  f"| {n_chords:4d} chords | {n_measures:3d} mm | {elapsed:.2f}s")
            success_count += 1

        except Exception as e:
            elapsed = time.time() - song_t0
            print(f"[{idx:02d}/{total_songs}] FAIL {score_name_abbr} ({score_name[:35]}): {e} ({elapsed:.2f}s)")
            import traceback
            traceback.print_exc()
            fail_count += 1

    # 4. Post-Flight Integrity Verification
    print("\n[VERIFY] Verifying MIDI preservation in 03_uniques...")
    violations = []
    final_fingerprints = get_midi_fingerprints(UNIQUES_DIR)
    for p, initial_stat in initial_fingerprints.items():
        curr_stat = final_fingerprints.get(p)
        if curr_stat is None:
            violations.append(f"DELETED: {p}")
        elif curr_stat != initial_stat:
            violations.append(f"MODIFIED: {p} (was {initial_stat}, now {curr_stat})")

    if violations:
        print(f"[ALERT] CRITICAL: {len(violations)} protected files were altered:")
        for v in violations:
            print(f"  - {v}")
    else:
        print(f"[OK] ZERO MUTATIONS: All {len(initial_fingerprints)} LITERAL and MELODY MIDIs remain bit-for-bit intact!")

    # 5. Verify Uniques Inventory
    print("\n[INVENTORY] Final 03_uniques Status:")
    uniques_folders = [d for d in os.listdir(UNIQUES_DIR) if os.path.isdir(os.path.join(UNIQUES_DIR, d))]
    has_chords = sum(1 for d in uniques_folders if glob.glob(os.path.join(UNIQUES_DIR, d, "*_CHORDS.tsv")))
    has_intensity = sum(1 for d in uniques_folders if glob.glob(os.path.join(UNIQUES_DIR, d, "*_INTENSITY.tsv")))
    has_literal = sum(1 for d in uniques_folders if glob.glob(os.path.join(UNIQUES_DIR, d, "*_LITERAL.mid")))
    has_melody = sum(1 for d in uniques_folders if glob.glob(os.path.join(UNIQUES_DIR, d, "*_MELODY*.mid")))
    has_targets = sum(1 for d in uniques_folders if os.path.exists(os.path.join(UNIQUES_DIR, d, "Intensity_Targets.tsv")))

    print(f"  * Total Song Folders:    {len(uniques_folders)}")
    print(f"  * _CHORDS.tsv Count:     {has_chords}/{len(uniques_folders)}")
    print(f"  * _INTENSITY.tsv Count:  {has_intensity}/{len(uniques_folders)}")
    print(f"  * _LITERAL.mid Count:    {has_literal}/{len(uniques_folders)}")
    print(f"  * _MELODY*.mid Count:    {has_melody}/{len(uniques_folders)}")
    print(f"  * Intensity_Targets.tsv: {has_targets}/5 calibration songs")

    total_time = time.time() - start_time
    print(f"\n==========================================")
    print(f"Reprocessing & Promotion Complete!")
    print(f"  * Success: {success_count}/{total_songs}")
    print(f"  * Failed:  {fail_count}/{total_songs}")
    print(f"  * Total Chords: {total_chords:,}")
    print(f"  * Total Intensity Measures: {total_measures:,}")
    print(f"  * Intensity Distribution: {intensity_distribution}")
    print(f"  * Total Time: {total_time:.2f}s ({total_time/60:.2f} min)")
    print(f"==========================================")


if __name__ == "__main__":
    reprocess_and_promote()
