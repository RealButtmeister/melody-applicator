"""Apply a guide's pitches while preserving the source MIDI's event stream.

Guide pitches follow note-on time, with the upper or lower note chosen at a
simultaneous onset. Rests hold the last pitch; an initial rest uses the first.
All musical time calculations use exact fractions of a quarter-note beat.
"""
from __future__ import annotations

import bisect
import copy
import io
import os
import tempfile
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import mido


MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_EVENTS = 250_000
MAX_TRACKS = 1024
LOOKAHEAD_BEATS = Fraction(1, 32)


@dataclass
class _Note:
    track: int
    index: int
    tick: int
    order: int
    channel: int
    pitch: int
    end_tick: int | None = None
    off_track: int | None = None
    off_index: int | None = None
    off_order: int | None = None
    new_pitch: int | None = None


def _load(path):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"Choose a MIDI file: {path.name}")
    with path.open("rb") as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("This MIDI is too large. Choose a file smaller than 16 MB.")
    try:
        midi = mido.MidiFile(file=io.BytesIO(raw), clip=False)
    except (OSError, EOFError, ValueError, KeyError, IndexError) as exc:
        raise ValueError(f"Could not read {path.name} as a valid MIDI file: {exc}") from exc
    if midi.type not in (0, 1):
        raise ValueError("Type 2 MIDI has independent track timelines. Export it as Type 0 or Type 1 first.")
    if midi.ticks_per_beat <= 0:
        raise ValueError("SMPTE-timed MIDI is not supported. Export a beat-based MIDI file first.")
    if not midi.tracks or len(midi.tracks) > MAX_TRACKS:
        raise ValueError("This MIDI must have between 1 and 1,024 tracks.")
    if sum(map(len, midi.tracks)) > MAX_EVENTS:
        raise ValueError("This MIDI has too many events. Choose a file with fewer than 250,000 events.")
    return path, midi


def _events(midi):
    result = []
    ends = []
    for track_index, track in enumerate(midi.tracks):
        tick = 0
        for index, message in enumerate(track):
            if not isinstance(message.time, int) or message.time < 0:
                raise ValueError("The MIDI contains an invalid event time.")
            tick += message.time
            result.append((tick, track_index, index, message))
        ends.append(tick)
    result.sort(key=lambda event: event[:3])
    return result, ends


def _note_on(message):
    return message.type == "note_on" and message.velocity > 0


def _note_off(message):
    return message.type == "note_off" or (message.type == "note_on" and message.velocity == 0)


def _pair_notes(events):
    """FIFO pairing across tracks on the shared MIDI channel/pitch timeline."""
    active = defaultdict(deque)
    notes = []
    orphans = []
    for order, (tick, track, index, message) in enumerate(events):
        if _note_on(message):
            note = _Note(track, index, tick, order, message.channel, message.note)
            notes.append(note)
            active[(message.channel, message.note)].append(note)
        elif _note_off(message):
            queue = active[(message.channel, message.note)]
            if queue:
                note = queue.popleft()
                note.end_tick, note.off_track, note.off_index, note.off_order = tick, track, index, order
            else:
                orphans.append((track, index, tick, message.channel, message.note))
    return notes, orphans


def _track_index(midi, index, label):
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(midi.tracks):
        raise ValueError(f"Choose a valid {label} track.")
    return index


def _selected_notes(notes, orphans, index, label):
    selected = [note for note in notes if note.track == index]
    if not selected:
        raise ValueError(f"The selected {label} track has no notes.")
    if any(note.end_tick is None for note in selected):
        raise ValueError(f"The selected {label} track has a note without a matching note-off. Repair or re-export that MIDI first.")
    if any(track == index for track, *_ in orphans):
        raise ValueError(f"The selected {label} track has an unmatched note-off. Repair or re-export that MIDI first.")
    return selected


def inspect_midi(path):
    """Return track choices and timing information without changing the file."""
    path, midi = _load(path)
    events, ends = _events(midi)
    tracks = []
    for index, track in enumerate(midi.tracks):
        names = [msg.name.strip() for msg in track if msg.type == "track_name" and msg.name.strip()]
        notes = [msg for msg in track if _note_on(msg)]
        tracks.append({
            "index": index,
            "label": names[0] if names else f"Track {index + 1}",
            "notes": len(notes),
            "pitches": sorted({msg.note for msg in notes}),
            "channels": sorted({msg.channel for msg in notes}),
            "length_beats": float(Fraction(ends[index], midi.ticks_per_beat)),
        })
    return {
        "name": path.name,
        "path": str(path),
        "tracks": tracks,
        "length_beats": float(Fraction(max(ends, default=0), midi.ticks_per_beat)),
        "ticks_per_beat": midi.ticks_per_beat,
        "midi_type": midi.type,
        "note_count": sum(item["notes"] for item in tracks),
    }


