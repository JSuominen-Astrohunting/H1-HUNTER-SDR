"""H1-HUNTER SDR - sky maps (RA/Dec, Galactic, polar), velocity maps, l-v diagram, Milky Way top view,
rotation curve and enclosed mass. The map code comes from H1-Hunter; every finished average is added in memory."""
import os
import json
import threading
import numpy as np
import matplotlib.pyplot as plt
from scipy.ndimage import gaussian_filter, gaussian_filter1d
from .common import *

DEFAULT_LAT, DEFAULT_LON, DEFAULT_HEIGHT = 61.485, 21.797, 15.0
DEFAULT_DISH = 0.9
MAP_UNIT = "K·MHz"


def ppm_shift_kms(ppm):
    return -C_KMS * float(ppm) * 1e-6


class _Get:
    """Small adapter: something with .get() (the map code was written for Tk entry widgets)."""

    def __init__(self, fn):
        self.fn = fn

    def get(self):
        v = self.fn()
        return "" if v is None else str(v)

NY, NX = 180, 360        # 1 degree grid: rows = Dec or b (-90..90), cols = RA or l (0..360)
VEL_STEP_MIN = 3.0       # km/s, minimum channel width of the velocity cube
VEL_MARGIN = 60.0        # km/s, margin around the band for Doppler corrections
R0_KPC = 8.15            # Sun - Galactic centre distance (Reid et al. 2019)
V0_KMS = 236.0           # circular speed at the Sun (Reid et al. 2019), flat rotation curve
TOPVIEW_SIGMA = 3.0      # only emission above this many sigma is placed on the top view
TOPVIEW_EXCL_DEG = 10.0  # no kinematic distances within this angle of l = 0 and l = 180 deg
TOPVIEW_HALF_KPC = 20.0  # half width of the map
TOPVIEW_CELL_KPC = 0.2
ROT_L_MIN_DEG, ROT_L_MAX_DEG = 10.0, 85.0   # tangent-point method: |sin l| range used (inner Galaxy, 1st and 4th quadrant)
G_KPC = 4.30091e-6       # gravitational constant in kpc (km/s)^2 / solar mass
POLAR_MIN_BEAM_DEG = 5.0     # the pointing marker on the polar map is never drawn smaller than this
POLAR_MARK_COLOR = "#ff2a2a"
POLAR_REFRESH_MS = 10000     # the Polar Map tab updates every 10 seconds (and at once when you open it)


def freq_to_vtopo(f_mhz):
    """Radio-definition velocity (km/s) from a frequency in MHz."""
    return C_KMS * (H1_MHZ - np.asarray(f_mhz, dtype=float)) / H1_MHZ


