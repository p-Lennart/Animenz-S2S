import os
import glob
import yaml
import shutil
from lib.ScoreProcessor import ScoreProcessor
from lib.ChordExtractor import ChordExtractor

# --- PATHS ---
INPUT_DIR = "./../test"
STAGING_DIR = "./../staging"
CONFIG_FILE = "default_config.yaml"

def load_config(path):
    with open(path, 'r') as f:
        return yaml.safe_load(f)

def clean_filename(fname: str) -> str:
    valid_chars = set(" -_")
    cleaned = "".join(c for c in fname if (c.isalnum() and c.isascii()) or c in valid_chars)
    return cleaned.strip()

def main():
    if not os.path.exists(CONFIG_FILE):
        print(f"Error: {CONFIG_FILE} not found.")
        return

    default_config = load_config(CONFIG_FILE)
    
    # Ensure dirs exist
    os.makedirs(INPUT_DIR, exist_ok=True)
    os.makedirs(STAGING_DIR, exist_ok=True)

    files = glob.glob(os.path.join(INPUT_DIR, "*.musicxml")) + \
            glob.glob(os.path.join(INPUT_DIR, "*.mxl"))
    
    print(f"Found {len(files)} files in {INPUT_DIR}")

    for xml_file in files:
        raw_name = os.path.splitext(os.path.basename(xml_file))[0]
        score_name = clean_filename(raw_name)
        
        # Create Staging Directory
        score_dir = os.path.join(STAGING_DIR, score_name)
        score_dir = os.path.normpath(score_dir)
        os.makedirs(score_dir, exist_ok=True)
        
        # Copy Config (meta.yaml)
        meta_path = os.path.join(score_dir, "meta.yaml")
        if not os.path.exists(meta_path):
            with open(meta_path, 'w') as f:
                yaml.dump(default_config, f, default_flow_style=False)
        
        # Load the *specific* config for this run (in real app, use meta.yaml)
        # For dummy ingest, we just use default
        current_config = default_config

        print(f"Processing: {score_name} -> {score_dir}")
        
        try:
            processor = ScoreProcessor(xml_file, current_config)
            
            # Hook for future Chord Extractor:
            literal_stream, _ = processor.process(output_dir=score_dir)
            if literal_stream:
                ChordExtractor(literal_stream).save(score_dir)
            
            # processor.process(output_dir=score_dir)
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"FAILED to process {xml_file}: {e}")

if __name__ == "__main__":
    main()