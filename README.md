# Melody Applicator

Apply the pitches from a guide MIDI melody to an existing MIDI rhythm. Keep the target performance's note starts, lengths, velocities, channels, and other MIDI events while changing selected note pitches.

## Run

Install Python 3.10 or newer with Tkinter, then install Mido:

```text
python -m pip install -r requirements.txt
python melody_applicator.py
```

On Windows, `Start.cmd` prefers `.venv\Scripts\python.exe` and otherwise uses `python` on PATH.

## Use

1. Select the target rhythm MIDI and the guide melody MIDI.
2. Choose the target and guide tracks.
3. Choose the guide's top note for melody or bottom note for bass, and decide whether the guide should loop.
4. Save to a new MIDI file and import it into your DAW.

Both files should use the same musical song start, including any leading silence. Matching uses quarter-note positions, not the order or number of notes. Different MIDI tick resolutions are supported. At a target onset, the active guide pitch is used; rests hold the previous guide pitch and positions before the first guide note use that first pitch. Top/bottom selection resolves guide chords. A small 1/32-beat tolerance accommodates onset variation. Optional guide looping rounds its length to whole bars.

All unselected tracks and non-pitch MIDI data are retained. Existing target rhythm and note velocities stay in place. Ambiguous overlapping same-pitch target notes produce an error. Source files and existing outputs are protected from overwriting. This is a MIDI pitch tool: it does not process vocal audio or generate synth presets.

## Command line

```text
python melody_applicator.py --apply rhythm.mid melody.mid result.mid
python melody_applicator.py --apply rhythm.mid melody.mid result.mid --target-track 1 --guide-track 1 --voice bottom --no-loop
```

Track indices are zero-based. Run `python melody_applicator.py --help` for options.

## Tests

```text
python -m unittest -v test_melody_core
```

The tests create synthetic MIDI in temporary folders and verify event preservation, alignment, chord selection, and output protection. User reference songs and generated exports are excluded. `Licenses/` preserves the third-party notices supplied with the original distribution; Python and dependency binaries are not bundled here.

## License

No application license has been selected for this source release. Public visibility alone does not grant a license to reuse, modify, or redistribute it. Existing third-party notices, where supplied, are retained and apply to their respective components.