def make_velocity_axis(f):
    """Fixed LSR velocity axis for the cube, limited to what the band covers (plus a margin for Doppler shifts)."""
    f = np.asarray(f, dtype=float)
    dv = abs(C_KMS * float(np.median(np.diff(f))) / H1_MHZ)
    step = max(VEL_STEP_MIN, 2.0 * dv)
    vmin = float(freq_to_vtopo(f.max())) - VEL_MARGIN
    vmax = float(freq_to_vtopo(f.min())) + VEL_MARGIN
    start = np.floor(vmin / step) * step
    stop = np.ceil(vmax / step) * step
    axis = np.arange(start, stop + step / 2.0, step)
    if len(axis) > 500:                     # safety: never build a huge cube
        axis = axis[(len(axis) - 500) // 2:(len(axis) - 500) // 2 + 500]
    return axis


def to_axis(v_native, spec, v_axis):
    """Resamples a spectrum onto the fixed velocity axis. NaN outside the band."""
    order = np.argsort(v_native)
    v, s = np.asarray(v_native, dtype=float)[order], np.asarray(spec, dtype=float)[order]
    dv = float(np.median(np.diff(v)))
    step = float(v_axis[1] - v_axis[0]) if len(v_axis) > 1 else dv
    n = max(1, int(round(step / max(dv, 1e-9))))
    if n > 1:
        s = np.convolve(s, np.ones(n) / n, mode="same")
    return np.interp(v_axis, v, s, left=np.nan, right=np.nan)


class VelCube:
    """Sky grid (Dec x RA, 1 degree cells) of weighted line spectra on a fixed LSR velocity axis."""

    def __init__(self, v_axis):
        self.v = np.asarray(v_axis, dtype=float)
        nv = len(self.v)
        self.sum = np.zeros((NY, NX, nv), np.float32)
        self.cnt = np.zeros((NY, NX, nv), np.float32)

    def add(self, dec_i, ra_i, spec, w):
        ok = np.isfinite(spec)
        self.sum[dec_i, ra_i, ok] += (spec[ok] * w).astype(np.float32)
        self.cnt[dec_i, ra_i, ok] += np.float32(w)

    def mean(self):
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(self.cnt > 0, self.sum / self.cnt, np.nan).astype(np.float32)


_GAL_INDEX_CACHE = {}


ROW_LAT = np.radians(np.arange(NY) - 89.5)          # latitude (Dec or b) of the centre of each grid row
ROW_COS = np.maximum(np.cos(ROW_LAT), 1e-3)


def smooth_sphere(a, sig):
    """Gaussian smoothing of a sky grid with the same width ON THE SKY in both directions: one cell in the
    longitude direction (RA or l) is only cos(latitude) degrees wide, so the sigma there grows as 1/cos."""
    out = gaussian_filter1d(a, sig, axis=0, mode="nearest")
    for i in range(NY):
        si = min(sig / ROW_COS[i], NX / 4.0)
        out[i] = gaussian_filter1d(out[i], si, mode="wrap")
    return out


def beam_mask(hit, radius_deg):
    """Cells whose great-circle distance to a measured cell is at most radius_deg."""
    mask = np.zeros(hit.shape, dtype=bool)
    rows = np.flatnonzero(hit.any(axis=1))
    if rows.size == 0:
        return mask
    cos_r = np.cos(np.radians(radius_deg))
    rad = int(np.ceil(radius_deg)) + 1
    for h in rows:
        row = hit[h]
        csum = np.concatenate(([0], np.cumsum(np.concatenate((row, row, row)))))
        for r in range(max(0, h - rad), min(NY, h + rad + 1)):
            den = np.cos(ROW_LAT[r]) * np.cos(ROW_LAT[h])
            num = cos_r - np.sin(ROW_LAT[r]) * np.sin(ROW_LAT[h])
            if den < 1e-9:
                if num <= 0.0:
                    mask[r] = True
                continue
            c = num / den
            if c > 1.0:
                continue
            k = NX if c <= -1.0 else int(np.floor(np.degrees(np.arccos(c))))
            if k >= NX // 2:
                mask[r] = True
                continue
            j = np.arange(NX) + NX                    # window [j-k, j+k] in the tripled row
            mask[r] |= (csum[j + k + 1] - csum[j - k]) > 0
    return mask


def lon_halfwidth(r_deg, lat_deg):
    """Half width in longitude cells (RA or l) of a circle of radius r_deg at the given latitude."""
    return int(min(NX // 2, round(r_deg / max(np.cos(np.radians(lat_deg)), 1e-3))))


def gal_to_eq_index():
    """For every Galactic cell (b row, l column): the Equatorial cell (dec row, RA column) containing its centre."""
    if "idx" not in _GAL_INDEX_CACHE:
        b = (np.arange(NY) + 0.5) - 90.0
        l = (np.arange(NX) + 0.5) - 180.0
        ll, bb = np.meshgrid(l, b)
        ra, dec = gal_to_eq_deg(ll.ravel() % 360.0, bb.ravel())
        ra_i = np.floor(ra).astype(int) % NX
        dec_i = np.clip(np.floor(dec + 90.0).astype(int), 0, NY - 1)
        _GAL_INDEX_CACHE["idx"] = (dec_i.reshape(NY, NX), ra_i.reshape(NY, NX))
    return _GAL_INDEX_CACHE["idx"]


_GAL_R = np.array([[-0.0548755604, -0.8734370902, -0.4838350155],
                   [0.4941094279, -0.4448296300, 0.7469822445],
                   [-0.8676661490, -0.1980763734, 0.4559837762]])   # ICRS -> Galactic


def gal_to_eq_deg(l_deg, b_deg):
    """ICRS (RA, Dec) in degrees from Galactic (l, b) in degrees (inverse rotation, no astropy needed)."""
    l, b = np.radians(np.asarray(l_deg, dtype=float)), np.radians(np.asarray(b_deg, dtype=float))
    g = np.stack([np.cos(b) * np.cos(l), np.cos(b) * np.sin(l), np.sin(b)])
    v = _GAL_R.T @ g
    return np.degrees(np.arctan2(v[1], v[0])) % 360.0, np.degrees(np.arcsin(np.clip(v[2], -1.0, 1.0)))


def eq_to_gal_deg(ra_deg, dec_deg):
    """Galactic (l, b) in degrees from ICRS (RA, Dec) in degrees. Works on arrays."""
    r, d = np.radians(np.asarray(ra_deg, dtype=float)), np.radians(np.asarray(dec_deg, dtype=float))
    v = np.stack([np.cos(d) * np.cos(r), np.cos(d) * np.sin(r), np.sin(d)])
    g = _GAL_R @ v
    return np.degrees(np.arctan2(g[1], g[0])) % 360.0, np.degrees(np.arcsin(np.clip(g[2], -1.0, 1.0)))


def velocity_arrays(cube, eq_w, kind, v1, v2):
    """Returns (s, w) 2D arrays whose smoothed ratio s/w is the requested velocity map, and a label.
    kind: 'channel' (mean T [K]), 'm0' (integral T dv [K km/s]), 'm1' (intensity-weighted velocity [km/s])."""
    v = cube.v
    dv = float(v[1] - v[0]) if len(v) > 1 else 1.0
    sel = (v >= min(v1, v2)) & (v <= max(v1, v2))
    if not sel.any():
        raise ValueError("No velocity channels in the selected range.")
    cnt = cube.cnt[:, :, sel]
    w2 = cnt.mean(axis=2)
    if kind == "channel":
        s = cube.sum[:, :, sel].sum(axis=2)
        wt = cnt.sum(axis=2)
        return s, wt, "Mean T (K)"
    with np.errstate(invalid="ignore", divide="ignore"):
        m = np.where(cnt > 0, cube.sum[:, :, sel] / cnt, np.nan)
    if kind == "m0":
        m0 = np.nansum(m, axis=2) * dv
        return m0 * w2, w2, "Integrated T (K km/s)"
    vals = m[np.isfinite(m)]
    sigma = 1.4826 * float(np.median(np.abs(vals - np.median(vals)))) if vals.size else 0.0
    thr = 2.0 * sigma                                   # only clearly positive channels count as emission
    tpos = np.where(np.nan_to_num(m, nan=0.0) > thr, np.nan_to_num(m, nan=0.0), 0.0)
    m0p = tpos.sum(axis=2) * dv
    with np.errstate(invalid="ignore", divide="ignore"):
        m1 = np.where(m0p > 0, (tpos * v[sel]).sum(axis=2) * dv / m0p, 0.0)
    w1 = np.where(m0p > 0, w2, 0.0)
    return m1 * w1, w1, "Mean velocity (km/s LSR)"


def rotation_curve(lv, l_deg, v, thr, r0=R0_KPC, v0=V0_KMS, lmin=ROT_L_MIN_DEG, lmax=ROT_L_MAX_DEG):
    """Tangent-point method (as in ezRA ezGal): in the 1st quadrant (0 < l < 90) the highest positive velocity with
    emission is the terminal velocity, in the 4th quadrant (270 < l < 360) the most negative one.
      V_rot(R) = |V_terminal| + V0 * |sin l|,   R = R0 * |sin l|,   M(<R) = V_rot^2 R / G
    lv: (n_l, n_v) mean T, l_deg: longitudes (deg), v: velocities (km/s), thr: emission threshold (K).
    Returns a list of dicts (one per longitude with a detection)."""
    out = []
    for i, l in enumerate(np.asarray(l_deg, dtype=float) % 360.0):
        if lmin <= l <= lmax:
            quad, sign = 1, +1.0
        elif 360.0 - lmax <= l <= 360.0 - lmin:
            quad, sign = 4, -1.0
        else:
            continue
        row = lv[i]
        ok = np.isfinite(row) & (row > thr) & (sign * v > 0)
        if not ok.any():
            continue
        vt = float(np.max(sign * v[ok]))                      # |terminal velocity|
        s = abs(np.sin(np.radians(l)))
        r = r0 * s
        vrot = vt + v0 * s
        out.append({"l_deg": float(l), "quadrant": quad, "v_terminal_kms": sign * vt, "R_kpc": r,
                    "v_rot_kms": vrot, "mass_msun": vrot ** 2 * r / G_KPC})
    return out


def topview_grid(lv, l_deg, v, thr, r0=R0_KPC, v0=V0_KMS, excl=TOPVIEW_EXCL_DEG,
                 half=TOPVIEW_HALF_KPC, cell=TOPVIEW_CELL_KPC, sub=3, blur=1.5):
    """Kinematic distance map (flat rotation curve) seen from the Galactic north pole.
    lv: (NL, NV) mean T [K] (NaN = no data), l_deg: (NL,) centres, v: (NV,) LSR velocities, thr: threshold [K].
    Sun at (0, -r0), Galactic centre at (0, 0), l = 0 points up, l = 90 points left.
    Three layers (mean T [K], NaN = empty):
      'all'  - every l-v cell with data (NaN = none), also below the noise threshold, at every possible distance
      'amb'  - emission above the threshold with two possible distances (near and far, both drawn)
      'sure' - emission above the threshold with only one possible distance
    Directions within 'excl' of l = 0 and l = 180 are never placed (distance undefined there)."""
    n = int(round(2.0 * half / cell))
    nums = {k: np.zeros((n, n)) for k in ("all", "sure", "amb")}
    cnts = {k: np.zeros((n, n)) for k in ("all", "sure", "amb")}
    lv = np.asarray(lv, dtype=float)
    l_deg = np.asarray(l_deg, dtype=float)
    v = np.asarray(v, dtype=float)
    dl = float(np.median(np.diff(l_deg))) if len(l_deg) > 1 else 1.0
    dv = float(np.median(np.diff(v))) if len(v) > 1 else 1.0
    ldiff = np.minimum(np.abs(((l_deg + 180.0) % 360.0) - 180.0), np.abs(l_deg % 360.0 - 180.0))   # to l=0 / l=180
    measured = np.isfinite(lv) & (ldiff > excl)[:, None]
    with np.errstate(invalid="ignore"):
        strong = measured & (lv > thr)
    offs = (np.arange(sub) + 0.5) / sub - 0.5

    def place(sel, keys_one, keys_two):
        ii, jj = np.nonzero(sel)
        T = lv[ii, jj]
        for oi in offs:
            for oj in offs:
                l = np.radians(l_deg[ii] + oi * dl)
                vv = v[jj] + oj * dv
                sl, cl = np.sin(l), np.cos(l)
                den = vv + v0 * sl
                with np.errstate(invalid="ignore", divide="ignore"):
                    R = r0 * v0 * sl / den
                    disc = R * R - (r0 * sl) ** 2
                ok = np.isfinite(R) & (R > 0) & (disc >= 0)
                s = np.sqrt(np.where(ok, disc, 0.0))
                d1 = r0 * cl + s
                d2 = r0 * cl - s
                two = ok & (d2 > 0) & (s > 0.5)           # near and far both possible
                one = ok & ~two & (d1 > 0)
                todo = [(one, np.where(d2 > 0, r0 * cl, d1), k) for k in keys_one]
                todo += [(two, d, k) for k in keys_two for d in (d1, d2)]
                for mask, d, key in todo:
                    if not mask.any():
                        continue
                    x = -d[mask] * sl[mask]
                    y = d[mask] * cl[mask] - r0
                    ix = np.floor((x + half) / cell).astype(int)
                    iy = np.floor((y + half) / cell).astype(int)
                    inb = (ix >= 0) & (ix < n) & (iy >= 0) & (iy < n)
                    np.add.at(nums[key], (iy[inb], ix[inb]), T[mask][inb])
                    np.add.at(cnts[key], (iy[inb], ix[inb]), 1.0)
        return len(ii)

    n_all = place(measured, ("all",), ("all",))
    n_strong = place(strong, ("sure",), ("amb",))
    out = {"extent": [-half, half, -half, half], "cell": cell, "n_samples": int(n_strong), "n_all": int(n_all)}
    for key in ("all", "sure", "amb"):
        nb = gaussian_filter(nums[key], blur)
        cb = gaussian_filter(cnts[key], blur)
        with np.errstate(invalid="ignore", divide="ignore"):
            val = np.where(cb > 0.02 * sub * sub, nb / cb, np.nan)
        out[key] = val
    return out



class MapStore:
    """All map data in memory. 'owner' is the main window: settings are read from its variables (owner.v)."""

    def __init__(self, owner):
        self.owner = owner
        v = owner.v
        g = (lambda k: _Get(lambda: v[k].get()))
        self.entry_dish = g("dish")
        self.entries = {k: g(k) for k in ("lat", "lon", "height", "az", "alt")}
        self.entry_observer = g("observer")
        self.scan_mode = g("scan_mode")
        self.entry_v1, self.entry_v2, self.entry_bwin = g("v1"), g("v2"), g("bwin")
        self.ppm_var = _Get(lambda: "1")       # PPM is set in the receiver and stored in every file
        self.ppm_corr = 0.0
        self.map_unit = MAP_UNIT
        self.grid_lock = threading.Lock()
        self.cal_idx = 0
        self.last_drawn = {"eq": -1, "gal": -1}
        self.polar_ax = None
        self.polar_rmax = 130.0
        self.polar_n_drawn = 0
        self.polar_last_pt = None
        self.cmap = "inferno"                  # colormap of the maps (Maps -> Limits -> Colormap)
        self.reset_grids()

    @property
    def t_sys(self):
        h = getattr(self.owner, "hot", None)
        return h["tsys"] if h else None

    @property
    def y_factor(self):
        h = getattr(self.owner, "hot", None)
        return h["y"] if h else None

    def _opt_float(self, entry, name, lo, hi):
        txt = entry.get().strip().replace(",", ".")
        if not txt:
            return None, None
        try:
            val = float(txt)
        except ValueError:
            return None, f"{name} must be a number."
        if not lo <= val <= hi:
            return None, f"{name} must be between {lo} and {hi}."
        return val, None


    def add_average(self, pr, rec, value, alt, t_line_k, t_ant_k):
        """One finished average -> one map point. pr = products of the average (sky position, LSR velocity)."""
        s = pr["sky"]
        if s is None:
            return False
        ra_h, dec_d = s["ra_deg"] / 15.0, s["dec_deg"]
        l_c = ((s["l_deg"] + 180.0) % 360.0) - 180.0
        f_mhz = pr["axis"] / 1e6
        keep = pr["mask"]
        if self.cube is None:
            self.v_axis = make_velocity_axis(f_mhz[keep])
            self.cube = VelCube(self.v_axis)
        spec_axis = None
        if t_line_k is not None and pr["vlsr"] is not None:
            spec_axis = to_axis(pr["vlsr"][keep], t_line_k[keep], self.cube.v)
        r = {"kind": "spectrum", "freq": f_mhz[keep], "power_raw": rec["p"][keep], "rfi": 0.0}
        t_iso = utc_str(pr["t_mid"])
        tk_s = t_ant_k[keep] if t_ant_k is not None else np.full(int(keep.sum()), np.nan)
        tl_s = t_line_k[keep] if t_line_k is not None else np.full(int(keep.sum()), np.nan)
        self.add_sample(ra_h, dec_d, l_c, s["b_deg"], value, float(rec["counts"]), alt, s["vcorr_kms"],
                        rec["counts"], spec_axis, t_iso, r, tk_s, tl_s, 0.0, cal=self.live_calibration())
        return True

    def live_calibration(self):
        """Calibration in force for a live average (the same values as in its spectrum file)."""
        c, h = getattr(self.owner, "cold", None), getattr(self.owner, "hot", None)
        if not c:
            return None
        cal = {"cold_time_utc": utc_str(c.get("t_last")), "cold_T_ref_K": c.get("tc")}
        if h:
            cal.update({"hot_time_utc": utc_str(h.get("t_last")), "hot_T_ref_K": h.get("th"),
                        "y_factor": h.get("y"), "tsys_K": h.get("tsys")})
        return cal
    def reset_grids(self):
        with self.grid_lock:
            self.eq_sum = np.zeros((NY, NX)); self.eq_w = np.zeros((NY, NX))
            self.gal_sum = np.zeros((NY, NX)); self.gal_w = np.zeros((NY, NX))
            self.n_points = 0
            self.cube = None
            self.v_axis = None
            self.S = {"time": [], "ra_h": [], "dec_d": [], "l": [], "b": [], "alt": [], "vlsr_add": [],
                      "counts": [], "cal_idx": [], "freq": [], "power": [], "tk": [], "tline": [], "rfi": [], "value": [], "tcont": [],
                      "cal": []}
        self.dirty = False
        self.lsr_failed = False

    def add_sample(self, ra_h, dec_d, l_c, b_deg, value, w, alt, vadd, counts, spec_axis, t_iso, r, tk_spec, tl_spec,
                   t_cont=0.0, cal=None):
        """Adds one sample to the grids, the cube and the spectrum store (thread safe)."""
        ra_i = int(ra_h * 15.0) % NX
        dec_i = min(max(int(np.floor(dec_d + 90.0)), 0), NY - 1)
        l_i = min(int(np.floor(l_c + 180.0)), NX - 1)
        b_i = min(max(int(np.floor(b_deg + 90.0)), 0), NY - 1)
        with self.grid_lock:
            self.eq_sum[dec_i, ra_i] += value * w; self.eq_w[dec_i, ra_i] += w
            self.gal_sum[b_i, l_i] += value * w; self.gal_w[b_i, l_i] += w
            self.n_points += 1
            if spec_axis is not None and self.cube is not None:
                self.cube.add(dec_i, ra_i, spec_axis, w)
            if r["kind"] == "spectrum":
                S = self.S
                S["time"].append(t_iso); S["ra_h"].append(ra_h); S["dec_d"].append(dec_d)
                S["l"].append(l_c % 360.0); S["b"].append(b_deg); S["alt"].append(alt)
                S["vlsr_add"].append(vadd); S["counts"].append(counts); S["cal_idx"].append(self.cal_idx)
                f = r["freq"]
                if S["freq"] and np.array_equal(S["freq"][-1], f):
                    f = S["freq"][-1]            # share the array between samples
                S["freq"].append(f)
                S["power"].append(r["power_raw"].astype(np.float32))
                S["tk"].append(tk_spec.astype(np.float32)); S["tline"].append(tl_spec.astype(np.float32))
                S["rfi"].append(float(r.get("rfi", 0.0))); S["value"].append(float(value)); S["tcont"].append(float(t_cont))
                cl = S.setdefault("cal", [])
                cl.extend([None] * (len(S["time"]) - 1 - len(cl)))   # spectra opened from older files: no record
                cl.append(cal)
            self.dirty = True

    def snapshot(self, kind):
        with self.grid_lock:
            if kind == "eq":
                return self.eq_sum.copy(), self.eq_w.copy(), self.n_points
            return self.gal_sum.copy(), self.gal_w.copy(), self.n_points

    def beam_deg(self):
        try:
            d = float(self.entry_dish.get().replace(",", "."))
        except ValueError:
            d = DEFAULT_DISH
        d = min(max(d, 0.1), 50.0)
        return 70.0 * 0.21 / d

    def sigma_cells(self):
        # Data is already convolved by the real beam, so smoothing is only used for interpolation:
        # sigma = half of the beam's Gaussian sigma. 1 cell = 1 degree.
        return max(self.beam_deg() / 2.355 * 0.5, 1.0)

    def compute_grid(self, s, w):
        """Weighted Gaussian smoothing (wraps in the x direction) limited to beam-radius around samples."""
        if not (w > 0).any():
            return np.full(s.shape, np.nan), np.zeros(s.shape, dtype=bool)
        sig = self.sigma_cells()
        ss, sw = smooth_sphere(s, sig), smooth_sphere(w, sig)
        mask = beam_mask(w > 0, self.beam_deg() / 2.0)
        grid = np.where(mask & (sw > 1e-12), ss / np.where(sw > 1e-12, sw, 1.0), np.nan)
        return grid, mask

    @staticmethod
    def color_limits(grid):
        vals = grid[np.isfinite(grid)]
        vmin, vmax = np.percentile(vals, 2), np.percentile(vals, 98)
        if vmax - vmin < 1e-6:
            vmax = vmin + 1.0
        return vmin, vmax

    def stats(self, grid, mask, n, kind, unit):
        wts = np.cos(np.radians(np.arange(NY) - 89.5))
        cov = 100.0 * (wts[:, None] * mask).sum() / (wts.sum() * NX)
        peak = "n/a"
        if np.isfinite(grid).any():
            iy, ix = np.unravel_index(np.nanargmax(grid), grid.shape)
            val = grid[iy, ix]
            if kind == "eq":
                peak = f"{val:.1f} {unit} @ RA {(ix + 0.5) / 15.0:.2f}h, Dec {iy - 90 + 0.5:+.1f}°"
            else:
                peak = f"{val:.1f} {unit} @ l {(ix - 180 + 0.5) % 360:.1f}°, b {iy - 90 + 0.5:+.1f}°"
        return n, cov, peak, self.cal_text()

    def cal_text(self):
        """Calibration of the map data itself (not of the current session): a point is calibrated when its
        stored line spectrum is in Kelvin (Cold -> Hot Y-factor was valid when it was measured)."""
        with self.grid_lock:
            tl = list(self.S.get("tline", []))
        n = len(tl)
        if n == 0:
            return "no points"
        k = sum(1 for t in tl if np.isfinite(np.asarray(t, dtype=float)).any())
        if k == n:
            return f"calibrated (Y-factor), {k}/{n} points"
        if k == 0:
            return "uncalibrated"
        return f"{k}/{n} points calibrated (Y-factor)"

    def style_colorbar(self, cb, horizontal=False):
        cb.ax.tick_params(labelsize=9, labelcolor="white")
        (cb.ax.xaxis if horizontal else cb.ax.yaxis).label.set_color("white")

    def draw_eq(self, fig, grid, mask, n, label, title, unit, cmap_name=None, velocity=False, text=True):
        fig.clf(); fig.set_facecolor(BG)
        ax = fig.add_subplot(111, facecolor=BG)
        cmap = plt.get_cmap(cmap_name or self.cmap).copy(); cmap.set_bad(BG)
        if np.isfinite(grid).any():
            vmin, vmax = self.color_limits(grid)
            im = ax.imshow(np.ma.masked_invalid(grid), extent=[0, 24, -90, 90], origin="lower", cmap=cmap,
                           aspect="auto", interpolation="bilinear", vmin=vmin, vmax=vmax)
            cb = fig.colorbar(im, ax=ax, label=label)
            self.style_colorbar(cb)
        ax.set_xlim(24, 0); ax.set_ylim(-90, 90)
        ax.set_xticks(np.arange(0, 25, 2)); ax.set_xticklabels([f"{h}h" for h in range(0, 25, 2)])
        ax.set_yticks(np.arange(-90, 91, 30)); ax.set_yticklabels([f"{d}°" for d in range(-90, 91, 30)])
        ax.set_xlabel("Right Ascension (h)", color="white"); ax.set_ylabel("Declination (°)", color="white")
        ax.tick_params(colors="white"); ax.grid(True, color="white", alpha=0.15, linestyle="--")
        ax.set_title(title, color="white", fontsize=12)
        n_, cov, peak, cal = self.stats(grid, mask, n, "eq", unit)
        notes = self.notes_text(velocity)
        fig.tight_layout(rect=[0, 0.15 if notes else 0.12, 1, 1])
        obs_line, site_line = self.site_lines()
        lines = [f"Points: {n_}   |   Sky Coverage: {cov:.1f} %   |   Max Peak: {peak}",
                 f"Beam {self.beam_deg():.1f}° ({self.entry_dish.get()} m)   |   {cal}",
                 f"{obs_line}   |   {site_line}"]
        if notes:
            lines.append(notes)
        self.fit_text(fig, fig.text(0.01, 0.01, "\n".join(lines), color="white", fontsize=8, family="monospace", va="bottom", ha="left"))
        if not text:
            self.clean_figure(fig)

    def draw_gal(self, fig, grid, mask, n, label, title, unit, cmap_name=None, velocity=False, text=True):
        fig.clf(); fig.set_facecolor(BG)
        info = None
        if text:
            n_, cov, peak, cal = self.stats(grid, mask, n, "gal", unit)
            obs_line, site_line = self.site_lines()
            notes = self.notes_text(velocity)
            info = (f"Points: {n_}\nSky Coverage: {cov:.1f} %\nMax Peak:\n {peak}\n"
                    f"Beam {self.beam_deg():.1f}°\n{cal}\n{obs_line}\n{site_line}")
            if notes:
                info += "\n" + notes.replace("   |   ", "\n")
            info = fig.text(0.01, 0.98, info, color="white", fontsize=8, family="monospace", va="top", ha="left")
        # the info text gets its own column on the left; the Aitoff ellipse goes to the right of it, never under it
        left = 0.02
        if info is not None:
            try:
                tx = info.get_window_extent(fig.canvas.get_renderer()).x1 / fig.bbox.width
            except Exception:
                tx = 0.22
            left = min(0.45, tx + (0.075 if text else 0.0))
        has_cb = bool(np.isfinite(grid).any())
        y0 = (0.22 if text else 0.17) if has_cb else (0.10 if text else 0.02)
        top = 0.88 if text else 0.98
        ax = fig.add_axes([left, y0, 0.99 - left, top - y0], projection="aitoff", facecolor=BG)
        cmap = plt.get_cmap(cmap_name or self.cmap).copy(); cmap.set_bad(BG)
        if has_cb:
            vmin, vmax = self.color_limits(grid)
            xe = np.radians(np.linspace(-180, 180, NX + 1))
            ye = np.radians(np.linspace(-90, 90, NY + 1))
            # x = -l so that l increases to the left (astronomical convention)
            mesh = ax.pcolormesh(xe, ye, np.ma.masked_invalid(grid[:, ::-1]), cmap=cmap, vmin=vmin, vmax=vmax,
                                 shading="flat")
            w_ = 0.99 - left
            cax = fig.add_axes([left + 0.1 * w_, 0.085, 0.8 * w_, 0.035])
            cb = fig.colorbar(mesh, cax=cax, orientation="horizontal", label=label)
            self.style_colorbar(cb, horizontal=True)
        ax.grid(True, color="white", alpha=0.2, linestyle="--")
        if text:
            xt = np.arange(-150, 151, 30)
            ax.set_xticks(np.radians(xt)); ax.set_xticklabels([f"{-d}°" for d in xt])
            yt = np.arange(-75, 76, 15)
            ax.set_yticks(np.radians(yt)); ax.set_yticklabels([f"{d}°" for d in yt])
            ax.tick_params(colors="white", labelsize=8)
            ax.set_xlabel("Galactic longitude l", color="white"); ax.set_ylabel("Galactic latitude b", color="white")
            ttl = ax.set_title(title, color="white", fontsize=11, pad=15)
            try:                                 # a long title is made smaller so that it is not cut at the edge
                r = fig.canvas.get_renderer()
                for _ in range(6):
                    bb = ttl.get_window_extent(r)
                    if (bb.x0 >= 0 and bb.x1 <= fig.bbox.width) or ttl.get_fontsize() <= 7:
                        break
                    ttl.set_fontsize(max(7.0, ttl.get_fontsize() * 0.9))
            except Exception:
                pass
        else:
            ax.grid(False)
            ax.set_xticklabels([]); ax.set_yticklabels([])

    def render_eq(self, fig, text=True):
        s, w, n = self.snapshot("eq")
        grid, mask = self.compute_grid(s, w)
        self.draw_eq(fig, grid, mask, n, f"Signal ({self.map_unit})", f"{APP} - radio map (Equatorial, J2000)",
                     self.map_unit, text=text)
        self.last_drawn["eq"] = n

    def render_gal(self, fig, text=True):
        s, w, n = self.snapshot("gal")
        grid, mask = self.compute_grid(s, w)
        self.draw_gal(fig, grid, mask, n, f"Signal ({self.map_unit})", f"{APP} - Galactic Aitoff projection",
                      self.map_unit, text=text)
        self.last_drawn["gal"] = n

    def draw_polar(self, fig, show_grid, marker=False, text=True):
        """Polar sky map: angle = Right Ascension (RA increases counter-clockwise, 0h at the top),
        radius = 90 deg - Declination, so the north celestial pole is in the centre."""
        fig.clf(); fig.set_facecolor(BG)
        show_grid = show_grid and text           # no grid = a clean picture: no coordinates, title or info text
        s, w, n = self.snapshot("eq")
        grid, mask = self.compute_grid(s, w)
        self.polar_n_drawn = n
        ax = fig.add_subplot(111, projection="polar", facecolor="#2a2a33")      # dark grey = not measured
        ax.set_theta_zero_location("N"); ax.set_theta_direction(1)
        cmap = plt.get_cmap(self.cmap).copy(); cmap.set_bad("#2a2a33")
        # radius = whole sky visible from the site (lowest Dec = latitude - 90, down to the next 10 deg circle;
        # -30 deg in Pori), or further out if there is data further south
        rows = np.where(mask.any(axis=1))[0] if mask.size else np.array([])
        try:
            lat = float(self.entries["lat"].get().replace(",", "."))
            r_site = 180.0 - lat if lat >= 0 else 180.0
        except (ValueError, KeyError, AttributeError):
            r_site = 130.0
        r_data = 180.0 - float(rows.min()) if rows.size else 0.0
        rmax = float(np.clip(np.ceil(max(r_site, r_data) / 10.0 - 1e-9) * 10.0, 30.0, 180.0))
        if np.isfinite(grid).any():
            vmin, vmax = self.color_limits(grid)
            te = np.radians(np.linspace(0.0, 360.0, NX + 1))
            re = np.linspace(0.0, 180.0, NY + 1)
            mesh = ax.pcolormesh(te, re, np.ma.masked_invalid(grid[::-1, :]), cmap=cmap, vmin=vmin, vmax=vmax,
                                 shading="flat")
            cb = fig.colorbar(mesh, ax=ax, orientation="horizontal", pad=0.1, fraction=0.045,
                              label=f"Signal ({self.map_unit})")
            self.style_colorbar(cb, horizontal=True)
        ax.set_rlim(0.0, rmax)
        ax.set_xticks(np.radians(np.arange(0, 360, 30)))
        ax.tick_params(axis="x", colors="white", labelsize=9)
        ax.grid(False)
        if show_grid:
            ax.set_xticklabels([f"{int(a / 15)}h" for a in range(0, 360, 30)])  # RA labels on the rim
            for h in range(24):                                                   # RA lines, every hour
                t = np.radians(h * 15.0)
                ax.plot([t, t], [0, rmax], color="white", alpha=0.35 if h % 2 == 0 else 0.15, linewidth=0.6)
            for dec in range(-80, 90, 10):                                        # Dec circles, every 10 deg
                r = 90.0 - dec
                if r <= rmax:
                    ax.plot(np.linspace(0, 2 * np.pi, 361), np.full(361, r), color="white",
                            alpha=0.4 if dec % 30 == 0 else 0.15, linewidth=0.6)
            for dec in range(-60, 90, 30):                                        # Dec scale, every 30 deg
                r = 90.0 - dec
                if r <= rmax:
                    ax.text(np.radians(7.5), r, f"{dec:+d}°", color="white", fontsize=8, ha="left", va="center",
                            bbox=dict(facecolor=BG, edgecolor="none", alpha=0.6, pad=1))
            ax.set_yticks([])
        else:
            ax.set_xticklabels([])                       # no coordinates at all unless the box is ticked
            ax.set_yticks([])
        ax.spines["polar"].set_edgecolor("#888888")
        self.polar_ax, self.polar_rmax = ax, rmax
        if not show_grid:
            return
        ax.set_title(f"{APP} - polar sky map (RA / Dec, J2000)", color="white", fontsize=11, pad=18)
        n_, cov, peak, cal = self.stats(grid, mask, n, "eq", self.map_unit)
        self.fit_text(fig, fig.text(0.01, 0.01, f"Points: {n_}   |   Sky coverage: {cov:.1f} %\nMax peak: {peak}\nNorth celestial pole in the "
                 f"centre, RA counter-clockwise.", color="white", fontsize=8, family="monospace",
                 va="bottom", ha="left"))

    @staticmethod
    def hide_ticks(ax):
        """No coordinates, but the axis limits stay exactly as they were."""
        from matplotlib.ticker import NullFormatter
        for a in (ax.xaxis, ax.yaxis):
            a.set_major_formatter(NullFormatter()); a.set_minor_formatter(NullFormatter())
        ax.tick_params(which="both", length=0)

    @staticmethod
    def fit_text(fig, t, min_size=5.0):
        """Makes an info text smaller until it fits the width of the figure (no text cut off at the edge)."""
        try:
            r = fig.canvas.get_renderer()
            for _ in range(8):
                bb = t.get_window_extent(r)
                if bb.x1 <= fig.bbox.width * 0.995 or t.get_fontsize() <= min_size:
                    break
                t.set_fontsize(max(min_size, t.get_fontsize() * fig.bbox.width * 0.99 / max(bb.x1, 1.0)))
        except Exception:
            pass
        return t

    def clean_figure(self, fig):
        """Removes titles, labels, coordinates, legends and info texts; the colour scale stays."""
        import warnings
        for t in list(fig.texts):
            t.remove()
        for lg in list(getattr(fig, "legends", [])):
            lg.remove()
        for ax in fig.axes:
            if hasattr(ax, "_colorbar"):
                continue
            ax.set_title(""); ax.set_xlabel(""); ax.set_ylabel("")
            self.hide_ticks(ax)
            ax.grid(False)
            for t in list(ax.texts):
                t.remove()
            if ax.get_legend() is not None:
                ax.get_legend().remove()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                fig.tight_layout(rect=[0, 0.01, 1, 0.99])
        except Exception:
            pass

    def vel_params(self):
        try:
            v1 = float(self.entry_v1.get().replace(",", "."))
            v2 = float(self.entry_v2.get().replace(",", "."))
        except ValueError:
            return None
        bw, _ = self._opt_float(self.entry_bwin, "b window", 0.5, 60.0)
        return v1, v2, (5.0 if bw is None else bw)

    def render_velocity(self, fig, kind, system, text=True):
        """kind: channel, m0, m1, lv. Returns True if something was drawn."""
        fig.clf(); fig.set_facecolor(BG)
        ok = self._render_velocity(fig, kind, system, text)
        if ok and not text and kind not in ("channel", "m0", "m1", "top"):
            self.clean_figure(fig)
        return ok

    def _render_velocity(self, fig, kind, system, text):

        def message(text):
            fig.text(0.5, 0.5, text, color="white", ha="center", va="center", fontsize=12)
            return False
        if self.cube is None or self.n_points == 0:
            return message("No spectra collected yet.\nVelocity maps need calibrated averages from 3. Measure.")
        params = self.vel_params()
        if params is None:
            return message("Velocity range must be numbers (km/s).")
        v1, v2, bw = params
        if kind == "lv":
            return self.draw_lv(fig, bw)
        if kind == "spec":
            return self.draw_spectra_by_l(fig, bw, v1, v2)
        if kind == "top":
            return self.draw_topview(fig, bw, text)
        if kind in ("rot", "mass"):
            return self.draw_rotation(fig, bw, kind)
        try:
            s, w, label = velocity_arrays(self.cube, self.eq_w, kind, v1, v2)
        except ValueError as e:
            return message(str(e))
        grid, mask = self.compute_grid(s, w)
        titles = {"channel": f"Channel map {min(v1, v2):g} to {max(v1, v2):g} km/s (LSR)",
                  "m0": f"Moment 0, {min(v1, v2):g} to {max(v1, v2):g} km/s (LSR)",
                  "m1": f"Moment 1, mean velocity ({min(v1, v2):g} to {max(v1, v2):g} km/s, LSR)"}
        cmap_name = None
        if kind == "m1":
            cmap_name = "coolwarm"
            s0, w0, _ = velocity_arrays(self.cube, self.eq_w, "m0", v1, v2)
            g0, _ = self.compute_grid(s0, w0)
            if np.isfinite(g0).any():
                grid = np.where(g0 > 0.2 * np.nanmax(g0), grid, np.nan)   # only where there is real emission
        unit = {"channel": "K", "m0": "K km/s", "m1": "km/s"}[kind]
        title = titles[kind] + "  - click a point for its spectrum"
        if system == "Equatorial":
            self.draw_eq(fig, grid, mask, self.n_points, label, title, unit, cmap_name, velocity=True, text=text)
        else:
            gdec, gra = gal_to_eq_index()
            self.draw_gal(fig, grid[gdec, gra], mask[gdec, gra], self.n_points, label, title, unit, cmap_name,
                          velocity=True, text=text)
        return True

    def spectra_by_l(self, bw):
        """Calibrated line spectra with |b| <= bw: list of (l_deg, b_deg, v_lsr array, T_line array)."""
        out = []
        with self.grid_lock:
            S = self.S
            for i in range(len(S["l"])):
                b = float(S["b"][i])
                tl = np.asarray(S["tline"][i], dtype=float)
                if abs(b) > bw or not np.isfinite(tl).any():
                    continue
                v = freq_to_vtopo(S["freq"][i]) + float(S["vlsr_add"][i])
                order = np.argsort(v)
                out.append((float(S["l"][i]) % 360.0, b, v[order], tl[order]))
        return out

    def draw_spectra_by_l(self, fig, bw, v1, v2):
        """All calibrated spectra on top of each other, coloured by Galactic longitude l."""
        fig.clf(); fig.set_facecolor(BG)
        sp = self.spectra_by_l(bw)
        if not sp:
            fig.text(0.5, 0.5, f"No calibrated spectra with |b| <= {bw:g}°.\nThey come from 3. Measure after "
                               "1. Cold and 2. Hot (b window: Limits).", color="white", ha="center", va="center",
                     fontsize=12)
            return False
        from matplotlib import cm, colors
        ls = np.array([s[0] for s in sp])
        norm = colors.Normalize(vmin=float(ls.min()), vmax=float(max(ls.max(), ls.min() + 1.0)))
        cmap = plt.get_cmap("turbo")
        ax = fig.add_subplot(111, facecolor=BG)
        order = np.argsort(ls)                    # low l first, so the highest l is drawn on top
        for k in order:
            l, b, v, t = sp[k]
            ax.plot(v, t, color=cmap(norm(l)), lw=0.9, alpha=0.9)
        lo, hi = sorted((v1, v2))
        ax.set_xlim(lo, hi)
        ax.axhline(0.0, color="white", alpha=0.3, lw=0.8)
        ax.set_xlabel("V_LSR (km/s)", color="white"); ax.set_ylabel("T_line (K)", color="white")
        ax.tick_params(colors="white"); ax.grid(True, color="white", alpha=0.12, linestyle="--")
        ax.set_title(f"{len(sp)} spectra, |b| <= {bw:g}°, coloured by Galactic longitude", color="white", fontsize=11)
        sm = cm.ScalarMappable(norm=norm, cmap=cmap)
        cb = fig.colorbar(sm, ax=ax, label="l (deg)")
        self.style_colorbar(cb)
        obs_line, site_line = self.site_lines()
        self.fit_text(fig, fig.text(0.01, 0.01, f"Beam {self.beam_deg():.1f}° ({self.entry_dish.get()} m)   |   {obs_line}   |   "
                             f"{site_line}", color="white", fontsize=8, family="monospace", va="bottom", ha="left"))
        fig.subplots_adjust(left=0.08, right=0.98, top=0.93, bottom=0.13)
        return True

    def lv_raw(self, bw):
        """Unsmoothed l-v table (NX, NV): weighted mean T_line per 1-degree l column of every calibrated spectrum with
        |b| <= bw, placed by the spectrum's own Galactic (l, b) - no detour through the equatorial grid, so no
        spectrum is lost. Weight = number of averaged spectra. NaN = no data. Falls back to the velocity cube for
        data that has no stored spectra."""
        v_ax = self.cube.v
        with self.grid_lock:
            S = self.S
            items = [(float(S["l"][i]), S["freq"][i], float(S["vlsr_add"][i]), S["tline"][i], float(S["counts"][i]))
                     for i in range(len(S["l"])) if abs(float(S["b"][i])) <= bw]
            have_s = len(S["l"]) > 0
        if not have_s:
            return self._lv_from_cube(bw)
        num = np.zeros((NX, len(v_ax))); den = np.zeros((NX, len(v_ax)))
        for l, f, vadd, tl, w in items:
            tl = np.asarray(tl, dtype=float)
            f = np.asarray(f, dtype=float)
            fin = np.isfinite(tl) & np.isfinite(f)          # only the channels the spectrum really has
            if fin.sum() < 2:
                continue
            sa = to_axis(freq_to_vtopo(f[fin]) + vadd, tl[fin], v_ax)
            ok = np.isfinite(sa)
            c = int(np.floor((l + 180.0) % 360.0)) % NX           # column centres: (c + 0.5 - 180) % 360
            w = max(w, 1.0)
            num[c, ok] += sa[ok] * w
            den[c, ok] += w
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan)

    def _lv_from_cube(self, bw):
        mean = self.cube.mean()
        gdec, gra = gal_to_eq_index()
        bc = (np.arange(NY) + 0.5) - 90.0
        rows = np.where(np.abs(bc) <= bw)[0]
        if len(rows) == 0:
            rows = np.array([int(np.argmin(np.abs(bc)))])
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            return np.nanmean(mean[gdec[rows, :], gra[rows, :], :], axis=0)

    def lv_smoothed(self, bw, fill=False):
        """Smoothed l-v diagram (mean T [K]) over |b| <= bw; returns (lv_s of shape (NX, NV), l centres in deg).
        fill=True (the l-v picture): like the sky maps, every cell within half a beam (in l) of a measured cell is
        shown (convolutional gridding, blank beyond half a beam). fill=False: only cells close to measured ones."""
        lv = self.lv_raw(bw)                                  # (NX, NV)
        valid = np.isfinite(lv).astype(float)
        sig = (self.sigma_cells(), 0.7)
        num = gaussian_filter(np.nan_to_num(lv, nan=0.0), sig, mode=("wrap", "nearest"))
        den = gaussian_filter(valid, sig, mode=("wrap", "nearest"))
        if fill:
            from scipy.ndimage import maximum_filter1d
            r = int(round(self.beam_deg() / 2.0))          # half a beam in l, 1 cell = 1 degree
            near = maximum_filter1d(valid, size=2 * r + 1, axis=0, mode="wrap") > 0
            ok = near & (den > 1e-6)
        else:
            ok = den > 0.1
        with np.errstate(invalid="ignore", divide="ignore"):
            lv_s = np.where(ok, num / np.where(ok, den, 1.0), np.nan)
        return lv_s, (np.arange(NX) + 0.5 - 180.0) % 360.0

    def lv_measured(self, bw):
        """l-v table (mean T [K]) over |b| <= bw from the measured longitudes only: no smoothing or filling in l
        (every column is an independent measurement), light smoothing along the velocity axis only (within each
        spectrum). Used for the rotation curve and the enclosed mass."""
        lv = self.lv_raw(bw)                                  # (NX, NV)
        valid = np.isfinite(lv)
        num = gaussian_filter1d(np.nan_to_num(lv, nan=0.0), 0.7, axis=1, mode="nearest")
        den = gaussian_filter1d(valid.astype(float), 0.7, axis=1, mode="nearest")
        with np.errstate(invalid="ignore", divide="ignore"):
            lv_m = np.where(valid & (den > 0), num / np.where(den > 0, den, 1.0), np.nan)
        return lv_m, (np.arange(NX) + 0.5 - 180.0) % 360.0

    def draw_lv(self, fig, bw):
        cube = self.cube
        lv_s, _ = self.lv_smoothed(bw, fill=True)
        ax = fig.add_subplot(111, facecolor=BG)
        cmap = plt.get_cmap(self.cmap).copy(); cmap.set_bad(BG)
        dv = float(cube.v[1] - cube.v[0]) if len(cube.v) > 1 else 1.0
        if np.isfinite(lv_s).any():
            vmin, vmax = self.color_limits(lv_s)
            im = ax.imshow(np.ma.masked_invalid(lv_s.T), extent=[-180, 180, cube.v[0] - dv / 2, cube.v[-1] + dv / 2],
                           origin="lower", aspect="auto", cmap=cmap, vmin=vmin, vmax=vmax, interpolation="bilinear")
            cb = fig.colorbar(im, ax=ax, label="Mean T (K)")
            self.style_colorbar(cb)
        ax.set_xlim(180, -180)                                 # l increases to the left
        ax.set_xticks(np.arange(-150, 181, 30)); ax.set_xticklabels([f"{d}°" for d in np.arange(-150, 181, 30)])
        ax.set_xlabel("Galactic longitude l (°)", color="white"); ax.set_ylabel("V_LSR (km/s)", color="white")
        ax.tick_params(colors="white"); ax.grid(True, color="white", alpha=0.15, linestyle="--")
        ax.set_title(f"l–v diagram (|b| ≤ {bw:g}°)", color="white", fontsize=12)
        notes = self.notes_text(velocity=True)
        obs_line, site_line = self.site_lines()
        fig.tight_layout(rect=[0, 0.12 if notes else 0.09, 1, 1])
        lines = [f"Points: {self.n_points}   |   Beam {self.beam_deg():.1f}° ({self.entry_dish.get()} m)",
                 f"{obs_line}   |   {site_line}"]
        if notes:
            lines.append(notes)
        self.fit_text(fig, fig.text(0.01, 0.01, "\n".join(lines), color="white", fontsize=8, family="monospace", va="bottom", ha="left"))
        return True

    def topview_data(self, bw):
        """Kinematic distance map from the velocity cube. Returns (result dict or None, threshold K, message)."""
        lv_s, l_c = self.lv_smoothed(bw, fill=True)     # a picture: same half-beam rule as the l-v diagram
        fin = lv_s[np.isfinite(lv_s)]
        if fin.size == 0:
            return None, 0.0, "No l-v data yet."
        med = float(np.median(fin))
        sigma = 1.4826 * float(np.median(np.abs(fin - med)))
        thr = med + TOPVIEW_SIGMA * sigma
        res = topview_grid(lv_s, l_c, self.cube.v, thr)
        if not any(np.isfinite(res[k]).any() for k in ("all", "sure", "amb")):
            return None, thr, (f"No measured directions outside l = 0 ± {TOPVIEW_EXCL_DEG:g}° and "
                               f"180 ± {TOPVIEW_EXCL_DEG:g}° yet.\nScan the Galactic plane (|b| <= {bw:g} deg).")
        return res, thr, ""

    def draw_topview(self, fig, bw, text=True):
        res, thr, msg = self.topview_data(bw)
        if res is None:
            fig.text(0.5, 0.5, msg, color="white", ha="center", va="center", fontsize=12)
            return False
        from matplotlib.colors import to_rgb, Normalize
        from matplotlib.cm import ScalarMappable
        from matplotlib.patches import Patch
        ax = fig.add_subplot(111, facecolor=BG)
        vals = np.concatenate([res[k][np.isfinite(res[k])] for k in ("all", "sure", "amb")])
        lo = float(np.percentile(vals, 2.0))
        vmax = max(float(np.percentile(vals, 99.5)), thr * 1.5, lo + 1e-3)
        norm = Normalize(vmin=lo, vmax=vmax)
        cmap = plt.get_cmap(self.cmap)
        img = np.ones(res["sure"].shape + (3,)) * np.array(to_rgb(BG))
        # three layers: faint base = all data (incl. noise), medium = two possible distances, bright = certain
        for key, alpha in (("all", 0.30), ("amb", 0.60), ("sure", 1.0)):
            m = np.isfinite(res[key])
            if m.any():
                col = cmap(norm(np.where(m, res[key], lo)))[..., :3]
                a = (alpha * m)[..., None]
                img = img * (1.0 - a) + col * a
        half = TOPVIEW_HALF_KPC
        ax.imshow(img, origin="lower", extent=res["extent"], interpolation="nearest")
        ax.set_xlim(-half, half); ax.set_ylim(-half, half); ax.set_aspect("equal")
        c = cmap(0.75)
        if not text:                             # clean picture: only the map, the centre, the Sun and the scale
            ax.plot(0, 0, marker="+", color="white", markersize=10)
            ax.plot(0, -R0_KPC, marker="*", color="#ffcc00", markersize=11)
            self.hide_ticks(ax)
            cb = fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=ax, label="Mean T (K)")
            self.style_colorbar(cb)
            fig.tight_layout(rect=[0, 0.01, 1, 0.99])
            return True
        for r in (4.0, R0_KPC, 12.0, 16.0):
            ax.add_patch(plt.Circle((0, 0), r, fill=False, color="white", alpha=0.18, linestyle="--", linewidth=0.8))
            ax.text(0.15, r, f"{r:g} kpc", color="white", alpha=0.45, fontsize=7, va="bottom")
        for lw_ in (TOPVIEW_EXCL_DEG, -TOPVIEW_EXCL_DEG, 180.0 - TOPVIEW_EXCL_DEG, 180.0 + TOPVIEW_EXCL_DEG):
            a_ = np.radians(lw_)
            ax.plot([0, -half * 1.5 * np.sin(a_)], [-R0_KPC, -R0_KPC + half * 1.5 * np.cos(a_)],
                    color="#888888", alpha=0.35, linewidth=0.8, linestyle=":")
        ax.plot(0, 0, marker="+", color="white", markersize=10)
        ax.text(0.3, 0.3, "Galactic centre", color="white", fontsize=8)
        ax.plot(0, -R0_KPC, marker="*", color="#ffcc00", markersize=11)
        ax.text(0.4, -R0_KPC - 0.2, "Sun", color="#ffcc00", fontsize=9, va="top")
        ax.set_xlim(-half, half); ax.set_ylim(-half, half); ax.set_aspect("equal")
        ax.set_xlabel("kpc  (Galactic l = 90° to the left, l = 270° to the right)", color="white")
        ax.set_ylabel("kpc  (l = 0° up)", color="white")
        ax.tick_params(colors="white")
        ax.set_title("Milky Way top view (seen from the Galactic north pole)", color="white", fontsize=12)
        cb = fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=ax, label="Mean T (K)")
        self.style_colorbar(cb)
        notes = self.notes_text(velocity=True)
        obs_line, site_line = self.site_lines()
        fig.tight_layout(rect=[0, 0.17 if notes else 0.14, 1, 1])
        # legend in the free space left of the square plot, not on top of it
        ax.apply_aspect()
        pos = ax.get_position()
        handles = [Patch(color=c, alpha=1.0, label="bright = certain\n(one distance)"),
                   Patch(color=c, alpha=0.6, label="medium = two\npossible distances"),
                   Patch(color=c, alpha=0.3, label="faint = all data\nincl. noise")]
        if pos.x0 > 0.22:
            fig.legend(handles=handles, loc="upper right", bbox_to_anchor=(pos.x0 - 0.075, pos.y1),
                       fontsize=8, facecolor=BG, labelcolor="white", edgecolor="#666666", labelspacing=1.0)
        else:
            # narrow window: reserve a strip above the title for the legend
            fig.tight_layout(rect=[0, 0.17 if notes else 0.14, 1, 0.93])
            for h_ in handles:
                h_.set_label(h_.get_label().replace("\n", " "))
            fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.995), ncol=3,
                       fontsize=7, facecolor=BG, labelcolor="white", edgecolor="#666666")
        lines = ["KINEMATIC DISTANCE MAP - APPROXIMATE. Distances come from a flat rotation model "
                 f"(R0 {R0_KPC:g} kpc, V0 {V0_KMS:g} km/s).",
                 f"Bright/medium: emission above {TOPVIEW_SIGMA:g} sigma ({thr:.2f} K). Faint: every l-v cell within half a beam "
                 f"of a measurement, also noise; both near and far distances drawn. |b| <= {bw:g} deg.",
                 f"Not computed within {TOPVIEW_EXCL_DEG:g} deg of l = 0 and 180 deg (dotted lines). "
                 f"Streaming motions (about 7 km/s) blur structures by ~1 kpc.",
                 f"Points: {self.n_points}   |   Beam {self.beam_deg():.1f}° ({self.entry_dish.get()} m)   |   {obs_line}   |   {site_line}"]
        if notes:
            lines.append(notes)
        self.fit_text(fig, fig.text(0.01, 0.01, "\n".join(lines), color="white", fontsize=7, family="monospace", va="bottom", ha="left"))
        return True

    def rotation_data(self, bw):
        """(points, threshold K, message). Emission threshold TOPVIEW_SIGMA above the median. One point per measured
        longitude (no smoothing or filling in l), so every point of the curve is an independent measurement."""
        lv_s, l_c = self.lv_measured(bw)
        fin = lv_s[np.isfinite(lv_s)]
        if fin.size == 0:
            return [], 0.0, "No l-v data yet."
        med = float(np.median(fin))
        sigma = 1.4826 * float(np.median(np.abs(fin - med)))
        thr = med + TOPVIEW_SIGMA * sigma
        pts = rotation_curve(lv_s, l_c, self.cube.v, thr)
        if not pts:
            return [], thr, (f"No emission above {TOPVIEW_SIGMA:g} sigma ({thr:.2f} K) at l = {ROT_L_MIN_DEG:g}-"
                             f"{ROT_L_MAX_DEG:g}° or {360 - ROT_L_MAX_DEG:g}-{360 - ROT_L_MIN_DEG:g}° yet.\n"
                             f"Scan the inner Galactic plane (|b| <= {bw:g}°).")
        return pts, thr, ""

    def draw_rotation(self, fig, bw, kind):
        pts, thr, msg = self.rotation_data(bw)
        if not pts:
            fig.text(0.5, 0.5, msg, color="white", ha="center", va="center", fontsize=12)
            return False
        ax = fig.add_subplot(111, facecolor=BG)
        for quad, col, mk in ((1, "#ffcc00", "o"), (4, "#00e5ff", "s")):
            q = [p for p in pts if p["quadrant"] == quad]
            if not q:
                continue
            r = [p["R_kpc"] for p in q]
            y = [p["v_rot_kms"] for p in q] if kind == "rot" else [p["mass_msun"] / 1e10 for p in q]
            ax.plot(r, y, mk, color=col, ms=5, label=f"{'1st' if quad == 1 else '4th'} quadrant "
                                                       f"(l {'10-85' if quad == 1 else '275-350'}°)")
        rr = np.linspace(0.5, R0_KPC, 50)
        if kind == "rot":
            ax.plot(rr, np.full_like(rr, V0_KMS), "--", color="#aaaaaa", lw=1, label=f"flat curve V0 = {V0_KMS:g} km/s")
            ax.set_ylabel("Rotation speed V (km/s)", color="white")
            ax.set_ylim(0, max(300.0, max(p["v_rot_kms"] for p in pts) * 1.1))
            title = "Milky Way rotation curve (tangent-point method)"
        else:
            ax.plot(rr, V0_KMS ** 2 * rr / G_KPC / 1e10, "--", color="#aaaaaa", lw=1,
                    label=f"flat curve: M = V0² R / G")
            ax.set_ylabel("Enclosed mass M(<R) (10¹⁰ solar masses)", color="white")
            ax.set_ylim(bottom=0)
            title = "Mass inside radius R (from the rotation curve, spherical approximation)"
        ax.set_xlim(0, R0_KPC + 0.3)
        ax.set_xlabel("Distance from the Galactic centre R (kpc)", color="white")
        ax.tick_params(colors="white"); ax.grid(True, color="white", alpha=0.15, linestyle="--")
        ax.legend(loc="lower right", fontsize=8, facecolor=BG, labelcolor="white", edgecolor="#666666")
        ax.set_title(title, color="white", fontsize=12)
        notes = self.notes_text(velocity=True)
        obs_line, site_line = self.site_lines()
        fig.tight_layout(rect=[0, 0.16 if notes else 0.13, 1, 1])
        lines = [f"APPROXIMATE. V = |V_terminal| + V0 |sin l|, R = R0 |sin l| (R0 {R0_KPC:g} kpc, V0 {V0_KMS:g} km/s), "
                 f"M = V² R / G.",
                 f"Terminal velocity = highest |V_LSR| with emission above {TOPVIEW_SIGMA:g} sigma ({thr:.2f} K), "
                 f"|b| <= {bw:g}°. The wide beam and gas motions bias it by roughly 5-10 km/s.",
                 f"Points: {self.n_points}   |   Beam {self.beam_deg():.1f}° ({self.entry_dish.get()} m)   |   "
                 f"{obs_line}   |   {site_line}"]
        if notes:
            lines.append(notes)
        self.fit_text(fig, fig.text(0.01, 0.01, "\n".join(lines), color="white", fontsize=7, family="monospace", va="bottom", ha="left"))
        return True

    def site_lines(self):
        """Observer and site text for the map info boxes and the log header."""
        obs = self.entry_observer.get().strip() or "-"
        try:
            lat = float(self.entries["lat"].get())
            lon = float(self.entries["lon"].get())
            h = float(self.entries["height"].get())
            site = (f"Site: {abs(lat):.3f}° {'N' if lat >= 0 else 'S'}, "
                    f"{abs(lon):.3f}° {'E' if lon >= 0 else 'W'}, {h:.0f} m")
        except ValueError:
            site = "Site: -"
        return f"Observer: {obs}", site

    def notes_text(self, velocity=False):
        parts = []
        d = self.defaults_used()
        if d:
            parts.append("Defaults used: " + ", ".join(d))
        if velocity and not self.ppm_var.get():
            parts.append("Velocity zero not verified (PPM not confirmed)")
        if velocity and abs(self.ppm_corr) > 1e-9:
            parts.append(f"PPM correction applied in software: {self.ppm_corr:+g} ppm "
                         f"({ppm_shift_kms(self.ppm_corr):+.1f} km/s)")
        if self.lsr_failed:
            parts.append("Doppler correction unavailable for some samples")
        return "   |   ".join(parts)

    def defaults_used(self):
        """Names of the groups of settings that still have their default values."""
        def same(entry, default):
            try:
                return abs(float(entry.get().replace(",", ".")) - default) < 1e-9
            except ValueError:
                return False
        used = []
        if same(self.entry_dish, DEFAULT_DISH):
            used.append("dish")
        if (same(self.entries["lat"], DEFAULT_LAT) and same(self.entries["lon"], DEFAULT_LON)
                and same(self.entries["height"], DEFAULT_HEIGHT)):
            used.append("site")
        if self.scan_mode.get() == "drift" and same(self.entries["az"], DEFAULT_AZ) \
                and same(self.entries["alt"], DEFAULT_ALT):
            used.append("pointing")
        return used


    # ---------------- profile at a map point ----------------
    def profile(self, ra_i, dec_i):
        """Mean line spectrum of the cells within a quarter beam of (ra_i, dec_i). Returns a dict or None."""
        cube = self.cube
        if cube is None:
            return None
        r = max(0, int(round(self.beam_deg() / 4.0)))
        d0, d1 = max(0, dec_i - r), min(NY, dec_i + r + 1)
        rr = lon_halfwidth(r, dec_i - 90.0 + 0.5)          # the same distance on the sky in RA as in Dec
        ras = np.unique((ra_i + np.arange(-rr, rr + 1)) % NX)
        with self.grid_lock:
            ssum = cube.sum[d0:d1][:, ras, :].sum(axis=(0, 1))
            scnt = cube.cnt[d0:d1][:, ras, :].sum(axis=(0, 1))
            n_cells = int((self.eq_w[d0:d1][:, ras] > 0).sum())
            ra_set = set(ras.tolist())
            idx = [i for i, (ra_h, dec) in enumerate(zip(self.S["ra_h"], self.S["dec_d"]))
                   if d0 <= int(np.floor(dec + 90.0)) < d1 and int(ra_h * 15.0) % NX in ra_set]
            n_spec = len(idx)
            cl = self.S.get("cal", [])
            cals = [cl[i] if i < len(cl) else None for i in idx]
            times = [self.S["time"][i] for i in idx]
        with np.errstate(invalid="ignore", divide="ignore"):
            prof = np.where(scnt > 0, ssum / scnt, np.nan)
        if not np.isfinite(prof).any():
            return None
        ra_deg, dec_deg = ra_i + 0.5, dec_i - 90.0 + 0.5
        l, b = eq_to_gal_deg(np.array([ra_deg]), np.array([dec_deg]))
        k = int(np.nanargmax(prof))
        return {"v": cube.v, "t": prof, "ra_deg": ra_deg, "dec_deg": dec_deg, "l_deg": float(l[0]) % 360.0,
                "b_deg": float(b[0]), "n_cells": n_cells, "n_spectra": n_spec, "peak_K": float(prof[k]),
                "peak_v": float(cube.v[k]), "radius_cells": r, "ra_i": ra_i, "dec_i": dec_i, "cals": cals,
                "times": times}

    # ---------------- opening saved data ----------------
    def load_npz(self, path):
        """H1-Hunter v1 data file (.npz). Returns its metadata. The data are converted to text when saved again."""
        z = np.load(path, allow_pickle=False)
        meta = json.loads(str(z["meta_json"]))
        S = {k: [] for k in self.S}
        if "spec_len" in z.files:
            lens = z["spec_len"]
            offs = np.concatenate([[0], np.cumsum(lens)])
            for i in range(len(lens)):
                a, b = int(offs[i]), int(offs[i + 1])
                f = z["spec_freq"][a:b]
                if S["freq"] and np.array_equal(S["freq"][-1], f):
                    f = S["freq"][-1]
                S["freq"].append(f); S["power"].append(z["spec_power"][a:b])
                S["tk"].append(z["spec_tk"][a:b]); S["tline"].append(z["spec_tline"][a:b])
            S["time"] = [str(t) for t in z["spec_time"]]
            for k in ("ra_h", "dec_d", "l", "b", "alt", "vlsr_add", "counts"):
                S[k] = z["spec_" + k].tolist()
            S["cal_idx"] = z["spec_cal_idx"].astype(int).tolist()
            for k in ("rfi", "value", "tcont"):
                S[k] = z["spec_" + k].tolist() if ("spec_" + k) in z.files else [0.0] * len(lens)
        cube = None
        if "cube_sum" in z.files:
            cube = VelCube(z["v_axis"])
            cube.sum = z["cube_sum"].astype(np.float32)
            cube.cnt = z["cube_cnt"].astype(np.float32)
        with self.grid_lock:
            self.eq_sum, self.eq_w = z["eq_sum"].astype(float), z["eq_w"].astype(float)
            self.gal_sum, self.gal_w = z["gal_sum"].astype(float), z["gal_w"].astype(float)
            self.n_points = int(meta.get("n_points", len(S["time"])))
            self.S = S
            self.cube = cube
            self.v_axis = cube.v if cube is not None else None
        self.map_unit = meta.get("map_unit", "K")
        self.lsr_failed = bool(meta.get("doppler_failed_for_some_samples", False))
        self.dirty = True
        self.last_drawn = {"eq": -1, "gal": -1}
        return meta

    def load_package(self, folder):
        """Science package written by this program (plain text): spectra/ is read back and every spectrum is
        added to the maps again. Returns metadata.json (or {})."""
        P = lambda *a: os.path.join(folder, *a)
        idx_path = P("spectra", "spectra_index.csv")
        if not os.path.isfile(idx_path):
            raise ValueError("This folder has no spectra/spectra_index.csv (not a science package of this program).")
        meta = {}
        if os.path.isfile(P("metadata.json")):
            with open(P("metadata.json"), "r", encoding="utf-8") as fh:
                meta = json.load(fh)

        def wide(name):
            path = P("spectra", name)
            if not os.path.isfile(path):
                return None, None
            with open(path, "r", encoding="utf-8") as fh:
                head = fh.readline().strip().split(",")[1:]
            arr = np.loadtxt(path, delimiter=",", skiprows=1, ndmin=2)
            return np.array([float(x) for x in head]), arr[:, 1:]
        f_mhz, tl = wide("spectra_Tline_K.csv")
        _, pw = wide("spectra_power_raw.csv")
        _, tk_ = wide("spectra_T_total_K.csv")
        rows = []
        with open(idx_path, "r", encoding="utf-8") as fh:
            names = fh.readline().strip().split(",")
            for line in fh:
                if line.strip():
                    rows.append(dict(zip(names, line.strip().split(","))))
        # Packages written before format 2 put spectra with different channel sets side by side by POSITION, so
        # their columns did not match the header frequencies. Those spectra are read from their own average files
        # (spectra/averages, matched by the middle time) instead; spectra without one are skipped.
        old_format = int(meta.get("spectra_table_format", 1)) < 2 and \
            not meta.get("same_frequency_axis_for_all_spectra", True)
        own = read_average_files(P("spectra", "averages")) if old_format else {}
        cals = read_average_calibrations(P("spectra", "averages"))
        imp = P("imported")                              # earlier data carried in this package (own subfolders)
        if os.path.isdir(imp):
            for entry in sorted(os.listdir(imp)):
                for sub in (("spectra", "averages"), ()):
                    d = os.path.join(imp, entry, *sub)
                    for k, c in read_average_calibrations(d).items():
                        cals.setdefault(k, c)
        self.reset_grids()
        skipped = 0
        for i, r in enumerate(rows):
            ra_h, dec_d = float(r["ra_deg"]) / 15.0, float(r["dec_deg"])
            l_c = ((float(r["l_deg"]) + 180.0) % 360.0) - 180.0
            value, counts = float(r["line_integral_K_MHz"]), float(r["counts"])
            vadd = float(r["v_corr_kms"])
            if old_format:
                a = own.get(r["time_utc"])
                if a is None:
                    skipped += 1
                    continue
                f_i, tl_i, tk_i, pw_i = a["freq"], a["tline"], a["tk"], a["power"]
            else:
                f_i = f_mhz
                tl_i = tl[i] if tl is not None else None
                tk_i = tk_[i] if tk_ is not None else None
                pw_i = pw[i] if pw is not None else None
            spec_axis = None
            if tl_i is not None and f_i is not None:
                keep = np.isfinite(f_i) & np.isfinite(tl_i)
                if self.cube is None and keep.any():
                    self.v_axis = make_velocity_axis(f_i[keep])
                    self.cube = VelCube(self.v_axis)
                if keep.any():
                    spec_axis = to_axis(freq_to_vtopo(f_i[keep]) + vadd, tl_i[keep], self.cube.v)
            nan = np.full(len(f_i) if f_i is not None else 0, np.nan)
            rec = {"kind": "spectrum", "freq": f_i if f_i is not None else nan,
                   "power_raw": pw_i if pw_i is not None else nan, "rfi": float(r.get("rfi_fraction", 0) or 0)}
            self.add_sample(ra_h, dec_d, l_c, float(r["b_deg"]), value, counts, float(r["alt_deg"]), vadd, counts,
                            spec_axis, r["time_utc"], rec, tk_i if tk_i is not None else nan,
                            tl_i if tl_i is not None else nan, float(r.get("T_continuum_K", 0) or 0),
                            cal=cals.get(r["time_utc"]))
        self.dirty = True
        self.last_drawn = {"eq": -1, "gal": -1}
        meta["_old_format_read_from_averages"] = old_format
        meta["_skipped_no_average_file"] = skipped
        return meta


