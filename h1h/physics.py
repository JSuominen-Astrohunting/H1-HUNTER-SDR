"""H1-HUNTER SDR - signal processing and physics: FFT, baseline, noise, calibration model, sky position, LSR."""
import math
import time
import numpy as np
from .common import *
try:
    from scipy import fft as _sfft         # keeps single precision and can use several cores
except Exception:
    _sfft = None

ASTRO_ERROR = None
try:
    from astropy.coordinates import SkyCoord, EarthLocation, AltAz
    from astropy.time import Time
    import astropy.units as u
    from astropy.utils import iers
    # Must work without internet: never download IERS tables, use the bundled ones (same as H1-Hunter).
    for _name, _val in (("auto_download", False), ("auto_max_age", None), ("iers_degraded_accuracy", "ignore")):
        try:
            setattr(iers.conf, _name, _val)
        except Exception:
            pass
except Exception as _e:
    ASTRO_ERROR = f"{type(_e).__name__}: {_e}"


_APEX_VEC = np.array([np.cos(np.radians(APEX_DEC_DEG)) * np.cos(np.radians(APEX_RA_DEG)),
                      np.cos(np.radians(APEX_DEC_DEG)) * np.sin(np.radians(APEX_RA_DEG)),
                      np.sin(np.radians(APEX_DEC_DEG))])


def make_window(name, n):
    k = np.arange(n)
    x = 2.0 * np.pi * k / n
    if name == "Rectangular":
        return np.ones(n)
    if name == "Hann":
        return 0.5 - 0.5 * np.cos(x)
    if name == "Hamming":
        return 0.54 - 0.46 * np.cos(x)
    return 0.35875 - 0.48829 * np.cos(x) + 0.14128 * np.cos(2 * x) - 0.01168 * np.cos(3 * x)


