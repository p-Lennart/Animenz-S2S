import mido
import bisect
import math


class MidiAligner:
    def __init__(self, target_tpb=480, default_tempo=500000):
        """
        target_tpb: standard ticks per beat for the output MIDI
        default_tempo: 500000 microseconds per beat (120 BPM)
        """
        self.target_tpb = target_tpb
        self.default_tempo = default_tempo

    def load_beats(self, beat_txt_path):
        beat_times = []
        with open(beat_txt_path, "r") as f:
            for line in f:
                parts = line.strip().split()
                if parts:
                    beat_times.append(float(parts[0]))
        return beat_times

    def build_time_map(self, mid):
        tempo_map = [(0, 500000)]
        for track in mid.tracks:
            abs_tick = 0
            for msg in track:
                abs_tick += msg.time
                if msg.type == "set_tempo":
                    tempo_map.append((abs_tick, msg.tempo))

        tempo_map.sort(key=lambda x: x[0])

        unique_tempo_map = []
        for t, tempo in tempo_map:
            if not unique_tempo_map or unique_tempo_map[-1][0] != t:
                unique_tempo_map.append((t, tempo))
            else:
                unique_tempo_map[-1] = (t, tempo)

        time_map = [
            (0, 0.0, unique_tempo_map[0][1] if unique_tempo_map[0][0] == 0 else 500000)
        ]

        for curr_tick, curr_tempo in unique_tempo_map:
            if curr_tick == 0:
                time_map[0] = (0, 0.0, curr_tempo)
                continue

            prev_tick, prev_time, prev_tempo = time_map[-1]
            delta_ticks = curr_tick - prev_tick
            delta_time = mido.tick2second(delta_ticks, mid.ticks_per_beat, prev_tempo)

            time_map.append((curr_tick, prev_time + delta_time, curr_tempo))

        return time_map

    def tick_to_seconds(self, tick, time_map, ticks_per_beat):
        keys = [tm[0] for tm in time_map]
        idx = bisect.bisect_right(keys, tick) - 1
        base_tick, base_time, tempo = time_map[idx]
        delta_ticks = tick - base_tick
        return base_time + mido.tick2second(delta_ticks, ticks_per_beat, tempo)

    def seconds_to_beat(self, sec, beat_times):
        if sec <= beat_times[0]:
            if len(beat_times) > 1:
                return (sec - beat_times[0]) / (beat_times[1] - beat_times[0])
            return 0.0

        if sec >= beat_times[-1]:
            if len(beat_times) > 1:
                diff = beat_times[-1] - beat_times[-2]
                return (len(beat_times) - 1) + (sec - beat_times[-1]) / diff
            return float(len(beat_times) - 1)

        idx = bisect.bisect_right(beat_times, sec) - 1
        t0 = beat_times[idx]
        t1 = beat_times[idx + 1]
        fraction = (sec - t0) / (t1 - t0) if t1 > t0 else 0
        return idx + fraction

    def align_midi(self, input_midi_path, beat_txt_path):
        mid = mido.MidiFile(input_midi_path)
        beat_times = self.load_beats(beat_txt_path)
        time_map = self.build_time_map(mid)

        # Detect Time Signature to calculate measure lengths
        time_sig_numerator = 4  # Default to 4/4
        for track in mid.tracks:
            for msg in track:
                if msg.type == "time_signature":
                    time_sig_numerator = msg.numerator
                    break

        raw_tracks_events = []
        min_tick = 0

        for track in mid.tracks:
            abs_tick = 0
            new_events = []

            for msg in track:
                abs_tick += msg.time

                if msg.type == "set_tempo":
                    continue

                sec = self.tick_to_seconds(abs_tick, time_map, mid.ticks_per_beat)
                new_beat = self.seconds_to_beat(sec, beat_times)
                new_abs_tick = int(round(new_beat * self.target_tpb))

                if new_abs_tick < min_tick:
                    min_tick = new_abs_tick

                # Tag events that occurred at absolute zero in the original file
                new_events.append((new_abs_tick, msg, abs_tick == 0))

            raw_tracks_events.append((track, new_events))

        aligned_mid = mido.MidiFile(ticks_per_beat=self.target_tpb)

        # Grid Quantization Fix:
        # Shift everything forward by whole measures rather than arbitrary ticks
        ticks_per_measure = self.target_tpb * time_sig_numerator
        measure_offset = (
            math.ceil(abs(min_tick) / ticks_per_measure) if min_tick < 0 else 0
        )
        tick_offset = measure_offset * ticks_per_measure

        for i, (original_track, events) in enumerate(raw_tracks_events):
            new_track = mido.MidiTrack()
            if hasattr(original_track, "name"):
                new_track.name = original_track.name

            shifted_events = []
            for ev_tick, msg, was_zero in events:
                # Keep initial setup meta events anchored to zero so they aren't pushed into the first measure
                if was_zero and msg.is_meta and msg.type != "end_of_track":
                    shifted_events.append((0, msg))
                else:
                    shifted_events.append((ev_tick + tick_offset, msg))

            # Ensure events remain sequentially ordered after the shift
            shifted_events.sort(key=lambda x: x[0])

            if i == 0:
                new_track.append(
                    mido.MetaMessage("set_tempo", tempo=self.default_tempo, time=0)
                )

            prev_tick = 0
            for ev_tick, msg in shifted_events:
                # Clamp any lingering negative ticks to 0
                ev_tick = max(0, ev_tick)

                new_msg = msg.copy(time=ev_tick - prev_tick)
                new_track.append(new_msg)
                prev_tick = ev_tick

            aligned_mid.tracks.append(new_track)

        return aligned_mid