def cal_from_meta(meta):
    """Calibration of one saved average from its header (None if the file has no Cold)."""
    if "Cold_time_UTC" not in meta:
        return None

    def fnum(k):
        try:
            return float(meta[k])
        except (KeyError, ValueError):
            return None
    cal = {"cold_time_utc": meta["Cold_time_UTC"], "cold_T_ref_K": fnum("Cold_T_ref_K")}
    if "Hot_time_UTC" in meta:
        cal.update({"hot_time_utc": meta["Hot_time_UTC"], "hot_T_ref_K": fnum("Hot_T_ref_K"),
                    "y_factor": fnum("Y_factor"), "tsys_K": fnum("Tsys_K")})
    return cal


def read_average_calibrations(folder):
    """{middle time UTC: calibration} of the saved averages of a folder (headers only)."""
    out = {}
    if not os.path.isdir(folder):
        return out
    for name in sorted(os.listdir(folder)):
        if not (name.startswith("H1_") and name.endswith(".csv")) or "_counts" in name or "_flagged" in name:
            continue
        meta = {}
        try:
            with open(os.path.join(folder, name), "r", encoding="utf-8") as fh:
                for line in fh:
                    if not line.startswith("#"):
                        break
                    if ":" in line:
                        k, v = line[1:].split(":", 1)
                        meta[k.strip()] = v.strip()
        except OSError:
            continue
        if meta.get("Data", "measure") == "measure" and "Average_mid_UTC" in meta:
            out[meta["Average_mid_UTC"]] = cal_from_meta(meta)
    return out


