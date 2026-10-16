"""
Test suite covering TokenizationCore.py.

Designed to catch critical tokenization/detokenization bugs:
  - Bug #1: MidiTok special token name mangling ([SEP] -> [SEP]_None -> mapped to ID 0 / chord token).
  - Bug #2: Stitched context arrangement leak during detokenization (failure to slice off previous literal).
  - Bug #3: Intensity TSV parsing and clamping logic (val = max(0, 4) bug and column schema mismatch).
  - Enharmonic chord normalization (sanitize_chord).
  - Sliding window chunking and state propagation (chunk_token_sequence).
  - End-to-end tokenization and detokenization roundtrip.

Usage:
  python -m unittest tests/test_tokenization_core.py
"""

import os
import sys
import json
import tempfile
import unittest
from pathlib import Path

# Ensure repo root and scripts are in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import symusic
from miditok import TokSequence
from lib.TokenizationCore import (
    PianoArrangerTokenizer,
    SYSTEM_TOKENS,
    sanitize_chord,
    chunk_token_sequence,
    load_and_map_chords,
    load_dictionary_entry,
    load_and_map_intensities,
)
from lib.IntensityExtractor import (
    discretize_intensity_records,
    discretize_intensity_tsv,
)


class TestChordSanitization(unittest.TestCase):
    """Verifies enharmonic normalization and chord string sanitization."""

    def test_sharp_to_flat_enharmonics(self):
        """Sharps must resolve to flat equivalents to prevent vocabulary bloat."""
        self.assertEqual(sanitize_chord("C#"), "[CHORD_Db]")
        self.assertEqual(sanitize_chord("D#"), "[CHORD_Eb]")
        self.assertEqual(sanitize_chord("F#"), "[CHORD_Gb]")
        self.assertEqual(sanitize_chord("G#"), "[CHORD_Ab]")
        self.assertEqual(sanitize_chord("A#"), "[CHORD_Bb]")

    def test_extended_and_quality_preservation(self):
        """Qualities (maj7, m7, dim, aug, power) must be preserved across normalization."""
        self.assertEqual(sanitize_chord("C#maj7"), "[CHORD_Dbmaj7]")
        self.assertEqual(sanitize_chord("F#m"), "[CHORD_Gbm]")
        self.assertEqual(sanitize_chord("G#m7"), "[CHORD_Abm7]")
        self.assertEqual(sanitize_chord("D#dim"), "[CHORD_Ebdim]")
        self.assertEqual(sanitize_chord("A#power"), "[CHORD_Bbpower]")

    def test_slash_chords(self):
        """Both root and bass in slash chords must resolve enharmonically."""
        self.assertEqual(sanitize_chord("F#/A#"), "[CHORD_Gb/Bb]")
        self.assertEqual(sanitize_chord("C#/F#"), "[CHORD_Db/Gb]")
        self.assertEqual(sanitize_chord("G#m7/C#"), "[CHORD_Abm7/Db]")

    def test_music21_flat_replacement(self):
        """music21 '-' flat syntax must convert to 'b'."""
        self.assertEqual(sanitize_chord("B-"), "[CHORD_Bb]")
        self.assertEqual(sanitize_chord("E-m"), "[CHORD_Ebm]")

    def test_unidentified_chord_handling(self):
        """Unidentifiable chords must standardize to [CHORD_NC]."""
        self.assertEqual(
            sanitize_chord("Chord Symbol Cannot Be Identified"), "[CHORD_NC]"
        )
        self.assertEqual(sanitize_chord("Cannot Be Identified"), "[CHORD_NC]")


