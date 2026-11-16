"""H1-HUNTER SDR v1.0 - start file.

Starts the program window. The installer ("Install H1-HUNTER SDR") installs Python and every library;
after that the program works fully offline.
    python h1_hunter_sdr.py             start the program
    python h1_hunter_sdr.py --check     self-check (libraries, receiver library, simulator) - used by the installer
"""
import os
import sys
import socket

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)


# --- HARD OFFLINE GUARD: the program never uses the network. The only exception is THIS computer (localhost),
# used on Linux to read the mount position from the INDI server that KStars / Ekos runs. ---
_LOCAL = ("localhost", "127.0.0.1", "::1")
_orig_getaddrinfo = socket.getaddrinfo
_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex


def _blocked():
    return OSError("H1-HUNTER SDR works offline only: network access is blocked (only this computer is allowed).")


def _local_only(address):
    host = address[0] if isinstance(address, tuple) else address
    return isinstance(host, str) and host in _LOCAL


def _getaddrinfo(host, *args, **kwargs):
    if host not in _LOCAL:
        raise _blocked()
    return _orig_getaddrinfo(host, *args, **kwargs)


def _connect(self, address):
    if not _local_only(address):
        raise _blocked()
    return _orig_connect(self, address)


def _connect_ex(self, address):
    if not _local_only(address):
        raise _blocked()
    return _orig_connect_ex(self, address)


socket.getaddrinfo = _getaddrinfo
socket.socket.connect = _connect
socket.socket.connect_ex = _connect_ex

REQUIRED = {"numpy": "numpy", "scipy": "scipy", "matplotlib": "matplotlib", "astropy": "astropy"}
OPTIONAL = {"rtlsdr": "pyrtlsdr (RTL-SDR receiver)", "serial": "pyserial (reference load relay)"}
if sys.platform.startswith("win"):
    OPTIONAL["win32com"] = "pywin32 (ASCOM mount, Windows)"


def missing_libraries():
    out = []
    for mod, pip_name in REQUIRED.items():
        try:
            __import__(mod)
        except Exception:
            out.append(pip_name)
    return out


def self_check():
    """Checks everything the installer has installed. Prints a report; exit code 0 = OK."""
    ok = True
    print("H1-HUNTER SDR self-check")
    print(f"  Python {sys.version.split()[0]}")
    for mod, name in REQUIRED.items():
        try:
            m = __import__(mod)
            print(f"  OK   {name} {getattr(m, '__version__', '')}")
        except Exception as e:
            ok = False
            print(f"  FAIL {name}: {e}")
    try:
        import tkinter
        tkinter.Tcl()
        print("  OK   tkinter (window library)")
    except Exception as e:
        ok = False
        print(f"  FAIL tkinter: {e}")
    for mod, name in OPTIONAL.items():
        try:
            __import__(mod)
            print(f"  OK   {name}")
        except Exception as e:
            print(f"  --   {name} not available: {str(e).splitlines()[0][:100]}")
    if ok:
        try:
            import time
            import numpy as np
            from h1h.receiver import Averager, Receiver, SimSource, list_rtl_devices, RTL_ERROR
            from h1h.physics import sky_position
            av = Averager()
            cfg = {"target_hz": 1420405752.0, "offset_hz": 0.0, "fs": 2.4e6, "gain": 29.7, "ppm": 0.0, "bias": True,
                   "nfft": 512, "window": "Hann", "overlap": True, "dc_remove": True, "notch_hz": 3e3,
                   "offset_tuning": False}
            rx = Receiver(SimSource(), cfg, av)
            rx.start()
            time.sleep(2.0)
            rx.running = False
            rx.join(3.0)
            snap = av.snapshot()
            assert rx.error is None, rx.error
            assert snap["counts"] > 1000, "simulator produced no spectra"
            print(f"  OK   simulator: {snap['counts']:,} spectra in 2 s")
            s = sky_position(180.0, 45.0, 61.485, 21.797, 15.0, time.time())
            print(f"  OK   sky position (offline): l {s['l_deg']:.1f}, b {s['b_deg']:+.1f}")
            devs = list_rtl_devices()
            if devs:
                print(f"  OK   RTL-SDR found: {', '.join(devs)}")
            elif RTL_ERROR:
                print(f"  --   RTL-SDR library: {RTL_ERROR.splitlines()[0][:120]}")
            else:
                print("  --   no RTL-SDR plugged in" + (" (or the WinUSB driver is missing - install it with Zadig)"
                                                   if sys.platform.startswith("win") else
                                                   " (or the DVB-T driver still holds it - unplug and plug in again)"))
        except Exception as e:
            ok = False
            print(f"  FAIL program test: {type(e).__name__}: {e}")
    print("RESULT: OK" if ok else "RESULT: PROBLEMS FOUND")
    return ok