def read_average_files(folder):
    """Saved averages (H1_*.csv, Data: measure) of a folder, by their middle time: {time_utc: dict of arrays}.
    The arrays are the trusted, not RFI-removed channels, corrected for the atmosphere like the live maps."""
    from .physics import atmosphere_attenuation
    out = {}
    if not os.path.isdir(folder):
        return out
    for name in sorted(os.listdir(folder)):
        if not (name.startswith("H1_") and name.endswith(".csv")) or "_counts" in name or "_flagged" in name:
            continue
        try:
            meta, names, rows = {}, None, []
            with open(os.path.join(folder, name), "r", encoding="utf-8") as fh:
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
            if meta.get("Data", "measure") != "measure" or not rows or names is None or "t_line_K" not in names:
                continue
            arr = np.array(rows)
            col = {nm: arr[:, j] for j, nm in enumerate(names)}
            good = np.isfinite(col["t_line_K"])
            if "trusted" in col:
                good &= col["trusted"] > 0.5
            if "rfi_removed" in col:
                good &= col["rfi_removed"] < 0.5
            att = atmosphere_attenuation(float(meta.get("Pointing_alt_deg", 45)), float(meta.get("Site_height_m", 0)))
            nan = np.full(int(good.sum()), np.nan)
            out[meta.get("Average_mid_UTC", "")] = {
                "freq": col["freq_hz"][good] / 1e6, "tline": col["t_line_K"][good] * att,
                "tk": col["t_ant_K"][good] * att if "t_ant_K" in col else nan,
                "power": col["power_raw"][good] if "power_raw" in col else nan}
        except Exception:
            continue
    return out