class TestChunkTokenSequence(unittest.TestCase):
    """Verifies the sliding window chunking algorithm and state propagation."""

    def test_grouping_by_bar(self):
        """Slices token sequence into discrete bar groups."""
        tokens = [
            "Bar_None", "Position_0", "Pitch_60", "Duration_1.0.8",
            "Bar_None", "Position_0", "Pitch_62", "Duration_1.0.8",
            "Bar_None", "Position_0", "Pitch_64", "Duration_1.0.8",
        ]
        chunks = chunk_token_sequence(tokens, chunk_size_bars=1, stride_bars=1)
        self.assertEqual(len(chunks), 3)

    def test_state_propagation_across_chunk_boundaries(self):
        """Entering time signature and active chord must carry over into subsequent chunks."""
        tokens = [
            "Bar_None", "TimeSig_3/4", "Position_0", "[CHORD_Dm]", "Pitch_62", "Duration_1.0.8",
            "Bar_None", "Position_0", "Pitch_65", "Duration_1.0.8",  # No TimeSig or Chord explicit
        ]
        chunks = chunk_token_sequence(tokens, chunk_size_bars=1, stride_bars=1)
        self.assertEqual(len(chunks), 2)

        chunk_1 = chunks[1]
        self.assertIn("TimeSig_3/4", chunk_1, "Time signature from Bar 1 must propagate into Bar 2 chunk.")
        self.assertIn("[CHORD_Dm]", chunk_1, "Active chord from Bar 1 must propagate into Bar 2 chunk.")

    def test_stride_overlap(self):
        """Verifies sliding window count with stride < chunk_size."""
        tokens = []
        for i in range(8):
            tokens.extend(["Bar_None", "Position_0", f"Pitch_{60 + i}", "Duration_1.0.8"])

        # 8 bars, chunk_size=4, stride=2 -> 3 chunks: [0..3], [2..5], [4..7]
        chunks = chunk_token_sequence(tokens, chunk_size_bars=4, stride_bars=2)
        self.assertEqual(len(chunks), 3)


class TestCriticalBug1MidiTokSpecialTokens(unittest.TestCase):
    """
    Catches Bug #1:
    MidiTok enforces Type_Value naming on tokens.
    Tokens without underscores (like '[SEP]') get renamed to '[SEP]_None' in the vocabulary.
    Looking up '[SEP]' fails and defaults to ID 0 (which is an unrelated chord token).
    """

    def setUp(self):
        self.chord_vocab = ["[CHORD_C]", "[CHORD_Dm]", "[CHORD_G7]"]
        self.core = PianoArrangerTokenizer(positions_per_beat=8)
        self.core.initialize_new(self.chord_vocab)

    def test_chunk_separator_in_vocab_without_mangling(self):
        """
        [SEP] token must exist directly in tokenizer.vocab under SYSTEM_TOKENS['chunk_separator'].
        Fails if MidiTok renamed it to '[SEP]_None' while code queries '[SEP]'.
        """
        sep = SYSTEM_TOKENS["chunk_separator"]
        self.assertIn(
            sep,
            self.core.tokenizer.vocab,
            f"Bug #1 caught: '{sep}' is missing from tokenizer vocabulary! "
            f"MidiTok likely renamed it to '{sep}_None'.",
        )

    def test_chunk_separator_does_not_map_to_fallback_id_zero(self):
        """
        [SEP] token must not resolve to fallback ID 0.
        ID 0 belongs to the first chord in the vocabulary, not the separator.
        """
        sep = SYSTEM_TOKENS["chunk_separator"]
        token_id = self.core.tokenizer.vocab.get(sep, 0)
        self.assertNotEqual(
            token_id,
            0,
            f"Bug #1 caught: '{sep}' mapped to fallback ID 0! "
            f"(ID 0 is '{self.core.tokenizer[0]}', causing separator to be misidentified as a chord).",
        )

    def test_intensity_control_tokens_registered_in_vocab(self):
        """All 5 intensity control tokens must be present in vocabulary with valid IDs."""
        for intensity_tok in SYSTEM_TOKENS["control_intensities"]:
            self.assertIn(
                intensity_tok,
                self.core.tokenizer.vocab,
                f"Intensity token '{intensity_tok}' is missing from tokenizer vocabulary.",
            )
            self.assertNotEqual(
                self.core.tokenizer.vocab.get(intensity_tok, 0),
                0,
                f"Intensity token '{intensity_tok}' mapped to fallback ID 0.",
            )


