import os
import glob
import yaml
import re

from lib.ScoreProcessor import ScoreProcessor

# --- PATHS ---
INPUT_DIR = "./../01_xml_raw"
STAGING_DIR = "./../02_uniques_staging"
UNIQUES_DIR = "./../03_uniques"
CONFIG_FILE = "ingest_defaults.yaml"


def load_config(path):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def clean_filename(fname: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 _-]", "", fname)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.strip()
    cleaned = cleaned.rstrip(". ")
    return cleaned[:100]


def abbr_scorename(sname: str) -> str:
    return sname[:5]


def process_single_score(
    xml_file: str, score_dir: str, score_name_abbr: str, config: dict
) -> bool:
    """Helper to process a single score. Called by ingest and review scripts."""
    try:
        processor = ScoreProcessor(xml_file, score_name_abbr, config)
        processor.process(output_dir=score_dir)
        return True
    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"❌ FAILED to process {xml_file}: {e}")
        return False


def main():
    if not os.path.exists(CONFIG_FILE):
        print(f"Error: {CONFIG_FILE} not found.")
        return

    default_config = load_config(CONFIG_FILE)

    # Ensure all dirs exist
    os.makedirs(INPUT_DIR, exist_ok=True)
    os.makedirs(STAGING_DIR, exist_ok=True)
    os.makedirs(UNIQUES_DIR, exist_ok=True)

    files = glob.glob(os.path.join(INPUT_DIR, "*.musicxml")) + glob.glob(
        os.path.join(INPUT_DIR, "*.mxl")
    )

    if not files:
        print(f"🔎 No XML files found in {INPUT_DIR}")
        return

    print(f"🔎 Found {len(files)} files in {INPUT_DIR}")

    num_processed = 0
    num_skipped = 0

    for xml_file in files:
        raw_name = os.path.splitext(os.path.basename(xml_file))[0]
        score_name = clean_filename(raw_name)
        score_name_abbr = abbr_scorename(score_name)

        # --- Check if already promoted to UNIQUES_DIR ---
        uniques_path = os.path.join(UNIQUES_DIR, score_name)
        if os.path.exists(uniques_path) and os.path.isdir(uniques_path):
            num_skipped += 1
            continue

        # Create Staging Directory
        score_dir = os.path.join(STAGING_DIR, score_name)
        score_dir = os.path.normpath(score_dir)
        os.makedirs(score_dir, exist_ok=True)
        if not os.path.exists(score_dir):
            raise RuntimeError(f"Directory creation failed: {score_dir}")

        meta_path = os.path.join(score_dir, f"{score_name_abbr}_META.yaml")

        # Inject state tracking into the config if it's a fresh run
        if not os.path.exists(meta_path):
            run_config = default_config.copy()
            run_config["SOURCE_XML"] = xml_file
            run_config["REPROCESS"] = False

            with open(meta_path, "w") as f:
                yaml.dump(run_config, f, default_flow_style=False)
        else:
            run_config = load_config(meta_path)

        print(f"⏳ Processing: {score_name_abbr} -> {score_dir}")
        success = process_single_score(xml_file, score_dir, score_name_abbr, run_config)
        if success:
            num_processed += 1

    # Final summary messages
    if num_skipped == len(files):
        print(
            "\n✅ All songs in INPUT_DIR have already been promoted to UNIQUES_DIR. Nothing to do!"
        )
    else:
        print(
            f"\n✅ Ingestion complete: {num_processed} processed/regenerated, {num_skipped} skipped (already in uniques)."
        )


if __name__ == "__main__":
    main()
