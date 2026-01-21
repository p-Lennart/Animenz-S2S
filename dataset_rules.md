The MusicXMLs are exported from a musescore mscz file and follow the rules and styles outlined by the musescore documentation. Most notably, the scores (which all use a grand staff with two staves) follow musescore's rules on voices: there will always be 1 main voice spanning each stave. Each stave can have additional voices which may pop in and out at any time without requiring rests. Note that each extra track has a new track ID every time it reappears. These scores are mostly limited to 4 concurrent voices (up to 1 extra per stave).

Also note that many of the scores have pickup measures in the beginning.

These are the two MIDI files that must be derived:

1) The first MIDI file is a "literal" rendition of the score's notes, following strict rules
- It preserves all notes, key signatures, time signatures, and voice information from the source, but discards other information such as velocity and pedal events.

- The MIDI should have 4 tracks. Let SATB represent the 4 tracks, with S being the highest in pitch and B the lowest. The main upper stave voice should go in S. Extra upper stave voices (whenever they appear) should go in A. The main lower stave voice should go in T. Extra upper stave voices (whenever they appear) should go in B.

- Tempo markings, rit. and accel should be discarded without affecting note timings or durations. I'm looking for grid-like behavior in both MIDI files. Every measure should be the same size, and all MIDI notes should fit in their measure the same way they do in the score.
- Accents and other articulations should be discarded without affecting note timings or durations.
- Tied notes are combined into a single note event combining their durations.
- 8va, 8vb, etc symbols should be applied to the pitch value of the notes they cover.
- Tremolo symbols should be "baked in", oscillating the note(s) at the frequency specified by the tremolo beam/marking (typically 16th notes or 32nd notes, marked by 2 lines or 3 lines respectively). To be clear, this applies both to single note/chord "slash" tremolos, and multi-note/chord "beam" tremolos.

- Grace notes / crush notes: 
1. These notes should not push the note they're attached to forward.
2. Rather, they should sit in the 1/16th duration before the note they're attached to.
3. At the very first note of the score, where there is no previous note to steal from, grace notes should be deleted instead.
4. If there's a "chain" of multiple grace/crush notes attached to a note, they should all sit in the same 1/16th duration before the note, splitting it equally.
5. If the 1/16th duration before the attached-to note is taken, overwrite it, shortening or replacing the note that extends into the duration.
  - Previous note is short: replace entirely. Previous event is rest: "eat into" the rest - that 1/16 duration before "belongs" to the grace notes, unless it doesn't exist (i.e. at the beginning of the score.) Barlines: The 1/16 will almost never be across a barline. If it is, delete the grace notes and leave everything in place.

- Block chords with arpeggiation symbols should be treated as normal blocked chords. In contrast with the items listed above, avoid MIDI conversion "baking in" the arpeggiation notes in the wrong place.

2) The second MIDI extracts the melody from the score, deleting non-melody notes.
- It is imperative that this shares the same timescale of the first MIDI file. Whether the second MIDI is derived from the first MIDI or directly from the MusicXML, its measure boundaries should be identical to the first MIDI, and the notes left over from deletion should never deviate in start time from their counterparts in the first MIDI.
- While melody notes will be sourced from different tracks/voices, they should be combined into one track.

- Locating melody notes:
1. The melody can be in any voice.
2. The melody will be the highest pitches of the voice it's located in. Consider all single notes, and the top note of every chord.
3. If there is a dynamic marking placed ABOVE the grand staff and it's quieter than the global dynamic, the highest voice in the upper staff is guaranteed to NOT contain the melody, and can be excluded from consideration.
4. If there are no extra voices (just the upper stave main voice and the lower stave main voice):
   4a. One of the two staves is guaranteed to hold the melody up until (crucially!) 1 measure before the introduction of an extra voice anywhere in the grand staff.
   4b. If the (only) voice in the upper staff was eliminated by step 3, the melody is guaranteed to be in the voice of the lower staff.
   4c. By default, the voice in the higher stave is guaranteed to hold the melody. (90% of cases)

- Tempo markings, rit. and accel should be discarded without affecting note timings or durations.
- Accents and other articulations should be discarded without affecting note timings or durations.
- Tied notes are combined into a single note event combining their durations.
- 8va, 8vb, etc symbols should be applied to the pitch value of the notes they cover.

- All grace notes should be deleted, leaving the note they were attached to in its place.
- All tremolos and arpeggio symbols should be removed. If the tremolo is a "beam" tremolo connecting two chords, the chords should be combined into one chord for the duration of the tremolo. 

The first MIDI file can be computed completely automatically. Parts of the second MIDI file can be safely computed whenever the melody is guaranteed by the rules to be in one voice. Therefore, I suggest the following pipeline:
1. Reduce all voices to their top notes.
2. Delete all other voices when the melody voice is guaranteed.
3. Leave other voices when the melody voice is ambiguous.
4. Export to an intermediary multi-voice MELODY_DRAFT midi file in a ./staging directory.
5. I will manually extract the melody voice in ambiguous cases.
6. Run a flattening script to finalize the melody midi, taking whatever notes are left in the edited MIDI file, ignoring their channel/track assignments, and squash them into a single Melody track.

Please ask any clarifying questions that would equip you to write the most robust music21 script that implements this pipeline. I don't want to tunnel vision on a specific approach, so I'm also open to any suggestions for a better approach.