def _loop_span(midi, events, end_tick):
    """Pad to a complete measure; meter changes must occur at bar boundaries."""
    end = Fraction(end_tick, midi.ticks_per_beat)
    signatures = {}
    for tick, _, _, message in events:
        if message.type != "time_signature" or Fraction(tick, midi.ticks_per_beat) >= end:
            continue
        if message.numerator <= 0 or message.denominator <= 0:
            raise ValueError("The melody MIDI contains an invalid time signature.")
        beat = Fraction(tick, midi.ticks_per_beat)
        length = Fraction(message.numerator * 4, message.denominator)
        if beat in signatures and signatures[beat] != length:
            raise ValueError("The melody MIDI has conflicting time signatures. Turn off Loop melody or re-export it with one shared meter map.")
        signatures[beat] = length
    start, bar = Fraction(0), Fraction(4)
    for beat, new_bar in sorted(signatures.items()):
        if new_bar == bar:
            continue
        if ((beat - start) / bar).denominator != 1:
            raise ValueError("A melody time-signature change falls inside a measure. Turn off Loop melody or move that change to a bar boundary.")
        start, bar = beat, new_bar
    measures = (end - start) / bar
    count = -(-measures.numerator // measures.denominator)
    return start + max(1, count) * bar


def _check_collisions(notes, selected_track, orphans):
    """Reject newly merged voices, including on-before-off at an equal tick."""
    changes = []
    affected_channels = {note.channel for note in notes if note.track == selected_track}
    for track, _, _, channel, _ in orphans:
        if channel in affected_channels and track != selected_track:
            raise ValueError("Another track has an unmatched note-off on the selected track's MIDI channel. Repair or re-export it before applying melody.")
    for note in notes:
        if note.channel not in affected_channels:
            continue
        changes.append((note.order, True, note))
        if note.off_order is not None:
            changes.append((note.off_order, False, note))
    changes.sort(key=lambda item: item[0])
    active = defaultdict(Counter)
    for _, starts, note in changes:
        pitch = note.new_pitch if note.new_pitch is not None else note.pitch
        bucket = active[(note.channel, pitch)]
        if starts:
            if any(original != note.pitch and count > 0 for original, count in bucket.items()):
                raise ValueError(
                    f"Applying this melody would make overlapping notes share pitch {pitch} on MIDI channel {note.channel + 1}. "
                    "Choose a single-note target track or shorten its overlaps first. No file was written."
                )
            bucket[note.pitch] += 1
        else:
            bucket[note.pitch] -= 1
            if bucket[note.pitch] <= 0:
                del bucket[note.pitch]


def _output_path(path, inputs):
    requested = Path(path).expanduser().absolute()
    if os.path.lexists(requested):
        raise FileExistsError(f"A file or folder already exists at {requested}. Choose a new output filename; existing files are never overwritten.")
    resolved = requested.resolve(strict=False)
    if any(os.path.normcase(str(resolved)) == os.path.normcase(str(item)) for item in inputs):
        raise FileExistsError("The output must be a new file, separate from both input MIDI files.")
    if resolved.suffix.lower() not in (".mid", ".midi"):
        raise ValueError("Choose an output filename ending in .mid or .midi.")
    return resolved


def _save_new(midi, output):
    """Write and verify a sibling file, then publish without replacing anything."""
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".melody-applicator-", suffix=".tmp", dir=output.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            midi.save(file=stream)
            stream.flush()
            os.fsync(stream.fileno())
        check = mido.MidiFile(temporary)
        if check.type != midi.type or check.ticks_per_beat != midi.ticks_per_beat or check.tracks != midi.tracks:
            raise ValueError("This source MIDI cannot be saved without changing other events. Re-export it as a standard MIDI file first.")
        if os.name == "nt":
            # Windows rename fails if the destination exists; it never replaces it.
            os.rename(temporary, output)
        else:
            # Unlike POSIX rename, creating a hard link is an exclusive publish.
            os.link(temporary, output)
            temporary.unlink()
    finally:
        if temporary.exists():
            temporary.unlink()


def apply_melody(flat_path, guide_path, output_path, *, target_track: int,
                 guide_track: int, voice="top", loop=True):
    """Create a new MIDI whose selected source track follows the guide pitches.

    Only note-on and their paired note-off pitch fields can change. Loop length
    includes the selected guide track's trailing silence, rounded to a full bar.
    Both files remain untouched and an existing output is always refused.
    """
    if voice not in ("top", "bottom"):
        raise ValueError("Choose the top or bottom melody voice.")
    if not isinstance(loop, bool):
        raise ValueError("Loop melody must be enabled or disabled.")
    flat_path, source = _load(flat_path)
    guide_path, guide = _load(guide_path)
    output = _output_path(output_path, (flat_path, guide_path))
    target_track = _track_index(source, target_track, "target")
    guide_track = _track_index(guide, guide_track, "melody")
    source_events, _ = _events(source)
    source_notes, source_orphans = _pair_notes(source_events)
    targets = _selected_notes(source_notes, source_orphans, target_track, "target")
    guide_events, guide_ends = _events(guide)
    guide_notes, guide_orphans = _pair_notes(guide_events)
    selected_guide = _selected_notes(guide_notes, guide_orphans, guide_track, "melody")

    groups = defaultdict(list)
    for note in selected_guide:
        groups[Fraction(note.tick, guide.ticks_per_beat)].append(note.pitch)
    onsets = sorted(groups)
    choose = max if voice == "top" else min
    pitches = [choose(groups[beat]) for beat in onsets]
    guide_end = max(guide_ends[guide_track], max(note.end_tick for note in selected_guide))
    span = _loop_span(guide, guide_events, guide_end) if loop else None
    changed = 0
    for note in targets:
        beat = Fraction(note.tick, source.ticks_per_beat) + LOOKAHEAD_BEATS
        if loop:
            beat %= span
        index = max(0, bisect.bisect_right(onsets, beat) - 1)
        note.new_pitch = pitches[index]
        changed += note.new_pitch != note.pitch

    _check_collisions(source_notes, target_track, source_orphans)
    result = copy.deepcopy(source)
    for note in targets:
        result.tracks[note.track][note.index].note = note.new_pitch
        result.tracks[note.off_track][note.off_index].note = note.new_pitch
    _save_new(result, output)
    return {
        "note_count": len(targets),
        "changed_count": changed,
        "output": str(output),
        "guide_note_count": len(selected_guide),
        "guide_onset_count": len(onsets),
        "guide_loop_beats": float(span) if span is not None else None,
        "target_track": target_track,
        "guide_track": guide_track,
        "voice": voice,
        "loop": loop,
    }
