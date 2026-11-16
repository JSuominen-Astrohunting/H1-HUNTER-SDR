"""H1-HUNTER SDR - shared GUI helpers, settings, receiver start/stop, update loop."""
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
from .theme import *
from .widgets import *


class GuiBaseMixin:
    def var(self, name, default, kind="str"):
        v = {"str": tk.StringVar, "bool": tk.BooleanVar, "double": tk.DoubleVar}[kind](value=default)
        self.v[name] = v
        return v

    # ---- widget helpers in the designed look (cards: label above the input, two columns) ----
    def label(self, parent, text, r, c=0, **kw):
        tk.Label(parent, text=text, bg=CARD, fg=MUTED, font=F_SMALL, anchor="w").grid(
            row=r, column=c, sticky="w", padx=0, pady=2, **kw)

    def note(self, parent, text, r=None, color=MUTED, wrap=310):
        lab = tk.Label(parent, text=text, bg=CARD, fg=color, font=F_SMALL, wraplength=wrap, justify="left",
                       anchor="w")
        # the text wraps at the real width of the card (no cut or too early line breaks)
        lab.bind("<Configure>", lambda e: lab.config(wraplength=max(e.width - 6, 80))
                 if abs(int(lab.cget("wraplength") or 0) - (e.width - 6)) > 4 else None)
        if r is None:
            lab.pack(fill="x", pady=(6, 0))
        else:
            lab.grid(row=r, column=0, columnspan=3, sticky="w", pady=(6, 0))
        return lab

    def _bind_cb(self, w, cb, kind):
        if cb is None:
            return
        if kind == "combo":
            w.bind("<<ComboboxSelected>>", lambda ev: cb())
        else:
            w.bind("<Return>", lambda ev: cb())
            w.bind("<FocusOut>", lambda ev: cb())

    def field(self, g, r, c, text, name, default, kind="entry", values=None, cb=None, group=None, span=1,
              hint=None, rng=(0, 1, 1), extra=None):
        """One cell of a card grid: grey label above the input. Returns the input widget."""
        cell = tk.Frame(g, bg=CARD)
        cell.grid(row=r, column=c, columnspan=span, sticky="ew", padx=(0, 8) if c == 0 and span == 1 else (0, 0),
                  pady=4)
        tk.Label(cell, text=text, bg=CARD, fg=MUTED, font=F_SMALL, anchor="w").pack(fill="x")
        row = tk.Frame(cell, bg=CARD)
        row.pack(fill="x")
        var = self.v[name] if name in self.v else self.var(name, default)
        if kind == "combo":
            w = ttk.Combobox(row, textvariable=var, values=values, state="readonly", font=F_MONO, width=6)
        elif kind == "spin":
            w = ttk.Spinbox(row, from_=rng[0], to=rng[1], increment=rng[2], textvariable=var, font=F_MONO, width=6,
                            command=cb if cb is not None else (lambda: None))
        else:
            w = ttk.Entry(row, textvariable=var, font=F_MONO, width=6)
        w.pack(side="left", fill="x", expand=True)
        if extra is not None:
            extra(row)
        self._bind_cb(w, cb, kind)
        if group is not None:
            group.append(w)
        if hint is not None:
            w.hint = Hint(w, var, hint)
        return w

    def value_cell(self, g, r, c, text, span=1):
        cell = tk.Frame(g, bg=CARD)
        cell.grid(row=r, column=c, columnspan=span, sticky="ew", pady=4)
        tk.Label(cell, text=text, bg=CARD, fg=MUTED, font=F_SMALL, anchor="w").pack(fill="x")
        val = tk.Label(cell, text="-", bg=CARD, fg=TEXT, font=F_VAL, anchor="w")
        val.pack(fill="x", pady=(4, 0))
        return val

    def entry(self, parent, name, default, r, width=12, cb=None, group=None):
        e = ttk.Entry(parent, textvariable=self.var(name, default), width=width, font=F_MONO)
        e.grid(row=r, column=1, sticky="w", padx=6, pady=2)
        self._bind_cb(e, cb, "entry")
        if group is not None:
            group.append(e)
        return e

    def combo(self, parent, name, values, default, r, width=12, cb=None, group=None):
        w = ttk.Combobox(parent, textvariable=self.var(name, default), values=values, width=width, state="readonly",
                         font=F_MONO)
        w.grid(row=r, column=1, sticky="w", padx=6, pady=2)
        self._bind_cb(w, cb, "combo")
        if group is not None:
            group.append(w)
        return w

    def check(self, parent, name, text, default, r=None, cb=None, group=None, style="TCheckbutton"):
        var = self.v[name] if name in self.v else self.var(name, default, "bool")
        w = ttk.Checkbutton(parent, text=text, variable=var, style=style,
                            command=cb if cb is not None else (lambda: None))
        if r is None:
            w.pack(fill="x", anchor="w", pady=2)
        else:
            w.grid(row=r, column=0, columnspan=3, sticky="w", pady=2)
        if group is not None:
            group.append(w)
        return w

    def spin(self, parent, name, default, r, lo, hi, inc, cb=None, group=None, width=8):
        sp = ttk.Spinbox(parent, from_=lo, to=hi, increment=inc, width=width, textvariable=self.var(name, default),
                         font=F_MONO, command=cb if cb is not None else (lambda: None))
        sp.grid(row=r, column=1, sticky="w", padx=6, pady=2)
        self._bind_cb(sp, cb, "entry")
        if group is not None:
            group.append(sp)
        return sp

    def section(self, parent, text, color=None):
        return card(parent, text)

    def set_status(self, text, color=FG):
        self.lbl_status.config(text=text, fg=color)

    def set_locked(self, widgets, locked):
        for w in widgets:
            try:
                if isinstance(w, ttk.Scale):
                    w.state(["disabled"] if locked else ["!disabled"])
                    continue
                if isinstance(w, ttk.Combobox):
                    w.config(state="disabled" if locked else "readonly")
                else:
                    w.config(state="disabled" if locked else "normal")
            except Exception:
                pass
        if not locked and any(w in widgets for w in (getattr(self, "chk_dc", None), getattr(self, "ent_notch", None))
                              if w is not None):
            self.dc_check_state()                  # stays greyed while offset tuning is on

    def style_ax(self, ax):
        ax.set_facecolor(BG)
        ax.tick_params(colors="#c9ccd1", labelsize=8)
        for s in ax.spines.values():
            s.set_color("#3a3d43")
        ax.grid(True, color="white", alpha=0.10, linestyle="--")

    def add_canvas(self, fig, parent):
        canvas = FigureCanvasTkAgg(fig, master=parent)
        tb = NavigationToolbar2Tk(canvas, parent, pack_toolbar=False)
        tb.update()
        tb.pack(side="bottom", fill="x")
        canvas.get_tk_widget().pack(fill="both", expand=True)
        return canvas

    def read_cfg(self):
        g = self.v
        try:
            f = float(g["freq"].get().replace(",", ".")) * 1e6
        except ValueError:
            return None, "Center frequency must be a number in MHz."
        if not 24e6 <= f <= 1766e6:
            return None, "Center frequency must be between 24 and 1766 MHz."
        fs = float(g["fs"].get()) * 1e6
        try:
            off = float(g["offset"].get().replace(",", ".")) * 1e3
        except ValueError:
            return None, "LO offset must be a number in kHz."
        if abs(off) > 0.4 * fs:
            return None, f"LO offset must be within ±{0.4 * fs / 1e3:.0f} kHz at this sample rate."
        try:
            gain = float(g["gain"].get())
        except ValueError:
            return None, "Choose a gain value."
        try:
            ppm = round(float(g["ppm"].get().replace(",", ".")), 2)
        except ValueError:
            return None, "PPM must be a number."
        if not -200 <= ppm <= 200:
            return None, "PPM must be between -200 and 200."
        try:
            notch = float(g["notch"].get().replace(",", ".")) * 1e3
        except ValueError:
            return None, "DC notch must be a number in kHz (0 = off)."
        if not 0 <= notch <= 50e3:
            return None, "DC notch must be 0-50 kHz."
        return {"target_hz": f, "offset_hz": off, "fs": fs, "gain": gain, "ppm": ppm, "bias": bool(g["bias"].get()),
                "nfft": int(g["nfft"].get()), "window": g["window"].get(), "overlap": bool(g["overlap"].get()),
                "dc_remove": bool(g["dc_remove"].get()), "notch_hz": notch,
                "offset_tuning": bool(g["offset_tuning"].get())}, None

    def opt_float(self, key, name, lo, hi):
        txt = self.v[key].get().strip().replace(",", ".")
        if not txt:
            return None
        try:
            val = float(txt)
        except ValueError:
            raise ValueError(f"{name} must be a number (or empty).")
        if not lo <= val <= hi:
            raise ValueError(f"{name} must be between {lo} and {hi}.")
        return val

    def read_site(self):
        """Site, pointing, antenna and temperatures. Raises ValueError with a readable message."""
        def req(key, name, lo, hi):
            val = self.opt_float(key, name, lo, hi)
            if val is None:
                raise ValueError(f"{name} is required (Settings -> Site, Observe -> Pointing).")
            return val
        return {"lat": req("lat", "Latitude", -90, 90), "lon": req("lon", "Longitude", -180, 360),
                "height": req("height", "Elevation", -500, 9000), "az": req("az", "Azimuth", -360, 360),
                "alt": req("alt", "Altitude", -90, 90), "air_c": self.opt_float("air_c", "Air temperature", -60, 50),
                "line_half": self.opt_float("line_half", "Line ± MHz", 0.05, 3.0),
                "dish": self.opt_float("dish", "Dish diameter", 0.05, 100) or DEFAULT_DISH_M,
                "eta": self.opt_float("eta", "Beam efficiency", 0.05, 1.0),
                "observer": self.v["observer"].get().strip()}

    def beam_efficiency(self, site):
        """(efficiency, note). Default 1.0: Galactic HI is extended - it fills the main beam and the sidelobes - so
        the antenna temperature of the line is its brightness temperature (verified against the LAB survey at the
        program's beam, 5.11.2026: 0.98-1.08). A value measured by the user replaces it."""
        if site is not None and site.get("eta") is not None:
            return site["eta"], "measured, entered by the user"
        return 1.0, "1.0 for extended Galactic HI (fills the beam; verified against the LAB survey at the same beam)"

    def refresh_devices(self):
        devs = list_rtl_devices()
        self.cmb_dev.config(values=devs + [SIM_DEVICE])
        if self.v["device"].get() not in devs + [SIM_DEVICE]:
            self.v["device"].set(devs[0] if devs else SIM_DEVICE)
        if not devs and self.rx is None:
            msg = "No RTL-SDR found - Simulator available."
            if RTL_ERROR:
                msg += "  (librtlsdr: " + RTL_ERROR.splitlines()[0][:120] + ")"
            self.set_status(msg, WARN_COL)

    def load_settings(self):
        try:
            with open(self.settings_path, "r", encoding="utf-8") as fh:
                s = json.load(fh)
            for k, val in s.items():
                if k in self.v and k != "device":
                    self.v[k].set(val)
        except Exception:
            pass
        try:
            float(self.v["gain"].get())
        except ValueError:
            self.v["gain"].set("29.7")
        self.restore_gain_steps()
        if not self.v["lat"].get().strip():                  # first start: the Pori preset, as in H1-Hunter
            for k, val in (("lat", "61.485"), ("lon", "21.797"), ("height", "15")):
                self.v[k].set(val)
        self.update_file_label()
        self.refresh_presets()
        self.update_iq_note()
        self.maps.cmap = self.v["cmap"].get() or "inferno"
        if self.v["scan_mode"].get() == "guided":
            self.v["scan_mode"].set("drift")                 # the mount must be connected again after a restart
        self.rb_drift.config(style="Sel.TButton")           # Drift is the selected pointing mode at start

    def save_settings(self):
        try:
            with open(self.settings_path, "w", encoding="utf-8") as fh:
                json.dump({k: v.get() for k, v in self.v.items()}, fh, indent=1)
        except Exception:
            pass

    def toggle_run(self):
        if self.rx is None:
            self.start()
        else:
            self.stop()

    def start(self):
        cfg, err = self.read_cfg()
        if err:
            messagebox.showerror("Settings", err)
            return
        self.cfg = cfg
        dev = self.v["device"].get()
        if dev == SIM_DEVICE:
            src = SimSource()
        else:
            try:
                src = RtlSource(int(dev.split(":")[0]))
            except ValueError:
                messagebox.showerror("Device", "Choose a device.")
                return
        self.gains_loaded = False
        self.averager.start(AVG_MODES[1], 1, False, "preview")
        self.rx = Receiver(src, cfg, self.averager)
        self.rx.flag_sigma = self.flag_sigma()
        self.rx.start()
        self.btn_run.config(text="Stop receiver", image=self.icon("stop"), style="Stop.TButton")
        self.cmb_dev.config(state="disabled")
        self.force_redraw()
        self.set_status(f"Receiver running: {dev}", OK_COL)
        self.refresh_buttons()

    def stop(self):
        self.stop_iq_recording()
        self.close_relay()
        rx = self.rx
        self.rx = None
        if rx is not None:
            rx.running = False
            rx.join(timeout=3.0)
        if self.phase != "idle":
            self.set_phase("idle")
        if self.btn_ppm_test.cget("text") == "Stop test":
            self.btn_ppm_test.config(text="PPM test")
        self.btn_run.config(text="Start receiver", image=self.icon("play"), style="Run.TButton")
        self.cmb_dev.config(state="readonly")
        self.set_status("Receiver stopped.", WARN_COL)
        self.refresh_buttons()

    def poll(self):
        try:
            self.update_views()
        except Exception as e:
            self.set_status(f"Display error: {type(e).__name__}: {e}", ERR_COL)
        self.poll_job = self.root.after(POLL_MS, self.poll)

    def update_views(self):
        rx = self.rx
        tab = self.nb.index("current")
        if rx is not None:
            if rx.error:
                err = rx.error
                self.stop()
                self.set_status("Receiver stopped: " + err.splitlines()[0], ERR_COL)
                if IS_WINDOWS:
                    hint = "On Windows the RTL-SDR needs the WinUSB driver (install it with Zadig)."
                else:
                    hint = ("On Linux this usually means that the DVB-T TV driver or another program still holds the "
                            "stick. Close other SDR programs, unplug the stick, run\n  sudo modprobe -r dvb_usb_rtl28xxu\n"
                            "plug it in again and test with:  rtl_test   (restarting the computer also helps).")
                messagebox.showerror("Receiver", err + "\n\n" + hint)
                return
            while rx.warnings:
                self.set_status("Warning: " + rx.warnings.pop(0), WARN_COL)
            if rx.gains and not self.gains_loaded:
                self.set_gain_steps(rx.gains)
                self.gains_loaded = True
                try:
                    cur = float(self.v["gain"].get())
                except ValueError:
                    cur = 29.0
                near = min(rx.gains, key=lambda x: abs(x - cur))
                if f"{near:.1f}" != self.v["gain"].get():
                    self.v["gain"].set(f"{near:.1f}")          # this tuner's nearest real gain step
                    self.on_rx_change()
            self.update_ppm_test()
            now = time.time()
            if now - self.rf_last >= RF_REDRAW_S:          # waterfall rows wait in the receiver meanwhile
                live = rx.take_live()
                if live is not None:
                    self.rf_last = now
                    self.update_rf(live, draw=(tab == 0))
        self.check_slewing()
        for rec in self.averager.pop_completed():
            self.on_completed(rec)
        snap = self.averager.snapshot()
        self.update_progress(snap)
        self.update_quality(snap, draw=(tab == 1 and "rad" in self.view_pos))
        if tab == 1:
            self.update_h1(self.view_snapshot())
            self.update_polar()
        elif tab == 2:
            self.update_maps_page()
        self.update_calinfo()
        now = time.time()
        if now - self.slow_last >= 1.0:                    # once per second: readouts, chips, pointing
            self.slow_last = now
            self.update_rx_readouts()
            self.update_iq_state()
            self.update_chips()
            self.update_now_text()
        self.poll_package()

    def update_chips(self):
        cfg, rx = self.cfg, self.rx
        if rx is not None and cfg:
            self.chip_rx.config(text=f"{cfg['fs'] / 1e6:.3f} MS/s · {cfg['target_hz'] / 1e6:.3f} MHz",
                                bg="#1f3524", fg="#8fe0a2")
        else:
            self.chip_rx.config(text="Receiver stopped", bg="#2a2b2f", fg="#c9ccd1")
        cv, hv = self.cold_valid(), self.hot_valid()
        if self.cold is None:
            self.chip_cal.config(text="Not calibrated", bg="#3a2a10", fg="#ffcc66")
        else:
            txt = f"Cold {'OK' if cv else 'changed'}  Hot {'OK' if hv else '-'}" + (f"  Tsys {self.hot['tsys']:.0f} K" if hv else "")
            self.chip_cal.config(text=txt, bg="#1f3524" if hv else "#3a2a10", fg="#8fe0a2" if hv else "#ffcc66")
        site = self.site_or_default()
        if self.guided():
            self.chip_point.config(text=f"Guided ({MOUNT_SYS})" + (" · mount connected" if self.mount else " · no mount"))
        elif site:
            self.chip_point.config(text=f"Drift · Az {site['az']:g}° Alt {site['alt']:g}°")
        else:
            self.chip_point.config(text="Pointing: check the site")

    def update_now_text(self):
        site = self.site_or_default()
        sky = self.sky_at(time.time(), site) if site else None
        if sky is None:
            self.lbl_now.config(text="Now: -" + ("" if site else "  (site or pointing missing)"))
            return
        txt = (f"Now: RA {sky['ra_deg'] / 15:.2f} h · Dec {sky['dec_deg']:+.1f}° · l {sky['l_deg']:.1f}° "
               f"b {sky['b_deg']:+.1f}°")
        if self.guided():
            txt += f"\n(mount: Az {sky['az_deg']:.1f}°, Alt {sky['alt_deg']:.1f}°)"
        self.lbl_now.config(text=txt)

    def show_about(self):
        """About window with a scrollbar (the text does not fit small screens)."""
        top = tk.Toplevel(self.root)
        top.title(f"About {APP}")
        top.configure(bg=CARD)
        top.transient(self.root)
        sh = self.root.winfo_screenheight()
        top.geometry(f"640x{max(300, min(620, sh - 120))}")
        frm = tk.Frame(top, bg=CARD)
        frm.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        sb = ttk.Scrollbar(frm, orient="vertical")
        txt = tk.Text(frm, wrap="word", bg=CARD, fg=TEXT, font=F_UI, relief="flat", highlightthickness=0,
                      padx=8, pady=6, yscrollcommand=sb.set)
        sb.config(command=txt.yview)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.insert("1.0", self.about_text())
        txt.config(state="disabled")
        ttk.Button(top, text="OK", command=top.destroy).pack(pady=(4, 10))
        top.bind("<Escape>", lambda e: top.destroy())
        txt.bind("<MouseWheel>", lambda e: txt.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units"))
        txt.bind("<Button-4>", lambda e: txt.yview_scroll(-3, "units"))
        txt.bind("<Button-5>", lambda e: txt.yview_scroll(3, "units"))
        top.focus_set()

    def about_text(self):
        return (
            f"{APP} v{VERSION}\nAuthor: Jarkko Suominen\nCopyright (c) 2026 Jarkko Suominen\n\nRTL-SDR receiver, hydrogen line observatory and sky mapper for the 21 cm line "
            "(1420.405752 MHz) in one program.\n\n"
            "Order of work:\n"
            "1. Receiver: choose the gain (no ADC OVERLOAD), Bias-T and PPM (0 for TCXO sticks; 'PPM test' compares "
            "the stick with the PC clock but never sets the value).\n"
            "2. Observe: 1. Cold at empty high-latitude sky, 2. Hot at the ground or an absorber (Y factor, Tsys; hot "
            "load = air temperature), 3. Measure until Stop - every finished average is saved and added to the maps.\n"
            "3. Maps: sky maps, velocity maps, l-v diagram, Milky Way top view, rotation curve and enclosed mass.\n"
            "4. Data & Export: what is saved, raw IQ (SigMF), the science package.\n\n"
            "Nothing is removed from the saved raw data: possible interference is only flagged and logged. "
            "'Remove flagged' recomputes the average without the flagged spectra (Restore undoes it).\n\n"
            "All saved files are plain text, except the optional raw IQ recording and the optional FITS cube.\n"
            "Windows: the RTL-SDR needs the WinUSB driver (install it with Zadig).\n"
            "The program makes no network connections.")

    def shutdown(self):
        """Stops the receiver (bias-T off), saves settings and stops the update loop. Safe to call twice."""
        if self.poll_job is not None:
            try:
                self.root.after_cancel(self.poll_job)
            except Exception:
                pass
            self.poll_job = None
        self.stop()
        try:
            if getattr(self, "temp_job", None) is not None:
                self.root.after_cancel(self.temp_job)
            self.temp_close()
        except Exception:
            pass
        if self.mount is not None and hasattr(self.mount, "disconnect"):
            try:
                self.mount.disconnect()          # INDI: close the read-only connection (the mount stays in Ekos)
            except Exception:
                pass
        self.save_settings()

    def on_close(self):
        self.shutdown()
        self.root.destroy()