class TestCriticalBug2StitchedContextDecoding(unittest.TestCase):
    """
    Catches Bug #2:
    During decoding, the previous literal arrangement stitched before [SEP]
    must be sliced off so the decoded melody MIDI only contains the current chunk's melody.
    """

    def test_decode_slices_off_previous_literal_arrangement(self):
        """
        Decoded melody MIDI must ONLY contain current chunk melody notes,
        not the previous literal notes passed as context before [SEP].
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            in_dir = os.path.join(temp_dir, "tokens_in")
            out_dir = os.path.join(temp_dir, "decoded_out")
            os.makedirs(in_dir)

            core = PianoArrangerTokenizer(positions_per_beat=8)
            core.initialize_new(["[CHORD_C]"])
            sep = SYSTEM_TOKENS["chunk_separator"]

            # Construct stitched sequence: 2 notes of previous literal + [SEP] + 1 note of current melody
            previous_literal_tokens = [
                "Bar_None", "TimeSig_4/4", "Position_0", "Pitch_48", "Duration_1.0.8",
                "Position_8", "Pitch_52", "Duration_1.0.8",
            ]
            current_melody_tokens = [
                "Bar_None", "TimeSig_4/4", "Position_0", "[CHORD_C]", "Pitch_72", "Duration_1.0.8",
            ]
            stitched_tokens = previous_literal_tokens + [sep] + current_melody_tokens

            token_ids = [core.tokenizer.vocab.get(t, 0) for t in stitched_tokens]

            token_file = os.path.join(in_dir, "test_chunk001_MELODY_CHORDS.json")
            with open(token_file, "w") as f:
                json.dump({"ids": token_ids, "tokens": stitched_tokens}, f)

            core.decode_directory(in_dir, out_dir)

            decoded_midi = os.path.join(out_dir, "test_chunk001", "test_chunk001_MELODY.mid")
            self.assertTrue(os.path.exists(decoded_midi), "Decoded melody MIDI was not created.")

            score = symusic.Score(decoded_midi)
            self.assertEqual(len(score.tracks), 1, "Decoded melody score should have exactly 1 track.")
            
            note_count = len(score.tracks[0].notes)
            self.assertEqual(
                note_count,
                1,
                f"Bug #2 caught: Decoded melody contains {note_count} notes instead of 1! "
                f"Previous literal context was not stripped before MIDI decoding.",
            )

    def test_no_spurious_separator_chord_in_recovered_tsv(self):
        """
        When [SEP] is decoded, it must not be mistaken for a chord token (e.g. [CHORD_A+/F])
        and written into the recovered chords TSV.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            in_dir = os.path.join(temp_dir, "tokens_in")
            out_dir = os.path.join(temp_dir, "decoded_out")
            os.makedirs(in_dir)

            core = PianoArrangerTokenizer(positions_per_beat=8)
            core.initialize_new(["[CHORD_C]"])
            sep = SYSTEM_TOKENS["chunk_separator"]

            stitched_tokens = [
                "Bar_None", "TimeSig_4/4", "Position_0", "Pitch_48", "Duration_1.0.8",
                sep,
                "Bar_None", "TimeSig_4/4", "Position_0", "[CHORD_C]", "Pitch_72", "Duration_1.0.8",
            ]
            token_ids = [core.tokenizer.vocab.get(t, 0) for t in stitched_tokens]

            token_file = os.path.join(in_dir, "test_chunk001_MELODY_CHORDS.json")
            with open(token_file, "w") as f:
                json.dump({"ids": token_ids, "tokens": stitched_tokens}, f)

            core.decode_directory(in_dir, out_dir)

            recovered_tsv = os.path.join(
                out_dir, "test_chunk001", "test_chunk001_CHORDS_RECOVERED.tsv"
            )
            self.assertTrue(os.path.exists(recovered_tsv), "Recovered chords TSV was not created.")

            with open(recovered_tsv, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("C\t", content, "Expected chord 'C' must be in recovered TSV.")
            self.assertNotIn(
                "A+/F",
                content,
                "Bug #2 caught: Spurious chord 'A+/F' found in recovered TSV from misidentified [SEP] token!",
            )


class TestIntensityParsingAndClamping(unittest.TestCase):
    """
    Catches Bug #3:
    In TokenizationCore.py load_and_map_intensities():
      - 'val = max(0, 4)' unconditionally overwrites intensity to 4.
      - float offsets parsed with int(parts[1]) throw ValueError and get dropped.
    """

    def test_intensity_clamping_math(self):
        """Verifies correct mathematical clamping across inputs [-1, 0, 1, 2, 3, 4, 5]."""
        test_inputs = [-1, 0, 1, 2, 3, 4, 5]
        expected_clamped = [0, 0, 1, 2, 3, 4, 4]
        for val, exp in zip(test_inputs, expected_clamped):
            clamped = max(0, min(val, 4))
            self.assertEqual(
                clamped,
                exp,
                f"Clamping {val} expected {exp}, got {clamped}",
            )

    def test_load_and_map_intensities_tsv_parsing(self):
        """Tests parsing of TSV containing intensity values and quarter offsets."""
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".tsv", encoding="utf-8") as tf:
            tf.write("Timestamp\tIntensity\tOffset\n")
            tf.write("[00:00.000]\t0\t0.0\n")
            tf.write("[00:02.000]\t2\t4.0\n")
            tf.write("[00:04.000]\t4\t8.0\n")
            tsv_path = tf.name

        try:
            tpq = 480
            intensities = load_and_map_intensities(tsv_path, tpq)

            # At tick 0 (offset 0.0), intensity is 0 -> [INTENSITY_LOWEST]
            # At tick 1920 (offset 4.0), intensity is 2 -> [INTENSITY_MEDIUM]
            self.assertIn(0, intensities, "Tick 0 missing from mapped intensities.")
            self.assertIn(1920, intensities, "Tick 1920 missing from mapped intensities.")

            # Catches val = max(0, 4) bug which sets everything to [INTENSITY_HIGHEST]
            self.assertEqual(
                intensities[0],
                [SYSTEM_TOKENS["control_intensities"][0]],
                f"Bug #3 caught: Tick 0 intensity mapped to '{intensities[0]}' instead of LOWEST (val = max(0, 4) bug).",
            )
            self.assertEqual(
                intensities[1920],
                [SYSTEM_TOKENS["control_intensities"][2]],
                f"Bug #3 caught: Tick 1920 intensity mapped to '{intensities[1920]}' instead of MEDIUM.",
            )
        finally:
            if os.path.exists(tsv_path):
                os.remove(tsv_path)

    def test_rich_schema_intensity_parsing(self):
        """Verifies parsing of Rich Schema TSV (Timestamp, MIDI_Offset, Intensity, Density_EPS, Bass, Diff)."""
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".tsv", encoding="utf-8") as tf:
            tf.write("Timestamp\tMIDI_Offset\tIntensity\tDensity_EPS\tBass_Median_Pitch\tPitch_Difference\n")
            tf.write("[00:00.000]\t0.0\t0\t2.5\tN/A\tN/A\n")
            tf.write("[00:02.000]\t4.0\t1\t6.0\t52.0\t16.0\n")
            tf.write("[00:04.000]\t8.0\t3\t16.0\t39.0\t28.0\n")
            tf.write("[00:06.000]\t12.0\t4\t20.0\t32.0\t36.0\n")
            tsv_path = tf.name

        try:
            tpq = 480
            intensities = load_and_map_intensities(tsv_path, tpq)

            self.assertEqual(intensities[0], [SYSTEM_TOKENS["control_intensities"][0]])
            self.assertEqual(intensities[4 * tpq], [SYSTEM_TOKENS["control_intensities"][1]])
            self.assertEqual(intensities[8 * tpq], [SYSTEM_TOKENS["control_intensities"][3]])
            self.assertEqual(intensities[12 * tpq], [SYSTEM_TOKENS["control_intensities"][4]])
        finally:
            if os.path.exists(tsv_path):
                os.remove(tsv_path)

    def test_continuous_schema_fallback_discretization(self):
        """Verifies continuous TSV without Intensity column triggers Method A on-the-fly."""
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".tsv", encoding="utf-8") as tf:
            tf.write("Timestamp\tMIDI_Offset\tDensity_EPS\tBass_Median_Pitch\tPitch_Difference\n")
            tf.write("[00:00.000]\t0.0\t2.0\tN/A\tN/A\n")
            tf.write("[00:02.000]\t4.0\t6.0\t52.0\t14.0\n")
            tf.write("[00:04.000]\t8.0\t11.0\t44.0\t22.0\n")
            tf.write("[00:06.000]\t12.0\t16.0\t39.0\t28.0\n")
            tf.write("[00:08.000]\t16.0\t22.0\t28.0\t42.0\n")
            tsv_path = tf.name

        try:
            tpq = 480
            intensities = load_and_map_intensities(tsv_path, tpq)

            # Measure 0 (missing bass, low density) -> Level 0 [LOWEST]
            self.assertEqual(intensities[0], [SYSTEM_TOKENS["control_intensities"][0]])
            # Measure 4 (sub-bass 28.0, high density 22.0) -> Level 4 [HIGHEST]
            self.assertEqual(intensities[16 * tpq], [SYSTEM_TOKENS["control_intensities"][4]])
        finally:
            if os.path.exists(tsv_path):
                os.remove(tsv_path)


