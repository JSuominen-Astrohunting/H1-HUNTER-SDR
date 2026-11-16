"""H1-HUNTER SDR - small Tk widgets shared by the pages, in the designed look (see theme.py)."""
import tkinter as tk
from tkinter import ttk
from .common import *
from .theme import *

GREY_HINT = HINT
BOX_BG = CARD


class ScrollPanel(tk.Frame):
    """Fixed-width vertical panel that scrolls when its content is taller than the window."""

    def __init__(self, parent, width, bg=PAGE):
        super().__init__(parent, bg=bg, width=width)
        self.pack_propagate(False)
        self.grid_propagate(False)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.win, width=e.width))
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bind_all("<MouseWheel>", self._wheel, add="+")         # Windows / macOS
        self.bind_all("<Button-4>", self._wheel, add="+")           # Linux (X11): wheel up
        self.bind_all("<Button-5>", self._wheel, add="+")           # Linux (X11): wheel down

    def _wheel(self, e):
        """Scrolls this panel when the mouse is over it (all panels share the global binding)."""
        try:
            w = self.winfo_containing(e.x_root, e.y_root)
        except Exception:
            return
        while w is not None:
            if w is self:
                up = getattr(e, "num", 0) == 4 or getattr(e, "delta", 0) > 0
                self.canvas.yview_scroll(-1 if up else 1, "units")
                return
            w = getattr(w, "master", None)


class Hint:
    """Grey placeholder text inside an empty ttk.Entry (e.g. 'empty = estimate'). The entry's variable stays
    really empty, so the program still sees 'no value'."""

    def __init__(self, entry, var, text):
        self.entry, self.var = entry, var
        self.lab = tk.Label(entry, text=text, fg=HINT, bg=INPUT, font=F_MONO, anchor="w", cursor="xterm")
        self.lab.bind("<Button-1>", lambda e: self.entry.focus_set())
        entry.bind("<FocusIn>", lambda e: self.update(), add="+")
        entry.bind("<FocusOut>", lambda e: self.update(), add="+")
        var.trace_add("write", lambda *a: self.update())
        self.update()

    def set_text(self, text):
        if self.lab.cget("text") != text:
            self.lab.config(text=text)

    def update(self):
        try:
            focused = self.entry.focus_get() is self.entry
        except Exception:
            focused = False
        if self.var.get() == "" and not focused:
            self.lab.place(x=6, rely=0.5, anchor="w", relwidth=0.9)
        else:
            self.lab.place_forget()


def card(parent, title=None, pad=12, fill="x", expand=False, side=None, **pack):
    """A box of the design: dark card, thin border, small uppercase grey title. Returns the inner frame."""
    outer = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER)
    if side is None:
        outer.pack(fill=fill, expand=expand, padx=6, pady=6, **pack)
    else:
        outer.pack(side=side, fill=fill, expand=expand, padx=6, pady=6, **pack)
    inner = tk.Frame(outer, bg=CARD)
    inner.pack(fill="both", expand=True, padx=pad, pady=pad)
    if title:
        tk.Label(inner, text=title.upper(), bg=CARD, fg=MUTED, font=F_SEC, anchor="w").pack(fill="x", pady=(0, 8))
    inner.outer = outer
    return inner


def card_grid(parent, title=None, **kw):
    """Card whose content is a 2-column grid (label above input, as in the design)."""
    inner = card(parent, title, **kw)
    g = tk.Frame(inner, bg=CARD)
    g.pack(fill="x")
    g.columnconfigure(0, weight=1, uniform="c")
    g.columnconfigure(1, weight=1, uniform="c")
    g.card = inner
    return g


def readout(parent, title, col, value="-", row=0, compact=False):
    """Small box: grey title, value below (Receiver / Observe readout rows). Returns the value label.
    compact = a low box with smaller text (Observe: one row under the plot, so the plot gets the room)."""
    box = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
    box.grid(row=row, column=col, sticky="nsew", padx=2 if compact else 4, pady=2)
    parent.columnconfigure(col, weight=1, uniform="ro")
    px, py = (6, 2) if compact else (10, 8)
    head = tk.Label(box, text=title, bg=CARD, fg=MUTED, font=F_SEC if compact else F_SMALL, anchor="w")
    head.pack(fill="x", padx=px, pady=(py, 0))
    val = tk.Label(box, text=value, bg=CARD, fg=TEXT, font=F_MONO_S if compact else F_VAL, anchor="w")
    val.pack(fill="x", padx=px, pady=(0, py))
    val.title_label = head
    return val


def heading(parent, text, side="left", bg=CARD):
    lab = tk.Label(parent, text=text.upper(), bg=bg, fg=MUTED, font=F_SEC)
    lab.pack(side=side, padx=(2, 10))
    return lab


def tag(parent, text, colors=TAG_GREEN):
    return tk.Label(parent, text=text, bg=colors[0], fg=colors[1], font=F_SEC, padx=8, pady=2)


class NavBar(tk.Frame):
    """Page tabs of the design: grey text, the open page white on a darker field with a yellow underline."""

    def __init__(self, parent, notebook):
        super().__init__(parent, bg=NAV)
        self.nb = notebook
        self.items = []
        line = tk.Frame(parent, bg=BORDER, height=1)
        self.line = line

    def add(self, page, text):
        f = tk.Frame(self, bg=NAV, cursor="hand2")
        lab = tk.Label(f, text=text, bg=NAV, fg=TAB_TEXT, font=F_TAB, padx=18, pady=8)
        lab.pack()
        bar = tk.Frame(f, bg=NAV, height=3)
        bar.pack(fill="x")
        for w in (f, lab):
            w.bind("<Button-1>", lambda e, p=page: self.nb.select(p))
            w.bind("<Enter>", lambda e, l=lab, p=page: self._hover(l, p, True))
            w.bind("<Leave>", lambda e, l=lab, p=page: self._hover(l, p, False))
        self.items.append({"page": page, "frame": f, "label": lab, "bar": bar, "shown": True})
        self.repack()

    def _hover(self, lab, page, on):
        if str(page) != self.nb.select():
            lab.config(fg="#ffffff" if on else TAB_TEXT, bg="#1f2125" if on else NAV)

    def show(self, page, shown):
        for it in self.items:
            if it["page"] is page:
                it["shown"] = shown
        self.repack()

    def repack(self):
        for it in self.items:
            it["frame"].pack_forget()
        for it in self.items:
            if it["shown"]:
                it["frame"].pack(side="left")
        self.refresh()

    def refresh(self):
        cur = self.nb.select()
        for it in self.items:
            on = str(it["page"]) == cur
            it["label"].config(fg="#ffffff" if on else TAB_TEXT, bg=TAB_ON if on else NAV)
            it["frame"].config(bg=TAB_ON if on else NAV)
            it["bar"].config(bg=ACCENT if on else NAV)
