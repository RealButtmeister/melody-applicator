"""Put melody pitches onto an existing MIDI rhythm; keep its other data intact."""
from __future__ import annotations

import argparse
from pathlib import Path
import queue
import sys
import threading

from melody_core import apply_melody, inspect_midi


def documents_directory():
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        import uuid

        class GUID(ctypes.Structure):
            _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD),
                        ('Data3', wintypes.WORD), ('Data4', ctypes.c_ubyte * 8)]

        folder = GUID.from_buffer_copy(uuid.UUID('FDD39AD0-238F-46AF-ADB4-6C85480369C7').bytes_le)
        shell, ole = ctypes.WinDLL('shell32'), ctypes.WinDLL('ole32')
        shell.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD,
                                              wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
        shell.SHGetKnownFolderPath.restype = ctypes.c_long
        ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        result = ctypes.c_void_p()
        if shell.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(result)) == 0:
            try:
                return Path(ctypes.wstring_at(result))
            finally:
                ole.CoTaskMemFree(result)
    return Path.home() / 'Documents'


def unique_filename(directory, stem):
    candidate = Path(directory) / (stem + '.mid')
    number = 2
    while candidate.exists():
        candidate = Path(directory) / f'{stem} ({number}).mid'
        number += 1
    return candidate


def launch_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    BG, PANEL, FIELD = '#101820', '#1a2631', '#283b49'
    TEXT, MUTED, ACCENT = '#e9f0f4', '#a4b8c6', '#82ddb9'

    class FileCard(ttk.Frame):
        def __init__(self, parent, heading, description, changed):
            super().__init__(parent, style='Card.TFrame', padding=20)
            self.path = None
            self.info = None
            self.changed = changed
            self.columnconfigure(0, weight=1)
            ttk.Label(self, text=heading, style='CardTitle.TLabel').grid(row=0, column=0, sticky='w')
            ttk.Label(self, text=description, style='CardMuted.TLabel', wraplength=335).grid(
                row=1, column=0, sticky='w', pady=(7, 20))
            self.file_text = tk.StringVar(value='No MIDI selected')
            ttk.Label(self, textvariable=self.file_text, style='Card.TLabel', wraplength=335).grid(
                row=2, column=0, sticky='w')
            self.open_button = ttk.Button(self, text='Open MIDI…', command=self.browse)
            self.open_button.grid(row=3, column=0, sticky='w', pady=(12, 20))
            ttk.Label(self, text='Instrument track', style='CardMuted.TLabel').grid(row=4, column=0, sticky='w')
            self.track = ttk.Combobox(self, state='disabled')
            self.track.grid(row=5, column=0, sticky='ew', pady=(6, 10))
            self.track.bind('<<ComboboxSelected>>', lambda _event: self.refresh())
            self.detail = tk.StringVar(value='')
            ttk.Label(self, textvariable=self.detail, style='CardMuted.TLabel', wraplength=335).grid(
                row=6, column=0, sticky='w')

        def browse(self):
            path = filedialog.askopenfilename(title='Open MIDI', filetypes=[('MIDI files', '*.mid *.midi'), ('All files', '*.*')])
            if path:
                try:
                    self.load_path(path)
                except Exception as exc:
                    messagebox.showerror('Could not open MIDI', str(exc))

        def load_path(self, path):
            info = inspect_midi(path)
            tracks = [track for track in info['tracks'] if track['notes']]
            if not tracks:
                raise ValueError('This MIDI has no musical notes to use.')
            self.path, self.info, self.tracks = Path(path), info, tracks
            self.file_text.set(self.path.name)
            self.track.configure(values=[f"{track['index'] + 1}. {track['label']}" for track in tracks], state='readonly')
            self.track.current(0)
            self.refresh()

        def selected_track(self):
            return self.tracks[self.track.current()]['index']

        def refresh(self):
            track = self.tracks[self.track.current()]
            count = len(track['pitches'])
            self.detail.set(f"{track['notes']:,} notes · {count} different pitch{'es' if count != 1 else ''}")
            self.changed()

        def set_enabled(self, enabled):
            self.open_button.configure(state='normal' if enabled else 'disabled')
            self.track.configure(state='readonly' if enabled and self.path else 'disabled')

    class Studio:
        def __init__(self, root):
            self.root = root
            self.busy = False
            self.last_output = None
            self.mailbox = queue.Queue()
            root.title('Melody Applicator')
            root.geometry('900x650')
            root.minsize(860, 630)
            root.configure(bg=BG)
            root.protocol('WM_DELETE_WINDOW', self.close)
            self.close_requested = False
            style = ttk.Style(root)
            style.theme_use('clam')
            style.configure('.', font=('Segoe UI', 10), background=BG, foreground=TEXT)
            style.configure('TFrame', background=BG)
            style.configure('TLabel', background=BG, foreground=TEXT)
            style.configure('Muted.TLabel', foreground=MUTED)
            style.configure('Title.TLabel', font=('Segoe UI', 26, 'bold'))
            style.configure('Card.TFrame', background=PANEL)
            style.configure('Card.TLabel', background=PANEL)
            style.configure('CardTitle.TLabel', background=PANEL, foreground=ACCENT, font=('Segoe UI', 14, 'bold'))
            style.configure('CardMuted.TLabel', background=PANEL, foreground=MUTED)
            style.configure('TButton', background=FIELD, foreground=TEXT, padding=(15, 9), borderwidth=0)
            style.map('TButton', background=[('active', '#3c5666')], foreground=[('disabled', MUTED)])
            style.configure('Primary.TButton', background=ACCENT, foreground=BG, font=('Segoe UI', 11, 'bold'))
            style.map('Primary.TButton', background=[('active', '#a3e9ce'), ('disabled', FIELD)], foreground=[('disabled', MUTED)])
            style.configure('TCombobox', fieldbackground=FIELD, background=FIELD, foreground=TEXT, padding=6)
            style.map('TCombobox', fieldbackground=[('readonly', FIELD)], foreground=[('readonly', TEXT)])
            style.configure('TCheckbutton', foreground=TEXT, background=BG)
            style.map('TCheckbutton', background=[('active', BG)])
            root.option_add('*TCombobox*Listbox.background', FIELD)
            root.option_add('*TCombobox*Listbox.foreground', TEXT)
            outer = ttk.Frame(root, padding=24)
            outer.pack(fill='both', expand=True)
            outer.columnconfigure(0, weight=1)
            ttk.Label(outer, text='Melody Applicator', style='Title.TLabel').grid(row=0, column=0, sticky='w')
            ttk.Label(outer, text='Your rhythm. Your melody. Keep the velocity work you already made.', style='Muted.TLabel').grid(
                row=1, column=0, sticky='w', pady=(5, 22))
            cards = ttk.Frame(outer)
            cards.grid(row=2, column=0, sticky='nsew')
            cards.columnconfigure((0, 1), weight=1, uniform='cards')
            self.flat = FileCard(cards, '01  YOUR RHYTHM', 'The flat MIDI whose notes, lengths, and velocities you want to keep.', self.refresh)
            self.guide = FileCard(cards, '02  YOUR MELODY', 'The MIDI that supplies the pitches, matched by position in the song.', self.refresh)
            self.flat.grid(row=0, column=0, sticky='nsew', padx=(0, 8))
            self.guide.grid(row=0, column=1, sticky='nsew', padx=(8, 0))
            options = ttk.Frame(outer)
            options.grid(row=3, column=0, sticky='ew', pady=(20, 14))
            ttk.Label(options, text='When the guide has stacked notes:', style='Muted.TLabel').pack(side='left')
            self.voice = ttk.Combobox(options, state='readonly', values=['Top note / melody', 'Bottom note / bass'], width=22)
            self.voice.current(0)
            self.voice.pack(side='left', padx=(10, 18))
            self.loop = tk.BooleanVar(value=True)
            self.loop_control = ttk.Checkbutton(options, text='Repeat a shorter guide', variable=self.loop)
            self.loop_control.pack(side='left')
            actions = ttk.Frame(outer)
            actions.grid(row=4, column=0, sticky='ew', pady=(2, 14))
            self.apply_button = ttk.Button(actions, text='Apply melody & save…', style='Primary.TButton', command=self.start_export, state='disabled')
            self.apply_button.pack(side='left')
            self.folder_button = ttk.Button(actions, text='Open output folder', command=self.open_output, state='disabled')
            self.folder_button.pack(side='left', padx=12)
            self.status = tk.StringVar(value='Open your flat MIDI and your melody guide to begin.')
            ttk.Label(outer, textvariable=self.status, style='Muted.TLabel', wraplength=835).grid(row=5, column=0, sticky='w')
            root.after(50, self.poll)

        def refresh(self):
            if not hasattr(self, 'apply_button'):
                return
            ready = self.flat.path and self.guide.path and not self.busy
            self.apply_button.configure(state='normal' if ready else 'disabled')
            if ready:
                self.status.set('Ready. Only the selected rhythm track receives the melody pitches.')

        def start_export(self):
            if self.busy or not self.flat.path or not self.guide.path:
                return
            directory = documents_directory()
            suggested = unique_filename(directory, self.flat.path.stem + ' - melody')
            output = filedialog.asksaveasfilename(title='Save MIDI with melody', initialdir=str(directory),
                initialfile=suggested.name, defaultextension='.mid', filetypes=[('MIDI file', '*.mid')])
            if output:
                self.export_to(output)

        def export_to(self, output):
            if self.busy:
                return
            args = dict(target_track=self.flat.selected_track(), guide_track=self.guide.selected_track(),
                        voice='top' if self.voice.current() == 0 else 'bottom', loop=self.loop.get())
            flat, guide = self.flat.path, self.guide.path
            self.busy = True
            self.last_output = None
            self.flat.set_enabled(False)
            self.guide.set_enabled(False)
            self.voice.configure(state='disabled')
            self.loop_control.configure(state='disabled')
            self.apply_button.configure(state='disabled')
            self.folder_button.configure(state='disabled')
            self.status.set('Applying melody and checking the saved MIDI…')

            def worker():
                try:
                    result = apply_melody(flat, guide, output, **args)
                    self.mailbox.put(('ok', result))
                except Exception as exc:
                    self.mailbox.put(('error', str(exc)))

            threading.Thread(target=worker, daemon=True).start()

        def poll(self):
            try:
                kind, result = self.mailbox.get_nowait()
            except queue.Empty:
                pass
            else:
                self.busy = False
                if self.close_requested:
                    self.root.destroy()
                    return
                self.flat.set_enabled(True)
                self.guide.set_enabled(True)
                self.voice.configure(state='readonly')
                self.loop_control.configure(state='normal')
                self.refresh()
                if kind == 'ok':
                    self.last_output = Path(result['output'])
                    self.folder_button.configure(state='normal')
                    self.status.set(f"Saved {result['note_count']:,} notes; {result['changed_count']:,} pitches changed.\n{self.last_output}")
                else:
                    self.status.set('Could not save. ' + result)
                    messagebox.showerror('Could not apply melody', result)
            self.root.after(50, self.poll)

        def open_output(self):
            if self.last_output:
                import os
                try:
                    os.startfile(str(self.last_output.parent))
                except OSError as exc:
                    messagebox.showerror('Could not open folder', str(exc))

        def close(self):
            if self.busy:
                self.close_requested = True
                self.status.set('Finishing the current file before closing…')
            else:
                self.root.destroy()

    root = tk.Tk()
    studio = Studio(root)
    # Build QA can inspect the same live widgets without changing normal use.
    root.studio = studio
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', nargs=3, metavar=('RHYTHM', 'MELODY', 'OUTPUT'))
    parser.add_argument('--target-track', type=int)
    parser.add_argument('--guide-track', type=int)
    parser.add_argument('--voice', choices=['top', 'bottom'], default='top')
    parser.add_argument('--no-loop', action='store_true')
    args = parser.parse_args()
    if not args.apply:
        launch_gui()
        return 0
    flat, guide, output = args.apply
    try:
        def select(path, specified):
            if specified is not None:
                return specified
            choices = [track['index'] for track in inspect_midi(path)['tracks'] if track['notes']]
            if len(choices) != 1:
                raise ValueError('Select a track explicitly when a MIDI has multiple note tracks.')
            return choices[0]
        result = apply_melody(flat, guide, output,
            target_track=select(flat, args.target_track), guide_track=select(guide, args.guide_track),
            voice=args.voice, loop=not args.no_loop)
        if sys.stdout:
            print(f"Saved {result['note_count']} notes to {result['output']}")
        return 0
    except Exception as exc:
        if sys.stderr:
            print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
