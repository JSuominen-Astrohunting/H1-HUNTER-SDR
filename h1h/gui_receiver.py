"""H1-HUNTER SDR - Receiver tab (RF spectrum, waterfall, tuning, PPM)."""
import os
import re
import sys
import json
import math
import time
import numpy as np
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.backends.backend_agg import FigureCanvasAgg
from .common import *
from .physics import *
from .receiver import *
from .files import *
from .widgets import *
from .theme import *


class GuiReceiverMixin:
    def build_receiver_page(self, page):
        """Page 1 (design: Receiver): tuning, signal, clock/PPM and interference flagging on the left;
        the RF spectrum + waterfall card on the right with the readout row below it."""
        page.columnconfigure(1, weight=1)
        page.rowconfigure(0, weight=1)
        left = ScrollPanel(page, 350)
        left.grid(row=0, column=0, sticky="ns", padx=(6, 0), pady=6)
        self.build_rx_controls(left.inner)
        right = tk.Frame(page, bg=PAGE)
        right.grid(row=0, column=1, sticky="nsew", padx=6, pady=6)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        box = tk.Frame(right, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        box.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        inner = tk.Frame(box, bg=CARD)
        inner.pack(fill="both", expand=True, padx=10, pady=10)
        hdr = tk.Frame(inner, bg=CARD)
        hdr.pack(fill="x", pady=(0, 8))
        heading(hdr, "RF spectrum and waterfall")
        self.lbl_rf_res = tk.Label(hdr, text="", bg=CARD, fg=MUTED, font=F_SMALL)
        self.lbl_rf_res.pack(side="left")
        ttk.Button(hdr, text="Reset view", command=self.reset_rf_view).pack(side="right")
        bot = tk.Frame(inner, bg=CARD)
        bot.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Button(bot, text="Save image", image=self.icon("save"), compound="left",
                   command=self.save_rf_image).pack(side="right")
        plot = tk.Frame(inner, bg=BG, width=200, height=200)
        plot.pack(fill="both", expand=True)
        plot.pack_propagate(False)
        self.build_rf_plot(plot)
        ro = tk.Frame(right, bg=PAGE)
        ro.grid(row=1, column=0, sticky="ew", padx=2, pady=(0, 2))
        self.rv = {}
        for i, (key, title) in enumerate((("rf", f"RF view (fixed FFT {RF_NFFT})"), ("h1fft", "Hydrogen line FFT"),
                                          ("thru", "Sample throughput"), ("clip", "Clipping"),
                                          ("flag60", "Flagged, 60 s"))):
            self.rv[key] = readout(ro, title, i)
        self.rv_h1fft_title = self.rv["h1fft"].title_label

    def build_rx_controls(self, tab):
        g = self.rx_widgets
        cb = self.on_rx_change
        G = card_grid(tab, "Tuning")

        def h1_button(row):
            b = ttk.Button(row, text="H1", style="Tiny.TButton", width=2, command=self.tune_h1)
            b.pack(side="left", padx=(4, 0))
            g.append(b)
        self.field(G, 0, 0, "Center MHz", "freq", f"{H1_MHZ:.6f}", cb=cb, group=g, extra=h1_button)
        self.field(G, 0, 1, "Sample rate MS/s", "fs", "2.4", kind="combo", values=SAMPLE_RATES_MSPS, cb=cb, group=g)
        self.build_gain_slider(G, 1, 0, g)
        self.field(G, 1, 1, "LO offset kHz", "offset", "0", cb=cb, group=g)
        self.lbl_band = tk.Label(G, text="", bg=CARD, fg=WARN_COL, font=F_SMALL, anchor="w", justify="left",
                                 wraplength=200)
        self.lbl_band.grid(row=2, column=1, sticky="ew")
        self.lbl_band.bind("<Configure>", lambda e: self.lbl_band.config(wraplength=max(e.width - 4, 80)))
        c = G.card
        chk = tk.Frame(c, bg=CARD)
        chk.pack(fill="x", pady=(6, 0))
        g.append(self.check(chk, "bias", "Bias-T (LNA power)", False, cb=cb))
        g.append(self.check(chk, "offset_tuning", "Offset tuning", False, cb=cb))
        self.chk_dc = self.check(chk, "dc_remove", "Remove DC from IQ", True, cb=cb)
        g.append(self.chk_dc)
        G2 = tk.Frame(c, bg=CARD)
        G2.pack(fill="x")
        G2.columnconfigure(0, weight=1, uniform="c")
        G2.columnconfigure(1, weight=1, uniform="c")
        self.ent_notch = self.field(G2, 0, 0, "DC notch ± kHz", "notch", "3", cb=cb, group=g)
        self.note(c, "LO offset moves the DC spike away from the tuned frequency; the averaged hydrogen line stays in the "
                     "middle. Offset tuning includes DC removal and needs no DC notch.")

        c = card(tab, "Signal")
        G = tk.Frame(c, bg=CARD)
        G.pack(fill="x")
        G.columnconfigure(1, weight=1)
        self.rd = {}
        for i, (name, key) in enumerate((("Noise floor", "floor"), ("Peak", "peak"), ("Peak - floor", "snr"),
                                         ("ADC level", "adc"))):
            tk.Label(G, text=name, bg=CARD, fg=MUTED, font=F_SMALL, anchor="w").grid(row=i, column=0, sticky="w",
                                                                                      pady=2)
            lab = ttk.Label(G, text="---", style="Data.TLabel")
            lab.grid(row=i, column=1, sticky="w", padx=(12, 0), pady=2)
            self.rd[key] = lab

        c = card(tab, "Clock and frequency error")
        self.check(c, "gps_clock", "GPS synchronized clock", False, cb=self.update_iq_note)
        G = tk.Frame(c, bg=CARD)
        G.pack(fill="x", pady=(6, 0))
        G.columnconfigure(0, weight=1, uniform="c")
        G.columnconfigure(1, weight=1, uniform="c")
        self.field(G, 0, 0, "PPM correction", "ppm", "0", kind="spin", rng=(-200, 200, 0.1), cb=cb, group=g)
        self.btn_ppm_test = ttk.Button(G, text="PPM test", command=self.on_ppm_test)
        self.btn_ppm_test.grid(row=0, column=1, sticky="sew", pady=4)
        self.lbl_ppm_test = self.note(c, "PPM test: measures the stick's crystal against the PC clock (like "
                                         "'rtl_test -p'). Only as accurate as the PC clock - the value is shown, "
                                         "not set.")
        self.note(c, "Stored in every spectrum's metadata. 1 ppm ~ 0.3 km/s. 0 is right for TCXO sticks "
                     "(RTL-SDR Blog V3/V4).")

        c = card(tab, "Interference flagging")
        G = tk.Frame(c, bg=CARD)
        G.pack(fill="x")
        G.columnconfigure(0, weight=1, uniform="c")
        G.columnconfigure(1, weight=1, uniform="c")
        self.field(G, 0, 0, "Flag limit sigma", "flag_sigma", "5", cb=self.on_flag_sigma)
        self.note(c, "FFT resolution and window are on the Observe page.")
        self.note(c, "Interference is only flagged; raw data is never changed. 'Remove flagged' on the Observe "
                     "page recomputes the average without the flagged spectra.")

    def build_gain_slider(self, G, r, c, group):
        """Gain as a slider that snaps to the tuner's real gain steps (no AGC). The receiver is retuned when the
        mouse button is released, not on every step while dragging."""
        cell = tk.Frame(G, bg=CARD)
        cell.grid(row=r, column=c, sticky="ew", padx=(0, 8), pady=4)
        head = tk.Frame(cell, bg=CARD)
        head.pack(fill="x")
        tk.Label(head, text="Gain dB", bg=CARD, fg=MUTED, font=F_SMALL, anchor="w").pack(side="left")
        self.lbl_gain = tk.Label(head, text="", bg=CARD, fg=TEXT, font=F_MONO, anchor="e")
        self.lbl_gain.pack(side="right")
        self.var("gain", "29.7")
        self.var("tuner_gains", "")       # the stick's own gain steps from the last start (shown before the next one)
        self.gain_steps = list(R820T_GAINS)
        self.gain_idx = tk.DoubleVar(value=0)
        self.sld_gain = ttk.Scale(cell, from_=0, to=len(self.gain_steps) - 1, variable=self.gain_idx,
                                  command=lambda v: self.on_gain_drag())
        self.sld_gain.pack(fill="x", pady=(6, 2))
        self.sld_gain.bind("<ButtonRelease-1>", lambda e: self.on_gain_release())
        for key in ("<Left>", "<Right>"):
            self.sld_gain.bind(key, lambda e: self.root.after(1, self.on_gain_release))
        group.append(self.sld_gain)
        self.v["gain"].trace_add("write", lambda *a: self.sync_gain_slider())
        self.sync_gain_slider()

    def set_gain_steps(self, gains):
        """The tuner reports its own gain steps when the receiver starts (R820T 29 steps, E4000 others)."""
        self.gain_steps = sorted(float(x) for x in gains) or list(R820T_GAINS)
        self.v["tuner_gains"].set(",".join(f"{g:.1f}" for g in self.gain_steps))
        self.sld_gain.config(to=len(self.gain_steps) - 1)
        self.sync_gain_slider()

    def restore_gain_steps(self):
        """Before the receiver starts: the gain steps the stick reported last time, so that its own values (e.g.
        E4000 21.5 dB) can be chosen and a saved value is not moved to another tuner's step."""
        try:
            steps = [float(x) for x in self.v["tuner_gains"].get().split(",") if x.strip()]
        except ValueError:
            steps = []
        if steps:
            self.gain_steps = sorted(steps)
            self.sld_gain.config(to=len(self.gain_steps) - 1)
        self.sync_gain_slider()

    def sync_gain_slider(self):
        try:
            cur = float(self.v["gain"].get())
        except ValueError:
            cur = 29.7
        i = min(range(len(self.gain_steps)), key=lambda k: abs(self.gain_steps[k] - cur))
        if int(round(self.gain_idx.get())) != i:
            self.gain_idx.set(i)
        self.lbl_gain.config(text=f"{self.gain_steps[i]:.1f} dB")

    def on_gain_drag(self):
        i = int(round(self.gain_idx.get()))
        i = min(max(i, 0), len(self.gain_steps) - 1)
        self.lbl_gain.config(text=f"{self.gain_steps[i]:.1f} dB")

    def on_gain_release(self):
        i = min(max(int(round(self.gain_idx.get())), 0), len(self.gain_steps) - 1)
        self.gain_idx.set(i)                                  # snap the knob onto the step
        val = f"{self.gain_steps[i]:.1f}"
        if val != self.v["gain"].get():
            self.v["gain"].set(val)
            self.on_rx_change()

    def build_rf_plot(self, parent):
        fig = Figure(figsize=(8, 6), facecolor=BG)
        gs = fig.add_gridspec(2, 1, height_ratios=[3, 2], hspace=0.06)
        self.ax_rf = fig.add_subplot(gs[0])
        self.ax_wf = fig.add_subplot(gs[1], sharex=self.ax_rf)
        for ax in (self.ax_rf, self.ax_wf):
            self.style_ax(ax)
        self.ax_rf.tick_params(labelbottom=False)
        self.ax_rf.set_ylabel("Power (dBFS)", color="white")
        self.ax_wf.set_xlabel("Frequency (MHz)", color="white")
        self.ax_wf.set_ylabel("Waterfall (newest on top)", color="white", fontsize=8)
        self.ln_rf, = self.ax_rf.plot([], [], color="#4fd3ff", lw=1.5)
        self.ln_floor = self.ax_rf.axhline(-100, color="#ffaa00", ls="--", lw=0.8)
        self.ax_rf.axvline(H1_MHZ, color="#ff5555", ls=":", lw=0.9)
        self.txt_rf = self.ax_rf.text(0.01, 0.97, "", transform=self.ax_rf.transAxes, va="top", color="white",
                                      family="monospace", fontsize=8)
        for ax in (self.ax_rf, self.ax_wf):
            ax.yaxis.label.set_color(MUTED)
            ax.xaxis.label.set_color(MUTED)
        fig.subplots_adjust(left=0.07, right=0.99, top=0.98, bottom=0.08)
        self.fig_rf = fig
        self.canvas_rf = self.add_canvas(fig, parent)

    def reset_rf_view(self):
        """Back to the full band and automatic levels (after zooming with the toolbar)."""
        self.rf_ylim = None
        self.rf_axis_id = -1
        try:
            self.canvas_rf.toolbar.update()          # clears the toolbar's zoom history
        except Exception:
            pass
        self.set_status("RF view reset: the scale is fitted to the present level and then kept fixed.", OK_COL)

    def rf_info_lines(self):
        cfg = self.cfg or {}
        lines = [f"{APP} v{VERSION}   {utc_str(time.time())[:19].replace('T', ' ')} UTC   RF view: fixed FFT {RF_NFFT} "
                 f"{RF_WINDOW}"]
        if cfg:
            lines.append(f"Center {cfg['target_hz'] / 1e6:.4f} MHz   LO offset {cfg['offset_hz'] / 1e3:g} kHz   "
                         f"Sample rate {cfg['fs'] / 1e6:g} MS/s   Gain {cfg['gain']:g} dB   PPM {cfg['ppm']:g}   "
                         f"Bias-T {'on' if cfg['bias'] else 'off'}")
        return lines

    def render_rf_png(self, path):
        """The RF spectrum and waterfall as they are now, with an information line, as a PNG."""
        self.fig_rf.savefig(path, dpi=150, facecolor=BG)
        return path

    def save_rf_image(self):
        if self.ln_rf.get_xdata() is None or len(self.ln_rf.get_xdata()) == 0:
            messagebox.showwarning("Save image", "Start the receiver first: the RF view is empty.")
            return
        try:
            path = os.path.join(self.images_saved_dir(), f"{self.save_name()}_rf_{time.strftime('%Y%m%d_%H%M%S', time.gmtime())}.png")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            txt = self.txt_rf.get_text()
            self.txt_rf.set_text(txt + "\n" + "\n".join(self.rf_info_lines()))
            try:
                self.render_rf_png(path)
            finally:
                self.txt_rf.set_text(txt)
            self.note_saved_image(path)
            self.set_status(f"Image saved: {path}", OK_COL)
        except Exception as e:
            messagebox.showerror("Save image", f"Saving the image failed:\n{e}")

    def update_rx_readouts(self):
        rx = self.rx
        cfg = self.cfg
        if cfg:
            rf = cfg["fs"] / RF_NFFT
            self.rv["rf"].config(text=f"{rf:,.0f} Hz")
            self.lbl_rf_res.config(text=f"RF view: FFT {RF_NFFT} (fixed) · {rf:,.0f} Hz")
            rbw = cfg["fs"] / cfg["nfft"]
            self.rv_h1fft_title.config(text=f"Hydrogen line FFT {cfg['nfft']}")
            self.rv["h1fft"].config(text=f"{rbw / 1e3:.2f} kHz ~ {C_KMS * rbw / H1_HZ:.2f} km/s")
        thr = rx.throughput() if rx is not None else None
        if thr is None:
            self.rv["thru"].config(text="-", fg=FG)
        else:
            self.rv["thru"].config(text=f"{100 * min(thr, 1.0):.1f} %", fg=OK_COL if thr > 0.98 else WARN_COL)
        if rx is None:
            self.rv["clip"].config(text="-", fg=FG)
            self.rv["flag60"].config(text="-", fg=FG)
            return
        self.rv["clip"].config(text=f"{100 * rx.clip:.2f} %", fg=OK_COL if rx.clip < 0.001 else ERR_COL)
        n = rx.flagged_last(60.0)
        self.rv["flag60"].config(text=f"{n:,} counts", fg=WARN_COL if n else OK_COL)

    def dc_check_state(self):
        """Offset tuning includes DC removal and needs no DC notch, so both are greyed (their values are not
        changed)."""
        locked = getattr(self, "phase", "idle") != "idle"
        for w in (getattr(self, "chk_dc", None), getattr(self, "ent_notch", None)):
            if w is None:
                continue
            try:
                w.config(state="disabled" if (locked or self.v["offset_tuning"].get()) else "normal")
            except Exception:
                pass

    def on_rx_change(self):
        self.dc_check_state()
        cfg, err = self.read_cfg()
        if err:
            self.set_status(err, ERR_COL)
            return
        old = self.cfg or {}
        changed = {k: v for k, v in cfg.items() if old.get(k) != v}
        self.cfg = cfg
        self.update_rbw()
        if not changed:
            return
        if self.rx is not None:
            if self.phase != "idle":         # widgets are locked; this is only a safety net
                return
            self.averager.start(AVG_MODES[1], 1, False, "preview")
            for k, v in changed.items():
                self.rx.send(k, v)
            msg = "Settings applied."
            if self.cold is not None:
                msg += "  Cold/Hot were made with other settings - redo them before measuring."
            self.set_status(msg, OK_COL if self.cold is None else WARN_COL)
        self.refresh_buttons()

    def on_flag_sigma(self):
        try:
            val = float(self.v["flag_sigma"].get().replace(",", "."))
            if not 2.0 <= val <= 50.0:
                raise ValueError
        except ValueError:
            self.set_status("Flag threshold must be 2-50 sigma.", ERR_COL)
            return
        if self.rx is not None:
            self.rx.flag_sigma = val

    def flag_sigma(self):
        try:
            return min(max(float(self.v["flag_sigma"].get().replace(",", ".")), 2.0), 50.0)
        except ValueError:
            return 5.0

    def tune_h1(self):
        self.v["freq"].set(f"{H1_MHZ:.6f}")
        self.on_rx_change()

    def update_rf(self, live, draw):
        axis_id, axis, p, rows, clip, notch_bins = live
        x = axis / 1e6                     # full RF_NFFT resolution: narrow interference is not averaged away
        y = to_db(p)
        floor = float(np.median(y))
        lo = axis[len(axis) // 2]
        excl = max(notch_bins, 2) * (axis[1] - axis[0])
        pm = np.where(np.abs(axis - lo) > excl, p, 0.0)
        ip = int(np.argmax(pm))
        peak = float(to_db(pm[ip]))

        wrows = [to_db(downsample_max(r, WF_COLS)) for r in rows]
        # The scale is reset only when the frequency axis really changes (centre, sample rate, LO offset) or with
        # Reset view - NOT when the gain changes, so that a gain change is seen as the level moving.
        axis_key = (round(float(axis[0])), round(float(axis[-1])), len(axis))
        if axis_key != getattr(self, "_rf_axis_seen", None) or self.wf is None or \
                (wrows and self.wf.shape[1] != len(wrows[0])):
            self._rf_axis_seen = axis_key
            ncol = len(wrows[0]) if wrows else len(downsample_max(p, WF_COLS))
            self.wf = np.full((WF_ROWS, ncol), np.nan, dtype=np.float32)
            self.wf_extent = [axis[0] / 1e6, axis[-1] / 1e6, WF_ROWS, 0]
            self.rf_ylim = None
            self.rf_axis_id = -1
        if wrows:
            k = min(len(wrows), WF_ROWS)
            self.wf = np.roll(self.wf, k, axis=0)
            self.wf[:k] = np.array(wrows[-k:][::-1], dtype=np.float32)

        self.rd["floor"].config(text=f"{floor:7.1f} dBFS")
        self.rd["peak"].config(text=f"{peak:7.1f} dB @ {axis[ip] / 1e6:.4f}")
        self.rd["snr"].config(text=f"{peak - floor:7.1f} dB")
        if clip > 0.001:
            self.rd["adc"].config(text=f"OVERLOAD {clip * 100:.1f} %", foreground=ERR_COL)
        else:
            self.rd["adc"].config(text="OK", foreground=OK_COL)
        if not draw:
            return

        # one point per screen pixel (mean of the channels in it): a thin, clean line. The waterfall keeps the
        # peak of each column, so narrow interference stays visible there.
        try:
            px = max(400, int(self.ax_rf.get_window_extent().width))
        except Exception:
            px = 1600
        if len(y) // px >= 2:
            # every channel is used, also the last ones (the line reaches the band edge)
            edges = np.linspace(0, len(y), px + 1).astype(int)
            cnt = np.diff(edges)
            xs = np.add.reduceat(x, edges[:-1]) / cnt
            ys = to_db(np.add.reduceat(p, edges[:-1]) / cnt)
        else:
            xs, ys = x, y
        self.ln_rf.set_data(xs, ys)
        self.ln_floor.set_ydata([floor, floor])
        if self.rf_axis_id != axis_id:
            self.ax_rf.set_xlim(x[0], x[-1])
            self.rf_axis_id = axis_id
        if self.rf_ylim is None:                  # set once (start, new frequency axis, Reset view), then fixed
            # room for the whole gain range, so the line stays visible when the gain is turned up or down
            try:
                g = float(self.v["gain"].get())
                steps = [float(s) for s in (getattr(self, "gain_steps", None) or [])]
                down, up = (g - min(steps), max(steps) - g) if steps else (25.0, 25.0)
            except (ValueError, KeyError):
                down, up = 25.0, 25.0
            top = max(peak, float(ys.max()), floor + 10.0) + 8.0 + up
            self.rf_ylim = (floor - 10.0 - down, min(top, 5.0))
            # waterfall colours fixed too (same moments): the noise floor gets darker / brighter with the gain,
            # so a raised floor (too much gain, overload) is seen at once; interference stays brighter
            self.rf_clim = (floor - 5.0 - down, floor + 25.0 + up)
        self.ax_rf.set_ylim(*self.rf_ylim)
        self.txt_rf.set_text(f"floor {floor:.1f} dBFS   peak {peak:.1f} dB   channel {(axis[1] - axis[0]):,.0f} Hz")
        if self.wf_img is None or self.wf_img.get_array().shape != self.wf.shape:
            if self.wf_img is not None:
                self.wf_img.remove()
            self.wf_img = self.ax_wf.imshow(self.wf, aspect="auto", extent=self.wf_extent, cmap="viridis",
                                            interpolation="nearest", origin="upper")
            self.ax_wf.set_xlim(x[0], x[-1])
        else:
            self.wf_img.set_data(self.wf)
            self.wf_img.set_extent(self.wf_extent)
        self.wf_img.set_clim(*self.rf_clim)                 # fixed with the RF scale (Reset view sets it again)
        self.canvas_rf.draw_idle()

    def on_ppm_test(self):
        rx = self.rx
        if rx is not None and rx.ppm_test:
            rx.ppm_test = False
            return
        if rx is None:
            messagebox.showinfo(APP, "Start the receiver first.")
            return
        if self.phase != "idle":
            messagebox.showinfo(APP, "Stop the running step first (Stop button).")
            return
        self.averager.start(AVG_MODES[1], 1, False, "preview")
        rx.ppm_test_result = None
        rx.ppm_test = True
        self.set_locked(self.rx_widgets, True)
        self.btn_ppm_test.config(text="Stop test")
        self.lbl_ppm_test.config(text="PPM test running... Let it run 10-30 min; the uncertainty shrinks with time. "
                                      "The spectrum is paused meanwhile.", fg=INFO_COL)

    def update_ppm_test(self):
        rx = self.rx
        res = rx.ppm_test_result if rx is not None else None
        running = rx is not None and rx.ppm_test
        if res is not None:
            m, sec = divmod(int(res["elapsed"]), 60)
            self.ppm_test_text = (f"{res['ppm']:+.2f} ± {res['err']:.2f} ppm vs PC clock "
                                  f"({m} min {sec:02d} s)")
            if running:
                self.lbl_ppm_test.config(text=f"PPM test running: {self.ppm_test_text}", fg=INFO_COL)
        if not running and self.btn_ppm_test.cget("text") == "Stop test":
            self.btn_ppm_test.config(text="PPM test")
            self.set_locked(self.rx_widgets, self.phase != "idle")
            if res is None:
                self.lbl_ppm_test.config(text="PPM test stopped before a result (needs a few seconds).", fg=WARN_COL)
            else:
                self.lbl_ppm_test.config(
                    text=f"PPM test result: {self.ppm_test_text}. The ± covers timing noise only; the PC clock's own "
                         "error (often 10-50 ppm on Windows) is NOT included. Type a value yourself only if you "
                         "trust the PC clock.", fg=OK_COL)

