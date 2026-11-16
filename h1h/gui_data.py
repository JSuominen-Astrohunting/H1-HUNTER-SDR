"""H1-HUNTER SDR - saving spectra (Data & Export)."""
import os
import re
import sys
import json
import math
import time
import threading
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
from .iqrec import IqRecorder
from . import package as pkg


class GuiDataMixin:
    def choose_folder(self):
        cur = self.v["save_folder"].get()
        d = filedialog.askdirectory(initialdir=cur if os.path.isdir(cur) else self.app_folder,
                                    title="Choose the folder for the measurements")
        if d:
            self.v["save_folder"].set(os.path.normpath(d))
            self.update_file_label()

    def save_name(self):
        """File name without extension; characters Windows does not allow are replaced."""
        name = os.path.splitext(self.v["save_name"].get().strip())[0]
        name = re.sub(r'[<>:"/\\|?*]', "_", name)
        return name or "H1"

    def data_folder(self):
        return self.v["save_folder"].get() or self.default_data_folder()

    def save_base(self):
        return os.path.join(self.data_folder(), self.save_name() + ".csv")

    # ---- where things are saved: a default for each, or a folder chosen on the Data & Export page ----
    def default_images_dir(self):
        return os.path.join(self.data_folder(), "images", "saved")

    def default_tables_dir(self):
        return os.path.join(self.data_folder(), "tables")

    def images_saved_dir(self):
        return self.v["images_folder"].get().strip() or self.default_images_dir()

    def tables_dir(self):
        return self.v["tables_folder"].get().strip() or self.default_tables_dir()

    def package_dir(self):
        return self.v["pkg_folder"].get().strip() or self.data_folder()

    def note_saved_image(self, path):
        """Images saved with Save image in this session go into the science package (wherever they were saved)."""
        if path and path not in self.saved_images:
            self.saved_images.append(path)

    def choose_place(self, name, title):
        cur = self.v[name].get().strip() or {"images_folder": self.default_images_dir(),
                                             "tables_folder": self.default_tables_dir(),
                                             "pkg_folder": self.data_folder()}[name]
        start = cur if os.path.isdir(cur) else (self.data_folder() if os.path.isdir(self.data_folder())
                                                 else os.path.expanduser("~"))
        d = filedialog.askdirectory(initialdir=start, title=title)
        if d:
            self.v[name].set(os.path.normpath(d))
            self.save_settings()

    def update_file_label(self):
        folder = self.data_folder()
        ok = os.path.isdir(folder)
        try:
            self.lbl_footer_folder.config(text=f"Data: {folder}")
        except Exception:
            pass
        self.lbl_file.config(text=f"{folder}" + ("" if ok else "  (will be created)"), fg=OK_COL if ok else WARN_COL)

    def metadata(self, rec, pr):
        sig = dict(zip(SIG_KEYS, rec["sig"]))
        fs, nfft = sig["fs"], sig["nfft"]
        hop = nfft // 2 if sig["overlap"] else nfft
        t_mid = pr["t_mid"]
        m = [("Application", f"{APP} v{VERSION}"), ("Saved_UTC", utc_str(time.time())), ("Data", rec["tag"]),
             ("Average_start_UTC", utc_str(rec["t_first"])), ("Average_mid_UTC", utc_str(t_mid)),
             ("Average_end_UTC", utc_str(rec["t_last"])), ("Average_counts", rec["counts"]),
             ("Integration_time_s", rec["counts"] * hop / fs),
             ("Spectra_flagged", rec["n_flagged"]), ("Flag_threshold_sigma", self.flag_sigma()),
             ("Flagged_note", "power_raw INCLUDES the flagged spectra; see _flagged.csv and _counts.csv"),
             ("Device", self.v["device"].get()),
             ("Tuned_frequency_Hz", sig["target_hz"]), ("LO_offset_Hz", sig["offset_hz"]),
             ("Sample_rate_Hz", fs), ("Gain_dB", sig["gain"]), ("AGC", "off"),
             ("PPM_total", sig["ppm"]), ("PPM_source", "entered by the user"), ("PPM_test_last", self.ppm_test_text or "-"), ("PPM_hardware", int(round(sig["ppm"]))),
             ("PPM_software", round(sig["ppm"] - round(sig["ppm"]), 4)), ("Bias_T", sig["bias"]),
             ("FFT_size", nfft), ("Window", sig["window"]), ("FFT_overlap", "50 %" if sig["overlap"] else "none"),
             ("Channel_width_Hz", fs / nfft), ("DC_removal", bool(sig["dc_remove"] or sig["offset_tuning"])), ("DC_notch_Hz", sig["notch_hz"]),
             ("Offset_tuning", sig["offset_tuning"]),
             ("Trusted_band", "central 80 % outside the DC notch"), ("Line_half_width_MHz", pr["half"])]
        thr = self.rx.throughput() if self.rx is not None else None
        if thr is not None:
            m.append(("Sample_throughput", round(min(thr, 1.0), 4)))
        if rec.get("clip_max") is not None:
            m.append(("ADC_clipped_fraction_max", round(rec["clip_max"], 5)))
        site = pr["site"]
        if site:
            m += [("Observer", site["observer"] or "-"), ("Site_lat_deg", site["lat"]), ("Site_lon_deg", site["lon"]),
                  ("Site_height_m", site["height"]),
                  ("Pointing", f"guided ({MOUNT_SYS} mount RA/Dec)" if self.guided() else "fixed Az/Alt (drift)"),
                  ("Pointing_az_deg", round(pr["sky"]["az_deg"], 3) if self.guided() and pr["sky"] else site["az"]),
                  ("Pointing_alt_deg", round(pr["sky"]["alt_deg"], 3) if self.guided() and pr["sky"] else site["alt"]),
                  ("Dish_diameter_m", site["dish"]), ("Beam_efficiency", round(pr["eta"], 3)),
                  ("Beam_efficiency_source", pr["eta_note"]),
                  ("Brightness_definition", "T_B = T_line / beam efficiency (1.0 for extended Galactic HI unless a "
                                            "value measured against a survey is entered)")]
        if pr["sky"]:
            s = pr["sky"]
            m += [("RA_deg_mid", round(s["ra_deg"], 4)), ("Dec_deg_mid", round(s["dec_deg"], 4)),
                  ("l_deg_mid", round(s["l_deg"], 4)), ("b_deg_mid", round(s["b_deg"], 4)),
                  ("V_LSR_correction_kms", round(s["vcorr_kms"], 4)),
                  ("V_LSR_definition", "v_LSR = c (f0 - f) / f0 + V_LSR_correction (radio definition)")]
        if self.cold is not None and self.cold_valid(rec["sig"]) and rec["tag"] != "cold":
            c = self.cold
            m += [("Cold_time_UTC", utc_str(c["t_last"])), ("Cold_counts", c["counts"]),
                  ("Cold_T_ref_K", round(c["tc"], 3)), ("Cold_alt_deg", c["alt"]), ("Cold_b_deg", round(c["b"], 3)),
                  ("Cold_model", ", ".join(f"{k} {v:.2f} K" for k, v in c["parts"].items()))]
            if t_mid is not None:
                m.append(("Cold_age_min_at_mid", round((t_mid - c["t_last"]) / 60.0, 1)))
            if self.hot_valid(rec["sig"]) and rec["tag"] != "hot":
                h = self.hot
                m += [("Hot_time_UTC", utc_str(h["t_last"])), ("Hot_T_ref_K", round(h["th"], 3)),
                      ("Hot_note", h["note"]), ("Y_factor", round(h["y"], 5)), ("Tsys_K", round(h["tsys"], 2)),
                      ("Calibration", "T_ant = (Tsys + T_cold) * P / P_cold - Tsys"),
                      ("Calibration_flag_treatment", "Cold and Hot without their flagged spectra, like this average"
                       if self.remove_flag else "Cold and Hot with all spectra, like this average")]
        r = self.radiometer(rec)
        if r is not None:
            m += [("Radiometer_sigma_measured", round(r[0], 7)), ("Radiometer_sigma_expected", round(r[1], 7)),
                  ("Radiometer_ratio", round(r[0] / r[1], 4)),
                  ("Radiometer_basis", "P / P_cold" + (" (smoothed Cold)" if self.cold_smooth_on() else "")
                   if r[2] else "raw spectrum: includes the receiver's bandpass shape (slope and fine structure), "
                                "so the ratio is an upper limit, not the noise")]
        return m

    def save_measurement(self, rec, auto=False, path=None, cal=False):
        """Saves one average as plain text: <stem>.csv (spectrum), <stem>_counts.csv and <stem>_flagged.csv."""
        if rec is None or rec["p"] is None or rec["counts"] == 0:
            if not auto:
                messagebox.showwarning("Save", "No averaged data yet.")
            return None
        try:
            base = self.save_base()
            os.makedirs(os.path.dirname(base) or ".", exist_ok=True)
            if path is None:
                path = next_multi_path(base)
            stem = os.path.splitext(path)[0]
            clean = remove_flagged(rec) if self.remove_flag else None    # Cold / Hot too (treated like the points)
            p_use = clean[0] if clean is not None else rec["p"]
            corr = (not cal) and self.gain_corrected(rec) and self.gain_g > 0
            if corr:
                p_use = p_use / self.gain_g
            use = dict(rec, p=p_use) if (clean is not None or corr) else rec
            pr = self.products(use)
            meta = self.metadata(rec, pr)
            if rec.get("sk_on"):
                skf = sk_fraction(rec, trusted_mask(rec["axis"], rec["sig"]))
                meta.append(("Spectral_kurtosis", f"on: blocks of {SK_M} spectra per channel, SK outside "
                             f"{SK_LOW}-{SK_HIGH} flagged (Pearson III, 0.135 % false alarms per tail); "
                             f"{int(rec.get('sk_flags', 0))} channel blocks flagged = {100 * skf:.3f} % of the trusted "
                             f"channel time; {int(pr['sk_dead'].sum()) if pr.get('sk_dead') is not None else 0} "
                             f"channels lost more than {100 * SK_DEAD_FRACTION:g} % and are left out with the RFI "
                             "channels; " + ("removed from power_used (column spectra_used = spectra per channel)"
                                                 if clean is not None else "not removed (Remove flagged off)")))
            if clean is not None:
                meta.append(("Flagged_removed", f"yes: {clean[1]} spectra removed from power_used and everything "
                                                "derived from it; power_raw is the full average"))
            if self.relay is not None or self.ref0 is not None:
                meta += [("Reference_load", "LNA input switched to a resistor by a USB relay (Dicke-type reference)"),
                         ("Reference_after_Cold_UTC", utc_str(self.ref0_t) if self.ref0_t else "-"),
                         ("Reference_every_averages", self.relay_every())]
            if corr:
                meta += [("Gain_drift_factor", round(self.gain_g, 6)),
                         ("Gain_factor_from_reference_UTC", utc_str(self.gain_t) if self.gain_t else "-"),
                         ("Gain_correction", "power_used = power / g, g = median(P_ref / P_ref_after_Cold) over the "
                                             "trusted band (latest reference); power_raw is uncorrected")]
            if pr.get("rfi") is not None:
                meta.append(("RFI_channels_removed", f"{int(pr['rfi'].sum())} narrow RFI channels left out of the "
                                                     "baseline fit and the line integral (column rfi_removed = 1)"))
            if pr.get("excluded") is not None:
                meta.append(("RFI_excluded_ranges_MHz", "; ".join(f"{lo:.4f}-{hi:.4f}" for lo, hi in
                                                                  self.rfi_excluded()) +
                             f" (set by the user, {int(pr['excluded'].sum())} channels, included in rfi_removed)"))
            if pr.get("cold_smoothed"):
                meta.append(("Cold_reference", "smoothed bandpass model of the Cold spectrum (narrow spikes "
                                               f"<= {RFI_MAX_WIDTH} channels interpolated over, running median "
                                               f"{COLD_SMOOTH_MEDIAN_HZ / 1e3:g} kHz, then cubic Savitzky-Golay "
                                               f"{COLD_SMOOTH_SG_HZ / 1e3:g} kHz); the measured Cold is in the "
                                               "calibration file"))
            if pr.get("drift_g") is not None:
                meta += [("Gain_drift_correction", "line-free channels: ratio_to_cold = (P / P_cold) / g, g = median "
                                                   "of P / P_cold outside the line window (continuum changes removed); "
                                                   "power_raw is uncorrected"),
                         ("Gain_drift_g", round(float(pr["drift_g"]), 6))]
            nanv = np.full(len(rec["axis"]), np.nan)
            cols = {"freq_hz": rec["axis"], "v_topo_kms": pr["vtopo"],
                    "v_lsr_kms": pr["vlsr"] if pr["vlsr"] is not None else nanv, "power_raw": rec["p"]}
            if clean is not None or corr:
                cols["power_used"] = use["p"]
            if clean is not None and rec.get("sk_rm_n") is not None:
                cols["spectra_used"] = spectra_used(rec)
            cols.update({"ratio_to_cold": pr["ratio"] if pr["ratio"] is not None else nanv,
                         "t_ant_K": pr["tant"] if pr["tant"] is not None else nanv,
                         "t_line_K": pr["tline"] if pr["tline"] is not None else nanv,
                         "t_b_line_K": pr["tb_line"] if pr["tb_line"] is not None else nanv,
                         "trusted": pr["mask"].astype(float)})
            if pr.get("rfi") is not None:
                cols["rfi_removed"] = pr["rfi"].astype(float)
            write_science_csv(stem + ".csv", cols, meta)
            self.saved_files.append({"kind": "cal" if cal else rec["tag"], "path": stem + ".csv"})
            if cal:
                self.saved_files[-1]["kind"] = rec["tag"] if rec["tag"] in ("cold", "hot") else "ref"
            extra = ""
            if rec.get("log_t") is not None and len(rec["log_t"]) and self.v["save_counts"].get():
                compact = self.v["count_log"].get() != "Full"
                write_counts_csv(stem + "_counts.csv", rec, meta, compact=compact)
                write_flagged_csv(stem + "_flagged.csv", rec, meta)
                kind = self.saved_files[-1]["kind"]
                self.saved_files += [{"kind": kind, "path": stem + "_counts.csv"},
                                     {"kind": kind, "path": stem + "_flagged.csv"}]
                extra = " + count log"
            self.lbl_saved.config(text=f"{'Auto-saved' if auto else 'Saved'} {os.path.basename(stem)}.csv{extra} "
                                       f"({rec['counts']:,} spectra) {time.strftime('%H:%M:%S')}", fg=OK_COL)
            return path
        except Exception as e:
            self.lbl_saved.config(text=f"Save failed: {e}", fg=ERR_COL)
            if not auto:
                messagebox.showerror("Save", f"Saving failed:\n{e}")
            return None

    def save_cal_file(self, kind, rec):
        if not self.v["save_cal"].get():
            return
        folder = os.path.dirname(self.save_base())
        stem = self.save_name()
        path = os.path.join(folder, f"{stem}_{kind}_{time.strftime('%Y%m%d_%H%M%S', time.gmtime(rec['t_last']))}.csv")
        self.save_measurement(rec, auto=True, path=path, cal=True)
        try:
            atomic_write(os.path.join(folder, f"{stem}_calibrations.json"), json.dumps(self.cal_history, indent=2))
        except Exception:
            pass

    def on_save_now(self):
        rec = self.averager.export()
        if rec is not None and rec["tag"] == "preview":
            rec["log_t"] = None
        self.save_measurement(rec, auto=False)


    # ---------------- page 4: Data & Export ----------------
    def build_data_page(self, page):
        sp = ScrollPanel(page, 10)
        sp.pack(fill="both", expand=True, padx=6, pady=6)
        sp.pack_propagate(True)
        inner = sp.inner
        cols = [tk.Frame(inner, bg=PAGE) for _ in range(2)]
        for i, c in enumerate(cols):
            c.grid(row=0, column=i, sticky="nsew")
            inner.columnconfigure(i, weight=1, uniform="dc")

        def item(parent, name, title, fname, desc, default=True, extra=None, tagtext="CSV", group=None, cb=None):
            row = tk.Frame(parent, bg=CARD)
            row.pack(fill="x")
            tk.Frame(row, bg=BORDER, height=1).pack(fill="x")
            body = tk.Frame(row, bg=CARD)
            body.pack(fill="x", pady=8)
            var = self.v[name] if name in self.v else self.var(name, default, "bool")
            ck = ttk.Checkbutton(body, variable=var, command=cb if cb else (lambda: None))
            ck.pack(side="left", anchor="n", padx=(0, 8))
            if group is not None:
                group.append(ck)
            if tagtext:
                tag(body, tagtext).pack(side="right", anchor="n")
            if extra is not None:
                extra(body)
            txt = tk.Frame(body, bg=CARD)
            txt.pack(side="left", fill="x", expand=True)
            tk.Label(txt, text=title, bg=CARD, fg=TEXT, font=F_UI, anchor="w", justify="left").pack(fill="x")
            line = tk.Frame(txt, bg=CARD)
            line.pack(fill="x")
            if fname:
                tk.Label(line, text=fname, bg=CARD, fg="#d4d7db", font=F_MONO_S).pack(side="left")
            if desc:
                tk.Label(line, text=desc, bg=CARD, fg=MUTED, font=F_SMALL, wraplength=330, justify="left",
                         anchor="w").pack(side="left", padx=(8, 0), fill="x")
            return ck

        c = card(cols[0], "1 · Saving - own telescope")
        self.note(c, "Saved automatically for every measurement point. All plain text.")
        item(c, "save_spectra", "Spectra", "H1_0037.csv", "frequency, velocity, raw power, T_ant, T_L + header")
        item(c, "save_log", "Scan log", "H1_log_YYYYMMDD.csv", "time, RA/Dec, l/b, T, flags")

        def count_combo(body):
            w = ttk.Combobox(body, textvariable=self.var("count_log", COUNT_LOGS[0]), values=COUNT_LOGS, width=8,
                             state="readonly", font=F_MONO_S)
            w.pack(side="right", anchor="n", padx=8)
        item(c, "save_counts", "Average count log for interference analysis", "H1_0037_counts.csv",
             "Compact: every 0.1 s · Full: every count", extra=count_combo)
        item(c, "save_cal", "Calibrations", "H1_cold_... · H1_hot_... · H1_calibrations.json", "", tagtext="CSV/JSON")
        tk.Frame(c, bg=BORDER, height=1).pack(fill="x", pady=(0, 8))
        r = tk.Frame(c, bg=CARD)
        r.pack(fill="x", pady=3)
        tk.Label(r, text="File name", bg=CARD, fg=MUTED, font=F_SMALL, width=9, anchor="w").pack(side="left")
        e = ttk.Entry(r, textvariable=self.var("save_name", "H1"), font=F_MONO, width=14)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self.update_file_label())
        e.bind("<FocusOut>", lambda ev: self.update_file_label())
        self.avg_widgets.append(e)
        tk.Label(r, text="-> H1_0001.csv, H1_0002.csv ...", bg=CARD, fg=MUTED, font=F_SMALL).pack(side="left", padx=8)
        r = tk.Frame(c, bg=CARD)
        r.pack(fill="x", pady=3)
        tk.Label(r, text="Folder", bg=CARD, fg=MUTED, font=F_SMALL, width=9, anchor="w").pack(side="left")
        self.var("save_folder", self.default_data_folder())
        e = ttk.Entry(r, textvariable=self.v["save_folder"], font=F_MONO)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<FocusOut>", lambda ev: self.update_file_label())
        ttk.Button(r, text="Browse...", style="Small.TButton", command=self.choose_folder).pack(side="left", padx=(8, 0))
        self.lbl_file = self.note(c, "")
        for name, label, hint, title in (
                ("images_folder", "Images", "default: <folder>/images/saved", "Choose the folder for saved images"),
                ("tables_folder", "Tables", "default: <folder>/tables", "Choose the folder for saved map tables")):
            self.place_row(c, name, label, hint, title)

        c = card(cols[0], "2 · Raw IQ")
        item(c, "save_iq", "Save raw IQ", "", "Off by default · the only binary file · recorded during 1. Cold, 2. Hot and 3. Measure",
             default=False, tagtext="", group=self.avg_widgets, cb=self.update_iq_note)
        self.lbl_iq_size = self.note(c, "")
        tree = tk.Frame(c, bg=INPUT, highlightthickness=1, highlightbackground=BORDER)
        tree.pack(fill="x", pady=(6, 0))
        for name, desc, indent in (("iq_YYYYMMDDTHHMMSSZ/", "", 0), ("iq.sigmf-data", "binary, cu8", 1),
                                   ("iq.sigmf-meta", "JSON: time, frequency, phase, gaps, SHA-512", 1),
                                   ("iq_blocks.csv", "time of every block, missing samples marked", 1),
                                   ("iq_excerpt.csv", "5 ms as text for checking", 1)):
            ln = tk.Frame(tree, bg=INPUT)
            ln.pack(fill="x", padx=10, pady=1)
            tk.Label(ln, text="    " * indent + name, bg=INPUT, fg="#d4d7db", font=F_MONO_S).pack(side="left")
            if desc:
                tk.Label(ln, text=desc, bg=INPUT, fg=MUTED, font=F_SMALL).pack(side="left", padx=8)
        dash = tk.Frame(c, bg=CARD, highlightthickness=1, highlightbackground="#4a4d53")
        dash.pack(fill="x", pady=(10, 0))
        tk.Label(dash, text='When "GPS synchronized clock" is ticked (Receiver)', bg=CARD, fg=TEXT, font=F_UI_B,
                 anchor="w").pack(fill="x", padx=10, pady=(8, 2))
        ln = tk.Frame(dash, bg=CARD)
        ln.pack(fill="x", padx=10)
        tk.Label(ln, text="+ station.json", bg=CARD, fg="#d4d7db", font=F_MONO_S).pack(side="left")
        tk.Label(ln, text="location, dish, Tsys, calibration, clock source GPS", bg=CARD, fg=MUTED,
                 font=F_SMALL).pack(side="left", padx=8)
        tk.Label(dash, text="The recording is then an interferometry-ready station packet.", bg=CARD, fg=MUTED,
                 font=F_SMALL, anchor="w").pack(fill="x", padx=10, pady=(2, 8))
        self.lbl_iq_state = self.note(c, "Raw IQ: not recording.", color=TEXT)

        c = card(cols[1], "3 · Science package")
        self.note(c, "One folder that can be analysed without this program. Start with README.txt.", color="#d4d7db")
        tree = tk.Frame(c, bg=INPUT, highlightthickness=1, highlightbackground=BORDER)
        tree.pack(fill="x", pady=(8, 0))
        for name, desc, indent in (("science_package_YYYYMMDD_HHMMSS/", "", 0),
                                   ("README.txt · summary.txt", "", 1),
                                   ("spectra/", "all spectra as CSV", 1),
                                   ("maps/", "sky cells, l-v tables CSV", 1),
                                   ("images/", "every map, polar map, hydrogen line, radiometer, RF + waterfall (PNG)", 1),
                                   ("saved/", "images saved with Save image", 2),
                                   ("calibrations/", "CSV + JSON", 1),
                                   ("settings.json", "every parameter", 1),
                                   ("station_packet/", "only if raw IQ was saved", 1)):
            ln = tk.Frame(tree, bg=INPUT)
            ln.pack(fill="x", padx=10, pady=1)
            tk.Label(ln, text="    " * indent + name, bg=INPUT, fg="#d4d7db", font=F_MONO_S).pack(side="left")
            if desc:
                tk.Label(ln, text=desc, bg=INPUT, fg=MUTED, font=F_SMALL).pack(side="left", padx=8)
        self.place_row(c, "pkg_folder", "Folder", "default: the measurement folder",
                       "Choose where science packages are written")
        self.check(c, "pkg_fits", "Also include a FITS cube (astronomy standard, binary)", False)
        self.check(c, "pkg_ezra", "Include ezRA data file (ezCon / SHOOS sharing)", False)
        self.btn_pkg = ttk.Button(c, text="Export science package", image=self.icon("package"), compound="left",
                                  style="Pkg.TButton", command=self.export_science_package)
        self.btn_pkg.pack(fill="x", pady=(10, 0))
        self.lbl_pkg = self.note(c, "")

        c = card(cols[1], "4 · Open and import")
        for text, cmd in (("Open science package and continue...", self.open_package),
                          ("Import spectrum files (.csv)...", self.import_csv_files),
                          ("Import H1-Hunter v1 data (.npz)...", self.import_npz)):
            ttk.Button(c, text=text, command=cmd, style="Left.TButton").pack(fill="x", pady=3)
        self.note(c, "Opened data are added to the maps. Imported files are converted to text when the science "
                     "package is written. Calibrations must be done again before measuring.")

    def place_row(self, parent, name, label, hint, title):
        """Folder row: empty = the default (shown greyed); Browse... chooses a folder, Default goes back."""
        r = tk.Frame(parent, bg=CARD)
        r.pack(fill="x", pady=3)
        tk.Label(r, text=label, bg=CARD, fg=MUTED, font=F_SMALL, width=9, anchor="w").pack(side="left")
        e = ttk.Entry(r, textvariable=self.var(name, ""), font=F_MONO)
        e.pack(side="left", fill="x", expand=True)
        Hint(e, self.v[name], hint)
        ttk.Button(r, text="Browse...", style="Small.TButton",
                   command=lambda: self.choose_place(name, title)).pack(side="left", padx=(8, 0))
        ttk.Button(r, text="Default", style="Small.TButton",
                   command=lambda: (self.v[name].set(""), self.save_settings())).pack(side="left", padx=(4, 0))

    def default_data_folder(self):
        home = os.path.expanduser("~")
        docs = os.path.join(home, "Documents")
        return os.path.join(docs if os.path.isdir(docs) else home, "H1-HUNTER SDR", "data")

    def update_iq_note(self):
        try:
            fs = float(self.v["fs"].get()) * 1e6
        except (ValueError, KeyError):
            fs = 2.4e6
        on = bool(self.v["save_iq"].get())
        gps = bool(self.v["gps_clock"].get())
        self.lbl_iq_size.config(text=f"About {2 * fs * 60 / 1e6:,.0f} MB per minute at {fs / 1e6:g} MS/s.")
        self.lbl_iqnote.config(text="Every finished point is saved automatically · raw IQ " +
                                    (("ON" + (" (station packet, GPS clock)" if gps else "")) if on else "off") +
                                    " - Data & Export page", fg=WARN_COL if on else MUTED)

    # ---------------- raw IQ ----------------
    def start_iq_recording(self, site, phase="measure"):
        self.stop_iq_recording()
        if not self.v["save_iq"].get() or self.rx is None or self.cfg is None:
            return
        cfg = self.cfg
        try:
            folder = self.data_folder()
            os.makedirs(folder, exist_ok=True)
            info = {"sample_rate": cfg["fs"], "center_freq": cfg["target_hz"] + cfg["offset_hz"],
                    "tuned_freq": cfg["target_hz"], "gain_dB": cfg["gain"], "ppm": cfg["ppm"],
                    "device": self.v["device"].get(), "observer": site["observer"], "lat": site["lat"],
                    "lon": site["lon"], "height": site["height"], "gps": bool(self.v["gps_clock"].get()),
                    "station": self.v["station"].get().strip(), "dish_m": site["dish"], "phase": phase}
            self.iq_rec = IqRecorder(folder, info)
            self.rx.iq_sink = self.iq_rec.write
            self.iq_folders.append(self.iq_rec.folder)
        except Exception as e:
            self.iq_rec = None
            messagebox.showerror("Raw IQ", f"The raw IQ recording could not be started:\n{e}")

    def stop_iq_recording(self):
        rec = self.iq_rec
        if rec is None:
            return
        if self.rx is not None:
            self.rx.iq_sink = None
        self.iq_rec = None
        extra = {}
        try:
            site = self.site_or_default()
            if self.hot_valid() and self.cold is not None:
                extra["calibration"] = {"t_cold_ref_K": self.cold["tc"], "t_hot_ref_K": self.hot["th"],
                                        "y_factor": self.hot["y"], "tsys_receiver_K": self.hot["tsys"]}
                extra["levels"] = {"t_sys_total_K": self.hot["tsys"] + self.cold["tc"]}
            if site:
                sky = self.sky_at(time.time(), site)
                az_, alt_ = self.pointing_azalt(site, sky)
                extra["pointing"] = {"mode": "guided" if self.guided() else "drift", "az_deg": az_, "alt_deg": alt_}
                if sky:
                    extra["pointing"].update(ra_deg=sky["ra_deg"], dec_deg=sky["dec_deg"])
            m = re.match(r"([+-]?[\d.]+) ± ([\d.]+) ppm", self.ppm_test_text or "")
            if m:
                extra["clock_prior"] = {"ppm": float(m.group(1)), "sigma_ppm": float(m.group(2)),
                                        "source": "PPM test vs PC clock"}
        except Exception:
            pass
        folder = rec.close(extra)
        gaps = rec.gaps()
        lost = sum(g[1] or 0 for g in gaps)
        gap_txt = (f"; {len(gaps)} gap(s), about {100.0 * lost / max(rec.n + lost, 1):.0f} % of the samples missing "
                   "(marked in iq_blocks.csv)") if gaps else "; complete"
        self.lbl_iq_state.config(text=f"Raw IQ saved ({rec.info.get('phase', 'measure')}): {os.path.basename(folder)} "
                                      f"({rec.size_mb():,.0f} MB, {rec.seconds():.0f} s{gap_txt})",
                                 fg=WARN_COL if gaps else OK_COL)

    def update_iq_state(self):
        rec = self.iq_rec
        if rec is not None:
            self.lbl_iq_state.config(text=f"Raw IQ recording: {rec.size_mb():,.0f} MB, {rec.seconds():.0f} s -> "
                                          f"{os.path.basename(rec.folder)}", fg=WARN_COL)

    # ---------------- after every finished sky average ----------------
    def after_measure_average(self, rec, path):
        """Maps, scan log and the ezRA rows for one finished sky average."""
        t_mid = 0.5 * (rec["t_first"] + rec["t_last"])
        p = rec["p"]
        if self.remove_flag:
            r = remove_flagged(rec)
            if r is not None:
                p = r[0]
        if self.gain_corrected(rec) and self.gain_g > 0:
            p = p / self.gain_g
        use = dict(rec, p=p)
        pr = self.products(use)
        # ezRA row: the same sky position as the spectrum file (middle of the average)
        self.ezra_rows.append((t_mid, rec["p"].copy(), False, rec["axis"], self.ezra_pointing(pr["sky"])))
        site = pr["site"]
        value = None
        if pr["tline"] is not None and site is not None:
            att = atmosphere_attenuation(self.pointing_azalt(site, pr["sky"])[1], site["height"])
            # K·MHz above the atmosphere; removed interference channels interpolated, not left out
            value = line_integral(pr["axis"], pr["tline"], pr["mask"], pr.get("rfi"), pr["half"]) * att
            tl = pr["tline"] * att
            ta = pr["tant"] * att if pr["tant"] is not None else None
            alt = pr["sky"]["alt_deg"] if pr["sky"] else site["alt"]
            if self.maps.add_average(pr, use, value, alt, tl, ta):
                self.maps_dirty = True
        if self.v["save_log"].get():
            self.write_scan_log(rec, pr, value, path)

    def ezra_pointing(self, sky=None):
        """Pointing line for the ezRA file: the mount's RA/Dec in guided mode (sky = the position of the average,
        as in its spectrum file; else now), otherwise the fixed Az/El."""
        if self.guided():
            if sky is None:
                sky = self.sky_at(time.time(), self.site_or_default())
            if sky is not None:
                return ("ra", round(sky["ra_deg"] / 15.0, 5), round(sky["dec_deg"], 4))
            return None
        site = self.site_or_default()
        return ("az", site["az"], site["alt"]) if site else None

    def write_scan_log(self, rec, pr, value, path):
        try:
            if self.scan_log_path is None:
                folder = self.data_folder()
                os.makedirs(folder, exist_ok=True)
                self.scan_log_path = os.path.join(folder, f"{self.save_name()}_log_{time.strftime('%Y%m%d', time.gmtime())}.csv")
            new = not os.path.isfile(self.scan_log_path)
            s = pr["sky"] or {}
            with open(self.scan_log_path, "a", encoding="utf-8") as fh:
                if new:
                    fh.write(f"# {APP} v{VERSION} scan log - one row per finished average\n")
                    fh.write("time_mid_utc,ra_deg,dec_deg,l_deg,b_deg,az_deg,alt_deg,counts,line_strength_K_MHz,"
                             "flagged_spectra,flagged_removed,gain_factor,file\n")
                fmt_ = lambda x, f="{:.4f}": f.format(x) if x is not None else "nan"
                fh.write(",".join([utc_str(pr["t_mid"]), fmt_(s.get("ra_deg")), fmt_(s.get("dec_deg")),
                                   fmt_(s.get("l_deg")), fmt_(s.get("b_deg")), fmt_(s.get("az_deg"), "{:.2f}"),
                                   fmt_(s.get("alt_deg"), "{:.2f}"), str(rec["counts"]), fmt_(value, "{:.6g}"),
                                   str(rec["n_flagged"]), "yes" if self.remove_flag else "no",
                                   f"{self.gain_g:.6f}", os.path.basename(path) if path else "-"]) + "\n")
        except Exception as e:
            self.set_status(f"Scan log could not be written: {e}", ERR_COL)

    # ---------------- science package ----------------
    def package_meta(self):
        site = self.site_or_default() or {}
        eta, eta_note = self.beam_efficiency(site or None)
        az_now, alt_now = self.pointing_azalt(site) if site else (None, None)
        return {"program": APP, "version": VERSION, "saved_utc": utc_str(time.time()),
                "station": self.v["station"].get().strip(), "observer": site.get("observer", ""),
                "site": {"lat_deg": site.get("lat"), "lon_deg": site.get("lon"), "height_m": site.get("height")},
                "dish_m": site.get("dish", DEFAULT_DISH_M), "eta_mb": eta, "eta_mb_source": eta_note,
                "spillover": "automatic (altitude and dish size)", "pointing_mode": f"guided ({MOUNT_SYS}): the position of every point is in its spectrum file and the "
                "scan log" if self.guided() else
                f"drift, Az {site.get('az')}°, Alt {site.get('alt')}°", "az_deg": az_now,
                "alt_deg": alt_now, "ppm": float(self.cfg["ppm"]) if self.cfg else 0.0,
                "ppm_test": self.ppm_test_text or "-", "gps_clock": bool(self.v["gps_clock"].get()),
                "flag_sigma": self.flag_sigma(), "map_unit": self.maps.map_unit,
                "defaults_used": self.maps.defaults_used(), "calibrations": self.cal_history,
                "n_points": self.maps.n_points, "raw_iq_recordings": [os.path.basename(d) for d in self.iq_folders],
                "velocity_frame": "LSR (kinematic, 20 km/s to the solar apex), radio definition"}

    def live_images(self):
        """PNG bytes of the live views (drawn here in the GUI thread)."""
        import io
        out = {}
        snap = self.view_snapshot()
        jobs = []
        if snap["p"] is not None:
            info = self.snapshot_info(snap)
            jobs.append(("hydrogen_line.png", lambda p: self.snapshot_line(p, info)))
            if self.rad_hist:
                jobs.append(("radiometer_check.png", lambda p: self.snapshot_noise(p, info)))
        if len(self.ln_rf.get_xdata()):
            jobs.append(("rf_spectrum_waterfall.png", self.render_rf_png))
        for name, fn in jobs:
            try:
                buf = io.BytesIO()
                fn(buf)
                out[name] = buf.getvalue()
            except Exception:
                pass
        return out

    def export_science_package(self):
        if self.pkg_thread is not None and self.pkg_thread.is_alive():
            messagebox.showinfo(APP, "The science package is being written already.")
            return
        if self.maps.n_points == 0 and not self.saved_files:
            if not messagebox.askyesno(APP, "There are no measurements yet. Write a package anyway?"):
                return
        if self.nb.index("current") != 3:
            self.nb.select(3)
        base = self.package_dir()                 # default: the measurement folder (Data & Export -> 3 -> Folder)
        try:
            os.makedirs(base, exist_ok=True)
        except OSError as e:
            messagebox.showerror(APP, f"The science package folder cannot be used:\n{base}\n{e}")
            return
        folder = os.path.join(base, "science_package_" + time.strftime("%Y%m%d_%H%M%S", time.gmtime()))
        images = self.live_images()
        fits_, ezra = bool(self.v["pkg_fits"].get()), bool(self.v["pkg_ezra"].get())
        self.pkg_result = None
        self.pkg_step = "starting"
        self.btn_pkg.config(state="disabled")

        def work():
            try:
                self.pkg_result = ("ok", folder, pkg.write_package(self, folder, fits_, ezra, images,
                                                                    progress=lambda st: setattr(self, "pkg_step", st)))
            except Exception as e:
                self.pkg_result = ("error", folder, str(e))
        self.pkg_thread = threading.Thread(target=work, daemon=True)
        self.pkg_thread.start()

    def poll_package(self):
        if self.pkg_thread is None:
            return
        if self.pkg_thread.is_alive():
            self.lbl_pkg.config(text=f"Writing... {self.pkg_step}", fg=INFO_COL)
            self.set_status(f"Writing the science package... {self.pkg_step}", INFO_COL)
            return
        self.pkg_thread = None
        self.btn_pkg.config(state="normal")
        res = self.pkg_result
        if res is None:
            return
        if res[0] == "error":
            self.lbl_pkg.config(text=f"Failed: {res[2]}", fg=ERR_COL)
            messagebox.showerror("Science package", f"Writing the package failed:\n{res[2]}")
            return
        written, failed = res[2]
        self.lbl_pkg.config(text=f"Written: {res[1]}  ({len(written)} files" +
                                 (f", {len(failed)} not written - see summary.txt" if failed else "") + ")",
                            fg=WARN_COL if failed else OK_COL)
        self.set_status(f"Science package written: {res[1]}", OK_COL)
        msg = f"Science package written:\n{res[1]}\n\n{len(written)} files."
        if failed:
            msg += "\n\nNot written (reason in summary.txt):\n" + "\n".join(failed[:8])
        messagebox.showinfo("Science package", msg)

    # ---------------- open and import ----------------
    def confirm_replace_maps(self):
        if self.maps.n_points == 0:
            return True
        return messagebox.askyesno(APP, f"The maps already have {self.maps.n_points} points. Opening other data "
                                        "replaces them (files already saved are not touched).\n\nContinue?")

    def open_package(self):
        if self.phase != "idle":
            messagebox.showinfo(APP, "Stop the running step first (Stop button).")
            return
        d = filedialog.askdirectory(initialdir=self.data_folder(), title="Choose a science package folder")
        if not d or not self.confirm_replace_maps():
            return
        try:
            meta = self.maps.load_package(d)
            self.loaded_files = []                      # maps were replaced
            self.imported_packages = [d]                # copied unchanged into imported/<name> of the next package
            self.apply_opened_meta(meta)
            self.maps_dirty = True
            note = ""
            if meta.get("_old_format_read_from_averages"):
                note = ("\n\nOlder package format: the spectra were read from their own files in spectra/averages "
                        "(the old spectra tables did not keep every channel at its own frequency).")
                k = int(meta.get("_skipped_no_average_file", 0))
                if k:
                    note += (f"\n{k} spectra have no file there and were left out - add their H1_*.csv files from "
                             "your data folder with Data & Export -> Import spectrum files (.csv)...")
            messagebox.showinfo(APP, f"Opened {os.path.basename(d)}: {self.maps.n_points} points.\n"
                                     "New measurements are added on top (calibrate again before measuring)." + note)
        except Exception as e:
            messagebox.showerror(APP, f"Could not open the package:\n{e}")

    def import_npz(self):
        if self.phase != "idle":
            messagebox.showinfo(APP, "Stop the running step first (Stop button).")
            return
        path = filedialog.askopenfilename(initialdir=self.data_folder(), title="Open H1-Hunter v1 data",
                                          filetypes=(("H1-Hunter v1 data (.npz)", "*.npz"), ("All files", "*.*")))
        if not path or not self.confirm_replace_maps():
            return
        try:
            meta = self.maps.load_npz(path)
            self.loaded_files = []                      # maps were replaced; an .npz has no spectrum files
            self.imported_packages = []
            meta = {"observer": meta.get("observer"), "site": meta.get("site", {}), "dish_m": meta.get("dish_m")}
            self.apply_opened_meta(meta)
            self.maps_dirty = True
            messagebox.showinfo(APP, f"Imported {os.path.basename(path)}: {self.maps.n_points} points.\n"
                                     "Export a science package to keep them as text files.")
        except Exception as e:
            messagebox.showerror(APP, f"Could not import the file:\n{e}")

    def apply_opened_meta(self, meta):
        site = meta.get("site") or {}
        for key, name in (("lat", "lat_deg"), ("lon", "lon_deg"), ("height", "height_m")):
            if site.get(name) is not None:
                self.v[key].set(str(site[name]))
        if meta.get("observer"):
            self.v["observer"].set(meta["observer"])
        if meta.get("dish_m"):
            self.v["dish"].set(str(meta["dish_m"]))
        self.on_site_change()

    def import_csv_files(self):
        """Spectrum CSV files of this program (or H1-SDR): calibrated averages with their sky position are added
        to the maps."""
        if self.phase != "idle":
            messagebox.showinfo(APP, "Stop the running step first (Stop button).")
            return
        paths = filedialog.askopenfilenames(initialdir=self.data_folder(), title="Spectrum files (.csv)",
                                            filetypes=(("Spectrum files", "*.csv"), ("All files", "*.*")))
        if not paths:
            return
        ok, skipped = 0, []
        for path in paths:
            try:
                if self.import_one_csv(path):
                    self.remember_loaded(path)
                    ok += 1
                else:
                    skipped.append(f"{os.path.basename(path)}: no sky position or no calibrated line")
            except Exception as e:
                skipped.append(f"{os.path.basename(path)}: {e}")
        self.maps_dirty = True
        msg = f"Added to the maps: {ok} file(s)."
        if skipped:
            msg += "\n\nSkipped:\n" + "\n".join(skipped[:12])
        messagebox.showinfo(APP, msg)

    def remember_loaded(self, path):
        """An imported spectrum file (and its _counts file) is copied into the next science package."""
        for p in (path, os.path.splitext(path)[0] + "_counts.csv"):
            if os.path.isfile(p) and p not in self.loaded_files:
                self.loaded_files.append(p)

    def import_one_csv(self, path):
        meta, names, rows = {}, None, []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    if ":" in line:
                        k, v = line[1:].split(":", 1)
                        meta[k.strip()] = v.strip()
                    continue
                if names is None:
                    names = line.split(",")
                    continue
                rows.append([float(x) for x in line.split(",")])
        if names is None or not rows or "t_line_K" not in names or "RA_deg_mid" not in meta:
            return False
        if meta.get("Data", "measure") in ("cold", "hot", "ref", "cold_ref"):
            return False
        arr = np.array(rows)
        col = {n: arr[:, i] for i, n in enumerate(names)}
        tl = col["t_line_K"]
        good = np.isfinite(tl) & (col.get("trusted", np.ones(len(tl))) > 0.5)
        removed = None
        if "rfi_removed" in col:
            removed = col["rfi_removed"] > 0.5        # written with trusted = 0; interpolated in the integral
            good &= ~removed                          # the same channels as the live maps (removed RFI left out)
        if good.sum() < 10:
            return False
        f = col["freq_hz"] / 1e6
        half = float(meta.get("Line_half_width_MHz", 0.8))
        alt = float(meta.get("Pointing_alt_deg", 45))
        height = float(meta.get("Site_height_m", 0))
        att = atmosphere_attenuation(alt, height)
        value = line_integral(col["freq_hz"], tl, good, removed, half) * att     # removed channels interpolated
        ms = self.maps
        ra_h, dec = float(meta["RA_deg_mid"]) / 15.0, float(meta["Dec_deg_mid"])
        l_c = ((float(meta["l_deg_mid"]) + 180.0) % 360.0) - 180.0
        vadd = float(meta.get("V_LSR_correction_kms", 0.0))
        from .maps import make_velocity_axis, VelCube, to_axis
        if ms.cube is None:
            ms.v_axis = make_velocity_axis(f[good])
            ms.cube = VelCube(ms.v_axis)
        spec_axis = to_axis(freq_to_vtopo(col["freq_hz"][good]) + vadd, tl[good] * att, ms.cube.v)
        counts = float(meta.get("Average_counts", 1))
        tk_ = col["t_ant_K"][good] * att if "t_ant_K" in col else np.full(int(good.sum()), np.nan)
        r = {"kind": "spectrum", "freq": f[good], "power_raw": col["power_raw"][good], "rfi": 0.0}
        from .maps import cal_from_meta
        cal = cal_from_meta(meta)
        ms.add_sample(ra_h, dec, l_c, float(meta["b_deg_mid"]), value, counts, alt, vadd, counts, spec_axis,
                      meta.get("Average_mid_UTC", "-"), r, tk_, tl[good] * att, 0.0, cal=cal)
        return True


def average_files_in(folder):
    """Spectrum files (H1_*.csv) and their _counts files in a folder; the large _flagged files are left out."""
    if not os.path.isdir(folder):
        return []
    return [os.path.join(folder, n) for n in sorted(os.listdir(folder))
            if n.startswith("H1_") and n.endswith(".csv") and "_flagged" not in n]
