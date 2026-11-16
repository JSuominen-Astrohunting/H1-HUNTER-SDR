"""H1-HUNTER SDR - plain-text files: spectrum CSV, average count log, flagged spectra, ezRA export."""
import time
import numpy as np
from .common import *

def write_science_csv(path, cols, meta):
    names = list(cols.keys())
    lines = [f"# {APP} v{VERSION} science spectrum"]
    lines += [f"# {k}: {fmt(v)}" for k, v in meta]
    lines.append(",".join(names))
    arr = np.column_stack([np.asarray(cols[k], dtype=float) for k in names])
    def cell(j, x):
        if not np.isfinite(x):
            return "nan"
        return f"{x:.3f}" if names[j] == "freq_hz" else f"{x:.9g}"
    lines += [",".join(cell(j, x) for j, x in enumerate(row)) for row in arr]
    atomic_write(path, "\n".join(lines) + "\n")


COUNT_LOGS = ["Compact", "Full"]
COMPACT_BIN_S = 0.1           # compact average count log: time bins of 0.1 s

COUNTS_README = [
    "Average count log. One FFT spectrum = one average count. Every spectrum is INCLUDED in the saved average;",
    "nothing was removed while measuring. power = mean of the trusted band, z = largest robust deviation of the band",
    "or one of 16 sub-bands, flag = z > threshold. Compact: bins of bin_s seconds; Full: one row per spectrum.",
    "The flagged spectra themselves are in <name>_flagged.csv."]
FLAGGED_README = [
    "sum_all = sum of ALL spectra of this average (counts in the header). flagged_<n> = one flagged spectrum",
    "(average count index n, time in the header). Flagged spectra beyond the storage limit are summed in",
    "flagged_extra_sum. To remove flagged spectra: mean = (sum_all - their sum) / (counts - their number)."]


def compact_series(t, p, z, f, bin_s=COMPACT_BIN_S):
    """Per-spectrum series -> bins of bin_s seconds (start time, spectra, mean/max power, max z, flagged spectra)."""
    if len(t) == 0:
        e = np.empty(0)
        return e, e, e, e, e, e
    idx = np.floor((t - t[0]) / bin_s).astype(np.int64)
    edges = np.flatnonzero(np.diff(idx)) + 1
    starts = np.concatenate([[0], edges])
    n = np.diff(np.append(starts, len(t)))
    return (t[0] + idx[starts] * bin_s, n.astype(np.int32),
            (np.add.reduceat(p.astype(np.float64), starts) / n).astype(np.float32),
            np.maximum.reduceat(p, starts).astype(np.float32), np.maximum.reduceat(z, starts).astype(np.float32),
            np.add.reduceat(f.astype(np.int32), starts).astype(np.int32))


def write_counts_csv(path, rec, meta, compact=True):
    """Average count log as plain text CSV."""
    head = [f"# {APP} v{VERSION} average count log"] + [f"# {x}" for x in COUNTS_README]
    head += [f"# {k}: {fmt(v)}" for k, v in meta]
    if compact:
        bt, bn, bpm, bpx, bz, bf = compact_series(rec["log_t"], rec["log_p"], rec["log_z"], rec["log_f"])
        head += [f"# Log_format: compact, bin_s {COMPACT_BIN_S}",
                 "bin_start_utc,bin_start_unix,spectra,power_mean,power_max,z_max,flagged"]
        rows = [f"{utc_str(a)},{a:.4f},{int(b)},{c:.7g},{d:.7g},{e:.4g},{int(g)}"
                for a, b, c, d, e, g in zip(bt, bn, bpm, bpx, bz, bf)]
    else:
        head += ["# Log_format: full, one row per spectrum", "time_unix,power,z,flag"]
        rows = [f"{a:.6f},{b:.7g},{c:.4g},{int(d)}"
                for a, b, c, d in zip(rec["log_t"], rec["log_p"], rec["log_z"], rec["log_f"])]
    atomic_write(path, "\n".join(head + rows) + "\n")


