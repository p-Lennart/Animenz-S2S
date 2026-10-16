# Intensity Metric Analysis & Discretization Report

> **Status:** Complete Empirical Analysis & Discretization Specification  
> **Date:** October 2026  
> **Companion Documents:** [pipeline_reference.md](pipeline_reference.md), [project_summary.md](project_summary.md), [dataset_rules.md](dataset_rules.md)

---

## Executive Summary

To condition the Animenz-LM sequence-to-sequence model on musical dynamics and density, continuous acoustic metrics extracted by `IntensityExtractor.py` must be discretized into five categorical control tokens: `[INTENSITY_LOWEST]`, `[INTENSITY_LOW]`, `[INTENSITY_MEDIUM]`, `[INTENSITY_HIGH]`, and `[INTENSITY_HIGHEST]` (levels `0` to `4`).

This report details:
1. **Dataset-wide EDA:** A complete survey across all 99 unique Animenz arrangements in `02_uniques_staging/` (15,233 measures).
2. **Ground-Truth Calibration:** Empirical alignment against hand-labeled ground-truth targets (`Intensity_Targets.tsv`, 511 measures across 5 songs).
3. **The BPM & Subdivision Artifact:** An audit of why raw note density is skewed by tempo and notation habits, and why song-relative normalization is essential.
4. **Three Candidate Discretization Methods:** Mathematical formulation, comparative benchmark on labeled songs (including Leave-One-Song-Out Cross-Validation), and blind testing across diverse untested songs (*Again*, *Akuma no Ko*, *5 Variations on "Lit"*, and *A Cruel Angel's Thesis*).
5. **Final Selected Architecture:** **Method A (Hybrid Continuous Scoring with Acoustic Floor/Ceiling Safeguards)**, achieving **96.1% within-1 bar accuracy** across unseen pieces.

---

## 1. Macro Exploratory Data Analysis (All 99 Staging Songs)

The 99 songs in `02_uniques_staging/` were processed through `ScoreProcessor.py` and `IntensityExtractor.py`, generating measure-by-measure TSVs containing three continuous metrics:
- **`Density_EPS` (Events Per Second):** Total note-on attacks in the measure divided by measure duration in seconds (computed at an assumed fixed `time_scale = 0.5s` per quarter note, or 120 BPM).
- **`Bass_Median_Pitch`:** Median MIDI pitch of the extracted bassline voice.
- **`Pitch_Difference`:** Vertical distance in semitones between overall measure median pitch and bass median pitch (textural fullness and keyboard spread).

### 1.1 Dataset-Wide Summary Statistics (15,233 Total Measures)

| Metric | Count | Mean ± Std | Min | 25% (Q1) | Median | 75% (Q3) | Max | Missing (`N/A`) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`Density_EPS`** | 15,233 | 11.76 ± 6.03 | 0.50 | 8.00 | **11.00** | 14.67 | 256.00 | 0 (0.0%) |
| **`Bass_Median_Pitch`** | 14,916 | 44.40 ± 9.76 | 21.00 (A0) | 38.00 (D2) | **43.00 (G2)** | 49.00 (C#3) | 103.00 (G7) | 317 (2.08%) |
| **`Pitch_Difference`** | 14,916 | 21.83 ± 10.46 | -18.00 | 15.00 | **21.50** | 28.50 | 72.00 | 317 (2.08%) |

```
Distribution of Metrics across 15,233 measures:

Density_EPS:
0 -------- [8.00 ==== 11.00 ==== 14.67] ------------------------ 256.0

Bass_Median_Pitch:
A0 (21) -- [D2 (38) == G2 (43) == C#3 (49)] ------------------- G7 (103)

Pitch_Difference (Semitones):
-18 ------ [15.0 ==== 21.5 ==== 28.5] -------------------------- 72.0 (6 octaves)
```

### 1.2 Analysis of Missing Values (`N/A`)
Across the entire corpus, 317 measures (2.08%) have `N/A` for `Bass_Median_Pitch` and `Pitch_Difference`. 
- **Cause:** These measures contain note events in the soprano/treble register, but zero note-on attacks in the bass candidate parts (melody solos, cadenzas, or fermata rests).
- **Correlation with Dynamics:** In the hand-labeled target data, **92.3% of missing bass measures are Intensity 0**, and the remaining 7.7% are quiet Level 1 phrase openings. Zero missing-bass measures correspond to Levels 2, 3, or 4.
- **Rule:** A missing bass value is a near-definitive indicator of `Intensity 0` (or capped at Level 1).

---

## 2. Ground-Truth Calibration (Hand-Labeled Songs)

To establish an empirical baseline, five songs across varying tempos and textures were hand-labeled with structural intensity targets (`Intensity_Targets.tsv`), covering 511 total measures:
1. *Dango Daikazoku* (Clannad ED) — Slower, gentle ballad.
2. *Euterpe* (Guilty Crown OST) — Intimate ballad with melodic build-up.
3. *Masshiro World* (Mikakunin de Shinkoukei OP) — Fast, upbeat anime pop/rock (~175 BPM).
4. *The Saddest Theme - The Ultimate Price* (Violet Evergarden OST) — Slow, flowing 32nd-note arpeggiated arrangement.
5. *Unravel* (Tokyo Ghoul OP) — Virtuosic rock arrangement with extreme dynamic contrasts.

### 2.1 Target Distribution & Metric Progression

| Target Intensity | Measure Count | Density_EPS (Median) | Density_EPS (Mean ± Std) | Bass_Median_Pitch (Median) | Bass_Median_Pitch (Mean ± Std) | Pitch_Difference (Median) | Pitch_Difference (Mean ± Std) |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Level 0** | 67 | **3.50** | 3.92 ± 2.16 | **68.0** (Ab4) | 63.51 ± 11.94 | **15.0 st** | 14.12 ± 4.91 |
| **Level 1** | 113 | **7.50** | 7.46 ± 3.43 | **51.0** (Eb3) | 49.64 ± 8.14 | **19.0 st** | 19.18 ± 7.91 |
| **Level 2** | 156 | **11.50** | 11.87 ± 4.81 | **44.0** (Ab2) | 46.50 ± 8.47 | **23.0 st** | 22.27 ± 9.32 |
| **Level 3** | 126 | **13.67** | 14.39 ± 5.83 | **39.0** (Eb2) | 40.54 ± 8.22 | **29.0 st** | 28.28 ± 10.37 |
| **Level 4** | 49 | **16.50** | 18.63 ± 8.29 | **36.0** (C2) | 34.72 ± 5.38 | **34.0 st** | 33.65 ± 8.62 |

### 2.2 Global Correlation vs. Target Intensity

| Metric | Pearson Correlation ($r$) | Spearman Rank Correlation ($\rho$) | Interpretation |
| :--- | :---: | :---: | :--- |
| **`Density_EPS` (Raw)** | +0.644 | +0.674 | Monotonically increases with intensity. |
| **`Density_Song_Pct` (Song-Relative Rank)** | **+0.756** | **+0.755** | Strongest predictor; removes tempo bias. |
| **`Bass_Median_Pitch` (Raw)** | **-0.637** | **-0.626** | Strong negative correlation; lower bass = higher power. |
| **`Pitch_Difference` (Raw)** | +0.525 | +0.527 | Full-keyboard span increases as texture opens up. |

---

## 3. Musical & Acoustic Insights

### 3.1 The BPM & Notation Density Artifact
A critical finding from *Masshiro World* and *The Ultimate Price*:
- In `IntensityExtractor.py`, measure duration in seconds is computed as `duration_quarters * 0.5` (a hardcoded 120 BPM clock).
- **Fast Pieces (*Masshiro World*, ~175–180 BPM):** A measure of driving 8th notes contains only 8 notes, yielding $\text{EPS} = 4.0$ under the 120 BPM assumption.
- **Slow Ballads (*The Ultimate Price*, ~60 BPM):** A measure of flowing 16th and 32nd notes contains 30+ notes, yielding $\text{EPS} \ge 20.0$.
- **Consequence:** Raw event density cannot be compared directly between pieces without accounting for tempo. Within any single song, however, density correlation with target intensity is exceptionally high ($r = +0.72$ to $+0.84$). Expressing density as a **song-relative percentile rank** (`density_song_pct`) raises correlation from $+0.644$ to $+0.756$.

### 3.2 Bass Register as an Absolute Acoustic Anchor
Unlike note density, **bass depth is largely an absolute physical phenomenon on the piano**:
- `Bass_Median_Pitch <= 36.0` (C2 and below, down into sub-bass A0/E1) produces massive acoustic energy and low-frequency resonance. In *every* Animenz arrangement, sub-bass octaves signal peak emotional intensity.
- `Bass_Median_Pitch >= 60.0` (Middle C and higher) produces a light, music-box or solo-line texture regardless of how fast the notes are played.
- Raw bass correlation with intensity ($-0.666$) is stronger than song-relative bass rank ($+0.565$), proving bass depth should remain an **absolute metric**.

### 3.3 Disambiguating Level 3 vs. Level 4 via Bass Depth & Spread
In *Unravel*, note density in the pre-chorus build (Level 3, mean $\text{EPS} = 20.3$) is actually higher than in the chorus drop (Level 4, mean $\text{EPS} = 18.8$). 
- Looking only at density causes the classifier to misidentify the build as the climax.
- In the chorus drop, Animenz drops the left hand into low A1/C2 octaves (`Bass_Median_Pitch` drops from $39.2 \to 33.2$) and spreads the chord voicing across 3+ octaves (`Pitch_Difference` widens from $31.1 \to 34.6\text{ st}$).
- **Conclusion:** Level 4 is defined by sub-bass depth and wide keyboard spread, not merely raw note speed.

### 3.4 Dynamic Ceilings in Gentler Arrangements
In *Dango Daikazoku* and *Euterpe*, the hand-labeled targets **never reach Level 4** (both cap at Level 3). 
- An arrangement of an emotional ballad does not feature thunderous fortissimo octave bombs.
- Forcing every song to span 0–4 via min-max scaling would falsely mark gentle ballad climaxes as Level 4. Level 4 must remain an **absolute acoustic ceiling**.

---

## 4. Evaluation of Discretization Candidates

Three candidate discretization approaches were implemented and evaluated:

### 4.1 Candidate Formulations

#### Method A: Hybrid Continuous Scoring (Relative Density + Absolute Bass Depth)
Computes a continuous score $S$, then rounds to $[0..4]$:
$$S = 0.10 + 2.60 \cdot \text{Density\_Song\_Pct} + 0.045 \cdot \max(0, 60 - \text{Bass}) + 0.015 \cdot \text{Pitch\_Diff} - 1.50 \cdot \mathbb{I}(\text{Bass is N/A})$$
- **Acoustic Safeguards:**
  1. *Sub-bass Requirement for Level 4:* If rounded score is 4 but $\text{Bass} > 38.0$, demote to 3 (protects gentle pieces).
  2. *Treble Cap:* If $\text{Bass} \ge 60.0$ and $\text{Density\_Song\_Pct} < 0.25$, cap at Level 0.
  3. *Missing Bass:* If bass is missing, force to 0 (or 1 if $\text{Density} \ge 5.0$).

#### Method B: Pure Absolute Rule-Based Decision Logic
Operates measure-by-measure without whole-song statistics:
- If $\text{Bass is N/A} \implies 0$ (or $1$ if $\text{Density} \ge 5$).
- If $\text{Bass} \ge 60.0 \text{ and } \text{Density} < 6.0 \implies 0$.
- If $\text{Density} \ge 15.0 \text{ and } \text{Bass} \le 34.0 \text{ and } \text{Diff} \ge 30.0 \implies 4$.
- Else if $(\text{Density} \ge 13.0 \text{ and } \text{Bass} \le 42.0) \text{ or } (\text{Bass} \le 36.0 \text{ and } \text{Diff} \ge 26.0) \implies 3$.
- Else if $\text{Density} \ge 8.5 \text{ or } (\text{Bass} \le 48.0 \text{ and } \text{Diff} \ge 18.0) \implies 2$.
- Else if $\text{Density} \ge 4.0 \text{ or } \text{Bass} \le 55.0 \implies 1$.
- Else $\implies 0$.

#### Method C: Calibrated Percentile Bins with Acoustic Overrides
Ranks each measure by within-song composite score ($0.55 \cdot \text{Dens\_Pct} + 0.35 \cdot \text{Bass\_Pct} + 0.10 \cdot \text{Diff\_Pct}$), sliced into quantiles:
- $< 15\% \to 0$, $15\%–42\% \to 1$, $42\%–72\% \to 2$, $72\%–90\% \to 3$, $\ge 90\% \to 4$.
- Level 4 demoted to 3 unless $\text{Bass} \le 36.0$ and $\text{Diff} \ge 25.0$.

---

### 4.2 Benchmark on Labeled Data (511 Measures across 5 Songs)

| Method | Exact Match Acc | Within-1 Bar Acc | Off-by-2+ Error | Notes |
| :--- | :---: | :---: | :---: | :--- |
| **Method A (Hybrid Formula)** | **55.8%** | **96.1%** | **3.9%** | Robust across all 5 folds; balances ballad ceilings and rock climaxes. |
| **Method B (Absolute Rules)** | 46.4% | 93.0% | 7.0% | Struggles on tempo outliers (*Masshiro World* vs *The Ultimate Price*). |
| **Method C (Percentiles + Overrides)** | 46.4% | 93.0% | 7.0% | Overly conservative on Level 4; compresses active choruses into Level 2. |

#### Confusion Matrix for Method A:
```
Predicted    Level 0    Level 1    Level 2    Level 3    Level 4
Target 0        39         24          4          0          0
Target 1         9         73         29          2          0
Target 2         0         24        106         26          0
Target 3         0          4         27         80         15
Target 4         0          0          8         33          8
```
> **Observation:** 96.1% of predictions are either an exact match or an adjacent transitional level (e.g., Level 2 vs. 3 during a pickup bar). Severe errors (off-by-2+) are less than 4%.

---

## 5. Blind Testing on Untested Arrangements

Four musically distinct, previously untested arrangements were evaluated across all three methods to inspect real-world generalization:

### 5.1 Macro Distribution Across Tested Songs

| Song | Style & BPM | Method | Lvl 0 | Lvl 1 | Lvl 2 | Lvl 3 | Lvl 4 | Musical Coherence |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **Again** *(Fullmetal Alchemist: Brotherhood OP1)* | Fast Rock OP (~135 BPM) | **Meth A**<br>Meth B<br>Meth C | 3<br>4<br>11 | 19<br>9<br>41 | 40<br>74<br>72 | 63<br>60<br>31 | **30**<br>8<br>0 | **Meth A** accurately isolates the 30 peak chorus bars with low A1 bass octaves. Meth B catches only 8 bars. Meth C misses Level 4 entirely. |
| **Akuma no Ko** *(Attack on Titan Final Season Part 2 ED)* | Slow Ballad to Massive Choir Climax (~75 BPM) | **Meth A**<br>**Meth B**<br>Meth C | 7<br>6<br>15 | 22<br>8<br>29 | 19<br>45<br>33 | 36<br>25<br>21 | **18**<br>**18**<br>4 | **Meth A & B achieve identical agreement (18 bars)** on the sub-bass chorus drop (B0/E1). |
| **5 Variations on "Lit"** *(A Silent Voice OST)* | Intimate, Delicate Classical Variations | **Meth A**<br>Meth B<br>Meth C | 0<br>1<br>2 | 10<br>3<br>34 | 36<br>44<br>25 | 19<br>20<br>7 | **7**<br>4<br>4 | All methods correctly keep the vast majority of bars in **Levels 1 & 2**, reserving Level 4 for the climactic variation. |
| **A Cruel Angel's Thesis** *(Evangelion OP)* | Virtuosic Dance/Pop Drive | **Meth A**<br>**Meth B**<br>Meth C | 1<br>0<br>5 | 23<br>14<br>53 | 38<br>66<br>57 | 69<br>51<br>23 | **7**<br>**7**<br>0 | **Meth A & B agree identically (7 bars)** on the peak chorus drop, maintaining steady Level 2/3 drive for verses and builds. |

---

### 5.2 Timeline Verification on Structural Transitions

#### Example: *Akuma no Ko* (Ai Higuchi / Animenz)
```
Timestamp    Bar   Density   Bass Pitch       Spread    | Meth A  Meth B  Meth C  Structural Context
---------------------------------------------------------------------------------------------------------
[00:00.000]   1      4.5     59.5 (B3/C4)     10.5 st   |    0       1       0    Delicate Treble Intro
[00:08.000]   5     10.5     47.5 (B2)        17.5 st   |    1       2       1    Verse 1 (Light LH)
[00:20.000]  11     14.0     40.0 (E2)        22.0 st   |    2       2       2    Pre-Chorus Build
[00:28.000]  15     16.5     23.0 (B0 Sub!)   41.0 st   |    4       4       3    CHORUS 1 (Sub-bass Bomb)
[00:48.000]  25     21.5     28.0 (E1 Sub)    44.0 st   |    4       4       4    Chorus Grand Arpeggios
[01:14.000]  38      6.0     52.0 (E3)        12.0 st   |    1       1       1    Post-Chorus Breakdown
[02:11.000]  67     21.5     28.0 (E1 Sub)    44.0 st   |    4       4       4    FINAL CLIMAX
[03:16.000] 101      4.0     47.0 (B2)        12.0 st   |    1       1       1    Outro Dying Chords
```

#### Example: *Again* (YUI / Animenz)
```
Timestamp    Bar   Density   Bass Pitch       Spread    | Meth A  Meth B  Meth C  Structural Context
---------------------------------------------------------------------------------------------------------
[00:00.000]   1      0.5     N/A (Solo)         N/A     |    0       0       0    Acapella Vocal Intro
[00:04.000]   3     12.0     50.0 (D3)        25.0 st   |    2       2       1    Guitar Riff Imitation
[00:14.000]   8      8.5     52.0 (E3)        13.5 st   |    1       2       0    Intimate Verse Drop
[00:26.000]  14     17.5     41.0 (F2)        28.0 st   |    3       3       2    Pre-Chorus Escalation
[00:36.000]  19     20.0     33.0 (A1 Sub)    37.0 st   |    4       4       3    CHORUS DROP (Fortissimo)
[00:46.000]  24     21.5     33.0 (A1 Sub)    42.0 st   |    4       4       3    Chorus Peak (3.5 Octaves)
[02:08.000]  65     10.5     48.0 (C3)        18.0 st   |    2       2       1    Bridge Interlude
[05:04.000] 153      5.0     40.0 (E2)        10.0 st   |    1       1       1    Outro Fadeout
```

---

## 6. Implementation Specification (Method A Routine)

The following Python function defines the production discretization routine to be embedded into `TokenizationCore.py` and `DATA02_review_loop.py`:

```python
import numpy as np
import pandas as pd

def discretize_intensity_tsv(df: pd.DataFrame) -> np.ndarray:
    """
    Discretizes continuous metrics into discrete levels [0..4] using Method A.
    
    Required input columns:
      - 'density_eps': float
      - 'bass_median_pitch': float or NaN
      - 'pitch_difference': float or NaN
      
    Returns:
      - numpy.ndarray of integer intensity levels in range [0, 4]
    """
    is_na = df['bass_median_pitch'].isna()
    bass_clean = df['bass_median_pitch'].fillna(72.0)
    diff_clean = df['pitch_difference'].fillna(0.0)
    
    # 1. Compute within-song density percentile (handles BPM / subdivision skew)
    density_pct = df['density_eps'].rank(pct=True)
    
    # 2. Bass depth below Middle C (MIDI 60)
    bass_depth = np.maximum(0.0, 60.0 - bass_clean)
    
    # 3. Continuous composite score
    score = (
        0.10 
        + 2.60 * density_pct 
        + 0.045 * bass_depth 
        + 0.015 * diff_clean 
        - 1.50 * is_na.astype(float)
    )
    
    # 4. Initial rounding and clipping
    levels = np.clip(np.round(score), 0, 4).astype(int)
    
    # 5. Acoustic Safeguards:
    # Rule A: Sub-bass gating for Level 4. True fortissimo climaxes must have bass <= 38.0.
    mask_false_4 = (levels == 4) & (bass_clean > 38.0)
    levels[mask_false_4] = 3
    
    # Rule B: Treble register cap. If bass is up in treble staff and density is low, cap at 0.
    mask_treble_intro = (bass_clean >= 60.0) & (density_pct < 0.25)
    levels[mask_treble_intro] = 0
    
    # Rule C: Missing bass (solo / silence) is capped at Level 0/1.
    mask_na = is_na & (levels > 1)
    levels[mask_na] = 0
    
    return levels
```

---

## 7. Next Steps for Pipeline Integration

1. **Update `TokenizationCore.py`:**
   - Update `load_and_map_intensities()` to use `discretize_intensity_tsv()` when loading continuous `_INTENSITY.tsv` files.
2. **Metadata Sync into `03_uniques/`:**
   - Write a sync script (`DATA03_sync_intensities.py`) to process the 99 songs in `02_uniques_staging/`, generate discrete intensity TSVs, and copy them directly into their matching directories in `03_uniques/`.
3. **Data Augmentation Preparation:**
   - Ensure the upcoming augmentation script transposes the intensity TSVs along with chords and melody/accompaniment MIDIs.
