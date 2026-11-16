"""H1-HUNTER SDR - Settings page (site, antenna, reference load, program)."""
import os
import sys
import json
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from .common import *
from .physics import (RFI_KMS_MIN, RFI_KMS_MAX, RFI_KMS_DEFAULT, RFI_MAX_WIDTH, RFI_MODES,
                      RFI_MODE_DEFAULT)
from . import tempsensor as ts
from .widgets import *
from .theme import *

TEMP_SOURCES = ["Off", "DS18B20 (Raspberry Pi)", "USB serial", f"{MOUNT_SYS} weather / focuser"]
PRESETS_BUILTIN = {"Pori": {"lat": "61.485", "lon": "21.797", "height": "15"}}


class GuiSettingsMixin:
    def build_settings_page(self, page):
        sp = ScrollPanel(page, 10)
        sp.pack(fill="both", expand=True, padx=6, pady=6)
        sp.pack_propagate(True)
        inner = sp.inner
        cols = [tk.Frame(inner, bg=PAGE) for _ in range(2)]
        for i, c in enumerate(cols):
            c.grid(row=0, column=i, sticky="nsew")
            inner.columnconfigure(i, weight=1, uniform="st")
        a = self.avg_widgets

        G = card_grid(cols[0], "Site")
        self.field(G, 0, 0, "Station name", "station", "", group=a)
        self.field(G, 0, 1, "Observer", "observer", "", group=a)
        self.field(G, 1, 0, "Latitude °", "lat", "", cb=self.on_site_change, group=a)
        self.field(G, 1, 1, "Longitude °", "lon", "", cb=self.on_site_change, group=a)
        self.field(G, 2, 0, "Elevation m", "height", "", cb=self.on_site_change, group=a)
        e = self.field(G, 2, 1, "Air temperature °C", "air_c", "", cb=self.on_site_change, group=a,
                       hint="empty = estimate")
        self.hint_air2 = e.hint
        r = tk.Frame(G.card, bg=CARD)
        r.pack(fill="x", pady=(8, 0))
        tk.Label(r, text="Preset", bg=CARD, fg=MUTED, font=F_SMALL).pack(side="left", padx=(0, 6))
        self.var("preset", "")
        self.cmb_preset = ttk.Combobox(r, textvariable=self.v["preset"], width=14, state="readonly", font=F_MONO_S)
        self.cmb_preset.pack(side="left")
        self.cmb_preset.bind("<<ComboboxSelected>>", lambda e: self.apply_preset(self.v["preset"].get()))
        ttk.Button(r, text="Save as preset...", style="Small.TButton", command=self.save_preset).pack(side="left", padx=8)
        self.var("presets_json", "{}")
        self.var("indi_server", "")
        self.note(G.card, "One site for the whole program: receiver, maps and station packet all use it. Air "
                          "temperature is also on the Observe page (same value).", wrap=520)

        G = card_grid(cols[0], "Antenna")
        self.field(G, 0, 0, "Dish diameter m", "dish", str(DEFAULT_DISH_M), cb=self.on_site_change, group=a)
        self.lbl_beam2 = self.note(G.card, "", color=TEXT)
        self.note(G.card, "Ground spillover in the Cold sky model is computed automatically from the altitude and "
                          "the dish size. Dish size is also on the Observe page (same value). " + MOUNT_SYS +
                          " mount: Observe page -> Pointing -> Connect to mount.", wrap=520)

        G = card_grid(cols[1], "RFI filter (Remove flagged)")
        r = tk.Frame(G.card, bg=CARD)
        r.pack(fill="x")
        tk.Label(r, text="Mode", bg=CARD, fg=MUTED, font=F_SMALL).pack(side="left", padx=(0, 8))
        self.var("rfi_mode", RFI_MODE_DEFAULT)
        for m in RFI_MODES:
            ttk.Radiobutton(r, text=m, value=m, variable=self.v["rfi_mode"],
                            command=self.on_rfi_width).pack(side="left", padx=(0, 10))
        self.rfi_custom_row = tk.Frame(G.card, bg=CARD)
        tk.Label(self.rfi_custom_row, text="Widest RFI spike", bg=CARD, fg=MUTED, font=F_SMALL).pack(side="left",
                                                                                                    padx=(0, 8))
        ttk.Scale(self.rfi_custom_row, from_=RFI_KMS_MIN, to=RFI_KMS_MAX,
                  variable=self.var("rfi_kms", RFI_KMS_DEFAULT, "double"), length=180,
                  command=self.on_rfi_width).pack(side="left")
        self.lbl_rfi_kms = tk.Label(self.rfi_custom_row, text="", bg=CARD, fg=TEXT, font=F_MONO_S)
        self.lbl_rfi_kms.pack(side="left", padx=8)
        self.lbl_rfi_w = tk.Label(G.card, text="", bg=CARD, fg=TEXT, font=F_MONO_S, anchor="w")
        self.lbl_rfi_w.pack(fill="x", pady=(6, 0))
        self.lbl_rfi_warn = tk.Label(G.card, text="", bg=CARD, fg=WARN_COL, font=F_SMALL, anchor="w")
        self.lbl_rfi_warn.pack(fill="x")
        tk.Label(G.card, text="Excluded ranges (MHz), e.g. 1419.64-1419.70, 1420.78-1420.82", bg=CARD, fg=MUTED,
                 font=F_SMALL, anchor="w").pack(fill="x", pady=(8, 0))
        ttk.Entry(G.card, textvariable=self.var("rfi_exclude", ""), font=F_MONO_S).pack(fill="x")
        self.lbl_rfi_ex = tk.Label(G.card, text="", bg=CARD, fg=MUTED, font=F_MONO_S, anchor="w")
        self.lbl_rfi_ex.pack(fill="x")
        self.v["rfi_exclude"].trace_add("write", lambda *_: self.on_rfi_exclude())
        self.note(G.card, "For the receiver's own spurs. Empty = nothing excluded. Used when Remove flagged is on; "
                          "the ranges are written in the metadata.", wrap=520)
        self.update_rfi_exclude_text()
        self.check(G.card, "sk_flag", "Spectral kurtosis flagging (per channel)", False,
                   cb=lambda: self.on_sk_flag())
        self.note(G.card, "Finds pulsed and switching (non-Gaussian) interference in each channel and 0.1 s block "
                          "and removes only those channel blocks; steady carriers are left to the narrow spike search. The hydrogen line is Gaussian noise and is never "
                          "flagged. Off by default; used when Remove flagged is on; starts with the next average.",
                  wrap=520)
        self.note(G.card, f"Narrow RFI spikes (and dips left by RFI in Cold) are left out when Remove flagged is "
                          f"on. Conservative: 1 channel, strong spikes only. Normal: up to 3 km/s. Aggressive: up "
                          f"to 5 km/s and weaker spikes. The automatic search never removes more than {RFI_MAX_WIDTH} "
                          f"channels.", wrap=520)
        self.update_rfi_width_text()

        G = card_grid(cols[0], "Reference load (relay) - optional")
        ck = tk.Frame(G, bg=CARD)
        ck.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.check(ck, "relay_on", "Use reference load relay (gain drift correction)", False, group=a)
        self.field(G, 1, 0, "Relay serial port", "relay_port", "COM3" if sys.platform.startswith("win") else
                   "/dev/ttyUSB0", group=a)
        self.field(G, 1, 1, "Sky averages between references", "relay_every", "5", group=a)
        r = tk.Frame(G.card, bg=CARD)
        r.pack(fill="x", pady=(6, 0))
        ttk.Button(r, text="Test relay", style="Small.TButton", command=self.on_test_relay).pack(side="left")
        self.lbl_relay = tk.Label(r, text="Relay: off", bg=CARD, fg=MUTED, font=F_SMALL)
        self.lbl_relay.pack(side="left", padx=8)
        self.note(G.card, "USB serial relay of LCUS-1 / CH340 type. The relay switches the LNA input to a resistor. "
                          "A reference is taken after Cold, then one every N sky averages; the gain drift is removed "
                          "from the sky spectra. With the Simulator the relay is simulated.\n"
                          "Recommended: Nooelec SAWbird+ H1 Barebones (built-in RF switch to a 50 ohm reference; the "
                          "enclosed SAWbird+ H1 has none). Wire the relay contacts COM and NO to header pins 3 and 4: "
                          "the relay closes during the reference.", wrap=520)

        G = card_grid(cols[0], "Temperature sensor - optional")
        self.field(G, 0, 0, "Source", "temp_source", TEMP_SOURCES[0], kind="combo", values=TEMP_SOURCES,
                   cb=lambda *_: self.temp_connect(interactive=True))
        self.field(G, 0, 1, "Serial port", "temp_port", "", hint="empty = detect")
        self.var("temp_ascom", "")
        self.var("temp_indi", "")
        r = tk.Frame(G.card, bg=CARD)
        r.pack(fill="x", pady=(6, 0))
        ttk.Button(r, text="Detect", style="Small.TButton", command=self.temp_detect).pack(side="left")
        ttk.Button(r, text="Connect", style="Small.TButton",
                   command=lambda: self.temp_connect(interactive=True)).pack(side="left", padx=(8, 0))
        self.lbl_temp = tk.Label(r, text="Sensor: off", bg=CARD, fg=MUTED, font=F_SMALL)
        self.lbl_temp.pack(side="left", padx=8)
        self.note(G.card, "Fills the Air temperature (the Hot load temperature) when that field is empty; a typed value "
                          f"always wins. DS18B20 on a Raspberry Pi (1-Wire), a USB serial device that prints °C as "
                          f"text lines, or an {MOUNT_SYS} weather station / focuser. Read only.", wrap=520)

        G = card_grid(cols[1], "Program")
        v1 = self.value_cell(G, 0, 0, "Map unit")
        v1.config(text="K·MHz")
        v2 = self.value_cell(G, 0, 1, "Time shown as")
        v2.config(text="UTC")
        self.note(G.card, "Settings are saved in h1_hunter_sdr_settings.json (plain text). The program never uses the "
                          "network.", wrap=520)
        r = tk.Frame(G.card, bg=CARD)
        r.pack(fill="x", pady=(8, 0))
        ttk.Button(r, text=f"About {APP} v{VERSION}", style="Small.TButton",
                   command=self.show_about).pack(side="left")
        ttk.Button(r, text="User manual", style="Small.TButton",
                   command=self.open_manual).pack(side="left", padx=(8, 0))
        if IS_PI:
            ttk.Button(G.card, text="Shut down Raspberry Pi", style="Stop.TButton",
                       command=self.shutdown_pi).pack(anchor="w", pady=(8, 0))
        self.refresh_presets()

    # ---------------- temperature sensor ----------------
    def sensor_reading(self):
        """(celsius, name) from the temperature sensor when its value is recent, else None."""
        sen = getattr(self, "temp_sensor", None)
        if sen is None:
            return None
        lt = sen.latest()
        if lt is None or lt[1] > ts.MAX_AGE_S:
            return None
        return lt[0], sen.name

    def temp_close(self):
        sen = getattr(self, "temp_sensor", None)
        self.temp_sensor = None
        if sen is not None:
            try:
                sen.close()
            except Exception:
                pass

    def temp_connect(self, interactive=False):
        """Connects the chosen source. interactive=False (program start) never opens dialogs."""
        self.temp_close()
        src = self.v["temp_source"].get()
        try:
            if src == TEMP_SOURCES[1]:
                self.temp_sensor = ts.Ds18b20().start()
            elif src == TEMP_SOURCES[2]:
                port = self.v["temp_port"].get().strip()
                if not port:
                    if not interactive:
                        raise RuntimeError("no serial port set")
                    found = self.temp_probe_serial()
                    if found is None:
                        raise RuntimeError("no serial temperature sensor found")
                    port = found[0]
                    self.v["temp_port"].set(port)
                self.temp_sensor = ts.SerialSensor(port).start()
            elif src == TEMP_SOURCES[3]:
                self.temp_sensor = self.temp_connect_ascom(interactive) if IS_WINDOWS else \
                    self.temp_connect_indi(interactive)
        except Exception as e:
            self.temp_sensor = None
            self.lbl_temp.config(text=f"Sensor: {e}"[:90], fg=WARN_COL if src != TEMP_SOURCES[0] else MUTED)
            if interactive and src != TEMP_SOURCES[0]:
                messagebox.showerror("Temperature sensor", str(e), parent=self.root)
        self.temp_tick(schedule=False)

    def temp_probe_serial(self):
        relay = self.v["relay_port"].get().strip()
        ports = [p for p in ts.serial_ports() if p != relay]
        self.lbl_temp.config(text="Sensor: searching serial ports...", fg=MUTED)
        self.root.update_idletasks()
        return ts.probe_serial(ports)

    def temp_connect_ascom(self, interactive):
        stored = self.v["temp_ascom"].get()
        kind, _, drv = stored.partition("|")
        if interactive or not drv:
            if not interactive:
                raise RuntimeError("choose the device with Connect")
            kind = "ObservingConditions" if messagebox.askyesno(
                "Temperature sensor", "Weather station / ObservingConditions device?\n\nNo = a focuser with a "
                "temperature probe.", parent=self.root) else "Focuser"
            drv = ts.ascom_choose(kind)
            if not drv:
                raise RuntimeError("no device chosen")
            self.v["temp_ascom"].set(f"{kind}|{drv}")
        return ts.AscomSensor(drv, kind)

    def temp_connect_indi(self, interactive):
        from tkinter import simpledialog
        from .indi import IndiClient, DEFAULT_HOST, DEFAULT_PORT
        addr = self.v["indi_server"].get().strip() or f"{DEFAULT_HOST}:{DEFAULT_PORT}"
        host, _, port = addr.partition(":")
        client = IndiClient(host.strip() or DEFAULT_HOST, int(port or DEFAULT_PORT))
        client.open()
        t0 = time.time()
        while time.time() - t0 < 4.0 and not ts.indi_temperature_devices(client):
            self.root.update()
            time.sleep(0.05)
        devs = ts.indi_temperature_devices(client)
        if not devs:
            client.close()
            raise RuntimeError(f"no INDI device with a temperature on {addr}")
        names = [f"{d}|{p}|{e}" for d, p, e in devs]
        pick = self.v["temp_indi"].get()
        if pick not in names:
            pick = names[0]
            if len(names) > 1 and interactive:
                ans = simpledialog.askstring("Temperature sensor", "INDI devices with a temperature:\n" +
                                             "\n".join(d for d, _, _ in devs) + "\n\nType the device to use:",
                                             initialvalue=devs[0][0], parent=self.root)
                hit = [n for n in names if ans and n.split("|")[0] == ans.strip()]
                if not hit:
                    client.close()
                    raise RuntimeError("no device chosen")
                pick = hit[0]
        self.v["temp_indi"].set(pick)
        return ts.IndiSensor(client, *pick.split("|"))

    def temp_detect(self):
        """Automatic search: DS18B20 first, then serial ports (the relay port is skipped). ASCOM / INDI devices are
        chosen with Connect."""
        if ts.ds18b20_devices():
            self.v["temp_source"].set(TEMP_SOURCES[1])
            self.temp_connect(interactive=True)
            return
        found = self.temp_probe_serial()
        if found is not None:
            self.v["temp_port"].set(found[0])
            self.v["temp_source"].set(TEMP_SOURCES[2])
            self.temp_connect(interactive=True)
            return
        self.lbl_temp.config(text="Sensor: none found", fg=MUTED)
        messagebox.showinfo("Temperature sensor", "No DS18B20 or serial temperature sensor was found.\n\n"
                            f"An {MOUNT_SYS} weather station or focuser: choose Source '{TEMP_SOURCES[3]}' and "
                            "press Connect.", parent=self.root)

    def temp_tick(self, schedule=True):
        """Every 2 s: ASCOM reading (GUI thread), status text and the Air temperature hint."""
        sen = getattr(self, "temp_sensor", None)
        if sen is not None:
            try:
                sen.poll()
            except Exception:
                pass
            lt = sen.latest()
            if lt is None:
                self.lbl_temp.config(text=f"Sensor: {sen.name} - waiting" + (f" ({sen.error})" if sen.error else ""),
                                     fg=WARN_COL if sen.error else MUTED)
            elif lt[1] > ts.MAX_AGE_S:
                self.lbl_temp.config(text=f"Sensor: {sen.name} - no new value for {lt[1] / 60:.0f} min", fg=WARN_COL)
            else:
                self.lbl_temp.config(text=f"Sensor: {lt[0]:.1f} °C ({sen.name})", fg=OK_COL)
        elif self.v["temp_source"].get() == TEMP_SOURCES[0]:
            self.lbl_temp.config(text="Sensor: off", fg=MUTED)
        try:
            self.update_air_hint()
        except Exception:
            pass
        if schedule:
            self.temp_job = self.root.after(2000, self.temp_tick)

    def open_manual(self):
        """Opens the user manual (MANUAL.html, Finnish and English) in the web browser. Local file, no network."""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "MANUAL.html")
        if not os.path.isfile(path):
            messagebox.showerror(APP, f"The user manual was not found:\n{path}", parent=self.root)
            return
        try:
            if IS_WINDOWS:
                os.startfile(path)
            else:
                import webbrowser
                from pathlib import Path
                if not webbrowser.open(Path(path).as_uri()):
                    raise RuntimeError("no web browser found")
        except Exception as e:
            messagebox.showinfo(APP, f"Open this file in a web browser:\n{path}\n\n({e})", parent=self.root)

    def shutdown_pi(self):
        """Stops the receiver, saves the settings and switches the Raspberry Pi off."""
        if not messagebox.askyesno(APP, "Shut down the Raspberry Pi?\n\nThe receiver is stopped and the settings are "
                                        "saved first. Wait until the green light stops blinking before you unplug "
                                        "the power.", parent=self.root):
            return
        self.shutdown()
        import subprocess
        for cmd in (["systemctl", "poweroff"], ["sudo", "-n", "shutdown", "-h", "now"]):
            try:
                if subprocess.run(cmd, timeout=20).returncode == 0:
                    return
            except Exception:
                pass
        messagebox.showerror(APP, "The Raspberry Pi could not be shut down from the program. Use the menu of the "
                                  "desktop, or the command: sudo shutdown -h now", parent=self.root)

    # ---------------- presets ----------------
    def user_presets(self):
        try:
            d = json.loads(self.v["presets_json"].get() or "{}")
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def refresh_presets(self):
        names = list(PRESETS_BUILTIN) + [n for n in self.user_presets() if n not in PRESETS_BUILTIN]
        self.cmb_preset.config(values=names)

    def apply_preset(self, name):
        p = dict(PRESETS_BUILTIN.get(name, {}))
        p.update(self.user_presets().get(name, {}))
        if not p:
            return
        for k, val in p.items():
            if k in self.v:
                self.v[k].set(val)
        self.on_site_change()
        self.set_status(f"Preset '{name}' applied.", OK_COL)

    def save_preset(self):
        name = simpledialog.askstring(APP, "Name of the preset (site, station, observer, dish, spillover):",
                                      parent=self.root)
        if not name:
            return
        d = self.user_presets()
        d[name.strip()] = {k: self.v[k].get() for k in ("lat", "lon", "height", "station", "observer", "dish")}
        self.v["presets_json"].set(json.dumps(d))
        self.refresh_presets()
        self.v["preset"].set(name.strip())
        self.save_settings()
        self.set_status(f"Preset '{name}' saved.", OK_COL)
