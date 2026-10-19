# Animenz-S2S Dataset Generation Rules (Technical Specification v34.0)

## I. Philosophy & Pipeline Overview
This pipeline transforms raw MusicXML (Piano Grand Staff) into two parallel MIDI representations for training a Language Model. The core challenge is dealing with the "Messy Reality" of engraved scores (visuals) vs. the "Musical Truth" (sound).

### The Two Streams
1.  **LITERAL_MIDI (Input Token):** A structural mirror of the score. Preserves repeats, layout, and visual artifacts. It represents *what the pianist reads*.
2.  **MELODY_DRAFT (Target Token):** A reduced, idealized monophonic reduction of the primary musical line. It represents *what the pianist brings out*.

---

## II. The Parsing Engine (`MxlPretokenizer`)
Robust parsing is the foundation. We do not blindly trust MusicXML duration attributes.

### 1. Content-Aware Measure Duration
MusicXML measures often lie about their duration for visual reasons. We use a **"Trust, but Verify"** approach:
* **The Ghost Measure Check:** If a measure contains *only* hidden objects (`print-object="no"`) or metadata, its duration is forced to `0.0`. This removes visual-only padding (e.g., key change spacers) from the timeline.
* **The Split Measure Check (Waterline Analysis):** We calculate the "Waterline"—the absolute end offset of the last printable note/rest *inside any voice* (using `.flatten()`).
    * *Trigger:* If `Waterline < TimeSignature_Duration` (with tolerance).
    * *Action:* The measure is truncated to the Waterline. This correctly handles performance scores where a 4/4 measure is visually split into two 2/4 chunks without explicit rest padding.

### 2. Tremolo Baking (LITERAL.midi specific)
`music21`'s default expansion is unreliable without context. We implement a **Custom Tremolo Baker**:
* **Single Tremolo:** (One note with dashes) Repeats the pitch.
* **Fingered Tremolo:** (Two notes, usually half-duration) Alternates Pitch A and Pitch B.
* **Duration Math:** We calculate the "step size" (speed) based on the `numberOfMarks` (beams).
    * *Rule:* 3 beams = 32nd notes. The baker generates the literal sequence of notes to fill the duration, ensuring the LM learns the *sound* of the tremolo, not just the symbol.

---

## III. The Melody Extraction Engine ("The Brain")
Reducing a Grand Staff to a single melody line requires a complex heuristic state machine, not a simple filter.

### 1. The Dynamic Exclusion State Machine
This system determines when the Upper Staff (Staff 1) has ceded the melody to the Lower Staff (Staff 2).

* **Nuance - Placement Detection:** The parser distinguishes between dynamics placed `above` Staff 1 (Global/Melodic) vs. `below` (Local/Accompaniment).
* **The Trigger Condition:**
    * State is tracked measure-by-measure via `curr_u` (Upper Volume) and `curr_l` (Lower Volume).
    * *Exclusion Starts* when `curr_u < curr_l` (strictly quieter).
* **The Target Voice:** Exclusion doesn't silence the whole staff immediately. It targets the `target_voice_id` (the voice with the highest median pitch), assuming that if the RH is quiet, its top line is likely accompaniment figuration.
* **The Termination Conditions:**
    * **Volume Equalization:** `curr_u >= curr_l`.
    * **Voice Structure Change:** The set of active voice IDs changes (implies new musical phrase).
    * **Octave Jump:** The excluded voice jumps > 1 octave (implies a reset or new melody).
    * **End of Score:** Cleanup.

### 2. "Safe Mode" & The Dominance Tracker
A critical failure mode in earlier versions was the "Mid-Measure Handoff," where a measure starting Loud (Melody) and ending Quiet (Accompaniment) was discarded entirely because the *final* state was "Lower is Louder."

* **The Fix (`upper_ever_dominant`):** We track a flag throughout the measure's timeline. If the Upper Staff was *ever* louder than the Lower Staff during the measure, we consider it "Dominant" for that measure.
* **Staff Selection Logic:**
    * **Default:** Output Upper Staff.
    * **Swap to Lower:** ONLY if `curr_u < curr_l` **AND** `upper_ever_dominant` is `False`. (i.e., The Upper staff was accompaniment *the entire time*).

### 3. Preservation Heuristics (Tenuto Protection)
Even when "Safe Mode" swaps to the Lower Staff, the Upper Staff might still contain essential notes.
* **Rule:** If the Lower Staff contains a high density of **Tenuto** articulations (Count >= 3, or >1 with dense neighbors), we assume *it* is the melody and the swap was correct.
* **Merge:** Conversely, if we stick to the Upper Staff, but the Lower Staff has these Tenutos, we **MERGE** those specific Lower Staff notes into the draft (creating a polyphonic draft momentarily) to preserve the counter-melody.

---

## IV. The Melody Reducer ("The Sculptor")
Post-processing to simplify the extracted melody into a training target.

### 1. Ostinato Removal (Texture Cleaning)
* **Pattern Matching:** Detects 3-4 note sequences that repeat (Loop count >= 2).
* **Context Awareness:**
    * *Supported:* If other notes exist simultaneously (in other parts), the ostinato is **DELETED** (it is accompaniment).
    * *Unsupported:* If the ostinato is the only thing playing, it is **REPLACED** with a single high note spanning the duration (preserving the structural "idea" of sound).
* **Nuclear Cleanup:** We use `_clear_range_in_part` to wipe the timeline before insertion, preventing "ghost" lower-octave notes from lingering after replacement.

### 2. Run Simplification (Virtuosity Reduction)
Converts scalar runs into structural grids.

* **Detection Gates:**
    * **Chronological Guard:** Notes with `offset < last_processed` are rejected. This fixes `music21` parsing bugs where notes from later measures ("Ghost Notes") report offset 0.0.
    * **Strict Duration Gating:** Notes must be shorter than `1.0 / density` to count.
    * **Ratio Guard:** A run breaks if adjacent durations differ by ratio > 2.5 (e.g., 32nd notes cannot merge with Quarter notes).
* **Granular Selection:** We keep notes based on specific flags:
    * `RUN_INCLUDE_ARTICULATED`: Accents/Tenutos are structural anchors.
    * `RUN_LEAP_THRESHOLD`: Large intervals (e.g., > 7 semitones) imply a structural melodic change.
    * Start/End notes are optional (Configurable).
* **The Grid:** Selected notes are merged by Pitch Class (octave insensitive) and snapped to a power-of-2 rhythmic grid, simplifying rubato/tuplet timing into clean "Target" tokens.

---

## V. Changelog Specifications
A `{score}_CHANGELOG.txt` provides audit trails.

**Format:** `[Part] [Mm. Start-End] Description`

**Key Log Events:**
1.  **Dynamic Exclusion:** Logs the *Trigger* conditions (`U=50 < L=80`) and the *Termination* reason (e.g., `Volumes Equalized`).
2.  **Safe Mode Merge:** Logs when Lower Staff notes are injected due to Tenuto protection.
3.  **Run Simplification:** Logs the detected note count, beat length, and a preview of the pitches (e.g., `[C4, D4, E4...]`) to verify if Ghost Notes were caught.