def write_flagged_csv(path, rec, meta):
    """Sum of all spectra and every flagged spectrum as plain text CSV (exact removal later)."""
    head = [f"# {APP} v{VERSION} flagged spectra"] + [f"# {x}" for x in FLAGGED_README]
    head += [f"# {k}: {fmt(v)}" for k, v in meta]
    idx, ts, spec = rec["cand_idx"], rec["cand_t"], rec["cand_spec"]
    head.append(f"# Flagged_stored: {len(idx)}   Flagged_extra_summed: {rec['extra_n']}")
    for i, tt in zip(idx, ts):
        head.append(f"# flagged_{int(i)}: {utc_str(tt)}")
    names = ["freq_hz", "sum_all", "flagged_extra_sum"]
    s_all = rec["sum"] if rec["sum"] is not None else np.full(len(rec["axis"]), np.nan)
    cols = [rec["axis"], s_all, rec["extra_sum"]]
    if rec.get("sk_rm_n") is not None:
        # spectral kurtosis: per channel the sum and number of the (not time-flagged) spectra in flagged blocks
        head.append("# sk_removed_sum / sk_removed_n: spectral kurtosis flagging, per channel. Mean with everything "
                    "removed = (sum_all - flagged sums - sk_removed_sum) / (counts - flagged number - sk_removed_n)")
        names += ["sk_removed_sum", "sk_removed_n"]
        cols += [rec["sk_rm_sum"], rec["sk_rm_n"]]
    names += [f"flagged_{int(i)}" for i in idx]
    cols += [spec[j] for j in range(len(idx))]
    arr = np.column_stack([np.asarray(c, dtype=float) for c in cols])
    rows = [",".join([f"{r[0]:.3f}"] + [f"{x:.9g}" for x in r[1:]]) for r in arr]
    atomic_write(path, "\n".join(head + [",".join(names)] + rows) + "\n")


class EzraWriter:
    """ezRA (Easy Radio Astronomy) .txt data file: 4-line header, then one line per average:
    UTC time stamp + one value per frequency bin (sqrt of power, like ezCol's RMS spectra), ' R' = reference load.
    ezCon only accepts files whose first line starts with 'from ezCol'. Guided pointing is written as
    'raH .. decDeg ..' lines before the data line (ezCon reads them per sample)."""

    def __init__(self, path, site, name, axis_hz):
        self.path = path
        self.keep = np.ones(len(axis_hz), dtype=bool)                # whole band, like ezCol (ezCon trims it)
        f = axis_hz / 1e6
        self.last_pointing = None
        head = (f"from ezCol-compatible export by {APP} v{VERSION}\n"
                f"lat {site['lat']:g} long {site['lon']:g} amsl {site['height']:g} name {name}\n"
                f"freqMin {f[0]:g} freqMax {f[-1]:g} freqBinQty {len(f)}\n"
                f"azDeg {site['az']:g} elDeg {site['alt']:g}\n"
                "# times are in UTC\n"
                "# frequency spectrums of sqrt(power), one average per line, ' R' = reference load (Dicke)\n")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(head)

    def add(self, t_unix, p, ref=False, pointing=None):
        """pointing: None (header Az/El), ('az', az, el) or ('ra', ra_h, dec_deg)."""
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t_unix))
        vals = np.sqrt(np.maximum(p[self.keep], 0.0))
        with open(self.path, "a", encoding="utf-8") as fh:
            if pointing is not None and pointing != self.last_pointing:
                self.last_pointing = pointing
                if pointing[0] == "ra":
                    fh.write(f"raH {pointing[1]:.5g} decDeg {pointing[2]:.5g}\n")
                else:
                    fh.write(f"azDeg {pointing[1]:.5g} elDeg {pointing[2]:.5g}\n")
            fh.write(stamp + " " + " ".join(f"{v:.9g}" for v in vals) + (" R" if ref else "") + "\n")

