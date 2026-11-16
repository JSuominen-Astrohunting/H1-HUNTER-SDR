"""H1-HUNTER SDR - Maps page: live line-strength maps, velocity maps, l-v, top view, rotation curve, mass."""
import os
import sys
import time
import numpy as np
import tkinter as tk
from tkinter import ttk, messagebox
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.backends.backend_agg import FigureCanvasAgg
from .common import *
from .widgets import *
from .theme import *
from .maps import NX, NY, gal_to_eq_index, draw_pointing
from . import package as pkg

MAP_KINDS = [  # (key, button text, group)
    ("eq", "RA / Dec", 0), ("gal", "Galactic (Aitoff)", 0), ("polar", "Polar map", 0),
    ("channel", "Channel map", 1), ("m0", "Moment 0 (intensity)", 1), ("m1", "Moment 1 (velocity)", 1),
    ("lv", "l-v diagram", 1), ("spec", "Spectra by l", 1), ("top", "Milky Way top view", 1), ("rot", "Rotation curve", 1),
    ("mass", "Enclosed mass", 1)]
MAP_TITLES = {k: t for k, t, _ in MAP_KINDS}
COLORMAPS = ["inferno", "viridis", "magma", "plasma", "cividis", "jet", "gray"]
MAP_REFRESH_S = 10.0