def frame_powers(x, nfft, win, hop):
    """Power spectrum of every frame (frames start every `hop` samples), DC in the middle. Shape (frames, nfft).
    Scaled so that a full-scale complex tone gives 1.0 (0 dBFS).
    32-bit FFT (as in other SDR programs; far more precise than the 8-bit samples), on all processor cores when
    scipy is available. The averages are summed in 64 bit (see Averager)."""
    if len(x) < nfft:
        return np.empty((0, nfft), dtype=np.float32)
    x = np.asarray(x, dtype=np.complex64)
    win = np.asarray(win, dtype=np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(x, nfft)[::hop]
    even = nfft % 2 == 0
    # DC in the middle: for an even FFT size the window is multiplied by (-1)^n, which moves the spectrum by exactly
    # nfft/2 channels (the same result as fftshift, without an extra copy of every frame)
    w = (win * np.where(np.arange(nfft) % 2 == 0, 1.0, -1.0).astype(np.float32)) if even else win
    if _sfft is not None:
        X = _sfft.fft(frames * w, axis=1, workers=-1, overwrite_x=True)
    else:
        X = np.fft.fft(frames * w, axis=1).astype(np.complex64)
    if not even:
        X = np.fft.fftshift(X, axes=1)
    P = X.real * X.real
    P += X.imag * X.imag
    P *= np.float32(1.0 / float(win.sum()) ** 2)
    return P


def noise_model(win, hop, lag=1):
    """Gaussian-noise statistics of the averaged spectrum for this window and frame step.
    Returns (v1, corr): relative variance per bin of an average of K frames is v1 / K; corr = correlation
    between bins `lag` apart (used by the radiometer check)."""
    n = len(win)
    s2 = float(np.sum(win ** 2)) ** 2

    def rho(L, j):
        sh = j * hop
        if sh >= n:
            return 0.0
        prod = win[:n - sh] * win[sh:]
        return abs(np.sum(prod * np.exp(-2j * np.pi * L * np.arange(n - sh) / n))) ** 2 / s2

    J = int(math.ceil(n / hop))
    v1 = 1.0 + 2.0 * sum(rho(0, j) for j in range(1, J))
    c1 = rho(lag, 0) + 2.0 * sum(rho(lag, j) for j in range(1, J))
    return v1, c1 / v1


def notch_center(p, center, half):
    """Replaces bins center-half..center+half (last axis) by a straight line between the neighbour levels."""
    n = p.shape[-1]
    if half <= 0 or n < 2 * half + 12:
        return p
    lo, hi = center - half, center + half
    left = p[..., lo - 4:lo].mean(axis=-1)
    right = p[..., hi + 1:hi + 5].mean(axis=-1)
    t = (np.arange(lo, hi + 1) - (lo - 1)) / float(hi + 2 - lo)
    p[..., lo:hi + 1] = left[..., None] * (1.0 - t) + right[..., None] * t
    return p


def shift_bins(offset_hz, fs, n):
    """Whole channels by which a hydrogen-line spectrum is moved so that the centre frequency stays in the middle
    when the stick is tuned LO offset away from it (the exact frequency of every channel stays on the axis)."""
    return int(round(-float(offset_hz) / (float(fs) / n)))


def trusted_mask(axis_hz, sig):
    """Trusted channels, from the spectrum's own frequency axis and the receiver settings: within 40 % of the sample
    rate from the frequency the stick was really tuned to (channels moved in by the LO-offset shift were never
    measured) and away from the DC notch. With offset tuning the DC spike is outside the band and the residual DC is
    removed, so nothing is cut in the middle."""
    axis_hz = np.asarray(axis_hz, dtype=float)
    n = len(axis_hz)
    if not sig:
        return valid_mask(n, 0)
    g = dict(zip(SIG_KEYS, sig))
    fs = float(g["fs"])
    df = fs / n
    lo = (g["target_hz"] + g["offset_hz"]) * (1.0 + (g["ppm"] - round(g["ppm"])) * 1e-6)
    d = np.abs(axis_hz - lo)
    mask = d < 0.4 * fs
    if not g.get("offset_tuning"):
        nb = int(round(g["notch_hz"] / df)) if g["notch_hz"] > 0 else 0
        mask &= d > (nb + 2.5) * df
    return mask


def band_note(axis_hz, mask, manual_half):
    """Short note when the hydrogen line window (with 0.2 MHz baseline beside it) does not fit in the trusted band,
    else None. manual_half = 'Line +- MHz' (None = automatic)."""
    f = np.asarray(axis_hz, dtype=float)[mask] / 1e6
    if f.size == 0:
        return "No trusted channels"
    gap = min(H1_MHZ - float(f.min()), float(f.max()) - H1_MHZ)
    kms = C_KMS / H1_MHZ
    if manual_half is not None:
        if gap >= manual_half + 0.2:
            return None
        return f"Line +-{manual_half:g} MHz does not fit: band reaches only {gap:.2f} MHz (~{gap * kms:.0f} km/s)"
    half = resolve_half_mhz(axis_hz, mask, None)
    if half >= 0.4:
        return None
    return f"Line window only +-{half:.2f} MHz (~{half * kms:.0f} km/s)"


def valid_mask(n, notch_bins):
    """Bins that are trusted: central 80 % of the band, outside the DC notch."""
    k = np.abs(np.arange(n) - n // 2)
    return (k < 0.4 * n) & (k > notch_bins + 2)


def smooth(y, n):
    n = int(n)
    if n <= 1 or len(y) < n:
        return y
    pad = n // 2
    yp = np.concatenate([np.full(pad, y[0]), y, np.full(n - 1 - pad, y[-1])])
    return np.convolve(yp, np.ones(n) / n, mode="valid")


def downsample(y, m):
    n = len(y)
    if n <= m:
        return y
    k = int(math.ceil(n / m))
    n2 = (n // k) * k
    return y[:n2].reshape(-1, k).mean(axis=1)


def downsample_max(y, m):
    """Like downsample() but keeps the highest value of each group, so narrow spikes stay visible (RF view)."""
    n = len(y)
    if n <= m:
        return y
    k = int(math.ceil(n / m))
    n2 = (n // k) * k
    return y[:n2].reshape(-1, k).max(axis=1)


def to_db(p):
    return 10.0 * np.log10(np.maximum(p, 1e-30))


def freq_to_vtopo(f_hz):
    """Radio-definition velocity (km/s, topocentric) relative to the H1 rest frequency."""
    return C_KMS * (H1_HZ - np.asarray(f_hz, dtype=float)) / H1_HZ


def resolve_half_mhz(axis_hz, mask, manual):
    """Line half width (MHz). None = automatic: up to 0.8 MHz, narrowed so that baseline channels remain."""
    if manual is not None:
        return float(manual)
    f = axis_hz[mask] / 1e6
    gap = min(H1_MHZ - float(f.min()), float(f.max()) - H1_MHZ)
    return float(min(0.8, max(0.2, gap - 0.35)))


COLD_SMOOTH_MEDIAN_HZ = 25e3      # running median: removes narrow RFI spikes of the Cold reference
COLD_SMOOTH_SG_HZ = 60e3          # then a Savitzky-Golay fit (cubic): removes the noise, keeps the bandpass shape


def smooth_reference(axis_hz, p, mask):
    """Bandpass ('M curve') model from the Cold spectrum: keeps the shape of the receiver (a local cubic fit follows
    it without bias), removes narrow RFI spikes and most of the noise of the Cold spectrum. Wider features of the
    Cold sky (its own hydrogen) stay, so Cold should still be taken far from the Milky Way.
    Channels outside the trusted band are kept as they are."""
    from scipy.ndimage import median_filter
    from scipy.signal import savgol_filter
    p = np.asarray(p, dtype=np.float64)
    out = p.copy()
    if mask.sum() < 50 or len(axis_hz) < 50:
        return out
    df = float(abs(axis_hz[1] - axis_hz[0]))
    idx = np.arange(len(p))
    good = mask & np.isfinite(p) & (p > 0)
    work = np.interp(idx, idx[good], p[good])                 # untrusted channels filled for the filters only
    spk = cold_spikes(work, good)                             # narrow spikes out first: they must not spread
    if spk.any():
        work = np.interp(idx, idx[good & ~spk], work[good & ~spk])
    work = median_filter(work, size=max(3, int(round(COLD_SMOOTH_MEDIAN_HZ / df)) | 1), mode="nearest")
    ws = max(7, int(round(COLD_SMOOTH_SG_HZ / df)) | 1)
    if ws < len(work):
        work = savgol_filter(work, ws, 3, mode="interp")
    out[mask] = np.maximum(work[mask], 1e-30)
    return out


def cold_spikes(y, good):
    """Narrow spikes / dips (<= RFI_MAX_WIDTH channels, > RFI_SIGMA) in a Cold spectrum, widened by one channel.
    The noise is measured from second differences, so the bandpass slope does not count as noise.
    Wider features (the receiver shape, the Cold sky's own hydrogen) are never marked."""
    from scipy.ndimage import median_filter, binary_dilation
    y = np.asarray(y, dtype=np.float64)
    out = np.zeros(len(y), dtype=bool)
    if good.sum() < 50:
        return out
    base = median_filter(y, size=7, mode="nearest")
    rel = y / np.maximum(base, 1e-30) - 1.0
    d2 = (y[:-2] - 2.0 * y[1:-1] + y[2:]) / np.maximum(y[1:-1], 1e-30)
    g2 = good[:-2] & good[1:-1] & good[2:]
    if g2.sum() < 30:
        return out
    d = d2[g2]
    sig = 1.4826 * float(np.median(np.abs(d - np.median(d)))) / math.sqrt(6.0)
    if not sig > 0:
        return out
    cand = good & (np.abs(rel) > RFI_SIGMA * sig)
    ok = _rfi_runs_ok(cand, np.sign(rel), RFI_MAX_WIDTH)
    return binary_dilation(ok, iterations=1) & good


def parse_ranges_mhz(text):
    """'1419.64-1419.70, 1420.78-1420.82' (also decimal commas) -> [(lo, hi), ...] in MHz."""
    import re
    out = []
    for a, b in re.findall(r"(\d+(?:[.,]\d+)?)\s*[-–]\s*(\d+(?:[.,]\d+)?)", text or ""):
        lo, hi = sorted((float(a.replace(",", ".")), float(b.replace(",", "."))))
        if hi > lo:
            out.append((lo, hi))
    return out


def excluded_channels(axis_hz, ranges):
    """Channels inside the user's excluded frequency ranges (MHz). Boolean array."""
    f = np.asarray(axis_hz, dtype=np.float64) / 1e6
    out = np.zeros(len(f), dtype=bool)
    for lo, hi in ranges:
        out |= (f >= lo) & (f <= hi)
    return out


RFI_SIGMA = 6.0          # narrow RFI channel: this many sigma above / below the local level ...
RFI_SHARE = 0.7          # ... and most of its excess is narrow (a broad hydrogen line fails this test)
RFI_MAX_WIDTH = 3        # channels; wider features are never removed (the hydrogen line is much wider)
RFI_KEEP_FRACTION = 2.0 / 3.0   # hysteresis: a channel found at n sigma is released only below 2/3 of it (6 -> 4)
RFI_KMS_DEFAULT, RFI_KMS_MIN, RFI_KMS_MAX = 3.0, 1.0, 5.0   # Settings slider: widest RFI feature in km/s
# RFI filter modes (Settings): widest feature in km/s (None = 1 channel only) and the threshold in sigma
RFI_MODES = {"Conservative": (None, 8.0), "Normal": (3.0, 6.0), "Aggressive": (5.0, 5.0), "Custom": ("slider", 6.0)}
RFI_MODE_DEFAULT = "Normal"


def rfi_width_channels(kms, chan_kms):
    """Widest RFI feature in channels: the slider value in km/s, at least 1 and at most RFI_MAX_WIDTH channels."""
    kms = min(max(float(kms), RFI_KMS_MIN), RFI_KMS_MAX)
    return int(max(1, min(RFI_MAX_WIDTH, math.floor(kms / max(chan_kms, 1e-6) + 1e-6))))


def _rfi_runs_ok(cand, sgn, max_width):
    """Keeps the candidate runs that look like RFI: one narrow spike or dip (<= max_width channels), or a narrow
    up/down pair next to each other (a spike that moved a little in frequency: each part <= max_width).
    Anything wider is left alone - it could be hydrogen."""
    from scipy.ndimage import label
    lab, nl = label(cand)
    ok = np.zeros(len(cand), dtype=bool)
    for k in range(1, nl + 1):
        idx = np.flatnonzero(lab == k)
        s = sgn[idx]
        parts = np.flatnonzero(np.diff(s) != 0) + 1          # where the sign changes inside the run
        widths = np.diff(np.concatenate([[0], parts, [len(idx)]]))
        if len(widths) == 1 and widths[0] <= max_width:
            ok[idx] = True
        elif len(widths) == 2 and widths.max() <= max_width:  # up + down (or down + up) side by side
            ok[idx] = True
    return ok


def rfi_channels(y, mask, max_width=RFI_MAX_WIDTH, n_sigma=RFI_SIGMA, keep=None):
    """Narrow RFI spikes and dips in an averaged spectrum (also dips left by RFI in the Cold reference, and up/down
    pairs of a spike that moved a little in frequency).
    A channel is a candidate when it differs from the 7-channel running median by more than n_sigma (robust sigma)
    and this narrow excess is most of its difference to the broad (31-channel) level. Only narrow runs are kept
    (_rfi_runs_ok); the kept runs are widened by one channel on both sides. Later passes only add new, separate
    runs, so a removed region can never grow wider than the limit (the hydrogen line is protected).
    keep: the channels found in the previous update of the same average (hysteresis). A region found before stays
    removed while its peak is still above RFI_KEEP_FRACTION * n_sigma; it is released only below that, so a spike
    near the threshold does not blink in and out between updates.
    Returns a boolean array."""
    from scipy.ndimage import median_filter, binary_dilation
    y = np.asarray(y, dtype=np.float64)
    n = len(y)
    out = np.zeros(n, dtype=bool)
    good = np.asarray(mask, dtype=bool) & np.isfinite(y)
    if good.sum() < 50:
        return out
    idx = np.arange(n)
    r0 = sig0 = None
    for _ in range(3):
        keep_ = good & ~out
        work = np.interp(idx, idx[keep_], y[keep_])
        r = work - median_filter(work, size=7, mode="nearest")
        broad = work - median_filter(work, size=31, mode="nearest")
        rs = r[keep_]
        sig = 1.4826 * float(np.median(np.abs(rs - np.median(rs))))
        if not sig > 0:
            break
        if r0 is None:
            r0, sig0 = r, sig
        cand = keep_ & (np.abs(r) > n_sigma * sig) & (np.sign(r) == np.sign(broad)) & \
            (np.abs(r) > RFI_SHARE * np.abs(broad))
        if out.any():                                      # no growing of regions already removed
            cand &= ~binary_dilation(out, iterations=2)
        ok = _rfi_runs_ok(cand, np.sign(r), max_width)
        if not ok.any():
            break
        new = binary_dilation(ok, iterations=1) & good & ~out
        if not new.any():
            break
        out |= new
    from scipy.ndimage import label
    if keep is not None and r0 is not None:              # hysteresis: earlier regions stay while still clearly there
        lab_k, nk = label(np.asarray(keep, dtype=bool) & good & ~out)
        near = binary_dilation(out, iterations=1)          # a region found again this time is not grown
        for k in range(1, nk + 1):
            ch = np.flatnonzero(lab_k == k)
            if near[ch].any():
                continue
            if np.max(np.abs(r0[ch])) > RFI_KEEP_FRACTION * n_sigma * sig0:
                out[ch] = True
    # hard limit: a removed region (with its widening) is never wider than an up/down pair of the widest spike
    lab, nl = label(out)
    if nl:
        width = np.bincount(lab)[1:]
        wide = np.flatnonzero(width > 2 * max_width + 2) + 1
        if wide.size:
            out &= ~np.isin(lab, wide)
    return out


def line_free_level(axis_hz, y, mask, half_mhz):
    """Median of y over the trusted channels outside the line window (the same channels as the baseline fit).
    None if there are too few channels."""
    d = axis_hz / 1e6 - H1_MHZ
    sel = mask & (np.abs(d) >= half_mhz + 0.2)
    if sel.sum() < 20:
        sel = mask & (np.abs(d) >= half_mhz)
    if sel.sum() < 20:
        return None
    v = float(np.median(y[sel]))
    return v if np.isfinite(v) and v > 0 else None


def line_integral(axis_hz, tline, good, removed, half_mhz):
    """Sum of T_line x channel width (K MHz) over the line window |f - f0| <= half.
    good = channels used (trusted, not removed); removed = channels removed as interference (or None).
    A removed channel inside the window is replaced by linear interpolation between its good neighbours, not left
    out of the sum (leaving it out would lower the integral by the removed share of the window)."""
    f = axis_hz / 1e6
    win = np.abs(f - H1_MHZ) <= half_mhz
    use = good & np.isfinite(tline)
    if use.sum() < 2:
        return float(np.sum(tline[win & use])) * abs(f[1] - f[0])
    y = np.where(use, tline, 0.0)
    fill = (removed & win & ~use) if removed is not None else np.zeros(len(f), dtype=bool)
    if fill.any():
        idx = np.arange(len(f))
        y[fill] = np.interp(idx[fill], idx[use], tline[use])
    return float(np.sum(y[win & (use | fill)])) * abs(f[1] - f[0])


def baseline_fit(axis_hz, y, mask, half_mhz):
    """Straight-line baseline fitted to trusted channels away from the line. Returns the baseline curve."""
    d = axis_hz / 1e6 - H1_MHZ
    sel = mask & (np.abs(d) >= half_mhz + 0.2)
    if sel.sum() < 20:
        sel = mask & (np.abs(d) >= half_mhz)
    if sel.sum() < 20:
        raise ValueError("Not enough baseline channels outside the line window (reduce 'Line ± MHz').")
    coef = np.polyfit(d[sel], y[sel], 1)
    return np.polyval(coef, d)


def measure_noise(y, mask, corr):
    """Relative noise (sigma / level) of an averaged spectrum from differences of neighbouring channels.
    Insensitive to the baseline shape and the line; robust (MAD) against narrow spikes."""
    m = mask[:-1] & mask[1:]
    a, b = y[:-1][m], y[1:][m]
    if a.size < 50:
        return None
    d = (b - a) / (0.5 * (a + b))
    mad = float(np.median(np.abs(d - np.median(d))))
    return 1.4826 * mad / math.sqrt(max(2.0 * (1.0 - corr), 1e-6))


def solar_term(ra_deg, dec_deg):
    ra, dec = np.radians(ra_deg), np.radians(dec_deg)
    src = np.array([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)])
    return SOLAR_SPEED_KMS * float(src @ _APEX_VEC)


def barycentric_kms(c, t, loc):
    """Barycentric velocity correction (km/s). Computed from a clean ICRS coordinate: newer astropy versions refuse
    a 'location' argument when the coordinate still carries the location of the frame it was transformed from."""
    clean = SkyCoord(ra=c.ra, dec=c.dec, frame="icrs")
    return float(clean.radial_velocity_correction(kind="barycentric", obstime=t, location=loc).to_value(u.km / u.s))


def sky_position(az, alt, lat, lon, h, t_unix):
    """RA/Dec, l/b and the LSR correction (km/s, add to topocentric velocity) for a fixed Az/Alt pointing."""
    if ASTRO_ERROR:
        raise RuntimeError("astropy is not available: " + ASTRO_ERROR)
    loc = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=h * u.m)
    t = Time(t_unix, format="unix")
    c = SkyCoord(az=az * u.deg, alt=alt * u.deg, frame=AltAz(obstime=t, location=loc)).transform_to("icrs")
    g = c.galactic
    vb = barycentric_kms(c, t, loc)
    return {"ra_deg": float(c.ra.deg), "dec_deg": float(c.dec.deg), "l_deg": float(g.l.deg),
            "b_deg": float(g.b.deg), "vcorr_kms": vb + solar_term(float(c.ra.deg), float(c.dec.deg)),
            "vbary_kms": vb}


def sky_position_radec(ra_h, dec_d, jnow, lat, lon, h, t_unix):
    """Same result as sky_position for a guided (ASCOM / INDI) pointing given as RA/Dec of the mount.
    jnow = True when the mount reports topocentric (JNow) coordinates. Also returns the Az/Alt of the pointing."""
    if ASTRO_ERROR:
        raise RuntimeError("astropy is not available: " + ASTRO_ERROR)
    from astropy.coordinates import TETE, ICRS
    loc = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=h * u.m)
    t = Time(t_unix, format="unix")
    if jnow:
        c = SkyCoord(ra=ra_h * u.hourangle, dec=dec_d * u.deg, frame=TETE(obstime=t, location=loc)).transform_to(ICRS())
    else:
        c = SkyCoord(ra=ra_h * u.hourangle, dec=dec_d * u.deg, frame="icrs")
    aa = c.transform_to(AltAz(obstime=t, location=loc))
    g = c.galactic
    vb = barycentric_kms(c, t, loc)
    return {"ra_deg": float(c.ra.deg), "dec_deg": float(c.dec.deg), "l_deg": float(g.l.deg),
            "b_deg": float(g.b.deg), "vcorr_kms": vb + solar_term(float(c.ra.deg), float(c.dec.deg)),
            "vbary_kms": vb, "az_deg": float(aa.az.deg), "alt_deg": float(aa.alt.deg)}


def atmosphere_attenuation(alt, site_h):
    """exp(tau x): factor that brings a measured line back to its value above the atmosphere (as in H1-Hunter)."""
    tau = TAU_ZENITH_SL * float(np.exp(-site_h / ATM_SCALE_HEIGHT_M))
    x = 1.0 / np.sin(np.radians(max(alt, 5.0)))
    return float(np.exp(tau * x))


def estimate_ambient_k(lat, manual_c, sensor=None):
    """Air temperature (K): typed value, else the temperature sensor (sensor = (celsius, name)), else the same
    offline estimate as H1-Hunter. Returns (K, note)."""
    if manual_c is not None:
        return manual_c + 273.15, f"air {manual_c:.1f} °C entered by the user"
    if sensor is not None:
        return sensor[0] + 273.15, f"air {sensor[0]:.1f} °C from the temperature sensor ({sensor[1]})"
    la = abs(lat)
    mean_c = 28.0 - 0.37 * la
    amp = 0.18 * la
    peak = 200 if lat >= 0 else 17
    doy = time.gmtime().tm_yday
    t_c = mean_c + amp * float(np.cos(2.0 * np.pi * (doy - peak) / 365.0))
    return t_c + 273.15, f"air {t_c:.0f} °C = automatic estimate, about ±10 K"


SPILL_RIM = 2.0 * T_SPILL / 280.0   # share of the feed pattern past the dish rim (gives T_SPILL at the zenith)
SPILL_SIDELOBE = 0.15               # share of the power in the near sidelobes (about 3 x the main beam width)


def spillover_model(alt, dish_m, t_ground):
    """Ground radiation picked up at this altitude (K), automatic:
    rim spillover (half of it sees the ground at the zenith, all of it at the horizon) + the part of the main beam
    and of the near sidelobes that is below the horizon (Gaussian beam, FWHM 70 lambda / D)."""
    from math import erfc, sqrt, cos, radians
    a = max(float(alt), 0.0)
    fwhm = 70.0 * 0.211 / max(float(dish_m), 0.05)
    s_main = fwhm / 2.3548
    s_side = 3.0 * s_main
    below_main = 0.5 * erfc(a / (sqrt(2.0) * s_main))
    below_side = 0.5 * erfc(a / (sqrt(2.0) * s_side))
    rim = SPILL_RIM * (0.5 + 0.5 * cos(radians(a)))
    frac = rim + SPILL_SIDELOBE * below_side + (1.0 - SPILL_SIDELOBE - SPILL_RIM) * below_main
    return float(t_ground * min(frac, 1.0))


def cold_reference(alt, b, site_h, amb_k, dish_m=DEFAULT_DISH_M):
    """Cold sky temperature (K) for the Y-factor calibration: CMB + Galaxy + atmosphere + ground spillover.
    The spillover is computed from the altitude and the dish size (spillover_model), no setting needed."""
    spill = spillover_model(alt, dish_m, amb_k)
    tau = TAU_ZENITH_SL * float(np.exp(-site_h / ATM_SCALE_HEIGHT_M))
    x = 1.0 / np.sin(np.radians(max(alt, 5.0)))
    t_atm = amb_k * (1.0 - np.exp(-tau * x))
    t_gal = T_GAL_HIGH + T_GAL_PLANE * np.exp(-abs(b) / 8.0)
    tc = T_CMB + t_gal + t_atm + spill
    return float(tc), {"cmb": T_CMB, "galaxy": float(t_gal), "atmosphere": float(t_atm), "spillover": float(spill)}


def gain_factor(p_ref, p_ref0, mask):
    """Receiver gain now relative to the reference taken after Cold: median over the trusted band."""
    r = p_ref[mask] / np.maximum(p_ref0[mask], 1e-30)
    r = r[np.isfinite(r)]
    return float(np.median(r)) if r.size else 1.0


def remove_flagged(rec):
    """Average without the flagged spectra and (when spectral kurtosis flagging was on) without the channel blocks
    flagged by SK: (sum_all - removed sum) / (counts - removed number), per channel. Returns (p, removed spectra)
    or None if not possible. The number of spectra used per channel is spectra_used(rec)."""
    if rec is None or rec.get("sum") is None or rec["counts"] <= 0:
        return None
    n_rm = len(rec["cand_idx"]) + int(rec["extra_n"])
    sk_n = rec.get("sk_rm_n")
    if n_rm == 0 and sk_n is None:
        return rec["p"], 0
    if rec["counts"] - n_rm < 1:
        return None
    s = rec["sum"] - rec["extra_sum"]
    if len(rec["cand_idx"]):
        s = s - rec["cand_spec"].astype(np.float64).sum(axis=0)
    used = spectra_used(rec)
    if sk_n is not None:
        s = s - rec["sk_rm_sum"]
    p = np.maximum(s, 0.0) / np.maximum(used, 1.0)
    if sk_n is not None:
        dead = sk_dead(rec)
        if dead.any():                      # (almost) all data of these channels removed: left out as RFI channels
            p[dead] = rec["p"][dead]
    return p, n_rm


SK_DEAD_FRACTION = 0.5    # a channel that lost more than half of its spectra to SK flagging is removed as a whole


def sk_dead(rec):
    """Channels where spectral kurtosis flagging removed more than SK_DEAD_FRACTION of the spectra (boolean)."""
    if rec is None or rec.get("sk_rm_n") is None:
        return None
    nominal = float(rec["counts"] - len(rec["cand_idx"]) - int(rec["extra_n"]))
    return spectra_used(rec) < (1.0 - SK_DEAD_FRACTION) * max(nominal, 1.0)


def spectra_used(rec):
    """Spectra per channel left after Remove flagged (array; the same for every channel without SK flagging)."""
    n = len(rec["sum"]) if rec.get("sum") is not None else len(rec["p"])
    used = np.full(n, float(rec["counts"] - len(rec["cand_idx"]) - int(rec["extra_n"])))
    if rec.get("sk_rm_n") is not None:
        used = used - rec["sk_rm_n"]
    return used


def sk_fraction(rec, mask=None):
    """Share of the channel time removed by spectral kurtosis flagging (trusted channels), 0 if none."""
    if rec is None or rec.get("sk_rm_n") is None or rec["counts"] <= 0:
        return 0.0
    m = np.ones(len(rec["sk_rm_n"]), dtype=bool) if mask is None else mask
    return float(rec["sk_rm_n"][m].sum()) / (rec["counts"] * max(int(m.sum()), 1))
