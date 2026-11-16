"""H1-HUNTER SDR - shared constants and small helpers."""
import os
import re
import sys
import time
import numpy as np

APP = "H1-HUNTER SDR"
VERSION = "1.0"
IS_WINDOWS = sys.platform.startswith("win")
def _pi_model():
    """'Raspberry Pi 4 Model B Rev 1.4' etc. on a Raspberry Pi, else ''."""
    try:
        with open("/proc/device-tree/model", "rb") as fh:
            m = fh.read().decode("ascii", "ignore").strip("\x00 \n")
        return m if m.startswith("Raspberry Pi") else ""
    except Exception:
        return ""


PI_MODEL = "" if IS_WINDOWS else _pi_model()
IS_PI = bool(PI_MODEL)
MOUNT_SYS = "ASCOM" if IS_WINDOWS else "INDI"   # guided pointing: ASCOM on Windows, INDI (KStars / Ekos) on Linux
BG = "#111214"           # plot background (theme.PLOT_BG)
PANEL = "#0e0f11"        # page background (theme.PAGE)
FG = "#e6e6e6"           # text (theme.TEXT)
OK_COL, WARN_COL, ERR_COL, INFO_COL = "#8fe0a2", "#ffb74d", "#ff8a80", "#7cc4ff"
H1_HZ = 1420405752.0
H1_MHZ = H1_HZ / 1e6
C_KMS = 299792.458

# Cold sky model - identical to H1-Hunter v1
SOLAR_SPEED_KMS = 20.0   # standard solar motion relative to the LSR
APEX_RA_DEG = 270.95934  # solar apex J2000
APEX_DEC_DEG = 30.00467
T_CMB = 2.725            # K
T_SPILL = 3.0            # K, ground spillover at the zenith (the altitude dependence is computed automatically)
COLD_GOOD_ALT = 45.0     # Cold locked is shown in yellow below this altitude ...
COLD_GOOD_B = 30.0       # ... or closer than this to the Galactic plane (|b|)
T_GAL_HIGH = 0.7         # K, Galactic continuum at 1420 MHz at high latitude
T_GAL_PLANE = 4.0        # K, extra continuum towards the plane (decays with |b|, scale 8 deg)
TAU_ZENITH_SL = 0.0075   # Np at sea level
ATM_SCALE_HEIGHT_M = 8400.0
DEFAULT_AZ, DEFAULT_ALT = 180.0, 45.0
DEFAULT_DISH_M = 0.9
COLD_MAX_AGE_MIN = 60.0  # min: older Cold gives a warning (receiver gain drifts with temperature)
RELAY_ON_CMD = b"\xA0\x01\x01\xA2"     # LCUS-1 / CH340 USB serial relay (same commands as ezRA ezSerRelay.py)
RELAY_OFF_CMD = b"\xA0\x01\x00\xA1"
RELAY_SETTLE_S = 0.5     # s: spectra right after a relay switch are not used
SIM_RELAY = "Simulator relay"
SIM_GAIN_DRIFT = 0.03    # simulator with relay: slow receiver gain drift (fraction, period 20 min) to test the correction

SAMPLE_RATES_MSPS = ["1.024", "1.4", "1.8", "2.048", "2.4", "2.56"]
FFT_SIZES = ["256", "512", "1024", "2048", "4096", "8192", "16384", "32768", "65536"]
RF_NFFT = 16384               # fixed FFT of the RF spectrum / waterfall view (independent of the hydrogen-line FFT)
RF_VIEW_SAMPLES = 65536       # samples of every block used for the RF view (display only)
RF_WINDOW = "Blackman-Harris" # low side lobes: weak narrow interference stays visible next to strong signals
WINDOW_NAMES = ["Hann", "Blackman-Harris", "Hamming", "Rectangular"]
R820T_GAINS = [0.0, 0.9, 1.4, 2.7, 3.7, 7.7, 8.7, 12.5, 14.4, 15.7, 16.6, 19.7, 20.7, 22.9, 25.4, 28.0, 29.7,
               32.8, 33.8, 36.4, 37.2, 38.6, 40.2, 42.1, 43.4, 43.9, 44.5, 48.0, 49.6]
