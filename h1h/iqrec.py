"""H1-HUNTER SDR - raw IQ recording in SigMF format (the only binary file the program writes).

One recording = one folder:
    iq.sigmf-data   8-bit unsigned I/Q pairs as delivered by the RTL-SDR (SigMF datatype cu8)
    iq.sigmf-meta   JSON text: time, frequency, sample rate, gain, PPM, location, SHA-512 of the data file
    iq_excerpt.csv  the first 5 ms as text (sample, time, I, Q) so the recording can be checked by eye
    station.json    only with "GPS synchronized clock": the station packet for interferometry post-processing
"""
import os
import json
import time
import hashlib
import threading
import queue
import numpy as np
from .common import *

SIGMF_VERSION = "1.0.0"
EXCERPT_S = 0.005


def iso_utc(ts):
    """ISO time with microseconds, e.g. 2026-10-10T18:02:11.123456Z."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + f".{int((ts % 1) * 1e6):06d}Z"


class IqRecorder:
    """Writes every received block to disk. write() is called from the receiver thread and only hands the stick's
    own bytes to a writer thread (file and checksum), so the recording costs the receiver almost no time.
    Every block is logged with its sample position, its time and the receiver's count of dropped blocks: a block
    lost before the recording (processing too slow, writer queue full) shows up as a marked gap."""

    def __init__(self, parent_folder, info):
        """info: dict with sample_rate, center_freq (Hz, the LO), tuned_freq, gain_dB, ppm, device, observer,
        lat, lon, height, gps (bool), station (name), dish_m, phase (cold / hot / measure)."""
        self.info = dict(info)
        self.t_start = None
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        self.folder = os.path.join(parent_folder, f"iq_{stamp}")
        os.makedirs(self.folder, exist_ok=True)
        self.data_path = os.path.join(self.folder, "iq.sigmf-data")
        self.fh = open(self.data_path, "wb")
        self.sha = hashlib.sha512()
        self.lock = threading.Lock()
        self.n = 0
        self.blocks = []                        # (first sample, samples, end time, receiver's dropped blocks so far)
        self.drop0 = None
        self.writer_dropped = 0                 # blocks lost because the disk could not keep up
        self.clock_check = True                 # False for a simulated source (no real-time samples)
        self.excerpt = []
        self.need_excerpt = int(round(EXCERPT_S * info["sample_rate"]))
        self.closed = False
        self.error = None
        self.q = queue.Queue(maxsize=IQ_WRITE_QUEUE_BLOCKS)
        self.thread = threading.Thread(target=self._writer, daemon=True)
        self.thread.start()

    def _writer(self):
        while True:
            b = self.q.get()
            if b is None:
                return
            try:
                self.fh.write(b)
                self.sha.update(b)
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"

    def write(self, iq, t_end, u8=None, dropped=0):
        """One block: u8 = the stick's interleaved 8-bit I/Q bytes when available (written as they are), otherwise
        the complex samples are converted back to 8 bit. dropped = the receiver's count of lost blocks."""
        if self.closed:
            return
        fs = self.info["sample_rate"]
        if u8 is None or len(u8) != 2 * len(iq):
            u8 = np.empty(2 * len(iq), dtype=np.uint8)
            u8[0::2] = np.clip(np.rint(iq.real * 127.5 + 127.5), 0, 255)
            u8[1::2] = np.clip(np.rint(iq.imag * 127.5 + 127.5), 0, 255)
        n = len(u8) // 2
        with self.lock:
            if self.closed:
                return
            if dropped is None:
                self.clock_check = False
                dropped = 0
            if self.t_start is None:
                self.t_start = t_end - n / fs
                self.drop0 = dropped
            try:
                self.q.put_nowait(u8.tobytes())
            except queue.Full:
                self.writer_dropped += 1
                self.blocks.append((self.n, 0, t_end, dropped - self.drop0 + 0.0, 1))
                return
            if len(self.excerpt) < self.need_excerpt:
                k = min(self.need_excerpt - len(self.excerpt), n)
                self.excerpt.extend(zip(u8[0:2 * k:2].tolist(), u8[1:2 * k:2].tolist()))
            self.blocks.append((self.n, n, t_end, dropped - self.drop0, 0))
            self.n += n

    def gaps(self):
        """[(sample position, lost samples (estimate), reason)] from the block log. A gap is either counted by the
        receiver (blocks dropped before processing) or seen in the clock: the time of the blocks runs ahead of their
        sample count by more than half a block, and stays ahead in the next block (a late block that is followed by
        an early one is timing jitter, not a loss)."""
        fs = self.info["sample_rate"]
        rows = [b for b in self.blocks]
        out = []
        t0 = self.t_start if self.t_start is not None else 0.0
        resid = [(b[2] - t0) - (b[0] + b[1]) / fs for b in rows]
        for i, (start, n, t_end, drop, wfull) in enumerate(rows):
            if wfull:
                out.append((start, None, "block lost: disk too slow"))
                continue
            if i == 0:
                continue
            d_drop = drop - rows[i - 1][3]
            if d_drop > 0:
                out.append((start, int(round(d_drop * n)), f"{int(d_drop)} block(s) dropped by the receiver"))
            elif self.clock_check and not rows[i - 1][4]:
                half = 0.5 * n / fs
                step = resid[i] - resid[i - 1]
                stays = (i + 1 >= len(rows)) or (resid[i + 1] - resid[i - 1] > half)
                if step > half and stays:
                    out.append((start, int(round(step * fs)), "time gap between blocks"))
        return out

    def seconds(self):
        return self.n / self.info["sample_rate"]

    def size_mb(self):
        return 2.0 * self.n / 1e6

    def close(self, extra=None):
        """Finishes the files. extra: dict with calibration / levels for station.json. Returns the folder."""
        with self.lock:
            if self.closed:
                return self.folder
            self.closed = True
        self.q.put(None)
        self.thread.join(timeout=30.0)
        self.fh.close()
        inf = self.info
        fs = inf["sample_rate"]
        t0 = self.t_start if self.t_start is not None else time.time()
        # timing quality: compare the sample count with the PC clock at every block
        jitter_ms = None
        gaps = self.gaps()
        good = [b for b in self.blocks if not b[4]]
        if len(good) > 2:
            # timing quality between gaps: the sample count of every block against the PC clock
            idx = np.array([b[0] + b[1] for b in good], dtype=float)
            te = np.array([b[2] for b in good])
            resid = (te - t0) - idx / fs
            cuts = sorted({g[0] for g in gaps})
            seg = np.searchsorted(np.array(cuts, dtype=float), idx - 1, side="right") if cuts else np.zeros(len(idx), int)
            r = np.concatenate([resid[seg == k] - np.median(resid[seg == k]) for k in np.unique(seg)])
            jitter_ms = float(np.std(r) * 1e3)
        lost = sum(g[1] or 0 for g in gaps)
        with open(os.path.join(self.folder, "iq_blocks.csv"), "w", encoding="utf-8") as fh:
            fh.write("# one row per received block: first sample in iq.sigmf-data, samples, PC time at the end of "
                     "the block (unix s), blocks dropped by the receiver since the start, block lost by the writer\n")
            fh.write("sample_start,samples,t_end_unix,receiver_dropped_blocks,writer_lost\n")
            for b in self.blocks:
                fh.write(f"{b[0]},{b[1]},{b[2]:.6f},{int(b[3])},{b[4]}\n")
        meta = {
            "global": {
                "core:datatype": "cu8",
                "core:sample_rate": fs,
                "core:version": SIGMF_VERSION,
                "core:sha512": self.sha.hexdigest(),
                "core:recorder": f"{APP} v{VERSION}",
                "core:author": inf.get("observer") or "-",
                "core:description": "Raw RTL-SDR IQ recording at the hydrogen line (8-bit unsigned I/Q pairs).",
                "core:hw": inf.get("device", "RTL-SDR"),
                "core:geolocation": {"type": "Point", "coordinates": [inf.get("lon"), inf.get("lat"), inf.get("height")]},
                "h1hunter:gain_dB": inf.get("gain_dB"),
                "h1hunter:ppm_correction": inf.get("ppm"),
                "h1hunter:tuned_frequency_Hz": inf.get("tuned_freq"),
                "h1hunter:clock_source": "GPS synchronized PC clock (NTP)" if inf.get("gps") else "PC clock",
                "h1hunter:timing_jitter_ms": jitter_ms,
                "h1hunter:phase": inf.get("phase", "measure"),
                "h1hunter:gaps": len(gaps),
                "h1hunter:lost_samples_estimated": int(lost),
                "h1hunter:complete_fraction": (self.n / (self.n + lost)) if (self.n + lost) else None,
                "h1hunter:block_log": "iq_blocks.csv",
                "h1hunter:samples": self.n,
                "h1hunter:duration_s": self.n / fs,
                "h1hunter:note": "Conversion to complex: (value - 127.5) / 127.5. The DC spike is NOT removed.",
            },
            "captures": [{"core:sample_start": 0, "core:frequency": inf["center_freq"], "core:datetime": iso_utc(t0)}],
            "annotations": [{"core:sample_start": int(g[0]), "core:sample_count": 0,
                             "core:comment": f"gap before this sample: {g[2]}"
                                             + (f", about {g[1]} samples" if g[1] else ""),
                             "h1hunter:lost_samples_estimated": g[1]} for g in gaps],
        }
        atomic_write(os.path.join(self.folder, "iq.sigmf-meta"), json.dumps(meta, indent=2))
        lines = [f"# {APP} v{VERSION} - first {EXCERPT_S * 1000:g} ms of iq.sigmf-data as text (raw 8-bit values)",
                 f"# start_utc={iso_utc(t0)}  sample_rate_Hz={fs:g}  center_Hz={inf['center_freq']:.0f}",
                 "# complex value = (I - 127.5) / 127.5 + j (Q - 127.5) / 127.5",
                 "sample,time_s,I_u8,Q_u8"]
        lines += [f"{k},{k / fs:.9f},{i},{q}" for k, (i, q) in enumerate(self.excerpt)]
        atomic_write(os.path.join(self.folder, "iq_excerpt.csv"), "\n".join(lines) + "\n")
        if inf.get("gps"):
            ex = extra or {}
            station = {
                "format_version": 1, "application": f"{APP} v{VERSION}",
                "station": {"name": inf.get("station") or "station", "lat": inf.get("lat"), "lon": inf.get("lon"),
                            "height": inf.get("height"), "dish_m": inf.get("dish_m"),
                            "observer": inf.get("observer") or "-"},
                "pointing": ex.get("pointing", {}),
                "receiver": {"device": inf.get("device"), "sample_rate": fs, "center_freq": inf["center_freq"],
                             "gain_dB": inf.get("gain_dB"), "ppm_hardware_correction": int(round(inf.get("ppm") or 0))},
                "timing": {"start_utc": iso_utc(t0), "method": "GPS synchronized PC clock (NTP), block time stamps",
                           "jitter_ms": jitter_ms if jitter_ms is not None else 10.0},
                "iq": {"file": "iq.sigmf-data", "format": "rtl_u8", "samples": self.n, "duration_s": self.n / fs,
                       "sha512": meta["global"]["core:sha512"]},
                "clock_prior": ex.get("clock_prior", {"ppm": 0.0, "sigma_ppm": 1.0, "source": "not measured"}),
                "calibration": ex.get("calibration", {}),
                "levels": ex.get("levels", {}),
                "flags": [],
            }
            atomic_write(os.path.join(self.folder, "station.json"), json.dumps(station, indent=2))
        return self.folder
