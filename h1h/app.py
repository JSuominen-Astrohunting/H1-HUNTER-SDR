"""H1-HUNTER SDR - main window: header, the pages and the status bar."""
import os
import sys
import tkinter as tk
from tkinter import ttk
from .common import *
from .receiver import Averager
from .maps import MapStore
from .widgets import heading, NavBar
from . import theme
from .theme import *
from .icons import load_icons
from .gui_base import GuiBaseMixin
from .gui_receiver import GuiReceiverMixin
from .gui_observe import GuiObserveMixin
from .gui_maps import GuiMapsMixin
from .gui_data import GuiDataMixin
from .gui_settings import GuiSettingsMixin

SETTINGS_FILE = "h1_hunter_sdr_settings.json"


class App(GuiBaseMixin, GuiReceiverMixin, GuiObserveMixin, GuiMapsMixin, GuiDataMixin, GuiSettingsMixin):
    def __init__(self, root, app_folder=None, settings_folder=None):
        self.root = root
        root.title(f"{APP} v{VERSION}")
        root.geometry("1500x930")
        root.minsize(1180, 720)
        root.configure(bg=PAGE)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        if app_folder is None:
            app_folder = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
                          else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.app_folder = app_folder
        icon = os.path.join(app_folder, "h1_hunter_sdr.ico")
        png = os.path.join(app_folder, "h1_hunter_sdr.png")
        try:
            if sys.platform.startswith("win") and os.path.exists(icon):
                root.iconbitmap(default=icon)
            elif os.path.exists(png):                       # Linux: window icon from the PNG
                self._win_icon = tk.PhotoImage(file=png)
                root.iconphoto(True, self._win_icon)
        except Exception:
            pass
        self.settings_path = os.path.join(settings_folder or app_folder, SETTINGS_FILE)
        self.frame = tk.Frame(root, bg=PAGE)
        self.frame.pack(fill="both", expand=True)

        self.averager = Averager()
        self.rx = None
        self.cfg = None
        self.phase = "idle"             # idle | cold | hot | measure
        self.pending = None             # pointing info captured when Cold/Hot/Measure starts
        self.cold = None
        self.hot = None
        self.cal_history = []
        self.view_rev = 0
        self.gains_loaded = False
        self.rf_axis_id = -1
        self.rf_ylim = None
        self.wf = None
        self.wf_img = None
        self.h1_key = None
        self.h1_xkey = None
        self.rad_hist = []
        self.rad_key = None
        self.rad_last = 0.0
        self.rad_now = None
        self.sky_cache = {}
        self.model_cache = {}
        self.poll_job = None
        self.ppm_test_text = ""
        self.rf_last = 0.0
        self.slow_last = 0.0
        self.series = False
        self.rfi_count = 0              # narrow RFI channels removed by 'Remove flagged' (shown on the button)
        self.remove_flag = False        # 'Remove flagged': shown / saved average without the flagged spectra
        self.flag_frac = 0.0
        self.relay = None               # SerialRelay / SimRelay while in use
        self.ref0 = None                # reference-load spectrum taken right after Cold
        self.ref0_t = None
        self.gain_g = 1.0               # receiver gain now / at the reference after Cold
        self.gain_t = None
        self.sky_since_ref = 0
        self.saved_count = 0
        self.h1_counts = 0              # spectra in the average at the last redraw (Intermediate average)
        self.mount = None               # ASCOM (Windows) or INDI (Linux) mount, guided mode
        self.mount_jnow = True
        self.mount_cache = None
        self.mount_slewing = False
        self.slew_reset = False
        self.saved_images = []          # images saved with Save image in this session (for the science package)
        self.saved_tables = []          # spectra saved from the Maps page in this session (for the science package)
        self.saved_files = []           # every file saved in this session (for the science package)
        self.loaded_files = []          # spectrum (+ _counts) files imported one by one (copied to imported/files)
        self.imported_packages = []     # science package opened into the maps (copied to imported/<name>)
        self.scan_log_path = None
        self.iq_rec = None              # running raw IQ recording
        self.iq_folders = []            # raw IQ recordings of this session
        self.ezra_rows = []             # (t_mid, power, reference?, axis) of every finished average
        self.maps_dirty = True
        self.pkg_thread = None
        self.pkg_result = None
        self.pkg_step = ""
        self.v = {}
        self.rx_widgets = []
        self.avg_widgets = []
        self.maps = MapStore(self)
        self.icons = load_icons()

        self.build_ui()
        self.load_settings()
        self.refresh_devices()
        self.dc_check_state()
        self.slider_state()
        self.on_info_text()
        self.on_sk_flag(quiet=True)
        self.temp_connect(interactive=False)
        self.temp_tick()
        self.cfg, _ = self.read_cfg()
        self.update_rbw()
        self.refresh_buttons()
        self.on_view_change()
        self.poll()

    def build_ui(self):
        theme.setup(self.root)
        theme.plot_fonts(self.app_folder)
        # The mouse wheel must scroll the panels, never silently change a gain or another setting
        for cls in ("TCombobox", "TSpinbox"):
            for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):     # Button-4/5 = the wheel on Linux
                try:
                    self.frame.unbind_class(cls, ev)
                except Exception:
                    pass
        self.frame.configure(bg=PAGE)

        top = tk.Frame(self.frame, bg=HEADER)
        top.pack(fill="x")
        tk.Frame(self.frame, bg=BORDER, height=1).pack(fill="x")
        tk.Label(top, text="H1-HUNTER SDR", font=F_TITLE, bg=HEADER, fg=TEXT).pack(side="left", padx=(16, 4), pady=10)
        tk.Label(top, text=f"v{VERSION}", font=F_TITLE, bg=HEADER, fg=ACCENT).pack(side="left", padx=(0, 14))
        self.btn_run = ttk.Button(top, text="Start receiver", image=self.icon("play"), compound="left",
                                  style="Run.TButton", command=self.toggle_run, width=16)
        self.btn_run.pack(side="left", padx=6)
        ttk.Button(top, text="Science package", image=self.icon("package"), compound="left", style="Pkg.TButton",
                   command=self.export_science_package).pack(side="left", padx=6)
        tk.Label(top, text="Device", bg=HEADER, fg=MUTED, font=F_SMALL).pack(side="left", padx=(12, 6))
        self.cmb_dev = ttk.Combobox(top, textvariable=self.var("device", SIM_DEVICE), width=30, state="readonly",
                                    font=F_MONO_S)
        self.cmb_dev.pack(side="left")
        ttk.Button(top, text="Refresh" if not self.icons else "", image=self.icon("refresh_light"),
                   command=self.refresh_devices).pack(side="left", padx=6)
        chips = tk.Frame(top, bg=HEADER)
        chips.pack(side="right", padx=12)
        mk = lambda: tk.Label(chips, text="", bg=CHIP_GREY[0], fg=CHIP_GREY[1], font=F_SMALL, padx=12, pady=4)
        self.chip_rx, self.chip_cal, self.chip_point = mk(), mk(), mk()
        for c in (self.chip_rx, self.chip_cal, self.chip_point):
            c.pack(side="left", padx=4)

        # Status bar first, so that it always keeps its place at the bottom
        bar = tk.Frame(self.frame, bg=HEADER)
        bar.pack(fill="x", side="bottom")
        tk.Frame(self.frame, bg=BORDER, height=1).pack(fill="x", side="bottom")
        self.lbl_status = tk.Label(bar, text="Stopped. Choose a device and press Start receiver.", anchor="w",
                                   bg=HEADER, fg=TEXT, font=F_SMALL)
        self.lbl_status.pack(side="left", fill="x", expand=True, padx=14, pady=6)
        tk.Label(bar, text="Offline", bg=HEADER, fg=TAB_TEXT, font=F_SMALL).pack(side="right", padx=14)
        self.lbl_footer_folder = tk.Label(bar, text="", bg=HEADER, fg=TAB_TEXT, font=F_SMALL)
        self.lbl_footer_folder.pack(side="right", padx=8)

        self.nb = ttk.Notebook(self.frame, style="Hidden.TNotebook")
        self.nav = NavBar(self.frame, self.nb)
        self.nav.pack(fill="x")
        tk.Frame(self.frame, bg=BORDER, height=1).pack(fill="x")
        self.nb.pack(fill="both", expand=True)
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self.on_tab_changed())
        pages = []
        for title in ("1 Receiver", "2 Observe", "3 Maps", "4 Data & Export", "5 Settings"):
            page = tk.Frame(self.nb, bg=PAGE)
            self.nb.add(page, text=title)
            self.nav.add(page, title)
            pages.append(page)
        self.build_receiver_page(pages[0])
        self.build_observe_page(pages[1])
        self.build_maps_page(pages[2])
        self.build_data_page(pages[3])
        self.build_settings_page(pages[4])

    def icon(self, name):
        """Button image, or '' (no image) if it could not be loaded."""
        return self.icons.get(name, "")

    def on_tab_changed(self):
        try:
            self.nav.refresh()
        except Exception:
            pass
        self.force_redraw()
        self.polar_n_drawn = -2
        self.map_drawn = None