AVG_MODES = ["Count (stop at N)", "Continuous", "Exponential (view only)"]
DISPLAY_MODES = ["Antenna temperature (K)", "Line temperature (K)", "Ratio to Cold", "Raw power",
                 "Line brightness T_B (K)"]
D_TANT, D_TLINE, D_RATIO, D_RAW, D_TB = DISPLAY_MODES
X_AXES = ["Frequency (MHz)", "Velocity, topocentric", "Velocity, LSR"]
X_LABELS = {"Frequency (MHz)": "Frequency (MHz)", "Velocity, topocentric": "Velocity, topocentric (km/s, radio)",
            "Velocity, LSR": "Velocity, LSR (km/s, radio)"}
SIG_KEYS = ("target_hz", "offset_hz", "fs", "gain", "ppm", "bias", "nfft", "window", "overlap", "dc_remove",
            "notch_hz", "offset_tuning")

SIM_DEVICE = "Simulator (no hardware)"
SIM_PPM_ERROR = 0.5           # crystal error of the simulated stick (TCXO class)
SIM_TRX = 100.0               # K, simulated receiver noise temperature

BLOCK_SAMPLES = 262144        # samples read per block (~0.11 s at 2.4 MS/s)
STREAM_QUEUE_BLOCKS = 96      # blocks waiting for processing (~10 s at 2.4 MS/s) before any are dropped
IQ_WRITE_QUEUE_BLOCKS = 128   # raw IQ blocks waiting for the disk (~14 s, 64 MB) before any are lost
MAX_PLOT_POINTS = 4096
WF_COLS, WF_ROWS = 512, 150
POLL_MS = 100                  # GUI check interval; drawing itself is limited separately (RF 3/s, H1 1/s)
H1_REDRAW_S = 1.0             # hydrogen line view: at most one redraw per second (keeps processor time for the data)
RF_REDRAW_S = 0.5             # RF spectrum view: two updates per second (data are processed regardless)
FLAG_REF_FRAMES = 2048        # frames in the running reference used for flagging (~2 s)
N_SUBBANDS = 16
MAX_CAND_SPECTRA = 400        # suspicious frames stored as full spectra; beyond this they are summed
MAX_FRAME_LOG = 8_000_000
# Spectral kurtosis (SK) flagging, optional (Settings -> RFI filter). Per channel and per block of SK_M frames:
# SK = (M+1)/(M-1) * (M*S2/S1^2 - 1), S1 = sum P, S2 = sum P^2 (Nita & Gary 2010; d = 1). Gaussian noise (and the
# hydrogen line, which is Gaussian noise too) gives SK = 1, sd = sqrt(4M^2/((M-1)(M+2)(M+3))) = 0.0624 for M = 1024.
# Bounds = Pearson type III quantiles 0.135 % / 99.865 % (the false-alarm rate of 3 sigma on each side); checked by
# simulation through this program's windowed, 50 % overlapping FFT (0.833 / 1.215).
SK_M = 1024
SK_LOW, SK_HIGH = 0.836, 1.214


def utc_str(ts=None):
    if ts is None:
        return "-"
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + f".{int((ts % 1) * 10)}Z"


def fmt(v):
    if isinstance(v, float):
        return f"{v:.10g}"
    return str(v)


def atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)                     # readers never see a half-written file


def next_multi_path(base):
    folder, name = os.path.split(base)
    stem, ext = os.path.splitext(name)
    ext = ext or ".csv"
    pat = re.compile("^" + re.escape(stem) + r"_(\d+)" + re.escape(ext) + "$", re.IGNORECASE)
    nums = [int(m.group(1)) for f in os.listdir(folder or ".") for m in [pat.match(f)] if m]
    return os.path.join(folder, f"{stem}_{(max(nums) + 1) if nums else 1:04d}{ext}")

