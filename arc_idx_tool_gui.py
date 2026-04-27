from __future__ import annotations

import io
import threading
import traceback
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import arc_idx_tool


@dataclass(frozen=True)
class _Job:
    title: str
    fn: Callable[[], None]


class ArcIdxToolGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("arc_idx_tool GUI")
        self.minsize(980, 640)

        self._busy = False

        self._build_ui()

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)

        self.nb = ttk.Notebook(root)
        self.nb.pack(fill=tk.BOTH, expand=True)

        self._tab_unpack = ttk.Frame(self.nb, padding=10)
        self._tab_list = ttk.Frame(self.nb, padding=10)
        self._tab_repack = ttk.Frame(self.nb, padding=10)
        self._tab_pack = ttk.Frame(self.nb, padding=10)
        self._tab_script = ttk.Frame(self.nb, padding=10)

        self.nb.add(self._tab_unpack, text="Unpack")
        self.nb.add(self._tab_list, text="List")
        self.nb.add(self._tab_repack, text="Repack")
        self.nb.add(self._tab_pack, text="Pack")
        self.nb.add(self._tab_script, text="Script")

        self._build_unpack_tab(self._tab_unpack)
        self._build_list_tab(self._tab_list)
        self._build_repack_tab(self._tab_repack)
        self._build_pack_tab(self._tab_pack)
        self._build_script_tab(self._tab_script)

        sep = ttk.Separator(root, orient=tk.HORIZONTAL)
        sep.pack(fill=tk.X, pady=(10, 8))

        log_bar = ttk.Frame(root)
        log_bar.pack(fill=tk.X)
        ttk.Label(log_bar, text="Log").pack(side=tk.LEFT)
        self._btn_clear = ttk.Button(log_bar, text="Clear", command=self._clear_log)
        self._btn_clear.pack(side=tk.RIGHT)

        self._log = tk.Text(root, height=14, wrap=tk.WORD)
        self._log.pack(fill=tk.BOTH, expand=False)
        self._log.configure(state=tk.DISABLED)

        self._status = ttk.Label(root, text="Ready", anchor=tk.W)
        self._status.pack(fill=tk.X, pady=(8, 0))

    def _build_unpack_tab(self, tab: ttk.Frame) -> None:
        def browse_input() -> None:
            p = filedialog.askopenfilename(
                title="Select idx.dat or arc.dat (or cancel to pick folder)",
                filetypes=[("DAT files", "*.dat"), ("All files", "*.*")],
            )
            if p:
                self.unpack_input.set(p)
                return
            d = filedialog.askdirectory(title="Select folder containing idx.dat + arc.dat")
            if d:
                self.unpack_input.set(d)

        def browse_out() -> None:
            d = filedialog.askdirectory(title="Select output folder")
            if d:
                self.unpack_out.set(d)

        grid = ttk.Frame(tab)
        grid.pack(fill=tk.X)

        self.unpack_input = tk.StringVar(value=str(Path(".").resolve()))
        self.unpack_out = tk.StringVar(value=str((Path(".") / "unpacked").resolve()))
        self.unpack_mode = tk.StringVar(value="game-auto")
        self.unpack_script_output = tk.StringVar(value="decoded")
        self.unpack_convert_raw_wav = tk.BooleanVar(value=True)

        ttk.Label(grid, text="Input (folder/idx.dat/arc.dat)").grid(row=0, column=0, sticky=tk.W, pady=4)
        ttk.Entry(grid, textvariable=self.unpack_input).grid(row=0, column=1, sticky=tk.EW, padx=8)
        ttk.Button(grid, text="Browse", command=browse_input).grid(row=0, column=2, sticky=tk.E)

        ttk.Label(grid, text="Output folder").grid(row=1, column=0, sticky=tk.W, pady=4)
        ttk.Entry(grid, textvariable=self.unpack_out).grid(row=1, column=1, sticky=tk.EW, padx=8)
        ttk.Button(grid, text="Browse", command=browse_out).grid(row=1, column=2, sticky=tk.E)

        ttk.Label(grid, text="Decode mode").grid(row=2, column=0, sticky=tk.W, pady=4)
        ttk.Combobox(
            grid,
            textvariable=self.unpack_mode,
            values=["game-auto", "raw", "xor", "rle-be", "xor-script"],
            state="readonly",
            width=20,
        ).grid(row=2, column=1, sticky=tk.W, padx=8)

        ttk.Label(grid, text="Script output").grid(row=3, column=0, sticky=tk.W, pady=4)
        ttk.Combobox(
            grid,
            textvariable=self.unpack_script_output,
            values=["decoded", "raw", "both"],
            state="readonly",
            width=20,
        ).grid(row=3, column=1, sticky=tk.W, padx=8)

        ttk.Checkbutton(
            grid, text="Convert .wav.raw -> .wav", variable=self.unpack_convert_raw_wav
        ).grid(row=4, column=1, sticky=tk.W, padx=8, pady=(4, 8))

        grid.columnconfigure(1, weight=1)

        btns = ttk.Frame(tab)
        btns.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(btns, text="Unpack", command=self._on_unpack).pack(side=tk.LEFT)

    def _build_list_tab(self, tab: ttk.Frame) -> None:
        def browse_input() -> None:
            p = filedialog.askopenfilename(
                title="Select idx.dat or arc.dat (or cancel to pick folder)",
                filetypes=[("DAT files", "*.dat"), ("All files", "*.*")],
            )
            if p:
                self.list_input.set(p)
                return
            d = filedialog.askdirectory(title="Select folder containing idx.dat + arc.dat")
            if d:
                self.list_input.set(d)

        grid = ttk.Frame(tab)
        grid.pack(fill=tk.X)

        self.list_input = tk.StringVar(value=str(Path(".").resolve()))

        ttk.Label(grid, text="Input (folder/idx.dat/arc.dat)").grid(row=0, column=0, sticky=tk.W, pady=4)
        ttk.Entry(grid, textvariable=self.list_input).grid(row=0, column=1, sticky=tk.EW, padx=8)
        ttk.Button(grid, text="Browse", command=browse_input).grid(row=0, column=2, sticky=tk.E)

        grid.columnconfigure(1, weight=1)

        btns = ttk.Frame(tab)
        btns.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(btns, text="List entries", command=self._on_list).pack(side=tk.LEFT)

    def _build_repack_tab(self, tab: ttk.Frame) -> None:
        def browse_root() -> None:
            d = filedialog.askdirectory(title="Select project root (contains mesh/raw/texture/script)")
            if d:
                self.repack_root.set(d)

        grid = ttk.Frame(tab)
        grid.pack(fill=tk.X)

        self.repack_root = tk.StringVar(value=str(Path(".").resolve()))
        self.repack_group = tk.StringVar(value="all")
        self.repack_unpacked_dir = tk.StringVar(value="unpacked_batch")
        self.repack_custom_dir = tk.StringVar(value="custom_data")
        self.repack_out_dir = tk.StringVar(value="repacked")
        self.repack_strict = tk.BooleanVar(value=False)

        ttk.Label(grid, text="Project root").grid(row=0, column=0, sticky=tk.W, pady=4)
        ttk.Entry(grid, textvariable=self.repack_root).grid(row=0, column=1, sticky=tk.EW, padx=8)
        ttk.Button(grid, text="Browse", command=browse_root).grid(row=0, column=2, sticky=tk.E)

        ttk.Label(grid, text="Group").grid(row=1, column=0, sticky=tk.W, pady=4)
        ttk.Combobox(
            grid,
            textvariable=self.repack_group,
            values=["mesh", "raw", "texture", "script", "all"],
            state="readonly",
            width=20,
        ).grid(row=1, column=1, sticky=tk.W, padx=8)

        ttk.Label(grid, text="Unpacked dir").grid(row=2, column=0, sticky=tk.W, pady=4)
        ttk.Entry(grid, textvariable=self.repack_unpacked_dir).grid(row=2, column=1, sticky=tk.W, padx=8)

        ttk.Label(grid, text="Custom dir").grid(row=3, column=0, sticky=tk.W, pady=4)
        ttk.Entry(grid, textvariable=self.repack_custom_dir).grid(row=3, column=1, sticky=tk.W, padx=8)

        ttk.Label(grid, text="Output dir").grid(row=4, column=0, sticky=tk.W, pady=4)
        ttk.Entry(grid, textvariable=self.repack_out_dir).grid(row=4, column=1, sticky=tk.W, padx=8)

        ttk.Checkbutton(grid, text="Strict repack (do not re-encode modified script)", variable=self.repack_strict).grid(
            row=5, column=1, sticky=tk.W, padx=8, pady=(4, 8)
        )

        grid.columnconfigure(1, weight=1)

        btns = ttk.Frame(tab)
        btns.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(btns, text="Repack", command=self._on_repack).pack(side=tk.LEFT)

    def _build_pack_tab(self, tab: ttk.Frame) -> None:
        def browse_in() -> None:
            d = filedialog.askdirectory(title="Select input directory to pack")
            if d:
                self.pack_in_dir.set(d)

        def browse_idx_out() -> None:
            p = filedialog.asksaveasfilename(
                title="Save idx.dat as",
                defaultextension=".dat",
                initialfile="idx.dat",
                filetypes=[("DAT files", "*.dat"), ("All files", "*.*")],
            )
            if p:
                self.pack_idx_out.set(p)

        def browse_arc_out() -> None:
            p = filedialog.asksaveasfilename(
                title="Save arc.dat as",
                defaultextension=".dat",
                initialfile="arc.dat",
                filetypes=[("DAT files", "*.dat"), ("All files", "*.*")],
            )
            if p:
                self.pack_arc_out.set(p)

        grid = ttk.Frame(tab)
        grid.pack(fill=tk.X)

        self.pack_in_dir = tk.StringVar(value=str((Path(".") / "unpacked").resolve()))
        self.pack_idx_out = tk.StringVar(value=str((Path(".") / "idx.dat").resolve()))
        self.pack_arc_out = tk.StringVar(value=str((Path(".") / "arc.dat").resolve()))
        self.pack_mode = tk.StringVar(value="xor")
        self.pack_flat_names = tk.BooleanVar(value=False)

        ttk.Label(grid, text="Input directory").grid(row=0, column=0, sticky=tk.W, pady=4)
        ttk.Entry(grid, textvariable=self.pack_in_dir).grid(row=0, column=1, sticky=tk.EW, padx=8)
        ttk.Button(grid, text="Browse", command=browse_in).grid(row=0, column=2, sticky=tk.E)

        ttk.Label(grid, text="idx.dat output").grid(row=1, column=0, sticky=tk.W, pady=4)
        ttk.Entry(grid, textvariable=self.pack_idx_out).grid(row=1, column=1, sticky=tk.EW, padx=8)
        ttk.Button(grid, text="Browse", command=browse_idx_out).grid(row=1, column=2, sticky=tk.E)

        ttk.Label(grid, text="arc.dat output").grid(row=2, column=0, sticky=tk.W, pady=4)
        ttk.Entry(grid, textvariable=self.pack_arc_out).grid(row=2, column=1, sticky=tk.EW, padx=8)
        ttk.Button(grid, text="Browse", command=browse_arc_out).grid(row=2, column=2, sticky=tk.E)

        ttk.Label(grid, text="Encode mode").grid(row=3, column=0, sticky=tk.W, pady=4)
        ttk.Combobox(
            grid,
            textvariable=self.pack_mode,
            values=["raw", "xor", "rle", "script"],
            state="readonly",
            width=20,
        ).grid(row=3, column=1, sticky=tk.W, padx=8)

        ttk.Checkbutton(grid, text="Flat names (drop subfolders)", variable=self.pack_flat_names).grid(
            row=4, column=1, sticky=tk.W, padx=8, pady=(4, 8)
        )

        grid.columnconfigure(1, weight=1)

        btns = ttk.Frame(tab)
        btns.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(btns, text="Pack", command=self._on_pack).pack(side=tk.LEFT)

    def _build_script_tab(self, tab: ttk.Frame) -> None:
        def browse_in() -> None:
            p = filedialog.askopenfilename(title="Select input file", filetypes=[("All files", "*.*")])
            if p:
                self.script_in.set(p)

        def browse_out() -> None:
            p = filedialog.asksaveasfilename(title="Select output file", filetypes=[("All files", "*.*")])
            if p:
                self.script_out.set(p)

        grid = ttk.Frame(tab)
        grid.pack(fill=tk.X)

        self.script_mode = tk.StringVar(value="decode")
        self.script_in = tk.StringVar(value="")
        self.script_out = tk.StringVar(value="")
        self.script_pre_xor = tk.BooleanVar(value=False)
        self.script_post_xor = tk.BooleanVar(value=True)

        ttk.Label(grid, text="Operation").grid(row=0, column=0, sticky=tk.W, pady=4)
        ttk.Combobox(
            grid,
            textvariable=self.script_mode,
            values=["decode", "encode"],
            state="readonly",
            width=20,
        ).grid(row=0, column=1, sticky=tk.W, padx=8)

        ttk.Label(grid, text="Input file").grid(row=1, column=0, sticky=tk.W, pady=4)
        ttk.Entry(grid, textvariable=self.script_in).grid(row=1, column=1, sticky=tk.EW, padx=8)
        ttk.Button(grid, text="Browse", command=browse_in).grid(row=1, column=2, sticky=tk.E)

        ttk.Label(grid, text="Output file").grid(row=2, column=0, sticky=tk.W, pady=4)
        ttk.Entry(grid, textvariable=self.script_out).grid(row=2, column=1, sticky=tk.EW, padx=8)
        ttk.Button(grid, text="Browse", command=browse_out).grid(row=2, column=2, sticky=tk.E)

        ttk.Checkbutton(grid, text="Decode: pre-XOR", variable=self.script_pre_xor).grid(
            row=3, column=1, sticky=tk.W, padx=8, pady=(4, 0)
        )
        ttk.Checkbutton(grid, text="Encode: post-XOR (recommended for repack)", variable=self.script_post_xor).grid(
            row=4, column=1, sticky=tk.W, padx=8, pady=(4, 8)
        )

        grid.columnconfigure(1, weight=1)

        btns = ttk.Frame(tab)
        btns.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(btns, text="Run", command=self._on_script).pack(side=tk.LEFT)

    def _set_busy(self, busy: bool, status: str) -> None:
        self._busy = busy
        self._status.configure(text=status)
        try:
            self.configure(cursor="watch" if busy else "")
        except Exception:
            pass

    def _append_log(self, text: str) -> None:
        self._log.configure(state=tk.NORMAL)
        self._log.insert(tk.END, text)
        self._log.see(tk.END)
        self._log.configure(state=tk.DISABLED)

    def _clear_log(self) -> None:
        self._log.configure(state=tk.NORMAL)
        self._log.delete("1.0", tk.END)
        self._log.configure(state=tk.DISABLED)

    def _run_job_async(self, job: _Job) -> None:
        if self._busy:
            messagebox.showinfo("Busy", "A job is already running. Please wait.")
            return

        def worker() -> None:
            buf = io.StringIO()
            ok = True
            err: Optional[str] = None
            try:
                with redirect_stdout(buf), redirect_stderr(buf):
                    job.fn()
            except Exception:
                ok = False
                err = traceback.format_exc()
            out = buf.getvalue()

            def done_ui() -> None:
                if out:
                    self._append_log(out if out.endswith("\n") else out + "\n")
                if not ok and err:
                    self._append_log(err + ("\n" if not err.endswith("\n") else ""))
                    messagebox.showerror("Error", f"{job.title} failed.\n\nSee Log for details.")
                self._set_busy(False, "Ready")

            self.after(0, done_ui)

        self._set_busy(True, f"Running: {job.title} ...")
        self._append_log(f"\n=== {job.title} ===\n")
        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def _on_list(self) -> None:
        inp = self.list_input.get().strip()

        def run() -> None:
            ns = arc_idx_tool.argparse.Namespace(input=inp, idx=None, arc=None)
            arc_idx_tool.cmd_list(ns)

        self._run_job_async(_Job("List entries", run))

    def _on_unpack(self) -> None:
        inp = self.unpack_input.get().strip()
        out = self.unpack_out.get().strip()
        mode = self.unpack_mode.get().strip()
        script_output = self.unpack_script_output.get().strip()
        convert_raw_wav = bool(self.unpack_convert_raw_wav.get())

        def run() -> None:
            ns = arc_idx_tool.argparse.Namespace(
                input=inp,
                idx=None,
                arc=None,
                out=out,
                mode=mode,
                script_output=script_output,
                convert_raw_wav=convert_raw_wav,
            )
            arc_idx_tool.cmd_unpack(ns)

        self._run_job_async(_Job("Unpack archive", run))

    def _on_repack(self) -> None:
        root = self.repack_root.get().strip()
        group = self.repack_group.get().strip()
        unpacked_dir = self.repack_unpacked_dir.get().strip()
        custom_dir = self.repack_custom_dir.get().strip()
        out_dir = self.repack_out_dir.get().strip()
        strict = bool(self.repack_strict.get())

        def run() -> None:
            ns = arc_idx_tool.argparse.Namespace(
                root=root,
                group=group,
                unpacked_dir=unpacked_dir,
                custom_dir=custom_dir,
                out_dir=out_dir,
                strict_repack=strict,
            )
            arc_idx_tool.cmd_repack(ns)

        self._run_job_async(_Job("Repack from template", run))

    def _on_pack(self) -> None:
        in_dir = self.pack_in_dir.get().strip()
        idx_out = self.pack_idx_out.get().strip()
        arc_out = self.pack_arc_out.get().strip()
        mode = self.pack_mode.get().strip()
        flat = bool(self.pack_flat_names.get())

        def run() -> None:
            ns = arc_idx_tool.argparse.Namespace(
                in_dir=in_dir,
                idx_out=idx_out,
                arc_out=arc_out,
                mode=mode,
                flat_names=flat,
            )
            arc_idx_tool.cmd_pack(ns)

        self._run_job_async(_Job("Pack directory", run))

    def _on_script(self) -> None:
        op = self.script_mode.get().strip()
        inp = self.script_in.get().strip()
        out = self.script_out.get().strip()
        pre = bool(self.script_pre_xor.get())
        post = bool(self.script_post_xor.get())

        if not inp or not out:
            messagebox.showwarning("Missing path", "Please choose both input and output paths.")
            return

        def run() -> None:
            if op == "decode":
                ns = arc_idx_tool.argparse.Namespace(input=inp, out=out, pre_xor=pre)
                arc_idx_tool.cmd_decode_script(ns)
            else:
                ns = arc_idx_tool.argparse.Namespace(input=inp, out=out, post_xor=post)
                arc_idx_tool.cmd_encode_script(ns)

        self._run_job_async(_Job(f"Script {op}", run))


def main() -> None:
    app = ArcIdxToolGui()
    app.mainloop()


if __name__ == "__main__":
    main()