class TestMethodADiscretization(unittest.TestCase):
    """Unit tests verifying Method A discretization rules and safeguards."""

    def test_acoustic_safeguards(self):
        """Tests Level 4 sub-bass gating, treble caps, and missing-bass rules."""
        records = [
            # 1. Missing bass intro -> Level 0
            {"density_eps": 2.0, "bass_median_pitch": "N/A", "pitch_diff": "N/A"},
            # 2. Treble music-box -> Level 0 (bass >= 60, low density)
            {"density_eps": 4.0, "bass_median_pitch": 64.0, "pitch_diff": 10.0},
            # 3. Mid verse -> Level 1 or 2
            {"density_eps": 8.0, "bass_median_pitch": 48.0, "pitch_diff": 18.0},
            # 4. Energetic chorus without sub-bass -> Demoted from 4 to 3 (bass = 44.0 > 36.0)
            {"density_eps": 24.0, "bass_median_pitch": 44.0, "pitch_diff": 22.0},
            # 5. True peak climax -> Level 4 (sub-bass <= 36.0, wide spread, high density)
            {"density_eps": 22.0, "bass_median_pitch": 28.0, "pitch_diff": 40.0},
        ]

        levels = discretize_intensity_records(records)
        self.assertEqual(len(levels), 5)
        self.assertEqual(levels[0], 0, f"Expected Level 0 for missing bass, got {levels[0]}")
        self.assertEqual(levels[1], 0, f"Expected Level 0 for treble register, got {levels[1]}")
        self.assertIn(levels[2], [1, 2], f"Expected Level 1 or 2 for verse, got {levels[2]}")
        self.assertEqual(levels[3], 3, f"Expected demotion to Level 3 due to bass > 36.0, got {levels[3]}")
        self.assertEqual(levels[4], 4, f"Expected Level 4 for sub-bass drop, got {levels[4]}")

    def test_discretize_intensity_tsv_roundtrip(self):
        """Verifies TSV string enrichment with Intensity column."""
        raw_tsv = (
            "Timestamp\tMIDI_Offset\tDensity_EPS\tBass_Median_Pitch\tPitch_Difference\n"
            "[00:00.000]\t0.0\t1.5\tN/A\tN/A\n"
            "[00:02.000]\t4.0\t18.0\t30.0\t36.0\n"
        )
        enriched = discretize_intensity_tsv(raw_tsv)
        lines = enriched.strip().splitlines()
        header = lines[0].split("\t")

        self.assertEqual(header, ["Timestamp", "MIDI_Offset", "Intensity", "Density_EPS", "Bass_Median_Pitch", "Pitch_Difference"])
        row0 = lines[1].split("\t")
        self.assertEqual(row0[2], "0")
        row1 = lines[2].split("\t")
        self.assertEqual(row1[2], "4")


