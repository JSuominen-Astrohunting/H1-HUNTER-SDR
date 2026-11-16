"""H1-HUNTER SDR - the look of the program (colours, fonts, ttk styles), taken from the designed page images.

Fonts: IBM Plex Sans / IBM Plex Mono (SIL Open Font License) are shipped in the fonts/ folder. On Windows they are
loaded privately for this program; on Linux the installer copies them to ~/.local/share/fonts. If they are missing,
the nearest system font is used.
"""
import os
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# ---- colours (from the design) ----
PAGE = "#0e0f11"        # window background
HEADER = "#16171a"      # top bar and status bar
NAV = "#131417"         # page tabs
TAB_ON = "#1b1c1f"
CARD = "#18191c"        # boxes
BORDER = "#2c2e33"
BTN = "#25272b"
BTN_BORDER = "#3a3d43"
BTN_HOVER = "#2f3237"
INPUT = "#0f1012"
INPUT_BORDER = "#34373c"
TEXT = "#e6e6e6"
MUTED = "#a9adb3"
HINT = "#7d828a"
TAB_TEXT = "#b4b8be"
ACCENT = "#ffcc00"
PLOT_BG = "#111214"
GREEN_BTN, GREEN_BORDER = "#23502e", "#2f6b3d"
GOLD_BTN, GOLD_BORDER = "#8a6a00", "#b38b00"
RED_BTN, RED_BORDER = "#b23a30", "#c9473c"
SEL_BTN, SEL_BORDER = "#2a3a4f", "#3f5a7a"
PICK_ON_BG, PICK_ON_FG = "#3a3110", "#ffe27a"
CHIP_GREEN = ("#1f3524", "#8fe0a2")
CHIP_GREY = ("#2a2b2f", "#c9ccd1")
CHIP_WARN = ("#3a2a10", "#ffcc66")
TAG_GREEN = ("#1f3524", "#8fe0a2")

FONT_FILES = ["IBMPlexSans-Regular.ttf", "IBMPlexSans-Medium.ttf", "IBMPlexSans-SemiBold.ttf", "IBMPlexSans-Bold.ttf",
              "IBMPlexMono-Regular.ttf", "IBMPlexMono-SemiBold.ttf", "IBMPlexMono-Bold.ttf"]

# named Tk fonts used by every widget
F_UI, F_UI_B, F_SMALL, F_SEC, F_TAB, F_TITLE, F_MONO, F_MONO_S, F_VAL, F_BTN, F_BTN_S = (
    "H1Ui", "H1UiBold", "H1Small", "H1Section", "H1Tab", "H1Title", "H1Mono", "H1MonoSmall", "H1Value", "H1Button",
    "H1ButtonSmall")


def load_private_fonts(app_folder):
    """Windows: makes the bundled fonts available to this program only (no installation, no admin rights).
    Must be called before the Tk window is created."""
    folder = os.path.join(app_folder, "fonts")
    if not sys.platform.startswith("win") or not os.path.isdir(folder):
        return
    try:
        import ctypes
        FR_PRIVATE = 0x10
        for f in FONT_FILES:
            p = os.path.join(folder, f)
            if os.path.exists(p):
                ctypes.windll.gdi32.AddFontResourceExW(p, FR_PRIVATE, 0)
    except Exception:
        pass


def plot_fonts(app_folder):
    """matplotlib can load the bundled fonts directly (all operating systems)."""
    try:
        from matplotlib import rcParams
        rcParams["axes.formatter.useoffset"] = False      # axes always show full values (1420.4, not 0.4 + offset)
    except Exception:
        pass
    try:
        from matplotlib import font_manager, rcParams
        folder = os.path.join(app_folder, "fonts")
        for f in FONT_FILES:
            p = os.path.join(folder, f)
            if os.path.exists(p):
                font_manager.fontManager.addfont(p)
        names = {f.name for f in font_manager.fontManager.ttflist}
        if "IBM Plex Sans" in names:
            rcParams["font.family"] = "IBM Plex Sans"
        if "IBM Plex Mono" in names:
            rcParams["font.monospace"] = ["IBM Plex Mono"] + list(rcParams["font.monospace"])
    except Exception:
        pass


