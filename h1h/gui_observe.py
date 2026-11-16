"""H1-HUNTER SDR - Observe tab (pointing, calibration, averaging, hydrogen line, quality)."""
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
from .maps import draw_pointing


class GuiObserveMixin:
    VIEW_TITLES = {"h1": "Hydrogen line", "polar": "Polar map", "rad": "Radiometer check"}

    def build_observe_page(self, page):
        """Page 2 (design: Observe): pointing, calibration and averaging cards on the left; the big view in the
        centre card; two small views on the right. Clicking a small view swaps it with the centre view."""
        self.obs_page = page
        page.columnconfigure(1, weight=1)
        page.rowconfigure(0, weight=1)
        left = ScrollPanel(page, 350)
        left.grid(row=0, column=0, sticky="ns", padx=(6, 0), pady=6)
        self.build_h1_controls(left.inner)

        mid = tk.Frame(page, bg=PAGE)
        mid.grid(row=0, column=1, sticky="nsew", padx=6, pady=6)
        mid.columnconfigure(0, weight=1)
        mid.rowconfigure(0, weight=1)
        box = tk.Frame(mid, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        box.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        centre = tk.Frame(box, bg=CARD)
        centre.pack(fill="both", expand=True, padx=10, pady=10)
        centre.columnconfigure(0, weight=1)
        centre.rowconfigure(2, weight=1)
        hdr = tk.Frame(centre, bg=CARD)
        hdr.grid(row=0, column=0, sticky="ew")
        self.lbl_view_title = heading(hdr, "Hydrogen line")
        self.obs_h1_sel = tk.Frame(hdr, bg=CARD)
        tk.Label(self.obs_h1_sel, text="Show", bg=CARD, fg=MUTED, font=F_SMALL).pack(side="left", padx=(6, 4))
        w = ttk.Combobox(self.obs_h1_sel, textvariable=self.var("display", D_TLINE), values=DISPLAY_MODES,
                         width=26, state="readonly", font=F_MONO)
        w.pack(side="left")
        w.bind("<<ComboboxSelected>>", lambda e: self.on_view_change())
        tk.Label(self.obs_h1_sel, text="X axis", bg=CARD, fg=MUTED, font=F_SMALL).pack(side="left", padx=(12, 4))
        w = ttk.Combobox(self.obs_h1_sel, textvariable=self.var("xaxis", X_AXES[0]), values=X_AXES, width=22,
                         state="readonly", font=F_MONO)
        w.pack(side="left")
        w.bind("<<ComboboxSelected>>", lambda e: self.on_view_change())
        self.obs_grid_slot_centre = tk.Frame(hdr, bg=CARD)

        ctrl_box = tk.Frame(centre, bg=CARD)
        self.obs_h1_ctrl = ctrl_box
        ctrl_box.grid(row=1, column=0, sticky="ew", pady=(8, 6))
        ctrl = tk.Frame(ctrl_box, bg=CARD)
        ctrl.pack(fill="x")
        ctrl2 = tk.Frame(ctrl_box, bg=CARD)
        ctrl2.pack(fill="x", pady=(6, 0))
        ctrl3 = tk.Frame(ctrl_box, bg=CARD)              # view options: always visible, also on narrow screens
        ctrl3.pack(fill="x", pady=(2, 0))
        grp = tk.Frame(ctrl, bg=INPUT, highlightthickness=1, highlightbackground=INPUT_BORDER)
        grp.pack(side="left", padx=(0, 10))
        ttk.Checkbutton(grp, text="Autoscale", variable=self.var("autoy", True, "bool"), style="Grp.TCheckbutton",
                        command=self.on_autoscale).pack(side="left", padx=(8, 4), pady=4)
        tk.Label(grp, text="Zoom", bg=INPUT, fg=MUTED, font=F_SMALL).pack(side="left", padx=(8, 4))
        # Zoom 0.1x ... 50x on a logarithmic slider (1x in the middle): left widens the view, right magnifies
        self.var("y_zoom", 1.0, "double")
        self.sld_zoom = ttk.Scale(grp, from_=-1.0, to=math.log10(50.0), variable=self.var("y_zoom_log", 0.0, "double"),
                                  length=90, command=lambda v: self.on_view_change())
        self.sld_zoom.pack(side="left")
        self.lbl_zoom = tk.Label(grp, text="1.0×", width=5, bg=INPUT, fg=TEXT, font=F_MONO_S)
        self.lbl_zoom.pack(side="left")
        tk.Label(grp, text="Level", bg=INPUT, fg=MUTED, font=F_SMALL).pack(side="left", padx=(8, 4))
        self.sld_level = ttk.Scale(grp, from_=-5.0, to=5.0, variable=self.var("y_level", 0.0, "double"), length=90,
                                   command=lambda v: self.on_view_change())
        self.sld_level.pack(side="left")
        self.lbl_level = tk.Label(grp, text="0", width=6, bg=INPUT, fg=TEXT, font=F_MONO_S)
        self.lbl_level.pack(side="left", padx=(0, 6))

        def small(label, name, width, hint, kind="entry", values=None, default="", ctrl=ctrl):
            tk.Label(ctrl, text=label, bg=CARD, fg=MUTED, font=F_SMALL).pack(side="left", padx=(8, 4))
            if kind == "combo":
                w_ = ttk.Combobox(ctrl, textvariable=self.var(name, default), values=values, width=width,
                                  state="readonly", font=F_MONO_S)
                w_.bind("<<ComboboxSelected>>", lambda e: self.on_view_change())
            else:
                w_ = ttk.Entry(ctrl, textvariable=self.var(name, default), width=width, font=F_MONO_S)
                w_.bind("<Return>", lambda e: self.on_view_change())
                w_.bind("<FocusOut>", lambda e: self.on_view_change())
                Hint(w_, self.v[name], hint)
            w_.pack(side="left")
            return w_
        small("Y scale", "yscale", 7, "", "combo", ["Linear", "dB"], "Linear")
        small("Smoothing bins", "smooth", 7, "none", ctrl=ctrl2)
        self.avg_widgets.append(small("Line ± MHz", "line_half", 8, "auto 0.8", ctrl=ctrl2))
        self.btn_rm_flag = ttk.Button(ctrl2, text="Remove flagged", style="Small.TButton",
                                      command=self.on_remove_flagged)
        self.btn_rm_flag.pack(side="left", padx=(12, 2))
        ttk.Button(ctrl2, text="Reset view", style="Small.TButton", command=self.reset_view).pack(side="right")
        w_ = self.check(ctrl3, "h1_info", "Info text", True, cb=self.on_info_text)
        w_.pack_forget()
        w_.pack(side="left", padx=(8, 16))
        w_ = self.check(ctrl3, "hold_last", "Hold last average", False, cb=self.on_hold_last)
        w_.pack_forget()
        w_.pack(side="left")

        self.slot_centre = tk.Frame(centre, bg=BG, width=200, height=200)
        self.slot_centre.grid(row=2, column=0, sticky="nsew")
        self.slot_centre.pack_propagate(False)            # the plot follows the window, not the other way round
        bot = tk.Frame(centre, bg=CARD)
        bot.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.lbl_view_note = tk.Label(bot, text="", bg=CARD, fg=MUTED, font=F_SMALL, anchor="w", justify="left",
                                      wraplength=700)
        self.lbl_view_note.pack(side="left", fill="x", expand=True)
        ttk.Button(bot, text="Save image", image=self.icon("save"), compound="left", style="Small.TButton",
                   command=self.on_snapshot).pack(side="right")
        ro = tk.Frame(mid, bg=PAGE)
        ro.grid(row=1, column=0, sticky="ew", padx=2, pady=(0, 2))
        self.ro = {}
        for i, (key, title) in enumerate((("peak", "Peak T_L"), ("strength", "Line K·MHz"), ("vpeak", "Peak V_LSR"),
                                          ("noise", "Noise σ"), ("base", "Baseline"), ("saved", "Saved"),
                                          ("flagged", "Flagged"))):
            self.ro[key] = readout(ro, title, i, compact=True)         # one low row: more room for the plot

        right = tk.Frame(page, bg=PAGE, width=350)
        self.obs_right = right
        right.grid(row=0, column=2, sticky="ns", pady=6, padx=(0, 6))
        right.grid_propagate(False)
        right.columnconfigure(0, weight=1)
        self.side = []
        for i in range(2):
            box = tk.Frame(right, bg=CARD, highlightthickness=1, highlightbackground=BORDER, cursor="hand2")
            box.grid(row=i, column=0, sticky="nsew", padx=6, pady=6)
            right.rowconfigure(i, weight=1, uniform="side")
            title = tk.Label(box, text="", bg=CARD, fg=MUTED, font=F_SEC, anchor="w")
            title.pack(fill="x", padx=10, pady=(8, 4))
            slot = tk.Frame(box, bg=BG, width=320, height=150)
            slot.pack(fill="both", expand=True, padx=10)
            slot.pack_propagate(False)
            cap = tk.Label(box, text="", bg=CARD, fg=MUTED, font=F_SMALL, anchor="w", justify="left",
                           wraplength=310)
            cap.pack(fill="x", padx=10, pady=(4, 2))
            gslot = tk.Frame(box, bg=CARD)
            gslot.pack(fill="x", padx=10, pady=(0, 6))
            for w_ in (box, title, cap):
                w_.bind("<Button-1>", lambda e, i=i: self.swap_view(i))
            box.bind("<Enter>", lambda e, b=box: b.config(highlightbackground=ACCENT))
            box.bind("<Leave>", lambda e, b=box: b.config(highlightbackground=BORDER))
            self.side.append({"box": box, "title": title, "slot": slot, "cap": cap, "gslot": gslot})
        tk.Label(right, text="Click a small view to swap it with the centre.", bg=PAGE, fg=MUTED,
                 font=F_SMALL).grid(row=2, column=0, sticky="w", padx=8)

        # the three views; created last so that they are drawn on top of the slot frames
        self.views = {}
        self.build_h1_plot(self.make_view("h1", page))
        self.build_rad_plot(self.make_view("rad", page))
        self.build_polar_plot(self.make_view("polar", page))
        self.chk_polar_grid = ttk.Checkbutton(page, text="Show RA/Dec grid and scale",
                                              variable=self.var("polar_grid", True, "bool"),
                                              command=self.on_polar_grid)
        self.btn_polar_save = ttk.Button(page, text="Save image", image=self.icon("save"), compound="left",
                                         style="Small.TButton", command=self.save_polar_image)
        self.view_pos = ["h1", "polar", "rad"]          # centre, small 1, small 2
        self.place_views()

    def make_view(self, key, page):
        holder = tk.Frame(page, bg=BG)
        self.views[key] = {"holder": holder}
        return holder

    def add_view_canvas(self, key, fig):
        holder = self.views[key]["holder"]
        canvas = FigureCanvasTkAgg(fig, master=holder)
        tb = NavigationToolbar2Tk(canvas, holder, pack_toolbar=False)
        tb.update()
        canvas.get_tk_widget().pack(side="top", fill="both", expand=True)
        canvas.mpl_connect("button_press_event", lambda e, k=key: self.on_view_click(k, e))
        self.views[key].update(canvas=canvas, toolbar=tb, fig=fig)
        return canvas

    def place_views(self):
        for pos, key in enumerate(self.view_pos):
            v = self.views[key]
            slot = self.slot_centre if pos == 0 else self.side[pos - 1]["slot"]
            v["holder"].pack_forget()
            v["holder"].pack(in_=slot, fill="both", expand=True)
            v["holder"].lift()
            if pos == 0:
                v["toolbar"].pack(side="bottom", fill="x", before=v["canvas"].get_tk_widget())
            else:
                v["toolbar"].pack_forget()
                self.side[pos - 1]["title"].config(text=self.VIEW_TITLES[key].upper())
        centre = self.view_pos[0]
        self.lbl_view_title.config(text=self.VIEW_TITLES[centre].upper())
        if centre == "h1":
            self.obs_h1_sel.pack(side="left")
            self.obs_h1_ctrl.grid()
        else:
            self.obs_h1_sel.pack_forget()
            self.obs_h1_ctrl.grid_remove()
        self.chk_polar_grid.pack_forget()
        self.btn_polar_save.pack_forget()
        if centre == "polar":
            self.obs_grid_slot_centre.pack(side="left", padx=8)
            self.chk_polar_grid.pack(in_=self.obs_grid_slot_centre, side="left")
        else:
            self.obs_grid_slot_centre.pack_forget()
            pos = self.view_pos.index("polar")
            self.chk_polar_grid.pack(in_=self.side[pos - 1]["gslot"], side="left")
            self.btn_polar_save.pack(in_=self.side[pos - 1]["gslot"], side="right")   # the centre has its own
            self.btn_polar_save.lift()
        self.chk_polar_grid.lift()
        for i in (0, 1):                                  # the small polar map gets two thirds of the height
            self.obs_right.rowconfigure(i, weight=2 if self.view_pos[i + 1] == "polar" else 1, uniform="side")
        notes = {"h1": "Dashed grey = fitted baseline. The red dotted line is the H1 rest frequency (or 0 km/s).",
                 "polar": "North celestial pole in the centre, RA counter-clockwise. Red = where the antenna points "
                          "now (beam size). Updates every 10 s.",
                 "rad": "Measured noise vs. the theory 1/sqrt(N). When the measured curve stops falling, longer averaging "
                        "no longer helps."}
        self.view_notes = notes
        self.show_view_note()
        self.update_side_captions()
        self.force_redraw()
        self.polar_n_drawn = -2
        for key in self.view_pos:
            self.views[key]["canvas"].draw_idle()

    def swap_view(self, side_index):
        pos = side_index + 1
        self.view_pos[0], self.view_pos[pos] = self.view_pos[pos], self.view_pos[0]
        self.place_views()

    def on_view_click(self, key, event):
        pos = self.view_pos.index(key)
        if pos > 0:
            self.swap_view(pos - 1)

    def update_side_captions(self):
        for pos in (1, 2):
            key = self.view_pos[pos]
            cap = self.side[pos - 1]["cap"]
            if key == "rad":
                cap.config(text=self.lbl_rad.cget("text"), fg=self.lbl_rad.cget("fg"))
            elif key == "polar":
                cap.config(text=f"{self.maps.n_points} points · click to enlarge", fg="#aaaaaa")
            else:
                cap.config(text="click to enlarge", fg="#aaaaaa")

    def build_h1_controls(self, tab):
        a = self.avg_widgets
        c = card(tab, "Pointing")
        row = tk.Frame(c, bg=CARD)
        row.pack(fill="x")
        row.columnconfigure(0, weight=1, uniform="m")
        row.columnconfigure(1, weight=1, uniform="m")
        self.var("scan_mode", "drift")
        self.rb_drift = ttk.Button(row, text="Drift (fixed)", command=lambda: self.pick_scan_mode("drift"))
        self.rb_drift.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.rb_guided = ttk.Button(row, text=f"Guided ({MOUNT_SYS})", command=lambda: self.pick_scan_mode("guided"))
        self.rb_guided.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        a += [self.rb_drift, self.rb_guided]
        row = tk.Frame(c, bg=CARD)
        row.pack(fill="x", pady=(8, 0))
        self.btn_mount = ttk.Button(row, text="Connect to mount...", style="Small.TButton",
                                    command=self.connect_to_mount)
        self.btn_mount.pack(side="left")
        a.append(self.btn_mount)
        self.lbl_mount = tk.Label(row, text="Mount: disconnected", bg=CARD, fg=MUTED, font=F_SMALL)
        self.lbl_mount.pack(side="left", padx=8)
        G = tk.Frame(c, bg=CARD)
        G.pack(fill="x", pady=(4, 0))
        G.columnconfigure(0, weight=1, uniform="c")
        G.columnconfigure(1, weight=1, uniform="c")
        self.ent_az = self.field(G, 0, 0, "Azimuth °", "az", str(DEFAULT_AZ), cb=self.on_site_change, group=a)
        self.ent_alt = self.field(G, 0, 1, "Altitude °", "alt", str(DEFAULT_ALT), cb=self.on_site_change, group=a)
        self.field(G, 1, 0, "Dish diameter m", "dish", str(DEFAULT_DISH_M), cb=self.on_site_change, group=a)
        self.lbl_beam = self.value_cell(G, 1, 1, "Beam")
        e = self.field(G, 2, 0, "Beam efficiency (optional)", "eta", "", cb=self.on_site_change, group=a, span=2,
                       hint="")
        self.hint_eta = e.hint
        self.note(c, "Leave empty: Galactic hydrogen fills the beam, so T_B = T_line (checked against the LAB "
                     "survey). Only a value measured against a survey belongs here")
        self.lbl_now = self.note(c, "Now: -", color=TEXT)

        c = card(tab, "Calibration - in this order")
        G = tk.Frame(c, bg=CARD)
        G.pack(fill="x")
        G.columnconfigure(0, weight=1)
        e = self.field(G, 0, 0, "Air temperature °C", "air_c", "", cb=self.on_site_change, group=a, hint="")
        self.hint_air = e.hint
        bf = tk.Frame(c, bg=CARD)
        bf.pack(fill="x", pady=(6, 0))
        self.btn_cold = ttk.Button(bf, text="1. Cold - empty sky", command=self.on_cold)
        self.btn_cold.pack(fill="x", pady=3)
        self.btn_hot = ttk.Button(bf, text="2. Hot - ground / absorber", command=self.on_hot)
        self.btn_hot.pack(fill="x", pady=3)
        r2 = tk.Frame(bf, bg=CARD)
        r2.pack(fill="x", pady=3)
        self.btn_stop = ttk.Button(r2, text="" if self.icons else "Stop", image=self.icon("stop_red"),
                                   command=self.on_stop_phase)
        self.btn_stop.pack(side="right", padx=(6, 0))
        self.btn_meas = ttk.Button(r2, text="3. Measure", image=self.icon("play"), compound="left",
                                   command=self.on_measure)
        self.btn_meas.pack(side="left", fill="x", expand=True)
        self.check(bf, "drift_corr", "Gain drift correction (line-free channels)", False, cb=self.on_drift_corr)
        self.check(bf, "cold_smooth", "Smooth Cold reference (bandpass only)", False, cb=self.on_cold_smooth)
        self.lbl_cal = self.note(c, "1. Point the antenna at empty sky far from the Milky Way and press Cold "
                                    "(background / baseline).", color=WARN_COL)
        self.lbl_calinfo = self.note(c, "Cold: -\nHot: -")
        self.btn_reset_cal = ttk.Button(c, text="Reset calibration", style="Small.TButton",
                                        command=self.on_reset_calibration)
        self.btn_reset_cal.pack(anchor="w", pady=(8, 0))

        g, cb = self.rx_widgets, self.on_rx_change       # FFT settings are receiver settings (they restart it)
        c = card(tab, "Averaging")
        G = tk.Frame(c, bg=CARD)
        G.pack(fill="x")
        G.columnconfigure(0, weight=1, uniform="c")
        G.columnconfigure(1, weight=1, uniform="c")
        self.field(G, 0, 0, "FFT resolution", "nfft", "512", kind="combo", values=FFT_SIZES, cb=cb, group=g)
        self.field(G, 0, 1, "Window", "window", "Hann", kind="combo", values=WINDOW_NAMES, cb=cb, group=g)
        ov = tk.Frame(G, bg=CARD)
        ov.grid(row=1, column=0, columnspan=2, sticky="ew", pady=4)
        g.append(self.check(ov, "overlap", "50 % overlapping spectra", True, cb=cb))
        self.lbl_rbw = tk.Label(ov, text="", bg=CARD, fg=MUTED, font=F_SMALL, anchor="w")
        self.lbl_rbw.pack(fill="x")
        self.field(G, 2, 0, "Dynamic averaging", "counts", "1050000", cb=self.update_rbw, group=a)
        self.field(G, 2, 1, "Intermediate average", "inter", "1000", cb=self.update_rbw)
        self.lbl_dur = self.value_cell(G, 3, 0, "Duration / point", span=2)
        self.lbl_upd = self.note(c, "")
        self.pbar = ttk.Progressbar(c, maximum=1000)
        self.pbar.pack(fill="x", pady=(8, 2))
        self.lbl_prog = self.note(c, "")
        r = tk.Frame(c, bg=CARD)
        r.pack(fill="x", pady=(8, 0))
        r.columnconfigure(0, weight=1, uniform="b")
        r.columnconfigure(1, weight=1, uniform="b")
        ttk.Button(r, text="Reset average", style="Small.TButton",
                   command=self.on_reset_average).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(r, text="Save spectrum now", image=self.icon("save"), compound="left", style="Small.TButton",
                   command=self.on_save_now).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.note(c, "Dynamic averaging is used for Cold, Hot and every measurement point. Intermediate average = "
                     "spectra per screen update.")
        self.lbl_saved = self.note(c, "")
        self.lbl_iqnote = self.note(c, "")

    def pick_scan_mode(self, mode):
        self.v["scan_mode"].set(mode)
        self.on_scan_mode()

    def build_h1_plot(self, parent):
        fig = Figure(figsize=(8, 5), facecolor=BG)
        self.ax_h1 = fig.add_subplot(111)
        self.style_ax(self.ax_h1)
        self.ln_h1, = self.ax_h1.plot([], [], color="#ffcc00", lw=1.0, animated=True)
        self.ln_h1_base, = self.ax_h1.plot([], [], color="#888888", lw=0.8, ls="--", animated=True)
        self.ln_h1_f0 = self.ax_h1.axvline(H1_MHZ, color="#ff5555", ls=":", lw=0.9)
        self.txt_h1 = self.ax_h1.text(0.01, 0.97, "No averaged data yet.", transform=self.ax_h1.transAxes,
                                      va="top", color="white", family="monospace", fontsize=9, animated=True)
        fig.subplots_adjust(left=0.09, right=0.98, top=0.96, bottom=0.11)
        self.fig_h1 = fig
        self.canvas_h1 = self.add_view_canvas("h1", fig)
        # Blitting: axes, grid and labels are drawn only when they change; the line and its text are 'animated'
        # and are redrawn on top of a saved background (much lighter).
        self.h1_bg = None
        self.h1_last = 0.0
        self.canvas_h1.mpl_connect("draw_event", self._on_h1_draw)
        self.canvas_h1.mpl_connect("resize_event", self._on_h1_resize)

    def _on_h1_resize(self, event=None):
        """Margins in pixels, not in fractions: the axis labels stay visible also when the plot is small."""
        try:
            w, h = self.fig_h1.bbox.width, self.fig_h1.bbox.height
            if w > 50 and h > 50:
                self.fig_h1.subplots_adjust(left=min(0.2, max(0.09, 62.0 / w)), right=0.98,
                                            top=min(0.98, max(0.9, 1.0 - 10.0 / h)), bottom=min(0.3, max(0.11, 46.0 / h)))
        except Exception:
            pass

    def build_rad_plot(self, parent):
        fig = Figure(figsize=(8, 5), facecolor=BG)
        self.ax_rad = fig.add_subplot(111)
        self.style_ax(self.ax_rad)
        self.ln_rad_m, = self.ax_rad.plot([], [], "o-", color="#4fd3ff", ms=3, lw=1.0, label="measured")
        self.ln_rad_e, = self.ax_rad.plot([], [], "--", color="#ffaa00", lw=1.0, label="expected 1/√N")
        self.ax_rad.set_xscale("log")
        self.ax_rad.set_yscale("log")
        self.ax_rad.set_xlim(100, 1e6)
        self.ax_rad.set_ylim(0.01, 10)
        self.ax_rad.yaxis.set_major_locator(LogLocator(base=10, subs=(1.0, 2.0, 5.0)))
        self.ax_rad.yaxis.set_major_formatter(FuncFormatter(lambda val, pos: f"{val:g}"))
        self.ax_rad.yaxis.set_minor_formatter(NullFormatter())
        self.ax_rad.set_xlabel("Average counts (spectra)", color="white", fontsize=8)
        self.ax_rad.set_ylabel("Noise σ (%)", color="white", fontsize=8)
        self.ax_rad.legend(loc="upper right", fontsize=7, facecolor=BG, labelcolor="white", edgecolor="#666666")
        fig.subplots_adjust(left=0.14, right=0.97, top=0.95, bottom=0.15)
        self.fig_rad = fig
        self.canvas_rad = self.add_view_canvas("rad", fig)
        self.lbl_rad = tk.Label(parent, text="Radiometer check: -", fg=MUTED)       # text source for the captions

    def build_polar_plot(self, parent):
        self.fig_polar = Figure(figsize=(6, 6.3), facecolor=BG)
        self.canvas_polar = self.add_view_canvas("polar", self.fig_polar)
        self.polar_n_drawn = -2
        self.polar_t = 0.0
        self.polar_marks = []
        self.polar_pt = None

    def polar_grid(self):
        try:
            return bool(self.v["polar_grid"].get())
        except Exception:
            return True

    def on_polar_grid(self):
        self.polar_n_drawn = -2
        self.maps_dirty = True
        self.map_drawn = None                  # the Maps page polar map too

    def update_polar(self, force=False):
        """Redraws the live polar map every 10 s (or at once after a change) and moves the pointing marker."""
        now = time.time()
        ms = self.maps
        if force or self.polar_n_drawn == -2 or (ms.n_points != self.polar_n_drawn and now - self.polar_t > 10.0):
            self.polar_t = now
            ms.draw_polar(self.fig_polar, self.polar_grid())
            if self.view_pos[0] != "polar":                  # small view: no info text or title (they would not fit)
                for t in self.fig_polar.texts:
                    t.set_visible(False)
                if ms.polar_ax is not None:
                    ms.polar_ax.title.set_visible(False)
                    # the map fills the small view; a thin colour scale under it
                    axs = self.fig_polar.axes
                    ms.polar_ax.set_position([0.04, 0.13, 0.92, 0.85])
                    if len(axs) > 1:
                        axs[1].set_position([0.12, 0.055, 0.76, 0.025])
                        axs[1].tick_params(labelsize=7)
                        axs[1].xaxis.label.set_fontsize(7)
            self.polar_n_drawn = ms.n_points
            self.polar_marks = []
            self.polar_pt = None
            self.update_polar_marker(redraw=False)
            self.canvas_polar.draw_idle()
            self.update_side_captions()
        elif now - self.polar_t > 2.0:
            self.polar_t = now if self.polar_n_drawn == ms.n_points else self.polar_t
            if self.update_polar_marker(redraw=False):
                self.canvas_polar.draw_idle()

    def update_polar_marker(self, redraw=True):
        ax = self.maps.polar_ax
        pt = self.pointing_radec()
        if pt is not None and self.polar_pt is not None and (self.polar_marks or not self.polar_grid()) and \
                abs(pt[0] - self.polar_pt[0]) < 0.1 and abs(pt[1] - self.polar_pt[1]) < 0.1:
            return False
        for art in self.polar_marks:
            try:
                art.remove()
            except Exception:
                pass
        self.polar_marks = []
        self.polar_pt = pt
        if ax is not None and pt is not None and self.polar_grid():   # hidden together with the grid and scale
            self.polar_marks = draw_pointing(ax, pt[0], pt[1], self.maps.beam_deg(), self.maps.polar_rmax)
        if redraw:
            self.canvas_polar.draw_idle()
        return True

    def _draw_h1_animated(self):
        for a in (self.ln_h1_base, self.ln_h1, self.txt_h1):
            self.ax_h1.draw_artist(a)

    def _on_h1_draw(self, event):
        """After every full redraw (also resize, zoom tool): save the background and draw the moving parts."""
        try:
            self.h1_bg = self.canvas_h1.copy_from_bbox(self.fig_h1.bbox)
            self._draw_h1_animated()
        except Exception:
            self.h1_bg = None

    def _blit_h1(self):
        """Light update: background + moving parts only."""
        if self.h1_bg is None:
            self.canvas_h1.draw_idle()
            return
        try:
            self.canvas_h1.restore_region(self.h1_bg)
            self._draw_h1_animated()
            self.canvas_h1.blit(self.fig_h1.bbox)
        except Exception:
            self.canvas_h1.draw_idle()

    def update_rbw(self):
        self.update_beam_text()
        self.update_band_note()
        if not self.cfg:
            return
        rbw = self.cfg["fs"] / self.cfg["nfft"]
        dv = C_KMS * rbw / H1_HZ
        hop = self.cfg["nfft"] // 2 if self.cfg["overlap"] else self.cfg["nfft"]
        rate = self.cfg["fs"] / hop
        rf_rbw = self.cfg["fs"] / RF_NFFT
        self.lbl_rbw.config(text=f"Resolution {rbw / 1e3:.2f} kHz ~ {dv:.2f} km/s · {rate:,.0f} spectra/s")
        self.update_rfi_width_text()

        try:
            n = int(float(self.v["counts"].get()))
            sec = n / rate
            d = f"{sec / 60:.1f} min" if sec >= 90 else f"{sec:.0f} s"
            ui = max(self.inter_count() / rate, H1_REDRAW_S)
            self.lbl_dur.config(text=f"~ {d}")
            self.lbl_upd.config(text=f"{n:,} spectra per point · screen update every {self.inter_count():,} spectra "
                                     f"~ {ui:.1f} s")
        except ValueError:
            self.lbl_dur.config(text="Dynamic averaging must be a whole number.")

    def update_band_note(self):
        """Short note under the LO offset when the hydrogen line does not fit in the trusted band."""
        lab, c = getattr(self, "lbl_band", None), self.cfg
        if lab is None:
            return
        text = ""
        if c and abs(c["target_hz"] - H1_HZ) < 0.5 * c["fs"]:
            n = c["nfft"]
            axis = c["target_hz"] + c["offset_hz"] + (np.arange(n) - n // 2 + shift_bins(c["offset_hz"], c["fs"], n)) * c["fs"] / n
            try:
                s = self.v["line_half"].get().strip().replace(",", ".")
                manual = float(s) if s else None
            except ValueError:
                manual = None
            text = band_note(axis, trusted_mask(axis, tuple(c[k] for k in SIG_KEYS)), manual) or ""
        lab.config(text=text)

    def update_beam_text(self):
        try:
            dish = float(self.v["dish"].get().replace(",", ".") or DEFAULT_DISH_M)
            beam = 70.0 * (C_KMS * 1e3 / H1_HZ) / dish
            self.lbl_beam.config(text=f"~ {beam:.1f}°", fg=TEXT)
            self.hint_eta.set_text("empty = 1.0 (extended hydrogen)")
        except (ValueError, ZeroDivisionError):
            self.lbl_beam.config(text="dish must be a number (m)", fg=ERR_COL)
        try:
            self.lbl_beam2.config(text="Beam " + self.lbl_beam.cget("text"))
        except Exception:
            pass
        self.update_air_hint()
        self.maps_dirty = True

    def update_air_hint(self):
        """Grey text in the empty Air temperature field: the sensor value, else the automatic estimate."""
        sr = self.sensor_reading()
        h2 = getattr(self, "hint_air2", None)              # the same field on the Settings page
        if h2 is not None:
            h2.set_text(f"{sr[0]:.1f} °C (sensor)" if sr is not None else "empty = estimate")
        if sr is not None:
            self.hint_air.set_text(f"{sr[0]:.1f} °C (sensor)")
            return
        try:
            lat = float(self.v["lat"].get().replace(",", "."))
            amb, _ = estimate_ambient_k(lat, None)
            self.hint_air.set_text(f"~ {amb - 273.15:.0f} °C (automatic estimate)")
        except Exception:
            self.hint_air.set_text("empty = automatic estimate")

    def reset_view(self):
        self.v["y_zoom"].set(1.0)
        self.v["y_zoom_log"].set(0.0)
        self.v["y_level"].set(0.0)
        self.v["autoy"].set(True)
        self.h1_xkey = None
        self.slider_state()
        self.on_view_change()

    def slider_state(self):
        """Zoom and Level are for the manual scale: greyed while Autoscale is on."""
        on = bool(self.v["autoy"].get())
        for w in (getattr(self, "sld_zoom", None), getattr(self, "sld_level", None)):
            if w is not None:
                w.state(["disabled"] if on else ["!disabled"])

    def on_autoscale(self):
        """Autoscale off: the present range is kept and Zoom / Level start from it (1.0x, 0)."""
        if not self.v["autoy"].get():
            self.h1_base = tuple(self.ax_h1.get_ylim())
            self.h1_base_key = self.ax_h1.get_ylabel()
        self.v["y_zoom"].set(1.0)
        self.v["y_zoom_log"].set(0.0)
        self.v["y_level"].set(0.0)
        self.slider_state()
        self.on_view_change()

    def on_site_change(self):
        """Site / dish / beam efficiency changed: update the beam text at once and redraw."""
        self.update_rbw()
        self.on_view_change()

    def on_view_change(self):
        self.view_rev += 1
        self.update_band_note()
        try:
            self.v["y_zoom"].set(round(10.0 ** float(self.v["y_zoom_log"].get()), 3))
        except (ValueError, tk.TclError):
            pass
        try:
            self.lbl_zoom.config(text=f"{float(self.v['y_zoom'].get()):.1f}×")
            self.lbl_level.config(text=f"{float(self.v['y_level'].get()):+.2f}")
        except Exception:
            pass
        self.force_redraw()

    def hold_last(self):
        try:
            return bool(self.v["hold_last"].get())
        except Exception:
            return False

    def view_snapshot(self):
        """What the hydrogen line view shows: the running average, or with 'Hold last average' the last completed
        one until the next has 25 % of its spectra."""
        return self.averager.display_snapshot() if self.hold_last() else self.averager.snapshot()

    def on_hold_last(self):
        self.force_redraw()

    def on_info_text(self):
        """Shows / hides the information text in the hydrogen line view (it can cover the line) and the note
        line under it."""
        try:
            self.txt_h1.set_visible(bool(self.v["h1_info"].get()))
            self.canvas_h1.draw_idle()
        except Exception:
            pass
        self.show_view_note()

    def show_view_note(self):
        try:
            centre = self.view_pos[0]
            hide = centre == "h1" and not self.v["h1_info"].get()
            self.lbl_view_note.config(text="" if hide else getattr(self, "view_notes", {}).get(centre, ""))
        except Exception:
            pass

    def cold_smooth_on(self):
        try:
            return bool(self.v["cold_smooth"].get())
        except Exception:
            return False

    def cal_power(self, rec):
        """Power of a Cold or Hot average processed exactly like the measurements: without the flagged spectra (and
        SK channel blocks) when 'Remove flagged' is on, so that the same treatment cancels in P / P_cold and in Y."""
        if self.remove_flag:
            key = (id(rec), rec.get("t_last"), rec.get("counts"))
            hold = getattr(self, "_cal_clean", {})
            if key not in hold:
                r = remove_flagged(rec)
                hold = {k: v for k, v in hold.items() if k[0] in (id(self.cold), id(self.hot))}
                hold[key] = r[0] if r is not None else rec["p"]
                self._cal_clean = hold
            return hold[key]
        return rec["p"]

    def cold_ref(self):
        """The Cold spectrum used for P / P_cold: as measured, or smoothed (bandpass model) when the box is ticked.
        With 'Remove flagged' on, from the Cold average without its flagged spectra (as the measurements)."""
        c = self.cold
        base = self.cal_power(c)
        if not self.cold_smooth_on():
            return base
        key = (id(c), c["t_last"], c["counts"], self.remove_flag)
        if getattr(self, "_cold_ref_key", None) != key:
            self._cold_ref = smooth_reference(c["axis"], base, trusted_mask(c["axis"], c["sig"]))
            self._cold_ref_key = key
        return self._cold_ref

    def hot_y_tsys(self, rec, th):
        """Y = P_hot / P_cold over the line-free channels, and T_sys, with the current Cold reference and treatment."""
        site = self.site_or_default()
        half = resolve_half_mhz(rec["axis"], trusted_mask(rec["axis"], rec["sig"]), site["line_half"] if site else None)
        sel = self.y_selection(rec["axis"], rec["sig"], half)
        y = float(np.median(self.cal_power(rec)[sel] / self.cold_ref()[sel]))
        tsys = (th - y * self.cold["tc"]) / (y - 1.0) if y > 1.0 else float("nan")
        return y, tsys

    def recompute_hot(self, why):
        """Y and T_sys again from the stored Hot and Cold averages (after a change of their treatment)."""
        if self.hot is None or self.cold is None:
            return
        y, tsys = self.hot_y_tsys(self.hot, self.hot["th"])
        if not (y > 1.01 and math.isfinite(tsys)):
            return
        old = (self.hot["y"], self.hot["tsys"])
        self.hot = dict(self.hot, y=y, tsys=tsys)
        self.cal_history.append({"kind": "hot_recomputed", "time_utc": utc_str(time.time()), "reason": why,
                                 "y_factor": y, "tsys_K": tsys, "previous_y": old[0], "previous_tsys_K": old[1]})
        self.say(f"Calibration recomputed ({why}): Y {old[0]:.3f} -> {y:.3f}, Tsys {old[1]:.0f} -> {tsys:.0f} K.",
                 OK_COL)

    def on_cold_smooth(self):
        self.force_redraw()
        if self.hot is not None and self.cold is not None:
            self.say("Cold reference changed: press 2. Hot again so that Y and Tsys use the same reference.",
                     WARN_COL)
        self.set_status("Smoothed Cold reference " + ("on: the bandpass shape is removed without the narrow features "
                        "of the Cold sky." if self.cold_smooth_on() else "off: the Cold spectrum is used as measured."),
                        OK_COL)

    def drift_corr_on(self):
        try:
            return bool(self.v["drift_corr"].get())
        except Exception:
            return False

    def on_drift_corr(self):
        self.force_redraw()
        if self.drift_corr_on():
            self.set_status("Gain drift correction on: line-free channels are scaled to the Cold level "
                            "(continuum changes are not shown). Raw power is saved uncorrected.", OK_COL)
        else:
            self.set_status("Gain drift correction off: plain Cold/Hot calibration.", OK_COL)

    def force_redraw(self):
        self.h1_key = None
        self.rf_axis_id = -1

    def set_phase(self, phase):
        self.phase = phase
        busy = phase != "idle"
        if busy and self.rx is not None:
            self.rx.clip_max = 0.0
            self.rx.reset_flags = True       # the antenna was probably just moved: new reference level
        self.set_locked(self.rx_widgets, busy)
        self.set_locked(self.avg_widgets, busy)
        if self.rx is not None and hasattr(self.rx.source, "scene"):   # simulator only
            self.rx.source.scene = {"cold": "cold", "hot": "hot"}.get(phase, "sky")
        self.refresh_buttons()

    def current_sig(self):
        return self.rx.signature() if self.rx is not None else None

    def cold_valid(self, sig=None):
        sig = sig if sig is not None else self.current_sig()
        return self.cold is not None and sig is not None and self.cold["sig"] == sig

    def hot_valid(self, sig=None):
        return self.cold_valid(sig) and self.hot is not None and self.hot["sig"] == self.cold["sig"]

    def refresh_buttons(self):
        def style(done, busy):
            return "Busy.TButton" if busy else ("Done.TButton" if done else "TButton")
        cv, hv = self.cold_valid(), self.hot_valid()
        self.btn_cold.configure(style=style(cv, self.phase == "cold"),
                                text="1. Cold - empty sky" + (f"   {self.cold['tc']:.1f} K" if cv else ""),
                                image=self.icon("check") if cv else "", compound="left")
        self.btn_hot.configure(style=style(hv, self.phase == "hot"),
                               text="2. Hot - ground / absorber" + (f"   Y {self.hot['y']:.2f}" if hv else ""),
                               image=self.icon("check") if hv else "", compound="left")
        self.btn_meas.configure(style=style(False, self.phase == "measure"),
                                text="3. Measure - running" if self.phase == "measure" else "3. Measure")
        self.btn_stop.configure(state="normal" if self.phase != "idle" else "disabled")
        self.btn_rm_flag.configure(text=self.rm_flag_text(), style="Busy.TButton" if self.remove_flag else "TButton")

    def can_start_phase(self):
        if self.rx is None:
            messagebox.showinfo(APP, "Start the receiver first.")
            return False
        if self.phase != "idle":
            messagebox.showinfo(APP, "Stop the running step first (Stop button).")
            return False
        return True

    def pointing_now(self):
        """Site + pointing + sky position now. Raises ValueError/RuntimeError with a readable message.
        Guided mode: the pointing comes from the mount (its Az/Alt replace the typed values)."""
        site = self.read_site()
        sky = self.sky_at(time.time(), site, cache=False)
        if sky is None:
            raise RuntimeError("The pointing could not be computed" +
                               (" (is the mount connected?)" if self.guided() else ".") +
                               (f"\n\nReason: {self.sky_error}" if getattr(self, "sky_error", None) else ""))
        if self.guided():
            site = dict(site, az=round(sky["az_deg"], 2), alt=round(sky["alt_deg"], 2))
        return site, sky

    # ---------------- guided pointing (ASCOM mount on Windows, INDI mount on Linux) ----------------
    def guided(self):
        return self.v["scan_mode"].get() == "guided"

    def pointing_azalt(self, site, sky=None):
        """(Az, Alt) of the antenna: the mount's in guided mode (now, or of the given sky position), else the typed
        drift values."""
        if self.guided():
            if sky is None:
                try:
                    sky = self.sky_at(time.time(), site)
                except Exception:
                    sky = None
            if sky and sky.get("az_deg") is not None:
                return float(sky["az_deg"]), float(sky["alt_deg"])
        return site.get("az"), site.get("alt")

    def on_scan_mode(self):
        if self.guided() and self.mount is None:
            self.v["scan_mode"].set("drift")
            if messagebox.askyesno(f"Guided ({MOUNT_SYS})", f"Guided mode needs a connected {MOUNT_SYS} mount.\n\nConnect now?"):
                self.connect_to_mount()
            return
        st = "disabled" if self.guided() else "normal"
        for e in (self.ent_az, self.ent_alt):
            e.config(state=st)
        self.rb_drift.config(style="TButton" if self.guided() else "Sel.TButton")
        self.rb_guided.config(style="Sel.TButton" if self.guided() else "TButton")
        self.on_site_change()

    def connect_to_mount(self):
        if not IS_WINDOWS:
            self.connect_indi()
            return
        try:
            import win32com.client
        except Exception:
            messagebox.showerror("ASCOM", "Guided mode needs Windows with the ASCOM Platform and the pywin32 library "
                                          "(the installer adds pywin32).")
            return
        try:
            chooser = win32com.client.Dispatch("ASCOM.Utilities.Chooser")
            chooser.DeviceType = "Telescope"
            driver_id = chooser.Choose("")
            if not driver_id:
                return
            mount = win32com.client.Dispatch(driver_id)
            mount.Connected = True
            if not mount.Connected:
                raise Exception("Unable to connect to the mount.")
            name = ""
            try:
                name = str(mount.Name)
            except Exception:
                pass
            try:
                ra, dec = float(mount.RightAscension), float(mount.Declination)
            except Exception as e:
                mount.Connected = False
                raise Exception(f"The driver connected but RA/Dec cannot be read:\n{e}")
            try:
                trk = "ON" if mount.Tracking else "OFF"
            except Exception:
                trk = "unknown"
            warn = ""
            if "simulator" in (str(driver_id) + name).lower():
                warn = "\n\nWARNING: this is an ASCOM SIMULATOR, not real hardware. The data would be fake."
            if not messagebox.askyesno(
                    "Confirm mount", f"Driver: {driver_id}\nName: {name}\nRA {ra:.3f} h, Dec {dec:+.2f}°, Tracking: {trk}"
                    f"\n\nIs this the correct mount, and do these coordinates match where it really points?{warn}"):
                mount.Connected = False
                return
            try:
                self.mount_jnow = int(mount.EquatorialSystem) == 1
            except Exception:
                self.mount_jnow = True
            self.mount = mount
            try:  # take the site from the mount when the driver provides it
                lat, lon = float(mount.SiteLatitude), float(mount.SiteLongitude)
                ele = float(mount.SiteElevation)
                if abs(lat) > 1e-4 or abs(lon) > 1e-4:
                    self.v["lat"].set(f"{lat:.4f}")
                    self.v["lon"].set(f"{lon:.4f}")
                    self.v["height"].set(f"{ele:.0f}")
            except Exception:
                pass
            self.lbl_mount.config(text=f"Mount: connected ({name or driver_id})", fg=OK_COL)
            self.btn_mount.config(text="Mount connected")
            self.v["scan_mode"].set("guided")
            self.on_scan_mode()
        except Exception as e:
            self.mount = None
            messagebox.showerror("ASCOM", f"Connection failed:\n{e}")

    def connect_indi(self):
        """Linux: reads the mount from the INDI server of KStars / Ekos (EQMod etc.). Read only - never moves it."""
        from tkinter import simpledialog
        from .indi import IndiClient, IndiMount, DEFAULT_HOST, DEFAULT_PORT
        addr = simpledialog.askstring(
            "INDI mount", "Start KStars / Ekos and connect the mount first (e.g. EQMod Mount for a Sky-Watcher HEQ5).\n"
            "H1-HUNTER SDR only READS the pointing; Ekos keeps control of the mount.\n\nINDI server on this computer:",
            initialvalue=self.v["indi_server"].get() or f"{DEFAULT_HOST}:{DEFAULT_PORT}", parent=self.root)
        if not addr:
            return
        host, _, port = addr.strip().partition(":")
        try:
            client = IndiClient(host.strip() or DEFAULT_HOST, int(port or DEFAULT_PORT))
            client.open()
        except Exception as e:
            messagebox.showerror("INDI", f"Could not connect to the INDI server {addr}:\n{e}\n\nIs KStars / Ekos running "
                                         "with the mount connected (the INDI server starts with Ekos)?")
            return
        self.v["indi_server"].set(addr.strip())
        t0 = time.time()
        while time.time() - t0 < 4.0 and not client.telescopes():       # wait for the server's properties
            self.root.update()
            time.sleep(0.05)
        devs = client.telescopes()
        if not devs:
            client.close()
            messagebox.showerror("INDI", "The INDI server has no mount (no device publishes equatorial coordinates).\n"
                                         "Connect the mount in Ekos first.")
            return
        dev = devs[0]
        if len(devs) > 1:
            pick = simpledialog.askstring("INDI mount", "Several mounts found:\n" + "\n".join(devs) +
                                          "\n\nType the name of the one to use:", initialvalue=devs[0], parent=self.root)
            if not pick or pick.strip() not in devs:
                client.close()
                return
            dev = pick.strip()
        mount = IndiMount(client, dev)
        try:
            ra, dec, jnow = float(mount.RightAscension), float(mount.Declination), mount.EquatorialSystem == 1
            busy = mount.Slewing
        except Exception as e:
            client.close()
            messagebox.showerror("INDI", f"The mount {dev} cannot be read:\n{e}")
            return
        warn = ""
        if "simulator" in dev.lower():
            warn = "\n\nWARNING: this is an INDI SIMULATOR, not real hardware. The data would be fake."
        if not messagebox.askyesno(
                "Confirm mount", f"INDI device: {dev}\nRA {ra:.3f} h, Dec {dec:+.2f}° ({'JNow' if jnow else 'J2000'})"
                f"{'   - slewing now' if busy else ''}\n\nIs this the correct mount, and do these coordinates match "
                f"where it really points?{warn}"):
            client.close()
            return
        self.mount, self.mount_jnow, self.mount_cache = mount, jnow, None
        site = client.site(dev)
        if site and (abs(site[0]) > 1e-4 or abs(site[1]) > 1e-4):
            self.v["lat"].set(f"{site[0]:.4f}")
            self.v["lon"].set(f"{site[1]:.4f}")
            self.v["height"].set(f"{site[2]:.0f}")
        self.lbl_mount.config(text=f"Mount: connected (INDI: {dev})", fg=OK_COL)
        self.btn_mount.config(text="Mount connected")
        self.v["scan_mode"].set("guided")
        self.on_scan_mode()

    def mount_radec(self):
        """(RA h, Dec deg) of the mount, read at most every 2 s (COM calls are slow). None if unavailable."""
        if self.mount is None:
            return None
        now = time.time()
        if self.mount_cache is None or now - self.mount_cache[0] > 2.0:
            try:
                self.mount_slewing = bool(getattr(self.mount, "Slewing", False))
                if self.mount_slewing:
                    self.mount_cache = (now, None)
                else:
                    self.mount_cache = (now, (float(self.mount.RightAscension), float(self.mount.Declination)))
                if self.lbl_mount.cget("fg") == ERR_COL:
                    self.lbl_mount.config(text=f"Mount: connected ({getattr(self.mount, 'Name', '') or MOUNT_SYS})",
                                          fg=OK_COL)
            except Exception as e:
                self.mount_cache = (now, None)
                self.mount_slewing = False
                self.lbl_mount.config(text=f"Mount: not readable - {str(e)[:60]}", fg=ERR_COL)
        return self.mount_cache[1]

    def check_slewing(self):
        """Guided mode: while the mount slews, the running Cold / Hot / Measure average is restarted, so that no
        spectrum taken during the slew ends up in an average (H1-Hunter skipped such samples in the same way)."""
        if not self.guided() or self.mount is None or self.phase == "idle":
            self.slew_reset = False
            return
        self.mount_radec()
        if self.mount_slewing:
            self.averager.reset()
            if self.rx is not None:
                self.rx.reset_flags = True
            if not self.slew_reset:
                self.slew_reset = True
                self.set_status("Mount is slewing: the running average was restarted and waits until the mount stops.",
                                WARN_COL)
        elif self.slew_reset:
            self.slew_reset = False
            self.set_status("Mount stopped: averaging continues from zero at the new position.", OK_COL)

    def pointing_radec(self):
        """Pointing now as (RA deg, Dec deg) ICRS, or None."""
        sky = self.sky_at(time.time(), self.site_or_default())
        return (sky["ra_deg"], sky["dec_deg"]) if sky else None

    def counts_value(self, key, name):
        try:
            n = int(float(self.v[key].get()))
            if n < 1:
                raise ValueError
            return n
        except ValueError:
            messagebox.showerror(APP, f"{name} must be a positive whole number.")
            return None

    def relay_wanted(self):
        return bool(self.v["relay_on"].get())

    def relay_every(self):
        try:
            return max(1, int(float(self.v["relay_every"].get())))
        except ValueError:
            return 5

    def open_relay(self):
        """Opens the relay if it is wanted and not open yet. Returns False (with a message) on failure."""
        if self.relay is not None or not self.relay_wanted():
            return True
        try:
            if self.v["device"].get() == SIM_DEVICE and self.rx is not None:
                relay = SimRelay(self.rx.source)
            else:
                relay = SerialRelay(self.v["relay_port"].get().strip())
            relay.open()
        except Exception as e:
            messagebox.showerror("Relay", f"The relay could not be opened:\n{e}")
            self.lbl_relay.config(text=f"Relay: error - {e}", fg=ERR_COL)
            return False
        self.relay = relay
        self.lbl_relay.config(text="Relay: ready (antenna)", fg=OK_COL)
        return True

    def set_relay(self, on):
        if self.relay is None:
            return
        try:
            self.relay.set(on)
            self.lbl_relay.config(text="Relay: REFERENCE LOAD" if on else "Relay: antenna",
                                  fg=WARN_COL if on else OK_COL)
        except Exception as e:
            self.lbl_relay.config(text=f"Relay: error - {e}", fg=ERR_COL)

    def close_relay(self):
        if self.relay is not None:
            self.relay.close()
            self.relay = None
            self.lbl_relay.config(text="Relay: off", fg=FG)

    def on_test_relay(self):
        if self.rx is None and self.v["device"].get() == SIM_DEVICE:
            messagebox.showinfo(APP, "Start the receiver first (the simulator relay belongs to the simulator).")
            return
        if self.phase != "idle":
            messagebox.showinfo(APP, "Stop the running step first (Stop button).")
            return
        was = self.v["relay_on"].get()
        self.v["relay_on"].set(True)
        ok = self.open_relay()
        self.v["relay_on"].set(was)
        if not ok:
            return
        self.set_relay(True)
        self.root.after(2000, lambda: (self.set_relay(False), None if self.relay_wanted() else self.close_relay()))
        self.set_status("Relay test: reference load for 2 s, then back to the antenna. Listen for the click.", OK_COL)

    def start_reference(self, tag):
        """Switches to the reference load and averages one reference spectrum (tag 'cold_ref' or 'ref')."""
        n = self.counts_value("counts", "Dynamic averaging")
        if n is None:
            return False
        self.set_relay(True)
        if self.rx is not None:
            self.rx.reset_flags = True
        self.averager.start(AVG_MODES[0], n, False, tag, settle=RELAY_SETTLE_S)
        return True

    def on_cold(self):
        if not self.can_start_phase():
            return
        try:
            site, sky = self.pointing_now()
        except Exception as e:
            messagebox.showerror("Site & pointing", str(e))
            return
        n = self.counts_value("counts", "Dynamic averaging")
        if n is None:
            return
        if abs(sky["b_deg"]) < 20 or site["alt"] < 20:
            if not messagebox.askyesno(
                    "Cold pointing", f"The antenna points at b = {sky['b_deg']:+.0f}°, altitude {site['alt']:.0f}°.\n"
                    "Cold works best at empty high-latitude sky (|b| > 20°, altitude > 20°): there the hydrogen "
                    "in the background is weakest.\n\nUse this pointing anyway?"):
                return
        if self.relay_wanted() and not self.open_relay():
            return
        self.set_relay(False)
        self.ref0 = None
        self.pending = {"site": site, "sky": sky}
        self.start_iq_recording(site, "cold")
        self.averager.start(AVG_MODES[0], n, False, "cold")
        self.set_phase("cold")
        self.say(f"Cold (background): averaging {n:,} spectra at b = {sky['b_deg']:+.0f}°. Keep the antenna still.",
                 INFO_COL)

    def on_hot(self):
        if not self.can_start_phase():
            return
        if not self.cold_valid():
            self.say("Do Cold first (with the current receiver settings), then Hot.", ERR_COL)
            return
        try:
            site = self.read_site()
        except ValueError as e:
            messagebox.showerror("Site & pointing", str(e))
            return
        n = self.counts_value("counts", "Dynamic averaging")
        if n is None:
            return
        self.pending = {"site": site}
        self.start_iq_recording(site, "hot")
        self.averager.start(AVG_MODES[0], n, False, "hot")
        self.set_phase("hot")
        self.say(f"Hot: point the antenna at the ground or cover it with the absorber. Averaging {n:,} spectra.",
                 INFO_COL)

    def on_measure(self):
        """Measures until Stop: every finished average (Dynamic averaging spectra) is saved automatically."""
        if not self.can_start_phase():
            return
        if not self.cold_valid():
            self.say("Measuring needs a Cold (background) made with the current receiver settings.", ERR_COL)
            messagebox.showwarning(APP, "Measuring needs a Cold (background) made with the current receiver "
                                        "settings. Press 1. Cold first.")
            return
        try:
            site = self.read_site()
        except ValueError as e:
            messagebox.showerror("Site & pointing", str(e))
            return
        age = (time.time() - self.cold["t_last"]) / 60.0
        if age > COLD_MAX_AGE_MIN and not messagebox.askyesno(
                "Cold age", f"The Cold is {age:.0f} min old (limit {COLD_MAX_AGE_MIN:.0f} min). The receiver gain "
                "drifts with temperature, so an old background can bend the baseline.\n\nMeasure anyway?"):
            return
        if not self.hot_valid() and not messagebox.askyesno(
                "No Hot calibration", "Without Hot the result is a ratio to Cold, not Kelvin.\n\nMeasure anyway?"):
            return
        n = self.counts_value("counts", "Dynamic averaging")
        if n is None:
            return
        if self.relay_wanted():
            if not self.open_relay():
                return
            if self.ref0 is None and not messagebox.askyesno(
                    "Reference load", "There is no reference taken after Cold (do Cold with the relay on).\n\n"
                    "Measure now? The first reference of this measurement is then used as the start level."):
                return
        self.set_relay(False)
        self.pending = {"site": site}
        self.series = True
        self.sky_since_ref = 0
        self.saved_count = 0
        self.start_iq_recording(site, "measure")
        self.averager.start(AVG_MODES[0], n, True, "measure", 0.0, settle=RELAY_SETTLE_S if self.relay else 0.0)
        self.set_phase("measure")
        extra = f" Reference load every {self.relay_every()} averages." if self.relay else ""
        self.say(f"Measuring: {n:,} spectra per average, each finished average is saved. Press Stop to end it.{extra}",
                 INFO_COL)

    def on_stop_phase(self):
        if self.phase == "measure":
            rec = self.averager.export()
            if (rec is not None and rec["tag"] == "measure" and rec["counts"] > 0
                    and not self.averager.snapshot()["done"]):
                if messagebox.askyesno(APP, f"Save the unfinished average ({rec['counts']:,} spectra)?"):
                    self.save_measurement(rec, auto=False)
            self.say("Measurement stopped. The last average stays on the plot.", WARN_COL)
        elif self.phase in ("cold", "hot"):
            self.say(f"{self.phase.capitalize()} cancelled.", WARN_COL)
            self.averager.start(AVG_MODES[1], 1, False, "preview")
        self.set_relay(False)
        self.stop_iq_recording()
        self.set_phase("idle")

    def say(self, text, color=WARN_COL):
        self.lbl_cal.config(text=text, fg=color)

    def y_selection(self, axis, sig, half_mhz):
        mask = trusted_mask(axis, sig)
        return mask & (np.abs(axis / 1e6 - H1_MHZ) > half_mhz)

    def finish_cold(self, rec):
        site, sky = self.pending["site"], self.pending["sky"]
        amb, amb_note = estimate_ambient_k(site["lat"], site["air_c"], self.sensor_reading())
        tc, parts = cold_reference(site["alt"], sky["b_deg"], site["height"], amb, site["dish"])
        self.cold = dict(rec, tc=tc, parts=parts, amb=amb, amb_note=amb_note, alt=site["alt"], az=site["az"],
                         b=sky["b_deg"], l=sky["l_deg"])
        self.hot = None
        self.cal_history.append({"kind": "cold", "time_utc": utc_str(rec["t_last"]), "counts": rec["counts"],
                                 "t_cold_ref_K": tc, "alt_deg": site["alt"], "b_deg": sky["b_deg"]})
        self.save_cal_file("cold", self.cold)
        good = site["alt"] >= COLD_GOOD_ALT and abs(sky["b_deg"]) >= COLD_GOOD_B   # yellow = low or near the plane
        self.say(f"Cold locked: {tc:.1f} K (CMB {parts['cmb']:.1f} + Galaxy {parts['galaxy']:.1f} + atmosphere "
                 f"{parts['atmosphere']:.1f} + spillover {parts['spillover']:.1f}; alt {site['alt']:.0f}°, "
                 f"b {sky['b_deg']:+.0f}°). Next: 2. Hot - point at the ground / absorber.",
                 OK_COL if good else WARN_COL)

    def finish_hot(self, rec):
        if not self.cold_valid(rec["sig"]):
            self.say("Hot was made with different settings than Cold. Redo Cold and Hot.", ERR_COL)
            return
        site = self.pending["site"]
        amb, amb_note = estimate_ambient_k(site["lat"], site["air_c"], self.sensor_reading())
        th, hot_note = amb, "hot load = " + amb_note          # ground / absorber at air temperature
        half = resolve_half_mhz(rec["axis"], trusted_mask(rec["axis"], rec["sig"]), site["line_half"])
        sel = self.y_selection(rec["axis"], rec["sig"], half)
        y = float(np.median(self.cal_power(rec)[sel] / self.cold_ref()[sel]))
        if y <= 1.01:
            self.say(f"Hot is not clearly above Cold (Y = {y:.3f}). Check the pointing / absorber and gain, "
                     "then press Hot again.", ERR_COL)
            messagebox.showerror("Calibration", f"Hot is not clearly above Cold (Y = {y:.3f}).")
            return
        tc = self.cold["tc"]
        tsys = (th - y * tc) / (y - 1.0)
        self.hot = dict(rec, th=th, y=y, tsys=tsys, note=hot_note)
        self.cal_history.append({"kind": "hot", "time_utc": utc_str(rec["t_last"]), "counts": rec["counts"],
                                 "t_hot_ref_K": th, "y_factor": y, "tsys_K": tsys})
        self.save_cal_file("hot", self.hot)
        self.say(f"Calibrated: Y = {y:.3f}, Tsys ~ {tsys:.0f} K (Cold {tc:.1f} K, Hot {th:.1f} K; {hot_note}). "
                 "Point at the target and press 3. Measure.", OK_COL)

    def on_reset_average(self):
        """Clears only the running average. The stored Cold/Hot calibration is never touched."""
        if self.phase == "idle":
            self.averager.start(AVG_MODES[1], 1, False, "preview")     # nothing running: clear the preview only
        else:
            self.averager.reset()                                      # restart the running Cold/Hot/Measure average
        if self.rx is not None:
            self.rx.reset_flags = True
        self.set_status("Average reset. The Cold/Hot calibration is kept.", OK_COL)

    def on_reset_calibration(self):
        """Forgets Cold and Hot (separate from Reset average). Saved calibration files are not deleted."""
        if self.phase != "idle":
            messagebox.showinfo(APP, "Stop the running step first (Stop button).")
            return
        if self.cold is None and self.hot is None:
            return
        if not messagebox.askyesno(APP, "Forget the Cold and Hot calibration?\n\nMeasuring then needs a new Cold "
                                        "(and Hot for Kelvin). The saved calibration files are kept."):
            return
        self.cold = self.hot = None
        self.cal_history.append({"kind": "reset", "time_utc": utc_str(time.time())})
        self.force_redraw()
        self.refresh_buttons()
        self.say("Calibration reset. 1. Point at empty sky and press Cold.", WARN_COL)

    def on_completed(self, rec):
        tag = rec["tag"]
        if {"cold_ref": "cold", "ref": "measure"}.get(tag, tag) != self.phase:
            return
        rec["clip_max"] = self.rx.clip_max if self.rx is not None else 0.0
        if self.rx is not None:
            self.rx.clip_max = 0.0
        if tag in ("cold", "hot", "cold_ref") and rec["clip_max"] > 0.001:
            self.stop_iq_recording()
            self.say(f"{tag.capitalize()} rejected: ADC overload ({100 * rec['clip_max']:.1f} % of samples clipped) "
                     "makes the Y factor wrong. Lower the gain on the RF Spectrum page, then redo Cold and Hot.",
                     ERR_COL)
            messagebox.showerror("Calibration", f"{tag.capitalize()} rejected because of ADC overload. "
                                 "Lower the gain, then redo Cold and Hot.")
            self.set_relay(False)
            self.set_phase("idle")
            return
        if tag in ("cold", "hot"):
            self.stop_iq_recording()                  # the Cold / Hot raw IQ ends with its average
        if tag == "cold":
            self.finish_cold(rec)
            if self.relay is not None and self.start_reference("cold_ref"):
                self.say(self.lbl_cal.cget("text") + "  Now: reference load (relay) for the gain reference...",
                         INFO_COL)
                return
            self.set_phase("idle")
        elif tag == "cold_ref":
            self.set_relay(False)
            self.ref0, self.ref0_t = rec["p"].copy(), rec["t_last"]
            self.gain_g, self.gain_t = 1.0, rec["t_last"]
            self.save_cal_file("ref", rec)
            self.say("Cold and reference locked. Next: 2. Hot - point at the ground / absorber.", OK_COL)
            self.set_phase("idle")
        elif tag == "hot":
            self.finish_hot(rec)
            self.set_phase("idle")
        elif tag == "measure":
            path = self.save_measurement(rec, auto=True) if self.v["save_spectra"].get() else None
            self.saved_count += 1
            self.after_measure_average(rec, path)
            self.sky_since_ref += 1
            if self.relay is not None and self.sky_since_ref >= self.relay_every():
                self.start_reference("ref")
        elif tag == "ref":
            self.set_relay(False)
            mask = self.trusted_mask(rec)
            if self.ref0 is None:
                self.ref0, self.ref0_t = rec["p"].copy(), rec["t_last"]
            self.gain_g, self.gain_t = gain_factor(rec["p"], self.ref0, mask), rec["t_last"]
            self.save_cal_file("ref", rec)
            self.ezra_rows.append((0.5 * (rec["t_first"] + rec["t_last"]), rec["p"].copy(), True, rec["axis"],
                                   self.ezra_pointing()))
            self.sky_since_ref = 0
            n = self.counts_value("counts", "Dynamic averaging") or rec["counts"]
            self.averager.start(AVG_MODES[0], n, True, "measure", 0.0, settle=RELAY_SETTLE_S)

    def trusted_mask(self, d):
        return trusted_mask(d["axis"], d["sig"])

    def gain_corrected(self, d):
        """True when sky data d should be divided by the current gain factor."""
        return self.ref0 is not None and d.get("tag") == "measure" and self.cold_valid(d.get("sig"))

    def update_calinfo(self):
        lines = []
        sig = self.current_sig()
        color = FG
        if self.cold is None:
            lines.append("Cold: not done")
        else:
            c = self.cold
            age = (time.time() - c["t_last"]) / 60.0
            valid = self.cold_valid(sig) if sig is not None else True
            lines.append(f"Cold {utc_str(c['t_last'])[11:16]} UTC · age {age:.0f} min (calculated, warns over "
                         f"{COLD_MAX_AGE_MIN:.0f} min)" + ("" if valid else "  (settings changed - redo)"))
            p = c["parts"]
            lines.append(f"Cold sky {c['tc']:.1f} K = CMB {p['cmb']:.1f} + Galaxy {p['galaxy']:.1f} + atmosphere "
                         f"{p['atmosphere']:.1f} + spillover {p['spillover']:.1f}")
            if age > COLD_MAX_AGE_MIN:
                lines.append("WARNING: Cold is older than the limit - redo Cold (and Hot).")
                color = WARN_COL
            if sig is not None and not valid:
                color = ERR_COL
        if self.hot is None:
            lines.append("Hot: not done")
        else:
            h = self.hot
            lines.append(f"Hot {utc_str(h['t_last'])[11:16]} UTC · load {h['th']:.0f} K (calculated from air "
                         f"temperature) · Y {h['y']:.2f} · Tsys {h['tsys']:.0f} K")
        if self.ref0 is not None:
            lines.append(f"Reference load: gain factor {self.gain_g:.4f} "
                         f"({utc_str(self.gain_t)[11:19] if self.gain_t else '-'} UTC)")
        self.lbl_calinfo.config(text="\n".join(lines), fg=color)

    def frame_time(self, sig):
        fs, nfft = sig[SIG_KEYS.index("fs")], sig[SIG_KEYS.index("nfft")]
        hop = nfft // 2 if sig[SIG_KEYS.index("overlap")] else nfft
        return hop / fs

    def update_progress(self, snap):
        n = snap["running_counts"]
        t_frame = self.frame_time(snap["sig"]) if snap["sig"] else 0.0
        label = {"cold": "Cold", "hot": "Hot", "measure": "Measure", "preview": "Preview", "cold_ref": "Reference load",
                 "ref": "Reference load"}.get(snap["tag"], "")
        if snap["mode"] == AVG_MODES[0]:
            frac = n / max(snap["target"], 1)
            txt = f"{label}: {n:,} / {snap['target']:,} spectra ({n * t_frame:.1f} s)"
            if snap["done"]:
                txt += "  (complete)"
        else:
            frac = 0.0
            txt = f"{label}: {n:,} spectra ({n * t_frame:.1f} s)"
        if snap["tag"] in ("measure", "ref") or (self.phase == "measure"):
            txt += f"\nSaved averages: {self.saved_count}"
            if self.relay is not None:
                txt += (f"   next reference after {max(self.relay_every() - self.sky_since_ref, 0)} more"
                        if snap["tag"] == "measure" else "   (reference load now)")
        self.pbar["value"] = 1000 * min(frac, 1.0)
        self.lbl_prog.config(text=txt)

    def site_or_default(self):
        try:
            return self.read_site()
        except ValueError:
            return None

    def sky_at(self, t_unix, site, cache=True):
        """Sky position of the pointing at time t (fixed Az/Alt, or the mount's RA/Dec in guided mode)."""
        if site is None or t_unix is None:
            return None
        if self.guided():
            rd = self.mount_radec()
            if rd is None:
                return None
            key = ("guided", round(rd[0], 4), round(rd[1], 3), site["lat"], site["lon"], site["height"],
                   round(t_unix / 10.0))
            fn = lambda: sky_position_radec(rd[0], rd[1], self.mount_jnow, site["lat"], site["lon"],
                                            site["height"], t_unix)
        else:
            key = (site["az"], site["alt"], site["lat"], site["lon"], site["height"], round(t_unix / 10.0))
            fn = lambda: dict(sky_position(site["az"], site["alt"], site["lat"], site["lon"], site["height"], t_unix),
                              az_deg=site["az"], alt_deg=site["alt"])
        if not cache or key not in self.sky_cache:
            try:
                self.sky_cache = {key: fn()}
                self.sky_error = None
            except Exception as e:
                self.sky_cache = {key: None}
                self.sky_error = f"{type(e).__name__}: {e}"
        return self.sky_cache[key]

    def products(self, d):
        """All spectra derived from an average d (snapshot or record): dict of arrays + scalars."""
        axis, p, sig = d["axis"], d["p"], d["sig"]
        n = len(axis)
        mask = trusted_mask(axis, sig)
        site = self.site_or_default()
        half = resolve_half_mhz(axis, mask, site["line_half"] if site else None)
        eta, eta_note = self.beam_efficiency(site)
        out = {"axis": axis, "mask": mask, "half": half, "p": p, "ratio": None, "tant": None, "tline": None,
               "tbase": None, "tb_line": None, "eta": eta, "eta_note": eta_note, "notes": []}
        out["drift_g"] = None
        out["rfi"] = None
        if self.cold_valid(sig) and d.get("tag") != "cold":
            out["ratio"] = p / np.maximum(self.cold_ref(), 1e-30)
            out["cold_smoothed"] = self.cold_smooth_on()
            if self.remove_flag and d.get("tag") not in ("hot", "cold_ref", "ref"):
                # 'Remove flagged' also removes narrow RFI channels (spikes, and dips left by RFI in Cold) from the
                # baseline fit, the line integral and the maps. The raw power is not changed.
                # the user's excluded ranges (Settings) first, then the automatic narrow-spike search
                w_ch, n_sig = self.rfi_setting(axis)
                ex = excluded_channels(axis, self.rfi_excluded()) & mask
                dead = d.get("sk_dead")
                if dead is None and d.get("sk_rm_n") is not None:
                    dead = sk_dead(d)
                dead = (dead & mask) if dead is not None else np.zeros(n, dtype=bool)
                # hysteresis: the channels found in the previous update of the same average (same start time,
                # Cold and settings) are passed on, so a spike near the threshold does not blink in and out
                hkey = (d.get("tag"), d.get("t_first"), n, id(self.cold), self.cold.get("t_last"),
                        out["cold_smoothed"], w_ch, n_sig)
                hold = getattr(self, "_rfi_hold", {})
                found = rfi_channels(out["ratio"], mask & ~ex & ~dead, w_ch, n_sig, keep=hold.get(hkey))
                if d.get("t_first") is not None:
                    hold.pop(hkey, None)
                    hold[hkey] = found                       # newest last; only the last few averages are kept
                    while len(hold) > 4:
                        hold.pop(next(iter(hold)))
                    self._rfi_hold = hold
                rfi = found | ex | dead
                out["excluded"] = ex if ex.any() else None
                out["sk_dead"] = dead if dead.any() else None
                if rfi.any():
                    mask = mask & ~rfi
                    out["mask"], out["rfi"] = mask, rfi
            if self.drift_corr_on() and d.get("tag") not in ("hot", "cold_ref", "ref"):
                # gain drift correction: the line-free channels are scaled back to the Cold level (ratio 1).
                # Only when the box is ticked; otherwise the calibration is exactly the plain one.
                g = line_free_level(axis, out["ratio"], mask, half)
                if g is not None:
                    out["ratio"] = out["ratio"] / g
                    out["drift_g"] = g
            if self.hot_valid(sig):
                tc, tsys = self.cold["tc"], self.hot["tsys"]
                out["tant"] = (tsys + tc) * out["ratio"] - tsys
                try:
                    out["tbase"] = baseline_fit(axis, out["tant"], mask, half)
                    out["tline"] = out["tant"] - out["tbase"]
                    if eta is not None:
                        out["tb_line"] = out["tline"] / eta
                except ValueError as e:
                    out["notes"].append(str(e))
        t_mid = 0.5 * (d["t_first"] + d["t_last"]) if d.get("t_first") is not None else None
        out["t_mid"] = t_mid
        out["sky"] = self.sky_at(t_mid, site)
        out["site"] = site
        out["vtopo"] = freq_to_vtopo(axis)
        out["vlsr"] = out["vtopo"] + out["sky"]["vcorr_kms"] if out["sky"] else None
        return out

    def smooth_bins(self):
        """Empty (or 1) = no smoothing."""
        try:
            return max(1, int(float(self.v["smooth"].get())))
        except ValueError:
            return 1

    def inter_count(self):
        """Intermediate average: new spectra needed before the hydrogen line view is redrawn."""
        try:
            return max(1, int(float(self.v["inter"].get())))
        except ValueError:
            return 1000

    def flag_cleaned(self, snap):
        """The snapshot as shown: flagged spectra removed (when 'Remove flagged' is on) and, with the reference
        load, the gain drift removed. Returns (snapshot, number of removed spectra)."""
        if snap["p"] is None:
            return snap, 0
        n_rm = 0
        self.sk_shown = 0.0
        if self.remove_flag:
            rec = self.averager.export_shown() if self.hold_last() else self.averager.export()
            r = remove_flagged(rec)
            if r is not None:
                snap, n_rm = dict(snap, p=r[0]), r[1]
                if rec is not None and rec.get("sk_on"):
                    self.sk_shown = sk_fraction(rec, trusted_mask(rec["axis"], rec["sig"]))
                    snap["sk_dead"] = sk_dead(rec)
        if self.gain_corrected(snap) and self.gain_g > 0 and self.gain_g != 1.0:
            snap = dict(snap, p=snap["p"] / self.gain_g)
        return snap, n_rm

    def rfi_max_kms(self):
        try:
            return min(max(float(self.v["rfi_kms"].get()), RFI_KMS_MIN), RFI_KMS_MAX)
        except Exception:
            return RFI_KMS_DEFAULT

    def on_sk_flag(self, quiet=False):
        """Spectral kurtosis flagging on/off. The averager uses it from the next average (a running average keeps
        the state it started with, so one average is never half flagged)."""
        try:
            on = bool(self.v["sk_flag"].get())
        except Exception:
            on = False
        self.averager.sk_on = on
        if not quiet:
            self.set_status("Spectral kurtosis flagging " + ("on" if on else "off") + " from the next average.",
                            OK_COL)

    def rfi_excluded(self):
        """The user's excluded frequency ranges (Settings -> RFI filter), [(lo, hi)] in MHz."""
        try:
            return parse_ranges_mhz(self.v["rfi_exclude"].get())
        except Exception:
            return []

    def update_rfi_exclude_text(self):
        lbl = getattr(self, "lbl_rfi_ex", None)
        if lbl is None:
            return
        rng = self.rfi_excluded()
        if not rng:
            lbl.config(text="none", fg=MUTED)
            return
        txt = ", ".join(f"{lo:.3f}-{hi:.3f}" for lo, hi in rng) + " MHz"
        half = 0.6
        try:
            site = self.site_or_default()
            if site and site.get("line_half"):
                half = float(site["line_half"])
        except Exception:
            pass
        near = any(lo <= H1_MHZ + half and hi >= H1_MHZ - half for lo, hi in rng)
        lbl.config(text=txt + ("  - inside the line window" if near else ""), fg=WARN_COL if near else TEXT)

    def on_rfi_exclude(self, *_):
        self.update_rfi_exclude_text()
        self.force_redraw()

    def rfi_mode(self):
        try:
            m = self.v["rfi_mode"].get()
        except Exception:
            m = RFI_MODE_DEFAULT
        return m if m in RFI_MODES else RFI_MODE_DEFAULT

    def rfi_width_for(self, chan_kms):
        """(widest RFI feature in channels, threshold in sigma) of the selected RFI filter mode."""
        kms, n_sig = RFI_MODES[self.rfi_mode()]
        if kms is None:
            return 1, n_sig
        if kms == "slider":
            kms = self.rfi_max_kms()
        return rfi_width_channels(kms, chan_kms), n_sig

    def rfi_setting(self, axis):
        chan_kms = abs(C_KMS * (axis[1] - axis[0]) / H1_HZ) if len(axis) > 1 else 1.0
        return self.rfi_width_for(chan_kms)

    def update_rfi_width_text(self):
        lbl = getattr(self, "lbl_rfi_w", None)
        if lbl is None:
            return
        mode = self.rfi_mode()
        try:                                          # slider only in Custom, warning only in Aggressive
            if mode == "Custom":
                self.rfi_custom_row.pack(fill="x", pady=(6, 0), before=lbl)
            else:
                self.rfi_custom_row.pack_forget()
            self.lbl_rfi_warn.config(text="Warning: Aggressive mode may suppress very narrow astrophysical features."
                                     if mode == "Aggressive" else "")
            self.lbl_rfi_kms.config(text=f"{self.rfi_max_kms():.1f} km/s")
        except Exception:
            pass
        if getattr(self, "cfg", None):
            chan = C_KMS * (self.cfg["fs"] / self.cfg["nfft"]) / H1_HZ
            n, n_sig = self.rfi_width_for(chan)
            lbl.config(text=f"FFT {self.cfg['nfft']}: up to {n} channel{'s' if n > 1 else ''} "
                            f"({n * chan:.1f} km/s), spikes above {n_sig:g} sigma")
        else:
            lbl.config(text="(channels are shown when the receiver runs)")

    def on_rfi_width(self, *_):
        self.update_rfi_width_text()
        self.force_redraw()

    def rm_flag_text(self):
        n = getattr(self, "rfi_count", 0) if self.remove_flag else 0
        return (("Restore flagged" if self.remove_flag else "Remove flagged") +
                f" ({100 * self.flag_frac:.1f} %" + (f" + {n} ch" if n else "") + ")")

    def on_remove_flagged(self):
        self.remove_flag = not self.remove_flag
        self.rfi_count = 0
        self.refresh_buttons()
        self.force_redraw()
        # Cold and Hot are treated like the measurements: Y and T_sys follow the new treatment
        self.recompute_hot("Remove flagged " + ("on" if self.remove_flag else "off"))
        if self.remove_flag:
            self.set_status("Flagged spectra and narrow RFI channels are removed from the shown and saved average "
                            "(raw data kept). Press Restore flagged to undo.", OK_COL)
        else:
            self.set_status("Flagged spectra and RFI channels restored: the average includes everything again.",
                            OK_COL)

    def update_h1(self, snap):
        key = (snap["rev"], self.view_rev, id(self.cold), id(self.hot), self.current_sig() == (self.cold or {}).get("sig"))
        if key == self.h1_key:
            return
        forced = self.h1_key is None                 # settings / tab changed: update now, full redraw
        now = time.time()
        if not forced and now - self.h1_last < H1_REDRAW_S:
            return                                   # new data, but at most one update per second
        if (not forced and not snap["done"] and snap["counts"] > self.h1_counts
                and snap["counts"] - self.h1_counts < self.inter_count()):
            return                                   # Intermediate average: wait for enough new spectra
        self.h1_last = now
        self.h1_key = key
        self.h1_counts = snap["counts"]
        snap, n_removed = self.flag_cleaned(snap)
        ax = self.ax_h1
        before = (ax.get_xlim(), ax.get_ylim(), ax.get_xlabel(), ax.get_ylabel(), tuple(self.ln_h1_f0.get_xdata()))
        if snap["p"] is None:
            self.ln_h1.set_data([], [])
            self.ln_h1_base.set_data([], [])
            self.txt_h1.set_text("No averaged data yet. Start the receiver, then 1. Cold.")
            self.canvas_h1.draw_idle()
            return
        pr = self.products(snap)
        want = self.v["display"].get()
        chains = {D_TANT: [D_TANT, D_TLINE, D_RATIO, D_RAW], D_TLINE: [D_TLINE, D_RATIO, D_RAW],
                  D_TB: [D_TB, D_TLINE, D_RATIO, D_RAW], D_RATIO: [D_RATIO, D_RAW], D_RAW: [D_RAW]}
        order = chains.get(want, chains[D_TANT])
        avail = {D_TANT: pr["tant"], D_TLINE: pr["tline"], D_RATIO: pr["ratio"], D_RAW: pr["p"], D_TB: pr["tb_line"]}
        shown = next(m for m in order if avail[m] is not None)
        y = avail[shown]
        if pr.get("rfi") is not None and shown != D_RAW:
            # removed RFI channels are drawn as a straight line between their neighbours (no gaps, and no loose
            # 1-2 channel pieces between two removed regions). Plot only - fits, integrals and files use the mask.
            from scipy.ndimage import binary_closing
            fill = pr["rfi"] | binary_closing(pr["rfi"], structure=np.ones(3, dtype=bool))
            ok = ~fill
            if ok.any():
                y = y.copy()
                y[fill] = np.interp(np.flatnonzero(fill), np.flatnonzero(ok), y[ok])
        y = smooth(y, self.smooth_bins())
        kelvin = shown in (D_TANT, D_TLINE, D_TB)
        db = (not kelvin) and self.v["yscale"].get() == "dB"
        yd = downsample(y, MAX_PLOT_POINTS)
        yd = to_db(yd) if db else yd
        xmode = self.v["xaxis"].get()
        if xmode not in X_AXES:
            xmode = X_AXES[0]
        if xmode == X_AXES[2] and pr["vlsr"] is None:
            xmode = X_AXES[1]
        xs = {X_AXES[0]: pr["axis"] / 1e6, X_AXES[1]: pr["vtopo"], X_AXES[2]: pr["vlsr"]}[xmode]
        xd = downsample(xs, MAX_PLOT_POINTS)
        self.ln_h1.set_data(xd, yd)
        if shown == D_TANT and pr["tbase"] is not None:
            self.ln_h1_base.set_data(xd, downsample(pr["tbase"], MAX_PLOT_POINTS))
        else:
            self.ln_h1_base.set_data([], [])
        zero = {X_AXES[0]: H1_MHZ, X_AXES[1]: 0.0, X_AXES[2]: 0.0}[xmode]
        self.ln_h1_f0.set_xdata([zero, zero])
        mvis = downsample(pr["mask"].astype(float), MAX_PLOT_POINTS) > 0.99
        xkey = (snap["sig"], xmode)
        if xkey != self.h1_xkey:
            x0, x1 = sorted((float(np.min(xd[mvis])), float(np.max(xd[mvis]))))
            if x0 <= zero <= x1:                      # the hydrogen line in the middle, also with an LO offset
                half = max(zero - x0, x1 - zero)
                ax.set_xlim(zero - half, zero + half)
            else:                                     # tuned elsewhere (no hydrogen line in the band): the band
                ax.set_xlim(x0, x1)
            self.h1_xkey = xkey
        ax.set_xlabel(X_LABELS[xmode], color="white")
        ylab = {D_TANT: "T_ant (K)", D_TLINE: "T_line (K)", D_RATIO: "Ratio to Cold", D_RAW: "Power",
                D_TB: f"T_B line (K), beam efficiency {pr['eta']:.2f}"}[shown]
        if not kelvin:
            ylab += " (dB)" if db else (" (linear)" if shown == D_RAW else "")
        ax.set_ylabel(ylab, color="white")
        def auto_range():
            x0, x1 = sorted(ax.get_xlim())
            sel = (xd >= x0) & (xd <= x1) & np.isfinite(yd) & mvis
            vals = yd[sel] if sel.any() else yd[np.isfinite(yd)]
            if not vals.size:
                return None
            lo, hi = np.percentile(vals, 0.5), np.percentile(vals, 99.5)
            # room above and below in proportion to the curve itself (raw power is ~1e-5, kelvins ~100)
            pad = max((hi - lo) * 0.12, 1e-3 * max(abs(lo), abs(hi)), 1e-30)
            return lo - pad, hi + pad
        if self.v["autoy"].get():
            # Autoscale: the whole line and baseline are kept in view automatically (Zoom / Level are not used)
            r = auto_range()
            if r is not None:
                t_lo, t_hi = r
                c_lo, c_hi = ax.get_ylim()
                # change the Y range only when needed, so most updates can be light (blit)
                if forced or t_lo < c_lo or t_hi > c_hi or (t_hi - t_lo) < 0.6 * (c_hi - c_lo):
                    slack = 0.08 * (t_hi - t_lo)             # a little extra room so small wiggles do not rescale
                    ax.set_ylim(t_lo - slack, t_hi + slack)
            self.h1_base, self.h1_base_key = tuple(ax.get_ylim()), ylab
        else:
            # Manual: the range autoscale left is kept; Zoom and Level adjust it by hand. If the line has gone
            # completely out of the view (e.g. the mount was turned), the range is fitted once again.
            c_lo, c_hi = sorted(ax.get_ylim())
            r_now = auto_range()
            out = r_now is not None and (r_now[1] < c_lo or r_now[0] > c_hi)
            # only when NEW DATA moved the line away (not when the user moved the view with the sliders)
            lost = out and not forced and getattr(self, "h1_line_seen", True)
            if lost:
                s = 0.08 * (r_now[1] - r_now[0])
                self.h1_base, self.h1_base_key = (r_now[0] - s, r_now[1] + s), ylab
                self.v["y_zoom"].set(1.0); self.v["y_zoom_log"].set(0.0); self.v["y_level"].set(0.0)
                try:
                    self.lbl_zoom.config(text="1.0×"); self.lbl_level.config(text="+0.00")
                except Exception:
                    pass
        if not self.v["autoy"].get() and (forced or lost):
            if getattr(self, "h1_base", None) is None or getattr(self, "h1_base_key", None) != ylab:
                r = auto_range()                     # other units (dB / linear, view): start from a fitted range
                if r is not None:
                    s = 0.08 * (r[1] - r[0])
                    self.h1_base, self.h1_base_key = (r[0] - s, r[1] + s), ylab
            if getattr(self, "h1_base", None) is not None:
                try:
                    zoom = min(max(0.1, float(self.v["y_zoom"].get())), 50.0)
                    level = float(self.v["y_level"].get())
                except (ValueError, tk.TclError):
                    zoom, level = 1.0, 0.0
                b_lo, b_hi = self.h1_base
                center, span = 0.5 * (b_lo + b_hi) + level * (b_hi - b_lo), (b_hi - b_lo) / zoom
                ax.set_ylim(center - span / 2, center + span / 2)
        if not self.v["autoy"].get():
            c_lo, c_hi = sorted(ax.get_ylim())
            r_now = auto_range()
            self.h1_line_seen = r_now is None or not (r_now[1] < c_lo or r_now[0] > c_hi)
        t_int = snap["counts"] * self.frame_time(snap["sig"])
        info = [f"{shown}   {snap['counts']:,} spectra, {t_int:.1f} s"
                + ("  (complete)" if snap["done"] else "")]
        if snap.get("showing_last"):
            info.append(f"Last completed average · next {100 * snap['running_counts'] / max(snap['target'], 1):.0f} %")
        n_rfi = int(pr["rfi"].sum()) if pr.get("rfi") is not None else 0
        if self.rfi_count != n_rfi:
            self.rfi_count = n_rfi
            self.btn_rm_flag.config(text=self.rm_flag_text())
        if self.remove_flag:
            sk = getattr(self, "sk_shown", 0.0)
            info.append(f"Removed: {n_removed:,} flagged spectra, {n_rfi} RFI channels"
                        + (f", SK {100 * sk:.2f} %" if self.v["sk_flag"].get() else "") + " (raw data kept)")
        if shown != want:
            info.append(f"('{want}' needs " + ("Hot" if self.cold_valid(snap["sig"]) else "Cold") +
                        f" - showing '{shown}')")
        self.update_obs_readouts(pr, snap)
        if pr["tline"] is not None:
            d = (np.abs(pr["axis"] / 1e6 - H1_MHZ) <= pr["half"]) & pr["mask"]
            pk = float(np.max(smooth(pr['tline'], 31)[d]))
            tb_txt = f", T_B {pk / pr['eta']:.2f} K" if pr["eta"] else ""
            info.append(f"Peak T_line {pk:.2f} K{tb_txt}, integral "
                        f"{line_integral(pr['axis'], pr['tline'], pr['mask'], pr.get('rfi'), pr['half']) * C_KMS / H1_MHZ:.0f}"
                        f" K km/s (±{pr['half']:.2f} MHz)")
        if pr["sky"]:
            s = pr["sky"]
            info.append(f"l {s['l_deg']:.1f}°, b {s['b_deg']:+.1f}°, V_LSR correction {s['vcorr_kms']:+.2f} km/s")
        info += pr["notes"]
        self.txt_h1.set_text("\n".join(info))
        after = (ax.get_xlim(), ax.get_ylim(), ax.get_xlabel(), ax.get_ylabel(), tuple(self.ln_h1_f0.get_xdata()))
        if forced or after != before:
            self.canvas_h1.draw_idle()               # axes changed: full redraw (moving parts follow in draw_event)
        else:
            self._blit_h1()

    def update_obs_readouts(self, pr, snap):
        """Peak T_L, line strength, peak V_LSR, noise, baseline level, spectra saved (readout row under the view)."""
        ro = self.ro
        ro["saved"].config(text=f"{self.saved_count}")
        m = pr["mask"]
        lvl = float(np.median(pr["p"][m])) if m.any() else float("nan")
        ro["base"].config(text=f"{10 * math.log10(lvl):.1f} dB" if lvl > 0 else "-")
        if pr["tline"] is None:
            for k in ("peak", "strength", "vpeak", "noise"):
                ro[k].config(text="-")
            return
        f = pr["axis"] / 1e6
        d = (np.abs(f - H1_MHZ) <= pr["half"]) & m
        if not d.any():
            return
        tl = pr["tline"]
        sm = smooth(tl, 31)
        k = int(np.flatnonzero(d)[np.argmax(sm[d])])
        ro["peak"].config(text=f"{sm[k]:.2f} K")
        ro["strength"].config(text=f"{line_integral(pr['axis'], tl, m, pr.get('rfi'), pr['half']):.2f}")
        ro["vpeak"].config(text=f"{pr['vlsr'][k]:+.1f} km/s" if pr["vlsr"] is not None else "-")
        out = m & ~d
        diff = np.diff(tl[out]) if out.sum() > 20 else None
        if diff is not None and diff.size:
            ro["noise"].config(text=f"{1.4826 * float(np.median(np.abs(diff - np.median(diff)))) / math.sqrt(2):.3f} K")

    def noise_stats(self, sig):
        key = (sig[SIG_KEYS.index("window")], sig[SIG_KEYS.index("nfft")], sig[SIG_KEYS.index("overlap")])
        if key not in self.model_cache:
            nfft = key[1]
            self.model_cache[key] = noise_model(make_window(key[0], nfft), nfft // 2 if key[2] else nfft)
        return self.model_cache[key]

    def radiometer(self, d):
        """(measured sigma, expected sigma, divided by Cold) relative, or None. Uses the ratio to Cold when it is
        valid. Before that (Cold itself, or no Cold yet) the raw spectrum still contains the receiver's bandpass
        (slope and fine structure), which a single spectrum cannot tell from noise: the check is then only a guide."""
        if d["p"] is None or d["counts"] < 200 or d["mode"].startswith("Exponential"):
            return None
        sig = d["sig"]
        mask = trusted_mask(d["axis"], sig)
        v1, corr = self.noise_stats(sig)
        var = v1 / d["counts"]
        y = d["p"]
        divided = False
        if d["tag"] in ("hot", "measure") and self.cold_valid(sig):
            y = d["p"] / np.maximum(self.cold_ref(), 1e-30)
            divided = True
            if not self.cold_smooth_on():                # a smoothed Cold adds (almost) no noise of its own
                var += v1 / self.cold["counts"]
        meas = measure_noise(y, mask, corr)
        if meas is None:
            return None
        return meas, math.sqrt(var), divided

    def update_quality(self, snap, draw):
        key = (snap["tag"], snap["t_first"], snap["sig"])
        if key != self.rad_key:
            self.rad_key = key
            self.rad_hist = []
            self.rad_now = None
        now = time.time()
        if now - self.rad_last >= 1.0 and snap["p"] is not None:
            self.rad_last = now
            r = self.radiometer(snap)
            self.rad_now = r
            if r is not None and (not self.rad_hist or snap["counts"] > self.rad_hist[-1][0] * 1.05):
                self.rad_hist.append((snap["counts"], r[0], r[1]))
                if draw:
                    self.draw_radiometer()
            if r is None:
                self.lbl_rad.config(text="Radiometer check: needs at least 200 spectra", fg=FG)
            elif not r[2]:
                self.lbl_rad.config(text=f"Noise (raw, before Cold): measured {100 * r[0]:.3f} %, expected "
                                         f"{100 * r[1]:.3f} %. Includes the bandpass shape - checked after Cold.",
                                    fg=MUTED)
            else:
                ratio = r[0] / r[1]
                ok = "OK" if ratio < 1.3 else ("excess noise" if ratio < 1.6 else "much excess noise")
                if ratio >= 1.3 and self.cold_smooth_on():
                    ok += " - compare with Smooth Cold off"
                col = OK_COL if ratio < 1.3 else (WARN_COL if ratio < 1.6 else ERR_COL)
                self.lbl_rad.config(text=f"Noise sigma: measured {100 * r[0]:.3f} %, expected {100 * r[1]:.3f} % "
                                         f"(× {ratio:.2f}) {ok}", fg=col)
            nfl = snap["n_flagged"]
            frac = nfl / max(snap["counts"], 1)
            self.flag_frac = frac
            self.ro["flagged"].config(text=f"{100 * frac:.2f} %" + (" (high)" if frac > 0.2 else ""),
                                      fg=WARN_COL if frac > 0.2 else FG)
            self.btn_rm_flag.config(text=self.rm_flag_text())
            self.update_side_captions()
        elif draw and self.h1_key is None:
            self.draw_radiometer(force=True)

    def draw_radiometer(self, force=False):
        """Noise curves, about once per second. Axis limits only change when the curves leave the view."""
        ax = self.ax_rad
        before = (ax.get_xlim(), ax.get_ylim())
        if self.rad_hist:
            h = np.array(self.rad_hist)
            self.ln_rad_m.set_data(h[:, 0], 100 * h[:, 1])
            self.ln_rad_e.set_data(h[:, 0], 100 * h[:, 2])
            lo = min(h[:, 1].min(), h[:, 2].min()) * 100
            hi = max(h[:, 1].max(), h[:, 2].max()) * 100
            x0, x1 = ax.get_xlim()
            y0, y1 = ax.get_ylim()
            if force or len(h) == 1 or h[-1, 0] * 1.05 > x1 or h[0, 0] < x0:
                ax.set_xlim(h[0, 0] * 0.8, max(h[-1, 0] * 2.0, h[0, 0] * 2))      # room to grow
            if force or len(h) == 1 or lo < y0 or hi > y1 or (y1 / y0) > 4.0 * (hi / max(lo, 1e-9)):
                ax.set_ylim(lo * 0.7, hi * 1.4)
        else:
            self.ln_rad_m.set_data([], [])
            self.ln_rad_e.set_data([], [])
        self.canvas_rad.draw_idle()

    def snapshot_info(self, snap):
        """Lines for the information box under the snapshot images."""
        sig = dict(zip(SIG_KEYS, snap["sig"]))
        pr = self.products(snap)
        t_mid = pr["t_mid"]
        t_int = snap["counts"] * self.frame_time(snap["sig"])
        lines = [f"{APP} v{VERSION}    {utc_str(t_mid)[:19].replace('T', ' ')} UTC (middle of the average)    "
                 f"{snap['counts']:,} spectra = {t_int:.1f} s",
                 f"Sample rate {sig['fs'] / 1e6:g} MS/s   Gain {sig['gain']:g} dB   PPM {sig['ppm']:g}   "
                 f"FFT {sig['nfft']} {sig['window']}{' 50 % overlap' if sig['overlap'] else ''}   "
                 f"Channel {sig['fs'] / sig['nfft']:,.0f} Hz"]
        cal = []
        if self.cold_valid(snap["sig"]):
            cal.append(f"Cold {self.cold['tc']:.1f} K")
            if self.hot_valid(snap["sig"]):
                cal.append(f"Hot {self.hot['th']:.1f} K   Y = {self.hot['y']:.3f}   Tsys ~ {self.hot['tsys']:.0f} K")
        if cal:
            lines.append("Calibration: " + "   ".join(cal))
        site = pr["site"]
        if site:
            pos = (f"Site {abs(site['lat']):.3f}° {'N' if site['lat'] >= 0 else 'S'}, "
                   f"{abs(site['lon']):.3f}° {'E' if site['lon'] >= 0 else 'W'}, {site['height']:.0f} m   "
                   + (f"Antenna Az {pr['sky']['az_deg']:.1f}°, Alt {pr['sky']['alt_deg']:.1f}° (mount)"
                      if self.guided() and pr["sky"] else f"Antenna Az {site['az']:g}°, Alt {site['alt']:g}°"))
            if pr["sky"]:
                s_ = pr["sky"]
                pos += (f"   l {s_['l_deg']:.1f}°, b {s_['b_deg']:+.1f}°   RA {s_['ra_deg'] / 15:.2f} h, "
                        f"Dec {s_['dec_deg']:+.1f}°   V_LSR corr. {s_['vcorr_kms']:+.2f} km/s")
            lines.append(pos)
            if site["observer"]:
                lines.append(f"Observer: {site['observer']}")
        return lines

    def on_snapshot(self):
        """Save image under the centre view: saves the view that is in the centre now (to images/saved)."""
        centre = self.view_pos[0]
        try:
            folder = self.images_saved_dir()
            os.makedirs(folder, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
            base = os.path.join(folder, f"{self.save_name()}_{stamp}")
            if centre == "polar":
                if self.maps.n_points == 0:
                    messagebox.showwarning("Save image", "No map points yet.")
                    return
                path = base + "_polar.png"
                self.save_polar_png(path)
            else:
                snap = self.view_snapshot()
                if snap["p"] is None:
                    messagebox.showwarning("Save image", "No averaged data yet.")
                    return
                info = self.snapshot_info(snap)
                if centre == "h1":
                    self.force_redraw()
                    self.update_h1(snap)                # make sure the plot shows the current average
                    path = self.snapshot_line(base + "_line.png", info)
                else:
                    if not self.rad_hist:
                        messagebox.showwarning("Save image", "No noise curve yet - it needs about 200 spectra.")
                        return
                    path = self.snapshot_noise(base + "_radiometer.png", info)
            self.note_saved_image(path)
            msg = f"Image saved: {path} (also goes into the science package)"
            self.lbl_saved.config(text=msg, fg=OK_COL)
            self.set_status(msg, OK_COL)
        except Exception as e:
            messagebox.showerror("Save image", f"Saving the image failed:\n{e}")

    def save_polar_image(self):
        """Save image of the polar map (also when it is a small view)."""
        if self.maps.n_points == 0:
            messagebox.showwarning("Save image", "No map points yet.")
            return
        try:
            folder = self.images_saved_dir()
            os.makedirs(folder, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
            path = self.save_polar_png(os.path.join(folder, f"{self.save_name()}_{stamp}_polar.png"))
            self.note_saved_image(path)
            self.set_status(f"Image saved: {path}", OK_COL)
        except Exception as e:
            messagebox.showerror("Save image", f"Saving the image failed:\n{e}")

    def save_polar_png(self, path):
        fig = Figure(figsize=(8, 8.4), facecolor=BG)
        FigureCanvasAgg(fig)
        self.maps.draw_polar(fig, self.polar_grid())       # no pointing marker in saved images
        fig.savefig(path, dpi=200, facecolor=BG)
        self.polar_n_drawn = -2                             # draw_polar replaced polar_ax: redraw the live map
        return path

    def _snapshot_figure(self, info, height):
        fig = Figure(figsize=(11, height), facecolor=BG)
        FigureCanvasAgg(fig)
        bottom = (0.17 * len(info) + 0.2 + 0.6) / height      # info box + x-axis label, in inches -> fraction
        top = 1.0 - 0.45 / height                              # room for the title
        ax = fig.add_axes([0.08, bottom, 0.89, top - bottom])
        self.style_ax(ax)
        fig.text(0.01, 0.01, "\n".join(info), color="#cccccc", family="monospace", fontsize=8, va="bottom")
        return fig, ax

    def snapshot_line(self, path, info):
        src = self.ax_h1
        fig, ax = self._snapshot_figure(info, 6.5)
        x, y = self.ln_h1.get_data()
        ax.plot(x, y, color="#ffcc00", lw=1.0)
        bx, by = self.ln_h1_base.get_data()
        if len(bx):
            ax.plot(bx, by, color="#888888", lw=0.8, ls="--")
        x0 = self.ln_h1_f0.get_xdata()[0]
        ax.axvline(x0, color="#ff5555", ls=":", lw=0.9)
        ax.set_xlim(src.get_xlim())
        ax.set_ylim(src.get_ylim())
        ax.set_xlabel(src.get_xlabel(), color="white")
        ax.set_ylabel(src.get_ylabel(), color="white")
        ax.set_title("Hydrogen line 1420.405752 MHz", color="white", fontsize=11)
        ax.text(0.01, 0.97, self.txt_h1.get_text(), transform=ax.transAxes, va="top", color="white",
                family="monospace", fontsize=9)
        fig.savefig(path, dpi=150, facecolor=BG)
        return path

    def snapshot_noise(self, path, info):
        fig, ax = self._snapshot_figure(info, 5.0)
        h = np.array(self.rad_hist)
        ax.plot(h[:, 0], 100 * h[:, 1], "o-", color="#00e5ff", ms=3, lw=1.0, label="measured")
        ax.plot(h[:, 0], 100 * h[:, 2], "--", color="#ffaa00", lw=1.0, label="expected 1/√N")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlim(self.ax_rad.get_xlim())
        ax.set_ylim(self.ax_rad.get_ylim())
        ax.yaxis.set_major_locator(LogLocator(base=10, subs=(1.0, 2.0, 5.0)))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda val, pos: f"{val:g}"))
        ax.yaxis.set_minor_formatter(NullFormatter())
        ax.xaxis.set_minor_formatter(NullFormatter())
        ax.set_xlabel("Average counts (spectra)", color="white")
        ax.set_ylabel("Noise σ (%)", color="white")
        ax.set_title("Radiometer check: measured noise vs. theory", color="white", fontsize=11)
        ax.legend(loc="upper right", fontsize=8, facecolor=BG, labelcolor="white", edgecolor="#666666")
        r = self.rad_hist[-1]
        ax.text(0.01, 0.04, f"last: measured {100 * r[1]:.3f} %, expected {100 * r[2]:.3f} % (× {r[1] / r[2]:.2f})",
                transform=ax.transAxes, color="white", family="monospace", fontsize=9)
        fig.savefig(path, dpi=150, facecolor=BG)
        return path

