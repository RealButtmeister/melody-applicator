"""Public-API checks for applying a melody while retaining a MIDI performance."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mido
from melody_core import apply_melody, inspect_midi


def hash_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def midi_track(events, end=None, name=''):
    """Create explicit musical examples; input order decides equal-tick order."""
    result = mido.MidiTrack()
    if name:
        result.append(mido.MetaMessage('track_name', name=name))
    last = 0
    for tick, message in sorted(events, key=lambda item: item[0]):
        result.append(message.copy(time=tick - last))
        last = tick
    result.append(mido.MetaMessage('end_of_track', time=(last if end is None else end) - last))
    return result


def on(tick, pitch=60, velocity=90, channel=0):
    return tick, mido.Message('note_on', note=pitch, velocity=velocity, channel=channel)


def off(tick, pitch=60, channel=0, zero_on=False):
    return tick, mido.Message('note_on' if zero_on else 'note_off', note=pitch, velocity=0, channel=channel)


def note_pitches(path, track=1):
    return [message.note for message in mido.MidiFile(path).tracks[track]
            if message.type == 'note_on' and message.velocity > 0]


def track_events(path, track):
    return [message.dict() for message in mido.MidiFile(path).tracks[track]]


class MelodyCoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='melody-test-')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)

    def save(self, name, tracks, ppq=96):
        path = self.folder / name
        document = mido.MidiFile(type=1, ticks_per_beat=ppq)
        document.tracks.extend(tracks)
        document.save(path)
        return path

    def conductor(self):
        return midi_track([(0, mido.MetaMessage('set_tempo', tempo=500000)),
                           (0, mido.MetaMessage('time_signature', numerator=4, denominator=4))])

    def flat(self, starts=(0, 96, 192), duration=24, ppq=96, pitch=60):
        events = [event for tick in starts for event in (on(tick, pitch), off(tick + duration, pitch))]
        return self.save('flat.mid', [self.conductor(), midi_track(events, name='Flat rhythm')], ppq)

    def guide(self, events=None, end=384, ppq=96):
        if events is None:
            events = [on(0, 72), off(48, 72), on(96, 74), off(144, 74), on(192, 76), off(240, 76)]
        return self.save('guide.mid', [self.conductor(), midi_track(events, end=end, name='Melody')], ppq)

    def assert_only_pitches_changed(self, source, output):
        before, after = mido.MidiFile(source), mido.MidiFile(output)
        self.assertEqual((before.type, before.ticks_per_beat), (after.type, after.ticks_per_beat))
        self.assertEqual(len(before.tracks), len(after.tracks))
        for original_track, changed_track in zip(before.tracks, after.tracks):
            self.assertEqual(len(original_track), len(changed_track))
            for original, changed in zip(original_track, changed_track):
                left, right = original.dict(), changed.dict()
                if original.type in ('note_on', 'note_off'):
                    left.pop('note')
                    right.pop('note')
                self.assertEqual(left, right)

    def test_different_ppq_preserves_ghosts_controllers_bends_sysex_and_all_timing(self):
        events = [
            (0, mido.Message('program_change', channel=2, program=114)),
            (0, mido.Message('sysex', data=(0x7d, 1, 2, 3))),
            on(0, velocity=25, channel=2),
            (12, mido.Message('control_change', channel=2, control=64, value=127)),
            off(48, channel=2, zero_on=True),
            (60, mido.Message('pitchwheel', channel=2, pitch=2048)),
            on(96, velocity=111, channel=2), off(144, channel=2),
            (150, mido.Message('control_change', channel=2, control=64, value=0)),
            on(192, velocity=58, channel=2), off(240, channel=2),
        ]
        source = self.save('expression.mid', [self.conductor(), midi_track(events, end=384, name='Drums')])
        guide = self.guide([on(0, 67), off(240, 67), on(480, 69), off(720, 69),
                            on(960, 71), off(1200, 71)], end=1920, ppq=480)
        source_hash, guide_hash = hash_file(source), hash_file(guide)
        output = self.folder / 'song.mid'
        report = apply_melody(source, guide, output, target_track=1, guide_track=1)
        self.assertEqual(note_pitches(output), [67, 69, 71])
        self.assertEqual(report['note_count'], 3)
        self.assertEqual(report['changed_count'], 3)
        self.assert_only_pitches_changed(source, output)
        notes = [message for message in mido.MidiFile(output).tracks[1]
                 if message.type in ('note_on', 'note_off')]
        self.assertEqual([(message.type, message.note, message.velocity) for message in notes],
                         [('note_on', 67, 25), ('note_on', 67, 0),
                          ('note_on', 69, 111), ('note_off', 69, 0),
                          ('note_on', 71, 58), ('note_off', 71, 0)])
        self.assertEqual((hash_file(source), hash_file(guide)), (source_hash, guide_hash))

    def test_simultaneous_guide_voice_top_and_bottom(self):
        source = self.flat((0, 96))
        guide = self.guide([on(0, 60), on(0, 67), on(0, 72),
                            off(48, 60), off(48, 67), off(48, 72),
                            on(96, 62), on(96, 74), off(144, 62), off(144, 74)])
        for voice, pitches in [('top', [72, 74]), ('bottom', [60, 62])]:
            with self.subTest(voice=voice):
                output = self.folder / f'{voice}.mid'
                apply_melody(source, guide, output, target_track=1, guide_track=1, voice=voice)
                self.assertEqual(note_pitches(output), pitches)
                self.assert_only_pitches_changed(source, output)

    def test_first_pitch_backfill_rest_hold_and_lookahead_tolerance(self):
        source = self.flat((0, 96, 191, 290), duration=8)
        guide = self.guide([on(2, 65), off(12, 65), on(194, 72), off(204, 72),
                            on(294, 79), off(304, 79)])
        output = self.folder / 'timing.mid'
        apply_melody(source, guide, output, target_track=1, guide_track=1)
        # 191->194 is exactly 1/32 beat at PPQ96; 290->294 is outside it.
        self.assertEqual(note_pitches(output), [65, 65, 72, 72])
        delayed_source = self.flat((0, 96), duration=8)
        delayed_guide = self.guide([on(48, 70), off(60, 70)], end=384)
        backfill = self.folder / 'backfill.mid'
        apply_melody(delayed_source, delayed_guide, backfill, target_track=1, guide_track=1)
        self.assertEqual(note_pitches(backfill), [70, 70])

    def test_loop_uses_complete_bars_and_no_loop_holds_last_pitch(self):
        source = self.flat((0, 30 * 96, 31 * 96, 32 * 96, 33 * 96), duration=8)
        guide = self.guide([on(0, 65), off(12, 65), on(30 * 96, 74), off(31 * 96, 74)], end=31 * 96)
        for loop, expected in [(True, [65, 74, 74, 65, 65]), (False, [65, 74, 74, 74, 74])]:
            with self.subTest(loop=loop):
                output = self.folder / f'loop-{loop}.mid'
                apply_melody(source, guide, output, target_track=1, guide_track=1, loop=loop)
                self.assertEqual(note_pitches(output), expected)

    def test_selected_track_only_and_fifo_noteoff_pairing(self):
        target = midi_track([on(0), on(96), off(144), off(192, zero_on=True)], end=384, name='Target')
        untouched = midi_track([on(0, 43, channel=5), off(200, 43, channel=5)], end=384, name='Bass')
        source = self.save('two parts.mid', [self.conductor(), target, untouched])
        guide = self.guide([on(0, 72), off(48, 72), on(96, 74), off(240, 74)])
        output = self.folder / 'selected.mid'
        apply_melody(source, guide, output, target_track=1, guide_track=1)
        musical = [message for message in mido.MidiFile(output).tracks[1]
                   if message.type in ('note_on', 'note_off')]
        self.assertEqual([message.note for message in musical], [72, 74, 72, 74])
        self.assertEqual(track_events(source, 0), track_events(output, 0))
        self.assertEqual(track_events(source, 2), track_events(output, 2))
        self.assert_only_pitches_changed(source, output)

    def test_selected_note_paired_off_in_another_track_changes_together(self):
        source = self.save('split pair.mid', [self.conductor(), midi_track([on(0)], end=96),
                                             midi_track([off(96)], end=96)])
        guide = self.guide([on(0, 75), off(48, 75)])
        output = self.folder / 'paired.mid'
        apply_melody(source, guide, output, target_track=1, guide_track=1)
        self.assertEqual(note_pitches(output), [75])
        self.assertEqual([message.note for message in mido.MidiFile(output).tracks[2]
                          if message.type == 'note_off'], [75])
        self.assert_only_pitches_changed(source, output)

    def test_malformed_pairs_collisions_and_invalid_selection_do_not_create_output(self):
        guide = self.guide()
        cases = [
            ('missing off.mid', [on(0)]),
            ('stray off.mid', [off(0), on(96), off(144)]),
            ('collision.mid', [on(0, 60), on(12, 64), off(120, 60), off(144, 64)]),
        ]
        for index, (name, events) in enumerate(cases):
            with self.subTest(name=name):
                source = self.save(name, [self.conductor(), midi_track(events, end=384)])
                source_hash = hash_file(source)
                output = self.folder / f'rejected-{index}.mid'
                with self.assertRaises(ValueError):
                    apply_melody(source, guide, output, target_track=1, guide_track=1)
                self.assertFalse(output.exists())
                self.assertEqual(hash_file(source), source_hash)
        valid = self.flat()
        output = self.folder / 'bad-selection.mid'
        with self.assertRaises(ValueError):
            apply_melody(valid, guide, output, target_track=99, guide_track=1)
        self.assertFalse(output.exists())

    def test_existing_output_inputs_and_hardlinks_are_never_overwritten(self):
        source, guide = self.flat(), self.guide()
        sentinel = self.folder / 'existing.mid'
        sentinel.write_bytes(b'Unrelated existing file must survive.\x00\xff')
        hardlink = self.folder / 'source hard link.mid'
        os.link(source, hardlink)
        protected = [source, guide, sentinel, hardlink]
        hashes = {path: hash_file(path) for path in protected}
        for destination in protected:
            with self.subTest(destination=destination.name):
                with self.assertRaises(FileExistsError):
                    apply_melody(source, guide, destination, target_track=1, guide_track=1)
                self.assertEqual({path: hash_file(path) for path in protected}, hashes)

    def test_existing_and_dangling_symlink_outputs_are_rejected(self):
        source, guide = self.flat(), self.guide()
        link = self.folder / 'source symlink.mid'
        dangling = self.folder / 'dangling output.mid'
        missing_target = self.folder / 'not created.mid'
        try:
            os.symlink(source, link)
            os.symlink(missing_target, dangling)
        except OSError as error:
            self.skipTest(f'Windows does not permit this account to create test symlinks: {error}')
        original = hash_file(source)
        for destination in (link, dangling):
            with self.subTest(destination=destination.name):
                with self.assertRaises(FileExistsError):
                    apply_melody(source, guide, destination, target_track=1, guide_track=1)
        self.assertEqual(hash_file(source), original)
        self.assertFalse(missing_target.exists())



if __name__ == '__main__':
    unittest.main(verbosity=2)
