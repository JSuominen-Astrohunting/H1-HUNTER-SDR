"""H1-HUNTER SDR - science package: one folder with everything (plain text + PNG images; FITS cube and ezRA file
only when chosen). Can be analysed without this program."""
import os
import json
import time
import shutil
import warnings
import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
from .common import *
from .physics import freq_to_vtopo as freq_hz_to_vtopo
from .maps import (NX, NY, R0_KPC, V0_KMS, TOPVIEW_SIGMA, TOPVIEW_EXCL_DEG, to_axis, velocity_arrays,
                   gal_to_eq_index, eq_to_gal_deg)
from .files import EzraWriter

PACKAGE_README = f"""{APP} science package - how to read the files
==================================================

Everything here is plain text (CSV / TXT / JSON) or PNG images, so it opens in Excel, Python, R, Notepad and so
on. {APP} is not needed. Optional binary files: cube.fits (only if chosen) and station_packet/*.sigmf-data (only
if raw IQ was saved).

summary.txt .............. human-readable summary: observation, calibration (and which values were estimated),
                           velocity frame, data quality, warnings. READ THIS FIRST.
metadata.json ............ the same information in machine-readable form.
settings.json ............ every setting of the program when the package was written.
scan_log.csv ............. one row per finished average: time, RA/Dec, l/b, line strength, flagged spectra.

spectra/
  averages/ .............. every saved average as its own CSV (frequency, velocity, raw power, T_ant, T_line,
                           T_B ... with the full metadata in the header) + average count logs and flagged spectra.
  spectra_index.csv ...... one row per average used in the maps: time (UTC), RA/Dec, l/b, altitude, average counts,
                           Doppler correction (v_corr_kms), line integral (K MHz).
  spectra_power_raw.csv .. every average as linear power. Column names = channel frequencies in MHz.
  spectra_T_total_K.csv .. the same calibrated to antenna temperature (K).
  spectra_Tline_K.csv .... line temperature (K), baseline subtracted.
  spectra_Tline_K_on_LSR_grid.csv  line temperature on the common LSR velocity grid (column names in km/s).

  Velocity of a channel:  v_LSR = c (f0 - f) / f0 + v_corr_kms,  c = 299792.458 km/s, f0 = 1420.405752 MHz.
  The PPM correction is already applied to the frequencies.

maps/
  sky_cells.csv .......... one row per measured 1-degree sky cell: RA, Dec, l, b, weight, line integral, moment 0
                           (K km/s), moment 1 (km/s), peak temperature and its velocity. No smoothing.
  lv_diagram.csv ......... l (deg), V_LSR (km/s), mean T (K) over the latitude window (no smoothing).
  spectra_by_l.csv ....... every calibrated spectrum in the latitude window: l, b (deg), V_LSR (km/s), T_line (K).
  velocity_axis_kms.csv .. the velocity grid of the cube.
  milkyway_topview.csv ... kinematic distance map: X, Y (kpc; Galactic centre 0,0, Sun 0,-{R0_KPC:g}), mean T (K)
                           and the layer: 2 = certain (one distance), 1 = two possible distances, 0 = all data
                           including noise (both distances). APPROXIMATE, flat rotation model.
  rotation_curve.csv ..... tangent-point rotation curve: l, quadrant, terminal velocity, R (kpc), V_rot (km/s) and
                           the enclosed mass M(<R) = V^2 R / G (solar masses). APPROXIMATE.
  saved/ ................. spectra saved at selected map points (Maps, right-click the spectrum): position, region,
                           number of spectra and their calibrations in the header; the image is in images/saved/.

calibrations/ ............ Cold, Hot (and reference load) spectra as CSV + calibrations.json (all calibrations).

imported/ ................ only if earlier data was opened into the maps: that data unchanged and apart from this
                           session's files (spectra/averages above holds only this session's averages). The spectra
                           tables and the maps contain both.
  <package name>/ ........ an opened science package: its spectra/averages, calibrations, metadata.json and
                           summary.txt. Its own imported/ folders are carried over beside it (the full history).
  files/ ................. spectrum files imported one by one (Import spectrum files).

images/ .................. every map, the polar map (without the pointing marker), hydrogen line, radiometer check,
                           RF spectrum and waterfall.
  saved/ ................. images saved during the observation with the "Save image" buttons.

station_packet/ .......... only if raw IQ was saved: the SigMF recordings of Cold, Hot and Measure (iq.sigmf-data
                           binary, iq.sigmf-meta with the phase and the gaps, iq_blocks.csv, iq_excerpt.csv,
                           station.json when the clock was GPS synchronized).
ezRA/ .................... only if chosen: the averages as an ezRA data file (for ezCon and SHOOS sharing).

Units: temperatures in K, line integrals in K MHz, moments in K km/s, velocities in km/s (LSR).
The Kelvin scale rests on model reference temperatures (see summary.txt).
"""


