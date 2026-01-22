The MusicXMLs are exported from MuseScore `.mscz` files and strictly follow MuseScore's voicing rules: 
- **Voice 1:** Main voice (Upper Staff).
- **Voice 5:** Main voice (Lower Staff).
- **Voices 2-4 / 6-8:** Extra voices that pop in/out without requiring rests. 
- *Note:* The "Grand Staff" is often split into two separate MusicXML Parts (Part 1 = RH, Part 2 = LH). The processing script aggregates these into a single "System Measure" before processing.

**Input Handling:**
- **Pickup Measures:** Treated as full measures with `Rest` padding inserted at the start to ensure strict grid alignment.
- **Empty Measures:** Explicitly assigned the duration of the current Time Signature to prevent 0-length collapse in sparse tracks.

---

### **1. LITERAL MIDI (The Grid)**
A "literal" rendition preserving strict timing and structure.

- **Tracks:** 4 tracks (SATB).
  - **S:** Upper Staff Main Voice.
  - **A:** Upper Staff Extra Voices.
  - **T:** Lower Staff Main Voice.
  - **B:** Lower Staff Extra Voices.
- **Timing:** Grid-like behavior. Tempo/Rubato is discarded. Measures are fixed-size containers based on the Time Signature.
- **Articulations:** Accents/staccatos are discarded.
- **Ties:** Combined into single note events.
- **Octave Lines:** 8va/8vb applied to pitch.
- **Tremolos:** "Baked in." Single/Multi-note tremolos oscillate at the noted frequency (16th/32nd).
- **Arpeggios:** Treated as block chords (no roll).
- **Grace Notes:**
  - Do not push main notes forward.
  - occupy the 1/16th duration *before* the main note.
  - If a chain exists, they split that 1/16th equally.
  - Overwrite/shorten the preceding note/rest to fit. (Exception: Deleted if at very start of score or crossing a barline).

---

### **2. MELODY DRAFT MIDI**
A consolidated track used to isolate the melody. It must share the **exact timescale and measure boundaries** of the Literal MIDI.

**Pipeline:**
1.  **Generate Draft:** The script produces a multi-track MIDI (`_MELODY_DRAFT.mid`) containing candidate notes.
2.  **Manual Edit:** I manually delete non-melody notes from this draft.
3.  **Flatten:** A final script squashes the edited draft into a single track.

**Melody Extraction Rules (The "Smart Selection"):**

**A. Ambiguity Detection**
- A measure is **Ambiguous** if:
  - More than 1 voice is active on either staff.
  - *OR* The **Next Measure** (Lookahead = 1) has multiple voices (catches pickups).
- **Action:** If ambiguous, export **ALL** voices + a "CHECK HERE" marker.

**B. Dynamic Exclusion (The "Original Rules")**
*Active only if `ENABLE_DYNAMIC_EXCLUSION` is True.*
- **Trigger:** If `Upper Staff Volume` < `Lower Staff Volume`.
  - *Upper Volume:* Set by dynamics above Staff 1.
  - *Lower Volume:* Set by dynamics on Staff 2.
  - *Global Volume:* Dynamics below Staff 1 reset BOTH.
- **Action:** Identify the **Highest Pitched Voice** on the Upper Staff (using Median Pitch). Exclude this voice from the draft.
- **Safety:** Exclusion stops immediately if:
  - Volumes equalize.
  - Voice structure changes (new voice enters/leaves).
  - The excluded voice jumps > 12 semitones (Octave Jump).
  - 8 measures have passed (Time Limit).
- **Edge Case (Lone Lower):** If a Lower dynamic appears without an accompanying Upper dynamic in the same measure, **disable exclusion** but prioritize Lower Staff for melody selection.

**C. Safe Mode (Default)**
If the measure is not ambiguous and no exclusion is active:
- **Default:** Melody is in **Upper Staff (S)**.
- **Override:** If `Upper Volume < Lower Volume` (and not excluded), Melody is in **Lower Staff (T)**.

**D. Note Processing**
- **Chords:** Reduced to top note only.
- **Tremolos:** Collapsed to single events (not baked).
- **Grace Notes:** Deleted.
- **Arpeggios:** Removed (block chords).