def crash_log_path():
    """<Documents>/H1-HUNTER SDR/crash_log.txt (next to the data folder), else the home folder."""
    home = os.path.expanduser("~")
    docs = os.path.join(home, "Documents")
    return os.path.join(docs if os.path.isdir(docs) else home, "H1-HUNTER SDR", "crash_log.txt")


def start_crash_log():
    """Writes the reason of a crash to crash_log.txt: Python errors in the window, in background threads and at
    start-up, and - with faulthandler - also hard crashes inside the receiver library (e.g. the USB stick
    disconnected). Returns the open file (kept open for faulthandler) or None."""
    import faulthandler
    import threading
    import traceback
    import datetime
    path = crash_log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.isfile(path) and os.path.getsize(path) > 2_000_000:      # keep the log small
            os.replace(path, path + ".old")
        fh = open(path, "a", encoding="utf-8", buffering=1)
    except OSError:
        return None

    def stamp():
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def write(kind, et, ev, tb):
        try:
            fh.write(f"\n===== {stamp()}  {kind}  (H1-HUNTER SDR v1.0, Python {sys.version.split()[0]}, "
                     f"{sys.platform})\n")
            fh.write("".join(traceback.format_exception(et, ev, tb)))
            fh.flush()
        except Exception:
            pass

    fh.write(f"\n----- {stamp()}  program started\n")
    faulthandler.enable(file=fh, all_threads=True)          # hard crashes (segmentation fault, abort)
    prev = sys.excepthook

    def hook(et, ev, tb):
        write("ERROR", et, ev, tb)
        prev(et, ev, tb)
    sys.excepthook = hook
    prev_t = threading.excepthook

    def thook(args):
        write(f"ERROR in thread {getattr(args.thread, 'name', '?')}", args.exc_type, args.exc_value,
              args.exc_traceback)
        prev_t(args)
    threading.excepthook = thook
    start_crash_log.write = write
    return fh


def main():
    if "--check" in sys.argv:
        sys.exit(0 if self_check() else 1)
    missing = missing_libraries()
    if missing:
        msg = ("These libraries are missing: " + ", ".join(missing) +
               "\n\nRun 'Install H1-HUNTER SDR' again (internet is needed once).")
        try:
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror("H1-HUNTER SDR", msg)
        except Exception:
            print(msg)
        sys.exit(1)
    import matplotlib
    matplotlib.use("TkAgg")
    import tkinter as tk
    from h1h.app import App
    from h1h.theme import load_private_fonts
    load_private_fonts(APP_DIR)                      # Windows: the bundled IBM Plex fonts, for this program only
    settings_folder = os.environ.get("H1HUNTER_SETTINGS") or APP_DIR
    log = start_crash_log()
    root = tk.Tk()
    if log is not None:
        prev_cb = root.report_callback_exception

        def tk_error(et, ev, tb):                        # errors in button and timer callbacks of the window
            start_crash_log.write("ERROR in the window", et, ev, tb)
            prev_cb(et, ev, tb)
        root.report_callback_exception = tk_error
    App(root, app_folder=APP_DIR, settings_folder=settings_folder)
    if "--fullscreen" in sys.argv:                  # Raspberry Pi autostart: the program fills the screen
        def full(on):
            try:
                root.attributes("-fullscreen", on)
            except Exception:
                pass
        root.after(300, lambda: full(True))
        root.bind("<F11>", lambda e: full(not bool(root.attributes("-fullscreen"))))
        root.bind("<Escape>", lambda e: full(False))
    root.mainloop()
    if log is not None:
        try:
            log.write(f"----- program closed normally\n")
        except Exception:
            pass


if __name__ == "__main__":
    main()