def _fig(w=10, h=5.5):
    fig = Figure(figsize=(w, h), facecolor=BG)
    FigureCanvasAgg(fig)
    return fig


def _link_or_copy(src, dst):
    """Large files: a hard link when possible (instant, no extra disk space), otherwise a copy."""
    try:
        os.link(src, dst)
    except Exception:
        shutil.copy2(src, dst)


def table_cells(ms, path, v1, v2):
    with ms.grid_lock:
        eq_sum, eq_w, cube = ms.eq_sum.copy(), ms.eq_w.copy(), ms.cube
    idx = np.argwhere(eq_w > 0)
    if not len(idx):
        raise ValueError("No measured cells yet.")
    di, ri = idx[:, 0], idx[:, 1]
    ra, dec = ri + 0.5, di - 90.0 + 0.5
    l, b = eq_to_gal_deg(ra, dec)
    cols = [ra, dec, l % 360.0, b, eq_w[di, ri], eq_sum[di, ri] / eq_w[di, ri]]
    names = "ra_deg,dec_deg,l_deg,b_deg,weight,line_integral_K_MHz"
    if cube is not None:
        s0, w0, _ = velocity_arrays(cube, eq_w, "m0", v1, v2)
        s1, w1, _ = velocity_arrays(cube, eq_w, "m1", v1, v2)
        with np.errstate(invalid="ignore", divide="ignore"):
            m0 = np.where(w0 > 0, s0 / w0, np.nan)[di, ri]
            m1 = np.where(w1 > 0, s1 / w1, np.nan)[di, ri]
        sub = cube.mean()[di, ri, :]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            tp = np.nanmax(sub, axis=1)
        vpk = cube.v[np.argmax(np.where(np.isfinite(sub), sub, -np.inf), axis=1)]
        cols += [m0, m1, tp, vpk]
        names += f",moment0_K_kms_{v1:g}_to_{v2:g},moment1_kms_LSR,Tpeak_K,v_at_Tpeak_kms"
    np.savetxt(path, np.column_stack(cols), delimiter=",", header=names, comments="", fmt="%.5g")