class TestEndToEndTokenizationPipeline(unittest.TestCase):
    """Integration test verifying complete tokenization and detokenization lifecycle."""

    def test_single_song_roundtrip(self):
        """Processes a synthetic song through tokenization and detokenization, verifying integrity."""
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = os.path.join(temp_dir, "input")
            song_dir = os.path.join(input_dir, "test_song")
            token_dir = os.path.join(temp_dir, "tokens")
            decode_dir = os.path.join(temp_dir, "decoded")
            os.makedirs(song_dir)

            tpq = 480

            # 1. Create MELODY MIDI (1 bar, 2 notes)
            melody_score = symusic.Score()
            melody_score.ticks_per_quarter = tpq
            m_track = symusic.Track(name="Melody", program=0)
            m_track.notes.append(symusic.Note(time=0, duration=tpq, pitch=72, velocity=100))
            m_track.notes.append(symusic.Note(time=tpq, duration=tpq, pitch=74, velocity=100))
            melody_score.tracks.append(m_track)
            melody_score.dump_midi(os.path.join(song_dir, "test_song_MELODY.mid"))

            # 2. Create LITERAL MIDI (1 bar, 3 notes)
            literal_score = symusic.Score()
            literal_score.ticks_per_quarter = tpq
            l_track = symusic.Track(name="Piano", program=0)
            l_track.notes.append(symusic.Note(time=0, duration=tpq * 2, pitch=48, velocity=90))
            l_track.notes.append(symusic.Note(time=0, duration=tpq, pitch=60, velocity=80))
            l_track.notes.append(symusic.Note(time=tpq, duration=tpq, pitch=64, velocity=85))
            literal_score.tracks.append(l_track)
            literal_score.dump_midi(os.path.join(song_dir, "test_song_LITERAL.mid"))

            # 3. Create CHORDS TSV
            with open(os.path.join(song_dir, "test_song_CHORDS.tsv"), "w", encoding="utf-8") as f:
                f.write("Timestamp\tChord\tOffset\n")
                f.write("[00:00.000]\tC\t0.0\n")

            # 4. Tokenize
            core = PianoArrangerTokenizer(positions_per_beat=8)
            core.initialize_new(["[CHORD_C]"])
            core.process_directory(
                input_dir,
                token_dir,
                chunk_size=1,
                stride=1,
                use_chunking=False,
                use_stitching=False,
            )

            # Verify token JSONs generated
            mel_json = os.path.join(token_dir, "test_song_chunk000_MELODY_CHORDS.json")
            lit_json = os.path.join(token_dir, "test_song_chunk000_LITERAL.json")
            self.assertTrue(os.path.exists(mel_json), "MELODY_CHORDS token JSON missing.")
            self.assertTrue(os.path.exists(lit_json), "LITERAL token JSON missing.")

            # Verify velocity stripping
            with open(mel_json) as f:
                mel_data = json.load(f)
            self.assertFalse(
                any(t.startswith("Velocity_") for t in mel_data["tokens"]),
                "Velocity tokens must be stripped from melody tokens.",
            )

            with open(lit_json) as f:
                lit_data = json.load(f)
            self.assertFalse(
                any(t.startswith("Velocity_") for t in lit_data["tokens"]),
                "Velocity tokens must be stripped from literal tokens.",
            )

            # 5. Decode
            core.decode_directory(token_dir, decode_dir)
            dec_mel = os.path.join(decode_dir, "test_song_chunk000", "test_song_chunk000_MELODY.mid")
            dec_lit = os.path.join(decode_dir, "test_song_chunk000", "test_song_chunk000_LITERAL.mid")
            self.assertTrue(os.path.exists(dec_mel), "Decoded melody MIDI missing.")
            self.assertTrue(os.path.exists(dec_lit), "Decoded literal MIDI missing.")

            # Verify note count recovery
            dec_mel_score = symusic.Score(dec_mel)
            self.assertEqual(len(dec_mel_score.tracks[0].notes), 2, "Decoded melody note count mismatch.")

            dec_lit_score = symusic.Score(dec_lit)
            self.assertEqual(len(dec_lit_score.tracks[0].notes), 3, "Decoded literal note count mismatch.")


if __name__ == "__main__":
    unittest.main()