def draw_pointing(ax, ra_deg, dec_deg, beam_deg, rmax):
    """Pointing marker on a polar map axes: the observed beam area (at least POLAR_MIN_BEAM_DEG wide) and a cross.
    Returns the artists (removed again before the next marker)."""
    rho = np.radians(max(beam_deg, POLAR_MIN_BEAM_DEG) / 2.0)
    pa = np.radians(np.linspace(0.0, 360.0, 121))
    d0, r0 = np.radians(dec_deg), np.radians(ra_deg)
    dec2 = np.arcsin(np.clip(np.sin(d0) * np.cos(rho) + np.cos(d0) * np.sin(rho) * np.cos(pa), -1.0, 1.0))
    ra2 = np.unwrap(r0 + np.arctan2(np.sin(pa) * np.sin(rho) * np.cos(d0), np.cos(rho) - np.sin(d0) * np.sin(dec2)))
    rr = 90.0 - np.degrees(dec2)
    area = ax.fill(ra2, rr, color=POLAR_MARK_COLOR, alpha=0.28, linewidth=0)[0]
    rim = ax.plot(ra2, rr, color=POLAR_MARK_COLOR, linewidth=1.4)[0]
    dot = ax.plot([r0], [90.0 - dec_deg], marker="+", markersize=13, markeredgewidth=2, color=POLAR_MARK_COLOR,
                  linestyle="none")[0]
    ax.set_rlim(0.0, rmax)
    return [area, rim, dot]