_FONTS = []


def _pick(families, wanted):
    low = {f.lower(): f for f in families}
    for w in wanted:
        if w.lower() in low:
            return low[w.lower()]
    return wanted[-1]


def setup(root):
    """Creates the named fonts and all ttk styles. Call once after the Tk window exists."""
    fams = set(tkfont.families(root))
    sans = _pick(fams, ["IBM Plex Sans", "Segoe UI", "Cantarell", "DejaVu Sans", "Helvetica"])
    sans_semi = _pick(fams, ["IBM Plex Sans SemiBold", "IBM Plex Sans Smbld", sans])
    mono = _pick(fams, ["IBM Plex Mono", "Consolas", "DejaVu Sans Mono", "Courier"])
    semi_is_family = sans_semi != sans
    specs = {F_UI: (sans, 10, "normal"), F_UI_B: (sans_semi, 10, "normal" if semi_is_family else "bold"),
             F_SMALL: (sans, 9, "normal"), F_SEC: (sans, 8, "bold"),
             F_TAB: (sans_semi, 11, "normal" if semi_is_family else "bold"),
             F_TITLE: (sans, 13, "bold"), F_MONO: (mono, 10, "normal"), F_MONO_S: (mono, 9, "normal"),
             F_VAL: (mono, 11, "bold"), F_BTN: (sans_semi, 10, "normal" if semi_is_family else "bold"),
             F_BTN_S: (sans_semi, 9, "normal" if semi_is_family else "bold")}
    for name, (fam, size, weight) in specs.items():
        try:
            f = tkfont.Font(root, name=name, family=fam, size=size, weight=weight, exists=False)
        except tk.TclError:
            f = tkfont.nametofont(name)
            f.configure(family=fam, size=size, weight=weight)
        _FONTS.append(f)          # keep a reference: Tk deletes a named font when its Python object is freed
    root.option_add("*Font", F_UI)
    root.option_add("*TCombobox*Listbox.background", INPUT)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", SEL_BTN)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox.font", F_MONO)
    root.configure(bg=PAGE)

    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure(".", background=CARD, foreground=TEXT, fieldbackground=INPUT, bordercolor=BORDER,
                 lightcolor=CARD, darkcolor=CARD, troughcolor=INPUT, focuscolor=ACCENT, font=F_UI,
                 selectbackground=SEL_BTN, selectforeground="#ffffff", insertcolor=TEXT)
    # buttons
    def button(name, bg, border, fg=TEXT, hover=None, font=F_BTN, pad=(12, 8)):
        st.configure(name, background=bg, foreground=fg, bordercolor=border, lightcolor=bg, darkcolor=bg,
                     focusthickness=0, focuscolor=bg, padding=pad, font=font, relief="flat", anchor="center")
        st.map(name, background=[("disabled", "#1d1f22"), ("pressed", border), ("active", hover or BTN_HOVER)],
               foreground=[("disabled", "#6b6f75")], bordercolor=[("active", ACCENT if bg == BTN else border)],
               lightcolor=[("active", hover or BTN_HOVER)], darkcolor=[("active", hover or BTN_HOVER)])
    button("TButton", BTN, BTN_BORDER)
    button("Small.TButton", BTN, BTN_BORDER, font=F_BTN_S, pad=(10, 4))
    button("Tiny.TButton", BTN, BTN_BORDER, font=F_BTN_S, pad=(4, 4))
    button("Run.TButton", GREEN_BTN, GREEN_BORDER, "#ffffff", "#2b6038")
    button("Stop.TButton", RED_BTN, RED_BORDER, "#ffffff", "#c24438")
    button("Pkg.TButton", GOLD_BTN, GOLD_BORDER, "#ffffff", "#9c7900")
    button("Done.TButton", GREEN_BTN, GREEN_BORDER, "#ffffff", "#2b6038")
    button("Busy.TButton", GOLD_BTN, GOLD_BORDER, "#ffffff", "#9c7900")
    button("Sel.TButton", SEL_BTN, SEL_BORDER, "#ffffff", "#33475f")
    button("Left.TButton", BTN, BTN_BORDER)
    st.configure("Left.TButton", anchor="w")
    for name in ("Done.TButton", "Busy.TButton", "Run.TButton"):
        st.configure(name, anchor="w")
    st.configure("Run.TButton", anchor="center")
    # inputs
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        st.configure(name, fieldbackground=INPUT, foreground=TEXT, bordercolor=INPUT_BORDER, lightcolor=INPUT,
                     darkcolor=INPUT, insertcolor=TEXT, padding=5, arrowcolor=MUTED, background=BTN)
        st.map(name, fieldbackground=[("readonly", INPUT), ("disabled", "#15161a")],
               foreground=[("disabled", "#6b6f75")], bordercolor=[("focus", ACCENT)],
               lightcolor=[("focus", INPUT)], selectbackground=[("readonly", INPUT)],
               selectforeground=[("readonly", TEXT)])
    # check / radio
    for name in ("TCheckbutton", "TRadiobutton"):
        st.configure(name, background=CARD, foreground=TEXT, indicatorbackground=INPUT, indicatorforeground=ACCENT,
                     indicatormargin=(0, 0, 8, 0), padding=2, font=F_UI)
        st.map(name, background=[("active", CARD)], foreground=[("disabled", "#6b6f75")],
               indicatorbackground=[("selected", INPUT), ("disabled", "#15161a")])
    st.configure("Grp.TCheckbutton", background=INPUT)
    st.map("Grp.TCheckbutton", background=[("active", INPUT)])
    st.configure("Page.TCheckbutton", background=PAGE)
    st.map("Page.TCheckbutton", background=[("active", PAGE)])
    # notebook: the tabs are drawn by the program itself (with the yellow underline), so ttk's tabs are hidden
    st.configure("TNotebook", background=PAGE, borderwidth=0, tabmargins=0, padding=0)
    st.layout("Hidden.TNotebook.Tab", [])
    st.configure("Hidden.TNotebook", background=PAGE, borderwidth=0)
    st.layout("Hidden.TNotebook", [("Notebook.client", {"sticky": "nswe"})])
    # scrollbars, progress, scale
    st.configure("Vertical.TScrollbar", background=BTN, troughcolor=CARD, bordercolor=CARD, arrowcolor=MUTED,
                 lightcolor=BTN, darkcolor=BTN, gripcount=0)
    st.map("Vertical.TScrollbar", background=[("active", BTN_HOVER)])
    st.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=INPUT, bordercolor=INPUT_BORDER,
                 lightcolor=ACCENT, darkcolor=ACCENT, thickness=10)
    st.configure("Horizontal.TScale", background=BTN, troughcolor=INPUT, bordercolor=INPUT_BORDER,
                 lightcolor=BTN, darkcolor=BTN)
    st.configure("Treeview", background=INPUT, fieldbackground=INPUT, foreground=TEXT, rowheight=22, font=F_SMALL,
                 bordercolor=BORDER)
    st.map("Treeview", background=[("selected", SEL_BTN)], foreground=[("selected", "#ffffff")])
    st.configure("Treeview.Heading", background=BTN, foreground=TEXT, font=F_BTN_S, bordercolor=BORDER,
                 lightcolor=BTN, darkcolor=BTN)
    st.configure("Data.TLabel", font=F_MONO, background=CARD, foreground="#8fe0a2")
    return {"sans": sans, "mono": mono}