def table_spectra_by_l(ms, path, bw):
    """Spectra by l as a long table: one row per channel of every calibrated spectrum with |b| <= bw."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(f"# H1-HUNTER SDR - calibrated spectra with |b| <= {bw:g} deg (Spectra by l)\n")
        fh.write("l_deg,b_deg,v_lsr_kms,T_line_K\n")
        for l, b, v, tl in ms.spectra_by_l(bw):
            for vv, tt in zip(v, tl):
                if np.isfinite(tt):
                    fh.write(f"{l:.3f},{b:.3f},{vv:.3f},{tt:.5f}\n")


def table_lv(ms, path, bw):
    cube = ms.cube
    if cube is None:
        raise ValueError("No spectra yet.")
    lv = ms.lv_raw(bw)                       # every calibrated spectrum by its own (l, b), no smoothing
    ii, jj = np.where(np.isfinite(lv))
    arr = np.column_stack([(ii + 0.5 - 180.0) % 360.0, cube.v[jj], lv[ii, jj]])
    np.savetxt(path, arr, delimiter=",", fmt=["%.1f", "%.2f", "%.5g"],
               header=f"l_deg,v_lsr_kms,mean_T_K   (mean over |b|<={bw:g} deg, no smoothing)", comments="")


def table_topview(ms, path, bw):
    res, thr, msg = ms.topview_data(bw)
    if res is None:
        raise ValueError(msg.replace("\n", " "))
    n_ = res["sure"].shape[0]
    c_ = (np.arange(n_) + 0.5) * res["cell"] + res["extent"][0]
    X, Y = np.meshgrid(c_, c_)
    rows = []
    for key, layer in (("sure", 2), ("amb", 1), ("all", 0)):
        m_ = np.isfinite(res[key])
        rows.append(np.column_stack([X[m_], Y[m_], res[key][m_], np.full(int(m_.sum()), layer)]))
    np.savetxt(path, np.vstack(rows), delimiter=",", fmt=["%.2f", "%.2f", "%.4g", "%d"],
               header=(f"x_kpc,y_kpc,mean_T_K,layer   layer 2 = certain (one distance), 1 = two possible distances, "
                       f"0 = all data incl. noise. KINEMATIC DISTANCE MAP - APPROXIMATE (flat rotation, R0={R0_KPC:g} "
                       f"kpc, V0={V0_KMS:g} km/s, threshold {TOPVIEW_SIGMA:g} sigma = {thr:.3f} K, |b|<={bw:g} deg)"),
               comments="")


def table_rotation(ms, path, bw):
    pts, thr, msg = ms.rotation_data(bw)
    if not pts:
        raise ValueError(msg.replace("\n", " "))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"# Rotation curve, tangent-point method - APPROXIMATE. V = |V_terminal| + V0|sin l|, R = R0|sin l|, "
                 f"M = V^2 R / G. R0={R0_KPC:g} kpc, V0={V0_KMS:g} km/s, emission above {TOPVIEW_SIGMA:g} sigma = "
                 f"{thr:.3f} K, |b|<={bw:g} deg\n")
        fh.write("l_deg,quadrant,v_terminal_kms,R_kpc,v_rot_kms,mass_enclosed_msun\n")
        for q in pts:
            fh.write(f"{q['l_deg']:.2f},{q['quadrant']},{q['v_terminal_kms']:.2f},{q['R_kpc']:.3f},"
                     f"{q['v_rot_kms']:.2f},{q['mass_msun']:.4g}\n")


def table_velocity_grid(ms, path, kind, v1, v2):
    """Channel / moment 0 / moment 1 values per measured cell (no smoothing)."""
    with ms.grid_lock:
        eq_w, cube = ms.eq_w.copy(), ms.cube
    if cube is None:
        raise ValueError("No spectra yet.")
    s, w, label = velocity_arrays(cube, eq_w, kind, v1, v2)
    idx = np.argwhere(w > 0)
    di, ri = idx[:, 0], idx[:, 1]
    ra, dec = ri + 0.5, di - 90.0 + 0.5
    l, b = eq_to_gal_deg(ra, dec)
    val = s[di, ri] / w[di, ri]
    np.savetxt(path, np.column_stack([ra, dec, l % 360.0, b, val]), delimiter=",", fmt="%.5g", comments="",
               header=f"ra_deg,dec_deg,l_deg,b_deg,value   ({label}, {min(v1, v2):g} to {max(v1, v2):g} km/s LSR)")


def write_spectra_tables(ms, folder, step):
    with ms.grid_lock:
        S = {k: list(v) for k, v in ms.S.items()}
        cube = ms.cube
    n = len(S["time"])
    if not n:
        return True
    P = lambda name: os.path.join(folder, name)

    def index():
        with open(P("spectra_index.csv"), "w", encoding="utf-8") as fh:
            fh.write("sample,time_utc,ra_deg,dec_deg,l_deg,b_deg,alt_deg,counts,v_corr_kms,calibration_index,"
                     "rfi_fraction,line_integral_K_MHz,T_continuum_K\n")
            for i in range(n):
                fh.write(f"{i},{S['time'][i]},{S['ra_h'][i] * 15.0:.5f},{S['dec_d'][i]:.5f},{S['l'][i]:.4f},"
                         f"{S['b'][i]:.4f},{S['alt'][i]:.2f},{S['counts'][i]:.0f},{S['vlsr_add'][i]:.4f},"
                         f"{S['cal_idx'][i]},{S['rfi'][i]:.5f},{S['value'][i]:.6g},{S['tcont'][i]:.4f}\n")
    step("spectra/spectra_index.csv", index)
    # One common frequency grid for all spectra: the union of every spectrum's own channel frequencies (1 Hz
    # resolution). Each value is written in the column of ITS OWN frequency; channels a spectrum does not have
    # (untrusted, removed RFI, other tuning) are NaN. Never aligned by position - spectra can have different
    # channel sets.
    keys = [np.round(np.asarray(f, dtype=float) * 1e6).astype(np.int64) for f in S["freq"]]
    grid = np.unique(np.concatenate(keys)) if keys else np.zeros(0, np.int64)
    cols = [np.searchsorted(grid, k) for k in keys]
    nch = len(grid)
    ids = np.arange(n)
    header = "sample," + ",".join(f"{x / 1e6:.6f}" for x in grid)
    d = np.diff(grid)
    step0 = float(np.min(d)) if d.size else 1.0
    q = d / step0
    same_axes = bool(d.size == 0 or np.all(np.abs(q - np.round(q)) <= 0.01))   # all on one receiver channel raster

    def wide(name, key, fmt_):
        m = np.full((n, nch), np.nan)
        for i, a in enumerate(S[key]):
            a = np.asarray(a, dtype=float)
            if len(a) == len(cols[i]):
                m[i, cols[i]] = a
        np.savetxt(P(name), np.column_stack([ids, m]), delimiter=",", fmt=["%d"] + [fmt_] * nch, header=header,
                   comments="")
    step("spectra/spectra_power_raw.csv", lambda: wide("spectra_power_raw.csv", "power", "%.8g"))
    step("spectra/spectra_T_total_K.csv", lambda: wide("spectra_T_total_K.csv", "tk", "%.5g"))
    step("spectra/spectra_Tline_K.csv", lambda: wide("spectra_Tline_K.csv", "tline", "%.5g"))
    if cube is not None:
        def lsr_grid():
            vax = cube.v
            def one(i):
                f_ = np.asarray(S["freq"][i], dtype=float); t_ = np.asarray(S["tline"][i], dtype=float)
                fin = np.isfinite(f_) & np.isfinite(t_)          # only the channels the spectrum really has
                if fin.sum() < 2:
                    return np.full(len(vax), np.nan)
                return to_axis(freq_hz_to_vtopo(f_[fin] * 1e6) + S["vlsr_add"][i], t_[fin], vax)
            m = np.array([one(i) for i in range(n)])
            np.savetxt(P("spectra_Tline_K_on_LSR_grid.csv"), np.column_stack([ids, m]), delimiter=",",
                       fmt=["%d"] + ["%.5g"] * len(vax), comments="",
                       header="sample," + ",".join(f"{v:.2f}" for v in vax))
        step("spectra/spectra_Tline_K_on_LSR_grid.csv", lsr_grid)
    return same_axes


def save_fits(ms, path, meta):
    from astropy.io import fits
    cube = ms.cube
    if cube is None or ms.n_points == 0:
        raise ValueError("FITS needs spectra: there is no velocity cube yet.")
    mean = np.transpose(cube.mean(), (2, 0, 1)).astype(np.float32)      # (v, dec, ra)
    hdu = fits.PrimaryHDU(mean)
    h = hdu.header
    h["BUNIT"] = "K"
    h["CTYPE1"], h["CRPIX1"], h["CRVAL1"], h["CDELT1"], h["CUNIT1"] = "RA---CAR", 0.5, 0.0, 1.0, "deg"
    h["CTYPE2"], h["CRPIX2"], h["CRVAL2"], h["CDELT2"], h["CUNIT2"] = "DEC--CAR", 90.5, 0.0, 1.0, "deg"
    dv = float(cube.v[1] - cube.v[0]) if len(cube.v) > 1 else 1.0
    h["CTYPE3"], h["CRPIX3"], h["CRVAL3"], h["CDELT3"], h["CUNIT3"] = "VRAD", 1.0, float(cube.v[0]), dv, "km/s"
    h["SPECSYS"] = "LSRK"
    h["RESTFRQ"] = H1_HZ
    h["OBSERVER"] = meta.get("observer") or "-"
    site = meta.get("site", {})
    h["SITELAT"], h["SITELONG"], h["SITEELEV"] = site.get("lat_deg", 0.0), site.get("lon_deg", 0.0), site.get("height_m", 0.0)
    h["DISH_M"] = meta.get("dish_m", 0.0)
    h["NSAMPLES"] = ms.n_points
    h["COMMENT"] = f"{APP} v{VERSION}. Cube of mean line temperature T_A (baseline subtracted)."
    h["COMMENT"] = "Axes: RA, Dec (1 deg cells), V_LSR. NaN = no data."
    fits.HDUList([hdu, fits.ImageHDU(ms.eq_w.astype(np.float32), name="WEIGHT")]).writeto(path, overwrite=True)


def write_ezra(rows, folder, site, name):
    """rows: list of (t_mid, p, ref, axis_hz, pointing). One file per frequency axis (normally one)."""
    groups = []
    for r in rows:
        for g in groups:
            if len(g[0][3]) == len(r[3]) and np.allclose(g[0][3], r[3]):
                g.append(r)
                break
        else:
            groups.append([r])
    out = []
    for i, g in enumerate(groups):
        stamp = time.strftime("%y%m%d_%H%M", time.gmtime(g[0][0]))
        path = os.path.join(folder, f"{name}_ezRA_{stamp}" + (f"_{i + 1}" if len(groups) > 1 else "") + ".txt")
        w = EzraWriter(path, site, name, g[0][3])
        for t, p, ref, _, pointing in g:
            w.add(t, p, ref=ref, pointing=pointing)
        out.append(path)
    return out


def build_summary(app, meta, same_axes, written, failed):
    ms = app.maps
    L = []
    add = L.append
    S = ms.S
    n = len(S["time"])
    add(f"{APP} SCIENCE PACKAGE - SUMMARY")
    add("=" * 60)
    add(f"Created {utc_str(time.time())}   {APP} v{VERSION}")
    add("")
    add("OBSERVATION")
    add(f"  Station ........... {meta.get('station') or '-'}")
    add(f"  Observer .......... {meta.get('observer') or '-'}")
    site = meta["site"]
    add(f"  Site .............. {site['lat_deg']:.4f} deg N, {site['lon_deg']:.4f} deg E, {site['height_m']:.0f} m")
    add(f"  Antenna ........... {meta['dish_m']:g} m  (beam about {ms.beam_deg():.1f} deg, 70 lambda / D); "
        + f"beam efficiency {meta['eta_mb']:.2f} ({meta['eta_mb_source']})")
    add(f"  Pointing .......... {meta['pointing_mode']}")
    if n:
        add(f"  First / last average  {S['time'][0]}  /  {S['time'][-1]}")
    add(f"  Averages in the maps  {ms.n_points}")
    if n:
        add(f"  Total average counts  {sum(S['counts']):.0f} spectra")
    grid, mask = ms.compute_grid(ms.eq_sum, ms.eq_w)
    _, cov, peak, _ = ms.stats(grid, mask, ms.n_points, "eq", ms.map_unit)
    add(f"  Sky coverage ...... {cov:.1f} % (within half a beam of a measured cell)")
    add(f"  Strongest peak .... {peak}")
    add("")
    add("CALIBRATION")
    cold, hot = app.cold, app.hot
    if cold is not None:
        p = cold["parts"]
        add(f"  Cold (empty sky) .. {cold['tc']:.1f} K = CMB {p['cmb']:.1f} + Galaxy {p['galaxy']:.1f} + atmosphere "
            f"{p['atmosphere']:.1f} + spillover {p['spillover']:.1f} K   (model, pointing alt {cold['alt']:.0f}, "
            f"b {cold['b']:+.0f} deg; {utc_str(cold['t_last'])})")
        add(f"                      air temperature: {cold['amb_note']}")
    else:
        colds = [c for c in app.cal_history if c["kind"] == "cold"]
        add("  Cold: none active at export" + (f" (last Cold in this session {colds[-1]['time_utc']}, "
                                               f"{colds[-1]['t_cold_ref_K']:.1f} K)" if colds else ""))
    hots = [c for c in app.cal_history if c["kind"] == "hot"]
    if hot is not None:
        add(f"  Hot load .......... {hot['th']:.1f} K ({hot['note']})")
        add(f"  Y factor .......... {hot['y']:.3f}      System temperature Tsys {hot['tsys']:.0f} K")
        rel = 100.0 * np.hypot(10.0, 2.0) / max(hot["th"] - cold["tc"], 1.0) if cold is not None else float("nan")
        add(f"  Uncertainty of the Kelvin scale from the reference temperatures alone: about +-{rel:.0f} %")
        add("    (does not include how well the hot load filled the beam, or baseline errors)")
    elif hots:
        add("  Hot: none active at export (a later Cold or a reset cleared it). Hot calibrations in this session:")
        for c in hots:
            add(f"    {c['time_utc']}  Y {c['y_factor']:.3f}, Tsys {c['tsys_K']:.0f} K, hot load {c['t_hot_ref_K']:.1f} K")
    else:
        add("  Hot: none in this session")
    for c in app.cal_history:
        if c["kind"] == "hot_recomputed":
            add(f"  Hot recomputed .... {c['time_utc']} ({c['reason']}): Y {c['previous_y']:.3f} -> {c['y_factor']:.3f}, "
                f"Tsys {c['previous_tsys_K']:.0f} -> {c['tsys_K']:.0f} K")
    add(f"  Map data .......... {ms.cal_text()}; each spectrum file records the Cold, Hot, Y and Tsys it was "
        f"measured with")
    try:
        dc = bool(app.v["drift_corr"].get())
    except Exception:
        dc = False
    try:
        cs = bool(app.v["cold_smooth"].get())
    except Exception:
        cs = False
    add(f"  Cold reference .... {'smoothed (bandpass model)' if cs else 'as measured'} at export "
        f"(each spectrum file says which was used)")
    add(f"  Gain drift corr. .. {'on' if dc else 'off'} at export (line-free channels scaled to the Cold level; "
        f"each spectrum file says if it was used)")
    if app.ref0 is not None:
        add(f"  Reference load .... used: gain drift removed (last gain factor {app.gain_g:.4f})")
    add(f"  Calibrations in this session: {sum(1 for c in app.cal_history if c['kind'] in ('cold', 'hot'))}")
    add("")
    add("VELOCITY")
    add("  Frame ............. LSR (kinematic, 20 km/s towards the solar apex), radio definition")
    add("  v = c (f0 - f) / f0 + correction,  f0 = 1420.405752 MHz; PPM correction applied to the frequencies")
    add(f"  PPM correction .... {meta['ppm']:g} ppm (1 ppm = 0.3 km/s)   last PPM test: {meta['ppm_test']}")
    if ms.cube is not None:
        add(f"  Velocity grid ..... {ms.cube.v[0]:.0f} to {ms.cube.v[-1]:.0f} km/s, steps of "
            f"{ms.cube.v[1] - ms.cube.v[0]:.1f} km/s")
        add(f"  Milky Way top view: KINEMATIC DISTANCE MAP - APPROXIMATE (flat rotation, R0 {R0_KPC:g} kpc, "
            f"V0 {V0_KMS:g} km/s). Bright = one distance, medium = two possible distances, faint = all data incl. noise.")
        add(f"    Nothing is computed within {TOPVIEW_EXCL_DEG:g} deg of l = 0 and 180. Not a measurement of spiral arms.")
    add("")
    add("DATA QUALITY")
    add(f"  Interference: possible interference is only flagged (limit {meta['flag_sigma']:g} sigma); "
        f"'Remove flagged' was {'ON' if app.remove_flag else 'off'} when the package was written.")
    if not same_axes:
        add("  NOTE: not all averages share the same frequency axis (settings were changed); the spectra tables use "
            "the union of all channel frequencies (NaN where a spectrum has no channel).")
    add("")
    add("WARNINGS")
    warn = []
    if meta["defaults_used"]:
        warn.append("Default values still in use for: " + ", ".join(meta["defaults_used"]) + ".")
    if cold is not None and (abs(cold["b"]) < 20 or cold["alt"] < 20):
        warn.append("The Cold pointing was near the Galactic plane or low: the cold reference is less certain.")
    # the warning follows the data in the package, not only the calibration active at export
    with ms.grid_lock:
        tl_all = list(ms.S.get("tline", []))
    n_pts = len(tl_all)
    n_cal = sum(1 for t in tl_all if np.isfinite(np.asarray(t, dtype=float)).any())
    if n_pts and n_cal == 0:
        warn.append("No Hot calibration for any point: values are ratios to Cold, not Kelvin.")
    elif n_pts and n_cal < n_pts:
        warn.append(f"{n_pts - n_cal} of {n_pts} points have no Hot calibration: their values are ratios to Cold, "
                    "not Kelvin.")
    elif not n_pts and hot is None:
        warn.append("No Hot calibration: values are ratios to Cold, not Kelvin.")
    for w_ in warn or ["none"]:
        add("  - " + w_)
    add("")
    add("FILES")
    for f_ in written:
        add("  " + f_)
    for f_ in failed:
        add("  NOT WRITTEN: " + f_)
    add("")
    add("The Kelvin values rest on model reference temperatures. Relative differences inside the maps are more")
    add("reliable than absolute values. See README.txt for how to read the files.")
    return "\n".join(L) + "\n"


def write_package(app, folder, include_fits=False, include_ezra=False, live_images=None, progress=None):
    """Writes the whole package. live_images: {name: png bytes} of the live views (drawn in the GUI thread).
    Returns (written, failed)."""
    ms = app.maps
    for sub in ("", "spectra", "spectra/averages", "maps", "calibrations", "images", "images/saved", "maps/saved"):
        os.makedirs(os.path.join(folder, sub), exist_ok=True)
    written, failed = [], []
    P = lambda *a: os.path.join(folder, *a)

    def step(label, fn):
        if progress:
            progress(label)
        try:
            fn()
            written.append(label)
        except Exception as e:
            failed.append(f"{label}: {e}")
    v1, v2, bw = ms.vel_params() or (-150.0, 150.0, 5.0)
    meta = app.package_meta()

    # ---- spectra ----
    for f in list(app.saved_files):
        if os.path.isfile(f["path"]):
            sub = "calibrations" if f["kind"] in ("cold", "hot", "ref") else "spectra/averages"
            step(f"{sub}/{os.path.basename(f['path'])}",
                 lambda f=f, sub=sub: shutil.copy2(f["path"], P(sub, os.path.basename(f["path"]))))
    # ---- earlier data opened into the maps: kept unchanged and apart from this session's files ----
    #   imported/<package name>/  spectra/averages, calibrations, metadata.json, summary.txt of an opened package
    #   imported/files/           spectrum files imported one by one (Import spectrum files)
    # An opened package's own imported/ folder is carried over too, so the whole history stays in the package.
    def free_name(parent, name):
        k, out = 2, name
        while os.path.exists(os.path.join(parent, out)):
            out = f"{name}_{k}"
            k += 1
        return out

    def copy_package(src_pkg, dst_root):
        name = free_name(dst_root, os.path.basename(os.path.normpath(src_pkg)))
        dst = os.path.join(dst_root, name)
        n = 0
        for sub in ("spectra/averages", "calibrations"):
            s_dir = os.path.join(src_pkg, *sub.split("/"))
            if os.path.isdir(s_dir):
                shutil.copytree(s_dir, os.path.join(dst, *sub.split("/")))
                n += len(os.listdir(s_dir))
        for fn in ("metadata.json", "summary.txt"):
            if os.path.isfile(os.path.join(src_pkg, fn)):
                os.makedirs(dst, exist_ok=True)
                shutil.copy2(os.path.join(src_pkg, fn), os.path.join(dst, fn))
        if n == 0 and not os.path.isdir(dst):
            raise FileNotFoundError("no spectra/averages or calibrations folder")
        return name

    imp = P("imported")
    for src_pkg in list(getattr(app, "imported_packages", [])):
        if not os.path.isdir(src_pkg):
            continue
        os.makedirs(imp, exist_ok=True)
        label = os.path.basename(os.path.normpath(src_pkg))
        step(f"imported/{label}/", lambda s_=src_pkg: copy_package(s_, imp))
        old = os.path.join(src_pkg, "imported")            # history of the opened package
        if os.path.isdir(old):
            for entry in sorted(os.listdir(old)):
                s_ = os.path.join(old, entry)
                if os.path.isdir(s_):
                    step(f"imported/{entry}/ (history)",
                         lambda s_=s_, entry=entry: shutil.copytree(s_, os.path.join(imp, free_name(imp, entry))))
    files = [f for f in getattr(app, "loaded_files", []) if os.path.isfile(f)]
    if files:
        os.makedirs(os.path.join(imp, "files"), exist_ok=True)
    for src in files:
        name = os.path.basename(src)
        dst = os.path.join(imp, "files", name)
        if os.path.exists(dst):
            stem, ext = (name[:-11], "_counts.csv") if name.endswith("_counts.csv") else os.path.splitext(name)
            k = 2
            while os.path.exists(os.path.join(imp, "files", f"{stem}_{k}{ext}")):
                k += 1
            name = f"{stem}_{k}{ext}"
            dst = os.path.join(imp, "files", name)
        step(f"imported/files/{name}", lambda src=src, dst=dst: shutil.copy2(src, dst))
    same_axes = write_spectra_tables(ms, P("spectra"), step)

    # ---- maps (tables) ----
    if ms.n_points:
        step("maps/sky_cells.csv", lambda: table_cells(ms, P("maps", "sky_cells.csv"), v1, v2))
    if ms.cube is not None:
        step("maps/velocity_axis_kms.csv", lambda: np.savetxt(P("maps", "velocity_axis_kms.csv"), ms.cube.v,
                                                             fmt="%.2f", header="v_lsr_kms", comments=""))
        step("maps/lv_diagram.csv", lambda: table_lv(ms, P("maps", "lv_diagram.csv"), bw))
        step("maps/spectra_by_l.csv", lambda: table_spectra_by_l(ms, P("maps", "spectra_by_l.csv"), bw))
        step("maps/milkyway_topview.csv", lambda: table_topview(ms, P("maps", "milkyway_topview.csv"), bw))
        step("maps/rotation_curve.csv", lambda: table_rotation(ms, P("maps", "rotation_curve.csv"), bw))

    # ---- calibrations ----
    step("calibrations/calibrations.json", lambda: atomic_write(P("calibrations", "calibrations.json"),
                                                                json.dumps(app.cal_history, indent=2)))

    # ---- images ----
    def img(name, drawer, w=10, h=5.5):
        fig = _fig(w, h)
        drawer(fig)
        fig.savefig(P("images", name), dpi=150, facecolor=BG)
    for name, data in (live_images or {}).items():
        step(f"images/{name}", lambda name=name, data=data: open(P("images", name), "wb").write(data))
    if ms.n_points:
        step("images/map_galactic.png", lambda: img("map_galactic.png", ms.render_gal))
        step("images/map_RA_Dec.png", lambda: img("map_RA_Dec.png", ms.render_eq))
        step("images/polar_map.png", lambda: img("polar_map.png", lambda f: ms.draw_polar(f, app.map_text(), text=app.map_text()), 8, 8.4))
    if ms.cube is not None:
        sky = app.v["sky"].get() if "sky" in app.v else "Galactic"
        for name, kind in (("channel_map", "channel"), ("moment0", "m0"), ("moment1", "m1"), ("lv_diagram", "lv"),
                           ("spectra_by_l", "spec"), ("milkyway_top_view", "top"), ("rotation_curve", "rot"), ("enclosed_mass", "mass")):
            step(f"images/{name}.png", lambda name=name, kind=kind: img(f"{name}.png",
                                                                       lambda f: ms.render_velocity(f, kind, sky)))
    saved = []                                            # only the images saved in this session (Save image)
    for p_ in getattr(app, "saved_images", []):
        if os.path.isfile(p_) and os.path.abspath(p_) not in {os.path.abspath(s) for s in saved}:
            saved.append(p_)
    for src in saved:
        fn = os.path.basename(src)
        step(f"images/saved/{fn}", lambda src=src, fn=fn: shutil.copy2(src, P("images", "saved", fn)))
    for src in getattr(app, "saved_tables", []):         # spectra saved at selected map points in this session
        if os.path.isfile(src):
            fn = os.path.basename(src)
            step(f"maps/saved/{fn}", lambda src=src, fn=fn: shutil.copy2(src, P("maps", "saved", fn)))
    ms.last_drawn = {"eq": -1, "gal": -1}

    # ---- logs, raw IQ, optional formats ----
    if app.scan_log_path and os.path.isfile(app.scan_log_path):
        step("scan_log.csv", lambda: shutil.copy2(app.scan_log_path, P("scan_log.csv")))
    for d in list(app.iq_folders):
        if os.path.isdir(d):
            dst = P("station_packet", os.path.basename(d))

            def cp(d=d, dst=dst):
                os.makedirs(dst, exist_ok=True)
                for fn in os.listdir(d):
                    (_link_or_copy if fn.endswith(".sigmf-data") else shutil.copy2)(os.path.join(d, fn),
                                                                                     os.path.join(dst, fn))
            step(f"station_packet/{os.path.basename(d)}/", cp)
    if include_fits:
        step("cube.fits", lambda: save_fits(ms, P("cube.fits"), meta))
    if include_ezra:
        def ez():
            if not app.ezra_rows:
                raise ValueError("no finished averages to export")
            os.makedirs(P("ezRA"), exist_ok=True)
            site = dict(lat=meta["site"]["lat_deg"], lon=meta["site"]["lon_deg"], height=meta["site"]["height_m"],
                        az=meta.get("az_deg", 0.0), alt=meta.get("alt_deg", 0.0))
            name = "".join(ch if ch.isalnum() else "_" for ch in (meta.get("station") or meta.get("observer")
                                                                    or app.save_name()))
            write_ezra(app.ezra_rows, P("ezRA"), site, name)
        step("ezRA/", ez)

    # ---- text documents ----
    step("settings.json", lambda: atomic_write(P("settings.json"),
                                               json.dumps({k: v.get() for k, v in app.v.items()}, indent=1)))
    step("README.txt", lambda: atomic_write(P("README.txt"), PACKAGE_README))
    step("summary.txt", lambda: atomic_write(P("summary.txt"), build_summary(app, meta, same_axes, written, failed)))
    meta.update({"package_files_written": written, "package_files_not_written": failed,
                 "velocity_range_for_moments_kms": [v1, v2], "lv_b_window_deg": bw,
                 "same_frequency_axis_for_all_spectra": same_axes,
                 "spectra_table_format": 2})          # 2 = every value in the column of its own frequency
    step("metadata.json", lambda: atomic_write(P("metadata.json"), json.dumps(meta, indent=2, default=str)))
    return written, failed
