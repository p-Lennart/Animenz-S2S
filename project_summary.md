# Animenz Style Piano Arranger (Seq2Seq ML)

## 1. Project Overview

This project trains a Sequence-to-Sequence (Seq2Seq) Transformer to arrange simple piano melodies and chord sheets into highly complex, virtuoso piano arrangements (specifically emulating the Animenz anime-piano style).

**The Core Objective:** Map an input sequence of `[Melody Notes + Chords + Structural Metadata]` to a target sequence of `[Literal Piano Arrangement Notes]`.

**The Model Architecture:** **T5 (Encoder-Decoder)**. _Rationale:_ We explicitly avoid Decoder-only (GPT) models. Autoregressive language models use causal attention, which causes them to "forget" the input melody and hallucinate continuations of their own complex left-hand patterns. T5's Cross-Attention mathematically forces the Decoder to continuously reference the Encoder's melody/chord states during generation.

## 2. The Custom Tokenization Engine (`TokenizationCore.py`)

Because standard MIDI tokenizers bloat the vocabulary or misalign harmonies, we built a highly customized pipeline extending `MidiTok`'s **REMI** (Revamped MIDI) format.

### Key Tokenization Mechanics:

1. **Absolute Enharmonic Normalization:** Chord strings are rigorously parsed to prevent vocabulary bloat. `music21`'s `-` flat syntax is enforced, and enharmonics are strictly resolved (e.g., `C#maj7` -> `[CHORD_Dbmaj7]`).
    
2. **Velocity Stripping:** Velocity is inherently noisy in transcribed data. We force all velocities to 127 during extraction, and the Tokenizer actively **strips** `Velocity_` tokens during encoding to save 25% of the Transformer's context window. The Decoder automatically re-injects `Velocity_127` before reconstructing the MIDI.
    
3. **The State Machine (Position Injection):** Chords and Intensities are supplied via `_CHORDS.tsv` and `_INTENSITY.tsv` files. The Tokenizer calculates their absolute ticks, groups them, and injects them immediately following the correct `Position_X` token within the REMI sequence.
    
4. **Deterministic Dictionary Bypass (`_CHORD_ENTRY.tsv`):** To prevent heuristic guessing errors when generating artificial chord dictionary data, the Tokenizer looks for a special `_ENTRY` suffix. If found, it bypasses math/sanitization and injects the literal token string at `Position_0`, guaranteeing O(1) complexity and 100% fidelity.
    

## 3. The Multi-Phase Curriculum Strategy

If we mix basic synthetic data and virtuoso data in a single dataset, Neural Networks optimize for the path of least resistance (Mode Collapse), resulting in lazy, basic outputs. Therefore, we use a strict two-stage Transfer Learning pipeline.

### Phase 1: Foundation Pre-Training ("Music School")

- **Dataset:** POP909 (909 Chinese pop songs) + The Master Chord Dictionary.
    
- **Goal:** Teach the model the mathematical definition of our custom vocabulary. It learns that `[CHORD_Fm]` correlates to F, Ab, C pitches, and it learns standard Seq2Seq translation behavior.
    
- **The Master Chord Dictionary:** POP909 lacks complex jazz/extended chords, which would leave target vocabulary items initialized as random noise ("Dead Embeddings"). We scan both datasets, dump a `master_vocab.json`, and synthesize an empty 1-bar MIDI pair for _every_ unique chord. This guarantees 100% mathematical coverage of the embedding layer before Phase 2 begins.
    
- **Chunking Params:** 8-bar chunks, Stride = 8 (No overlap). No control tokens.
    

### Phase 2: Stylistic Fine-Tuning ("Masterclass")

- **Dataset:** ~100 curated Animenz arrangements.
    
- **Augmentation:** 1. Transposed to all 12 keys (yields 1,200 songs). 2. Input Melody is octave-normalized to C4-C5 (teaches the model to throw melodies across registers). **Crucial:** The Target Literal arrangement is _never_ pitch-altered to prevent physical desynchronization (muddy bass or falling off the 88-key limit).
    
- **Goal:** Using Phase 1's weights, the model uses all its processing power to learn _how_ to play the chords in the target style.
    
- **Chunking Params:** 8-bar chunks, Stride = 2 (Heavy overlap, yields ~30,000 highly focused sequences).
    

## 4. Solving the Context Window: Chunking & State Tracking

Standard songs exceed a Transformer's context window (~10,000+ tokens). We mandate 8-bar chunks.

**The Challenge:** If you slice a sequence at Measure 5, MidiTok crashes because it doesn't know the Time Signature, Key Signature, or the active Chord held over from Measure 4. **The Solution (`chunk_token_sequence`):** A custom sliding window algorithm that pre-calculates the active state entering every bar. It intelligently injects the active `TimeSig_`, `KeySig_`, `[CHORD_]`, and `[INTENSITY_]` into the start of the chunk without colliding with existing `Position_0` events.

## 5. Architectural Rationales & Prompting Hacks

### The `[SEP]` Token (Continuous Context Prompting)

When generating sequentially, the model needs to know what it played in the previous 8 bars so the arrangement doesn't violently change style.

- **The Trap:** We cannot put past target data into the Decoder, as it breaks positional encoding synchronization with the Encoder's current melody.
    
- **The Fix:** We pass the past context to the _Encoder_. The data is stitched as: `[Previous Target Literal] [SEP] [Current Melody+Chords]` -> `[Current Target Literal]` The T5 Encoder's bidirectional attention reads the past context to understand the active pattern (e.g., Alberti bass), and cross-attention maps perfectly to the current melody.
    

### The `[INTENSITY_X]` Control Tokens

A sliding window model doesn't know if it is arranging a Verse or a Chorus.

- **The Fix:** We extract integer intensity maps from an `_INTENSITY.tsv` file \[0-4] and map them to `SYSTEM_TOKENS` (`[INTENSITY_LOWEST]` through `[INTENSITY_HIGHEST]`).
    
- These are injected into the Phase 2 sequence. The Transformer conditional bias quickly learns that these tokens dictate the density/virtuosity of the output, giving us manual macro-structure control at inference time.
    

## 6. Directory Structure (Tens-Blocking)

The pipeline relies on prefix grouping to prevent dataset contamination.

- `00s`: Raw custom data.
    
- `10s`: POP909 raw/processed data.
    
- `20s`: Master Chord Vocab & the Dictionary Injection bridge.
    
- `30s`: Phase 1 Tokenized JSONs (`tokenizer_config.json` is generated here).
    
- `40s`: Phase 2 Tokenized JSONs (Inherits config from 30s).