class GuiMapsMixin:
    def build_maps_page(self, page):
        page.columnconfigure(1, weight=1)
        page.rowconfigure(0, weight=1)
        left = ScrollPanel(page, 280)
        left.grid(row=0, column=0, sticky="ns", padx=(6, 0), pady=6)
        c = card(left.inner, None, pad=8)
        self.var("map_kind", "gal")
        self.map_picks = {}
        groups = (("Line strength (live)", 0), ("Velocity maps (LSR)", 1))
        for title, grp in groups:
            tk.Label(c, text=title.upper(), bg=CARD, fg=MUTED, font=F_SEC, anchor="w").pack(
                fill="x", padx=4, pady=(8 if grp else 4, 4))
            for key, text, gr in MAP_KINDS:
                if gr != grp:
                    continue
                b = tk.Label(c, text=text, bg=CARD, fg="#d4d7db", font=F_UI, anchor="w", padx=10, pady=8,
                             cursor="hand2")
                b.pack(fill="x", pady=1)
                b.bind("<Button-1>", lambda e, k=key: self.select_map(k))
                b.bind("<Enter>", lambda e, w=b, k=key: w.config(bg="#222428") if self.v["map_kind"].get() != k else None)
                b.bind("<Leave>", lambda e, w=b, k=key: w.config(bg=CARD) if self.v["map_kind"].get() != k else None)
                self.map_picks[key] = b
        G = card_grid(left.inner, "Limits")
        self.field(G, 0, 0, "V1 km/s", "v1", "-150", cb=self.on_map_limits)
        self.field(G, 0, 1, "V2 km/s", "v2", "150", cb=self.on_map_limits)
        self.field(G, 1, 0, "b window °", "bwin", "5", cb=self.on_map_limits)
        self.field(G, 1, 1, "Colormap", "cmap", "inferno", kind="combo", values=COLORMAPS, cb=self.on_map_limits)
        self.field(G, 2, 0, "Sky (velocity maps)", "sky", "Galactic", kind="combo", values=["Galactic", "Equatorial"],
                   cb=self.on_map_limits, span=2)
        self.note(G.card, "V1/V2: velocity range of the channel and moment maps. b window: latitude range of the "
                          "l-v diagram, top view and rotation curve.", wrap=240)

        mid = tk.Frame(page, bg=PAGE)
        mid.grid(row=0, column=1, sticky="nsew", padx=6, pady=6)
        mid.columnconfigure(0, weight=1)
        mid.rowconfigure(0, weight=1)
        box = tk.Frame(mid, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        box.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        centre = tk.Frame(box, bg=CARD)
        centre.pack(fill="both", expand=True, padx=10, pady=10)
        centre.columnconfigure(0, weight=1)
        centre.rowconfigure(1, weight=1)
        hdr = tk.Frame(centre, bg=CARD)
        hdr.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.lbl_map_title = heading(hdr, "")
        self.map_text_chk = ttk.Checkbutton(hdr, text="Text and coordinates", variable=self.var("map_text", True, "bool"),
                                            command=self.on_map_limits)
        self.map_text_chk.pack(side="right", padx=(8, 0))
        plot = tk.Frame(centre, bg=BG, width=200, height=200)
        plot.grid(row=1, column=0, sticky="nsew")
        plot.pack_propagate(False)
        self.fig_map = Figure(figsize=(9, 5.5), facecolor=BG)
        self.canvas_map = FigureCanvasTkAgg(self.fig_map, master=plot)
        tb = NavigationToolbar2Tk(self.canvas_map, plot, pack_toolbar=False)
        tb.update()
        tb.pack(side="bottom", fill="x")
        self.canvas_map.get_tk_widget().pack(fill="both", expand=True)
        self.canvas_map.mpl_connect("button_press_event", self.on_map_click)
        bot = tk.Frame(centre, bg=CARD)
        bot.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.lbl_map_note = tk.Label(bot, text="Click the map: the spectrum at that point opens on the right.",
                                     bg=CARD, fg=MUTED, font=F_SMALL, anchor="w")
        self.lbl_map_note.pack(side="left", fill="x", expand=True)
        ttk.Button(bot, text="Save table (CSV)", style="Small.TButton",
                   command=self.save_map_table).pack(side="right", padx=(6, 0))
        ttk.Button(bot, text="Save image", image=self.icon("save"), compound="left", style="Small.TButton",
                   command=self.save_map_image).pack(side="right")

        right = tk.Frame(page, bg=PAGE, width=390)
        right.grid(row=0, column=2, sticky="ns", pady=6, padx=(0, 6))
        right.grid_propagate(False)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=3)
        right.rowconfigure(1, weight=2)
        box = tk.Frame(right, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        box.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        tk.Label(box, text="SPECTRUM AT SELECTED POINT", bg=CARD, fg=MUTED, font=F_SEC,
                 anchor="w").pack(fill="x", padx=10, pady=(10, 6))
        info = tk.Frame(box, bg=CARD)
        info.pack(side="bottom", fill="x", padx=10, pady=(6, 10))
        self.fig_prof = Figure(figsize=(4, 3), facecolor=BG)
        self.canvas_prof = FigureCanvasTkAgg(self.fig_prof, master=box)
        self.canvas_prof.get_tk_widget().pack(fill="both", expand=True, padx=10)
        self.prof_lbl = {}
        for i, (k, t) in enumerate((("pos", "Position"), ("n", "Spectra in cell"), ("peak", "Peak"))):
            tk.Label(info, text=t, bg=CARD, fg=MUTED, font=F_SMALL).grid(row=i, column=0, sticky="w", pady=1)
            self.prof_lbl[k] = tk.Label(info, text="-", bg=CARD, fg=TEXT, font=F_MONO_S)
            self.prof_lbl[k].grid(row=i, column=1, sticky="w", padx=10, pady=1)
        tk.Label(info, text="Right-click the spectrum: save image + CSV", bg=CARD, fg=MUTED,
                 font=F_SMALL).grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.prof_last = None
        self.prof_menu = tk.Menu(self.root, tearoff=0)
        self.prof_menu.add_command(label="Save spectrum (image + CSV)", command=self.save_point_spectrum)
        w_prof = self.canvas_prof.get_tk_widget()
        for ev in ("<Button-3>", "<Button-2>" if sys.platform == "darwin" else "<Control-Button-1>"):
            w_prof.bind(ev, self.on_prof_menu)
        box2 = tk.Frame(right, bg=CARD, highlightthickness=1, highlightbackground=BORDER, cursor="hand2")
        box2.grid(row=1, column=0, sticky="nsew", padx=6, pady=6)
        t2 = tk.Label(box2, text="THUMBNAIL: L-V DIAGRAM", bg=CARD, fg=MUTED, font=F_SEC, anchor="w")
        t2.pack(fill="x", padx=10, pady=(10, 6))
        self.fig_lvt = Figure(figsize=(4, 2.4), facecolor=BG)
        self.canvas_lvt = FigureCanvasTkAgg(self.fig_lvt, master=box2)
        self.canvas_lvt.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.canvas_lvt.mpl_connect("button_press_event", lambda e: self.select_map("lv"))
        t2.bind("<Button-1>", lambda e: self.select_map("lv"))
        box2.bind("<Enter>", lambda e: box2.config(highlightbackground=ACCENT))
        box2.bind("<Leave>", lambda e: box2.config(highlightbackground=BORDER))
        self.map_drawn = None
        self.map_t = 0.0
        self.map_state = {}
        self.lvt_n = -1
        self.refresh_map_picks()
        self.draw_profile(None)

    def refresh_map_picks(self):
        cur = self.v["map_kind"].get()
        for k, w in self.map_picks.items():
            on = k == cur
            w.config(bg=PICK_ON_BG if on else CARD, fg=PICK_ON_FG if on else "#d4d7db", font=F_UI_B if on else F_UI)

    def select_map(self, kind):
        self.v["map_kind"].set(kind)
        self.on_map_choice()

    def on_map_choice(self):
        self.refresh_map_picks()
        self.maps_dirty = True
        self.map_drawn = None

    def on_map_limits(self):
        self.maps.cmap = self.v["cmap"].get() or "inferno"
        self.polar_n_drawn = -2
        self.maps_dirty = True
        self.map_drawn = None
        self.lvt_n = -1

    def update_maps_page(self, force=False):
        """Called from the update loop while the Maps page is open."""
        ms = self.maps
        kind = self.v["map_kind"].get()
        now = time.time()
        key = (kind, ms.n_points)
        if not force and key == self.map_drawn and not self.maps_dirty:
            return
        if not force and self.map_drawn is not None and self.map_drawn[0] == kind and now - self.map_t < MAP_REFRESH_S \
                and not self.maps_dirty:
            return
        self.map_t = now
        self.maps_dirty = False
        self.map_drawn = key
        self.draw_map(self.fig_map, kind)
        self.canvas_map.draw_idle()
        if self.lvt_n != ms.n_points:
            self.lvt_n = ms.n_points
            self.fig_lvt.clf()
            try:
                if ms.cube is not None and ms.n_points:
                    ms.render_velocity(self.fig_lvt, "lv", "Galactic")
                    for t in list(self.fig_lvt.texts):
                        t.remove()                                  # no info box in the thumbnail
                    for ax in self.fig_lvt.axes:
                        ax.set_title("")
                        ax.tick_params(labelsize=6)
                        ax.xaxis.label.set_fontsize(7)
                        ax.yaxis.label.set_fontsize(7)
                else:
                    self.fig_lvt.text(0.5, 0.5, "No spectra yet", color="#888888", ha="center", va="center")
            except Exception:
                pass
            self.canvas_lvt.draw_idle()

    def map_text(self):
        try:
            return bool(self.v["map_text"].get())
        except Exception:
            return True

    def draw_map(self, fig, kind, marker=True):
        """Draws one map into fig; remembers what is needed for clicks (axes and sky system)."""
        ms = self.maps
        txt = self.map_text()
        ms.cmap = self.v["cmap"].get() or "inferno"
        sky = self.v["sky"].get()
        fig.clf()
        fig.set_facecolor(BG)
        self.lbl_map_title.config(text=f"{MAP_TITLES[kind]} · {ms.n_points} points".upper())
        drawn, system = True, None
        try:
            if ms.n_points == 0:
                fig.text(0.5, 0.5, "No map points yet.\nPoints are added by every finished average of 3. Measure\n"
                                   "(or open data on the Data & Export page).", color="white", ha="center",
                         va="center", fontsize=12)
                drawn = False
            elif kind == "eq":
                ms.render_eq(fig, txt)
                system = "Equatorial"
            elif kind == "gal":
                ms.render_gal(fig, txt)
                system = "Galactic"
            elif kind == "polar":
                ms.draw_polar(fig, txt, text=txt)          # Maps page: 'Text and coordinates' controls everything
                system = "Polar"
                if marker and txt:                               # hidden together with the text and coordinates
                    pt = self.pointing_radec()
                    if pt is not None and ms.polar_ax is not None:
                        draw_pointing(ms.polar_ax, pt[0], pt[1], ms.beam_deg(), ms.polar_rmax)
                self.polar_n_drawn = -2                  # the live polar map on Observe must be redrawn
            else:
                drawn = ms.render_velocity(fig, kind, sky, txt)
                system = sky if kind in ("channel", "m0", "m1") else None
        except Exception as e:
            fig.clf()
            fig.text(0.5, 0.5, f"The map could not be drawn:\n{type(e).__name__}: {e}", color=ERR_COL,
                     ha="center", va="center", fontsize=10)
            drawn = False
        ax = fig.axes[0] if fig.axes else None
        self.map_state = {"kind": kind, "system": system if drawn else None, "ax": ax}
        clickable = drawn and system is not None
        self.lbl_map_note.config(text="Click the map: the spectrum at that point opens on the right." if clickable
                                 else "")
        return drawn

    def on_map_click(self, event):
        st = self.map_state
        if not st or st.get("system") is None or event.inaxes is None or event.inaxes is not st["ax"]:
            return
        if event.xdata is None or event.ydata is None:
            return
        try:
            if st["system"] == "Equatorial":
                ra_i = int(event.xdata * 15.0) % NX
                dec_i = int(np.floor(event.ydata + 90.0))
            elif st["system"] == "Polar":
                ra_i = int(np.degrees(event.xdata) % 360.0) % NX
                dec_i = int(np.floor(90.0 - event.ydata + 90.0))
            else:
                l_c = -float(np.degrees(event.xdata))
                b_deg = float(np.degrees(event.ydata))
                l_i = int(np.floor(l_c + 180.0)) % NX
                b_i = int(np.floor(b_deg + 90.0))
                if not 0 <= b_i < NY:
                    return
                gdec, gra = gal_to_eq_index()
                dec_i, ra_i = int(gdec[b_i, l_i]), int(gra[b_i, l_i])
            if 0 <= dec_i < NY:
                self.draw_profile(self.maps.profile(ra_i, dec_i))
        except Exception as e:
            self.set_status(f"Click: {e}", ERR_COL)

    def draw_profile(self, prof):
        fig = self.fig_prof
        fig.clf()
        fig.set_facecolor(BG)
        ax = fig.add_subplot(111)
        self.style_ax(ax)
        ax.tick_params(labelsize=7)
        self.prof_last = prof
        if prof is None:
            ax.text(0.5, 0.5, "Click a measured point\non the map", color="#888888", ha="center", va="center",
                    transform=ax.transAxes)
            for k in self.prof_lbl:
                self.prof_lbl[k].config(text="-")
        else:
            ax.plot(prof["v"], prof["t"], color="#ffcc00", lw=1.2)
            ax.axhline(0, color="white", alpha=0.3, lw=0.8)
            ax.set_xlabel("V_LSR (km/s)", color="white", fontsize=8)
            ax.set_ylabel("T line (K)", color="white", fontsize=8)
            self.prof_lbl["pos"].config(text=f"l {prof['l_deg']:.1f}° b {prof['b_deg']:+.1f}°  "
                                             f"(RA {prof['ra_deg'] / 15:.2f} h, Dec {prof['dec_deg']:+.1f}°)")
            self.prof_lbl["n"].config(text=f"{prof['n_spectra']}")
            self.prof_lbl["peak"].config(text=f"{prof['peak_K']:.2f} K @ {prof['peak_v']:+.0f} km/s")
        fig.subplots_adjust(left=0.17, right=0.97, top=0.95, bottom=0.18)
        self.canvas_prof.draw_idle()

    def on_prof_menu(self, event):
        self.prof_menu.entryconfig(0, state="normal" if self.prof_last is not None else "disabled")
        try:
            self.prof_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.prof_menu.grab_release()

    @staticmethod
    def calibration_lines(prof):
        """Calibrations of the spectra in the cell: one line per distinct calibration, with the number of spectra."""
        groups = {}
        for c in prof.get("cals", []):
            if not c:
                key = "no calibration record (spectra opened from files without one)"
            elif c.get("y_factor") is None:
                key = (f"Cold {c.get('cold_time_utc')} (T_cold {c.get('cold_T_ref_K'):.2f} K), no Hot: values are "
                       "ratios to Cold, not Kelvin")
            else:
                key = (f"Cold {c.get('cold_time_utc')} (T_cold {c.get('cold_T_ref_K'):.2f} K), Hot "
                       f"{c.get('hot_time_utc')} (T_hot {c.get('hot_T_ref_K'):.2f} K), Y {c.get('y_factor'):.4f}, "
                       f"Tsys {c.get('tsys_K'):.1f} K")
            groups[key] = groups.get(key, 0) + 1
        return [f"{n} spectr{'um' if n == 1 else 'a'}: {k}" for k, n in groups.items()]

    def save_point_spectrum(self):
        """Image + CSV of the spectrum at the selected point; both go into the science package."""
        prof = self.prof_last
        if prof is None:
            return
        try:
            stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
            name = (f"{self.save_name()}_spectrum_l{prof['l_deg']:05.1f}_b{prof['b_deg']:+05.1f}_{stamp}")
            r = int(prof.get("radius_cells", 0))
            region = (f"cells within {r} deg of the selected 1-degree cell (a quarter of the beam, the same "
                      f"distance on the sky in RA and Dec); measured cells in the region: {prof['n_cells']}; cell centre "
                      f"RA {prof['ra_deg']:.1f} deg, Dec {prof['dec_deg']:+.1f} deg" if r > 0 else
                      f"the selected 1-degree cell (RA {prof['ra_deg']:.1f} deg, Dec {prof['dec_deg']:+.1f} deg)")
            cal = self.calibration_lines(prof)
            ts = sorted(t for t in prof.get("times", []) if t and t != "-")
            # CSV
            folder_t = self.tables_dir()
            os.makedirs(folder_t, exist_ok=True)
            p_csv = os.path.join(folder_t, name + ".csv")
            with open(p_csv, "w", encoding="utf-8") as fh:
                fh.write(f"# {APP} {VERSION} - spectrum at a selected map point (mean line spectrum of the cells)\n")
                fh.write(f"# Position: l {prof['l_deg']:.2f} deg, b {prof['b_deg']:+.2f} deg; RA {prof['ra_deg'] / 15:.3f} h "
                         f"({prof['ra_deg']:.2f} deg), Dec {prof['dec_deg']:+.2f} deg (J2000, cell centre)\n")
                fh.write(f"# Region: {region}\n")
                fh.write(f"# Spectra: {prof['n_spectra']}" + (f" ({ts[0]} - {ts[-1]})" if ts else "") + "\n")
                for c in cal:
                    fh.write(f"# Calibration: {c}\n")
                fh.write("# Calibration formula: T_ant = (Tsys + T_cold) * P / P_cold - Tsys; T_line = T_ant minus the "
                         "fitted baseline, above the atmosphere\n")
                fh.write(f"# Peak: {prof['peak_K']:.3f} K at {prof['peak_v']:+.1f} km/s\n")
                fh.write("v_lsr_kms,t_line_K\n")
                for v, t in zip(prof["v"], prof["t"]):
                    if np.isfinite(t):
                        fh.write(f"{v:.3f},{t:.5f}\n")
            self.saved_tables.append(p_csv)
            # image
            folder_i = self.images_saved_dir()
            os.makedirs(folder_i, exist_ok=True)
            p_png = os.path.join(folder_i, name + ".png")
            fig = Figure(figsize=(10, 6.2), facecolor=BG)
            FigureCanvasAgg(fig)
            ax = fig.add_subplot(111)
            self.style_ax(ax)
            ax.plot(prof["v"], prof["t"], color="#ffcc00", lw=1.2)
            ax.axhline(0, color="white", alpha=0.3, lw=0.8)
            ax.set_xlabel("V_LSR (km/s)", color="white")
            ax.set_ylabel("T line (K)", color="white")
            ax.set_title(f"Spectrum at l {prof['l_deg']:.1f}°, b {prof['b_deg']:+.1f}°  (RA {prof['ra_deg'] / 15:.2f} h, "
                         f"Dec {prof['dec_deg']:+.1f}°)", color="white", fontsize=11)
            info = [f"Region: {region}", f"Spectra: {prof['n_spectra']}" + (f"  ({ts[0]} - {ts[-1]})" if ts else "")]
            info += [f"Calibration: {c}" for c in cal[:4]] + ([f"... {len(cal) - 4} more (see the CSV)"] if len(cal) > 4 else [])
            fig.text(0.07, 0.015, "\n".join(info), color="#bbbbbb", fontsize=7, ha="left", va="bottom", wrap=True)
            fig.subplots_adjust(left=0.08, right=0.98, top=0.93, bottom=0.12 + 0.028 * len(info))
            fig.savefig(p_png, dpi=150, facecolor=BG)
            self.note_saved_image(p_png)
            self.set_status(f"Spectrum saved: {p_png} and {p_csv} (both go into the science package)", OK_COL)
        except Exception as e:
            messagebox.showerror("Save spectrum", f"Saving the spectrum failed:\n{e}")

    def save_map_image(self):
        kind = self.v["map_kind"].get()
        if self.maps.n_points == 0:
            messagebox.showwarning("Save image", "No map points yet.")
            return
        try:
            folder = self.images_saved_dir()
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f"{self.save_name()}_{kind}_{time.strftime('%Y%m%d_%H%M%S', time.gmtime())}.png")
            fig = Figure(figsize=(8, 8.4) if kind == "polar" else (10, 5.5), facecolor=BG)
            FigureCanvasAgg(fig)
            self.draw_map(fig, kind, marker=False)            # saved images have no pointing marker
            fig.savefig(path, dpi=200, facecolor=BG)
            self.note_saved_image(path)
            self.map_drawn = None
            self.set_status(f"Image saved: {path}", OK_COL)
        except Exception as e:
            messagebox.showerror("Save image", f"Saving the image failed:\n{e}")

    def save_map_table(self):
        kind = self.v["map_kind"].get()
        ms = self.maps
        v1, v2, bw = ms.vel_params() or (-150.0, 150.0, 5.0)
        try:
            folder = self.tables_dir()
            os.makedirs(folder, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
            path = os.path.join(folder, f"{self.save_name()}_{kind}_{stamp}.csv")
            if kind in ("eq", "gal", "polar"):
                pkg.table_cells(ms, path, v1, v2)
            elif kind in ("channel", "m0", "m1"):
                pkg.table_velocity_grid(ms, path, kind, v1, v2)
            elif kind == "lv":
                pkg.table_lv(ms, path, bw)
            elif kind == "spec":
                pkg.table_spectra_by_l(ms, path, bw)
            elif kind == "top":
                pkg.table_topview(ms, path, bw)
            else:
                pkg.table_rotation(ms, path, bw)
            self.set_status(f"Table saved: {path}", OK_COL)
        except Exception as e:
            messagebox.showerror("Save table", f"Saving the table failed:\n{e}")
