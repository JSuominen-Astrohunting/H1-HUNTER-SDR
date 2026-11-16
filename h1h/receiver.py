"""H1-HUNTER SDR - receiver: RTL-SDR / simulator sources, the receiver thread, averaging, reference relay."""
import math
import time
import queue
import threading
import collections
import numpy as np
from .common import *
from .physics import *

# 8-bit values that count as ADC clipping: |(v - 127.5) / 127.5| > 0.97
_CLIP_LUT = np.zeros(256, dtype=bool)
_CLIP_LUT[:4] = True
_CLIP_LUT[252:] = True

RTL_ERROR = None
try:
    from rtlsdr import RtlSdr
    from rtlsdr import librtlsdr
except Exception as _e:          # library or its native DLL missing: the simulator still works
    RtlSdr, librtlsdr = None, None
    RTL_ERROR = f"{type(_e).__name__}: {_e}"



LAST_SHOWN_FRACTION = 0.25   # the plot keeps the last completed average until the next has this share

class Averager:
    """Averages FFT frames. One 'count' = one FFT frame. Frames flagged as possible interference are INCLUDED in
    the average; they are only recorded (time series + spectra) so post-processing can remove them exactly."""

    def __init__(self):
        self.lock = threading.Lock()
        self.mode, self.target, self.repeat, self.tag = AVG_MODES[1], 1, False, "preview"
        self.axis = None
        self.sig = None
        self.cycle = 0
        self.rev = 0
        self.completed = collections.deque(maxlen=20)
        self.delay = 0.0
        self.pause_until = 0.0
        self.last = None                # last completed average, shown while the next one is still empty
        self.last_rec = None
        self.sk_on = False              # spectral kurtosis flagging (Settings); takes effect from the next average
        self.sk_valid = None            # trusted channels (set by the receiver)
        self._clear()

    def _clear(self):
        self.acc = None
        self.counts = 0
        self.done = False
        self.t_first = None
        self.t_last = None
        self.n_flagged = 0
        self.log_t, self.log_p, self.log_z, self.log_f = [], [], [], []
        self.n_logged = 0
        self.cand_idx, self.cand_t, self.cand_spec = [], [], []
        self.extra_sum = None
        self.extra_n = 0
        # spectral kurtosis: running block sums, and per channel the sum / number of the frames removed by SK
        # (frames already flagged in time are not counted twice)
        self.sk_active = bool(self.sk_on) and not self.exponential()
        self.sk_s1 = self.sk_s2 = None
        self.sk_m = 0
        self.sk_mc = 0
        self.sk_rm_sum = None
        self.sk_rm_n = None
        self.sk_blocks = 0
        self.sk_flags = 0
        self.rev += 1

    def start(self, mode, target, repeat, tag, delay=0.0, settle=0.0):
        with self.lock:
            self.delay = max(0.0, float(delay))
            self.pause_until = time.time() + settle if settle > 0 else 0.0
            self.last = self.last_rec = None
            self.mode, self.target, self.repeat, self.tag = mode, max(1, int(target)), bool(repeat), tag
            self.cycle = 0
            self._clear()

    def reset(self, axis=None, sig=None):
        with self.lock:
            self.last = self.last_rec = None
            if axis is not None:
                self.axis, self.sig = axis, sig
            self._clear()

    def finish(self):
        with self.lock:
            self.done = True

    def exponential(self):
        return self.mode.startswith("Exponential")

    def _mean(self):
        if self.acc is None:
            return None
        return self.acc.copy() if self.exponential() else self.acc / max(self.counts, 1)

    def add(self, P, t, tot, z, flag):
        with self.lock:
            if self.done or len(P) == 0:
                return
            if self.pause_until and time.time() < self.pause_until:
                return                               # waiting between averages: frames are not used
            self.pause_until = 0.0
            self.rev += 1
            if self.mode != AVG_MODES[0]:
                self._take(P, t, tot, z, flag)
                return
            i = 0
            while i < len(P) and not self.done:     # Count mode: frames left over after N start the next average
                k = min(max(self.target - self.counts, 0), len(P) - i)
                if k > 0:
                    self._take(P[i:i + k], t[i:i + k], tot[i:i + k], z[i:i + k], flag[i:i + k])
                    i += k
                if self.counts >= self.target:
                    self.cycle += 1
                    rec = self._record()
                    self.completed.append(rec)
                    self.last = {k: rec[k] for k in ("p", "counts", "t_first", "t_last")}
                    self.last_rec = rec
                    if self.repeat:
                        self._clear()
                        if self.delay > 0:
                            self.pause_until = time.time() + self.delay
                            break
                    else:
                        self.done = True

    def _take(self, P, t, tot, z, flag):
        first_index = self.counts
        if self.exponential():
            for row in P:
                self.counts += 1
                a = max(1.0 / self.counts, 1.0 / self.target)   # linear start-up, then fixed time constant
                row = row.astype(np.float64)
                self.acc = row.copy() if self.acc is None else self.acc + a * (row - self.acc)
        else:
            s = P.sum(axis=0, dtype=np.float64)          # 32-bit spectra, 64-bit sum
            self.acc = s if self.acc is None else self.acc + s
            self.counts += len(P)
        if self.sk_active:
            self._sk_feed(P, flag)
        if self.t_first is None:
            self.t_first = float(t[0])
        self.t_last = float(t[-1])
        self.n_flagged += int(np.count_nonzero(flag))
        if self.tag == "preview" or self.n_logged >= MAX_FRAME_LOG:
            return
        self.log_t.append(np.asarray(t, dtype=np.float64))
        self.log_p.append(np.asarray(tot, dtype=np.float32))
        self.log_z.append(np.asarray(z, dtype=np.float32))
        self.log_f.append(np.asarray(flag, dtype=bool))
        self.n_logged += len(t)
        for j in np.flatnonzero(flag):
            if len(self.cand_spec) < MAX_CAND_SPECTRA:
                self.cand_idx.append(first_index + int(j))
                self.cand_t.append(float(t[j]))
                self.cand_spec.append(P[j].astype(np.float32))
            else:
                self.extra_sum = P[j].astype(np.float64) if self.extra_sum is None else self.extra_sum + P[j]
                self.extra_n += 1

    def _sk_feed(self, P, flag):
        """Adds frames to the spectral kurtosis block; every SK_M frames the block is tested channel by channel."""
        i, n = 0, len(P)
        while i < n:
            k = min(SK_M - self.sk_m, n - i)
            seg = P[i:i + k]
            good = ~np.asarray(flag[i:i + k], dtype=bool)
            if self.sk_s1 is None:
                nc = seg.shape[1]
                self.sk_s1, self.sk_s2 = np.zeros(nc), np.zeros(nc)
            if good.any():                           # SK only from frames not already flagged in time
                g = seg if good.all() else seg[good]
                self.sk_s1 += g.sum(axis=0, dtype=np.float64)          # 32-bit spectra, 64-bit sums
                self.sk_s2 += np.einsum("ij,ij->j", g, g, dtype=np.float64)
            self.sk_mc += int(np.count_nonzero(good))
            self.sk_m += k
            i += k
            if self.sk_m >= SK_M:
                self._sk_eval()

    def _sk_eval(self):
        m = self.sk_mc                               # frames of the block that are not flagged in time
        if m < SK_M // 2:                            # mostly flagged already: the block is not tested
            self._sk_reset()
            return
        sc = math.sqrt(SK_M / m)                     # bounds for M = SK_M, scaled for a slightly smaller block
        lo, hi = 1.0 - (1.0 - SK_LOW) * sc, 1.0 + (SK_HIGH - 1.0) * sc
        with np.errstate(divide="ignore", invalid="ignore"):
            sk = (m + 1.0) / (m - 1.0) * (m * self.sk_s2 / (self.sk_s1 * self.sk_s1) - 1.0)
        bad = np.isfinite(sk) & ((sk < lo) | (sk > hi))
        if self.sk_valid is not None and len(self.sk_valid) == len(bad):
            bad &= self.sk_valid
        self.sk_blocks += 1
        if bad.any() and self.sk_mc > 0:
            if self.sk_rm_sum is None:
                self.sk_rm_sum = np.zeros(len(bad))
                self.sk_rm_n = np.zeros(len(bad), dtype=np.int64)
            self.sk_rm_sum[bad] += self.sk_s1[bad]
            self.sk_rm_n[bad] += self.sk_mc
            self.sk_flags += int(np.count_nonzero(bad))
        self._sk_reset()

    def _sk_reset(self):
        self.sk_s1[:] = 0.0
        self.sk_s2[:] = 0.0
        self.sk_m = self.sk_mc = 0

    def _record(self):
        """Everything needed to save one average (called with the lock held)."""
        cat = (lambda lst, dt: np.concatenate(lst).astype(dt) if lst else np.empty(0, dt))
        n = len(self.axis) if self.axis is not None else 0
        return {"tag": self.tag, "cycle": self.cycle, "axis": self.axis, "sig": self.sig, "mode": self.mode,
                "p": self._mean(), "sum": None if self.exponential() or self.acc is None else self.acc.copy(),
                "counts": self.counts, "t_first": self.t_first, "t_last": self.t_last,
                "n_flagged": self.n_flagged, "log_t": cat(self.log_t, np.float64), "log_p": cat(self.log_p, np.float32),
                "log_z": cat(self.log_z, np.float32), "log_f": cat(self.log_f, bool),
                "log_complete": self.n_logged >= self.counts,
                "cand_idx": np.array(self.cand_idx, dtype=np.int64), "cand_t": np.array(self.cand_t, dtype=np.float64),
                "cand_spec": np.array(self.cand_spec, dtype=np.float32).reshape(-1, n),
                "extra_sum": np.zeros(n) if self.extra_sum is None else self.extra_sum.copy(),
                "extra_n": self.extra_n,
                "sk_on": self.sk_active, "sk_blocks": self.sk_blocks, "sk_flags": self.sk_flags,
                "sk_rm_sum": None if self.sk_rm_sum is None else self.sk_rm_sum.copy(),
                "sk_rm_n": None if self.sk_rm_n is None else self.sk_rm_n.copy()}

    def _show_last(self):
        """True while the plot should still show the last completed average: the next one is empty, or (repeating
        count averages) has less than LAST_SHOWN_FRACTION of its spectra."""
        if self.last is None:
            return False
        if self.acc is None:
            return True
        return self.mode == AVG_MODES[0] and self.repeat and self.counts < LAST_SHOWN_FRACTION * self.target

    def export_shown(self):
        """The average the plot shows (last completed or running) - for 'Remove flagged' in the view."""
        with self.lock:
            if self._show_last() and self.last_rec is not None:
                return self.last_rec
            return self._record() if self.acc is not None else self.last_rec

    def export(self):
        """The running average, or the last completed one when the next has not started yet (what the plot shows)."""
        with self.lock:
            return self._record() if self.acc is not None else self.last_rec

    def pop_completed(self):
        with self.lock:
            out = list(self.completed)
            self.completed.clear()
            return out

    def display_snapshot(self):
        """Like snapshot(), but the last completed average stays until the next one is good enough (plot only)."""
        return self.snapshot(display=True)

    def snapshot(self, display=False):
        with self.lock:
            src = {"p": self._mean(), "counts": self.counts, "t_first": self.t_first, "t_last": self.t_last}
            showing_last = (src["p"] is None or (display and self._show_last())) and self.last is not None
            if showing_last:
                src = self.last
            return {"axis": self.axis, "p": src["p"], "counts": src["counts"], "t_first": src["t_first"],
                    "t_last": src["t_last"], "showing_last": showing_last, "running_counts": self.counts,
                    "done": self.done, "rev": self.rev, "sig": self.sig, "tag": self.tag,
                    "n_flagged": self.n_flagged, "mode": self.mode, "target": self.target,
                    "wait": max(0.0, self.pause_until - time.time()) if self.pause_until else 0.0}


class RtlSource:
    def __init__(self, index):
        self.index = index
        self.sdr = None
        self.hw_ppm = 0

    def open(self):
        if RtlSdr is None:
            raise RuntimeError("pyrtlsdr / librtlsdr could not be loaded:\n" + (RTL_ERROR or ""))
        self.sdr = RtlSdr(device_index=self.index)
        self.hw_ppm = 0
        for name, arg in (("set_agc_mode", False), ("set_manual_gain_enabled", True)):   # no AGC anywhere
            try:
                getattr(self.sdr, name)(arg)
            except Exception:
                pass

    def gains(self):
        try:
            return [float(g) for g in self.sdr.valid_gains_db]
        except Exception:
            return None

    def set_rate(self, fs):
        self.sdr.sample_rate = fs

    def set_center(self, hz):
        self.sdr.center_freq = int(round(hz))

    def set_gain(self, g):
        self.sdr.gain = float(g)

    def set_ppm(self, ppm):
        ppm = int(round(ppm))
        if ppm == self.hw_ppm:      # librtlsdr fails (and pyrtlsdr closes the device) if the value is unchanged
            return
        self.sdr.freq_correction = ppm
        self.hw_ppm = ppm

    def set_offset_tuning(self, on):
        """E4000 tuners: moves the zero-IF DC spike out of the band (librtlsdr rtlsdr_set_offset_tuning)."""
        fn = getattr(librtlsdr, "rtlsdr_set_offset_tuning", None) if librtlsdr is not None else None
        if fn is None:
            if on:
                raise RuntimeError("Offset tuning is not available in this librtlsdr.")
            return
        if fn(self.sdr.dev_p, 1 if on else 0) < 0 and on:
            raise RuntimeError("Offset tuning is not supported by this tuner (E4000 only).")

    def set_bias(self, on):
        if not hasattr(self.sdr, "set_bias_tee"):
            if on:
                raise RuntimeError("This pyrtlsdr version has no bias-tee support (update pyrtlsdr).")
            return
        self.sdr.set_bias_tee(bool(on))

    def read(self, n):
        return np.asarray(self.sdr.read_samples(n), dtype=np.complex64)

    # ---- asynchronous streaming: librtlsdr keeps a ring of USB buffers, so no samples are lost while the
    # program is busy (the synchronous read above loses samples whenever the next read is a little late) ----
    def start_stream(self, block_samples, on_block):
        """Calls on_block(uint8 array, unix time, monotonic time) from a background thread for every block."""
        self.stream_error = None

        def cb(buf, ctx):
            t, tm = time.time(), time.monotonic()
            on_block(np.frombuffer(bytes(buf), dtype=np.uint8), t, tm)

        def worker():
            try:
                self.sdr.read_bytes_async(cb, num_bytes=2 * block_samples)
            except Exception as e:
                self.stream_error = f"{type(e).__name__}: {e}"
        self.stream_thread = threading.Thread(target=worker, daemon=True)
        self.stream_thread.start()

    def stop_stream(self):
        try:
            self.sdr.cancel_read_async()
        except Exception:
            pass
        th = getattr(self, "stream_thread", None)
        if th is not None:
            th.join(timeout=3.0)
        self.stream_thread = None

    def close(self):
        th = getattr(self, "stream_thread", None)
        if th is not None:
            self.stop_stream()
        if self.sdr is not None:
            try:
                self.set_bias(False)
            except Exception:
                pass
            try:
                self.sdr.close()
            except Exception:
                pass
            self.sdr = None


class SerialRelay:
    """LCUS-1 / CH340 type USB serial relay that switches the LNA input between the antenna and a reference load."""

    def __init__(self, port):
        self.port = port
        self.ser = None
        self.state = None

    def open(self):
        try:
            import serial                      # pyserial; optional, only needed for the relay
        except ImportError:
            raise RuntimeError("The relay needs the 'pyserial' library. Run the installer again (internet needed "
                               "once), or install it with:  python -m pip install pyserial")
        self.ser = serial.Serial(port=self.port, baudrate=9600, timeout=1)
        self.set(False)

    def set(self, on):
        if self.ser is None:
            return
        self.ser.write(RELAY_ON_CMD if on else RELAY_OFF_CMD)
        self.ser.flush()
        self.state = bool(on)

    def close(self):
        if self.ser is not None:
            try:
                self.set(False)
                self.ser.close()
            except Exception:
                pass
            self.ser = None


class SimRelay:
    """Relay for the simulator: switches the simulated LNA input to a 290 K load and turns on a slow gain drift."""

    def __init__(self, source):
        self.source = source
        self.state = None

    def open(self):
        if hasattr(self.source, "drift"):
            self.source.drift = True
        self.set(False)

    def set(self, on):
        self.state = bool(on)
        if hasattr(self.source, "ref_on"):
            self.source.ref_on = self.state

    def close(self):
        self.set(False)


class SimSource:
    """Fake RTL-SDR for testing without hardware: receiver noise (Trx 100 K), band-pass ripple and roll-off,
    a two-component H1 line, Cold/Hot scenes, a crystal error of SIM_PPM_ERROR ppm, LO leakage (DC spike),
    a spur, occasional interference bursts and ADC clipping at high gain."""

    def __init__(self):
        self.fs, self.lo, self.gain, self.hw_ppm, self.bias = 2.4e6, H1_HZ, 30.0, 0, False
        self.scene = "sky"            # "sky", "cold" or "hot" (set by the app; simulator only)
        self.burst_prob = 0.3         # probability of an interference burst per block
        self.offset_tuning = False
        self.rng = None
        self.t_next = 0.0
        self.n0 = 0
        self.ref_on = False           # simulator relay: input switched to the reference load
        self.drift = False            # slow gain drift, only while the simulator relay is in use
        self.t0 = time.time()

    def open(self):
        self.rng = np.random.default_rng()
        self.t_next = time.time()

    def gains(self):
        return list(R820T_GAINS)

    def set_rate(self, fs):
        self.fs = fs

    def set_center(self, hz):
        self.lo = hz

    def set_gain(self, g):
        self.gain = float(g)

    def set_ppm(self, ppm):
        self.hw_ppm = int(round(ppm))

    def set_bias(self, on):
        self.bias = bool(on)

    def set_offset_tuning(self, on):
        self.offset_tuning = bool(on)

    def read(self, n):
        fs = self.fs
        fr = np.fft.fftfreq(n, 1.0 / fs)
        lo_act = self.lo * (1.0 + (SIM_PPM_ERROR - self.hw_ppm) * 1e-6)
        fa = lo_act + fr
        line = np.exp(-0.5 * ((fa - (H1_HZ - 30e3)) / 90e3) ** 2) + 0.6 * np.exp(-0.5 * ((fa - (H1_HZ + 120e3)) / 45e3) ** 2)
        t_sky = {"hot": 290.0 + 0.0 * line, "cold": 6.0 + 3.0 * line}.get(self.scene, 6.0 + 40.0 * line)
        if self.ref_on:
            t_sky = 290.0 + 0.0 * line                 # reference load at ambient temperature
        if not self.bias:
            t_sky = 0.02 * t_sky                       # LNA unpowered: the sky barely reaches the stick
        bandpass = (1.0 + 0.05 * np.sin(2 * np.pi * fr / 400e3)) / (1.0 + (fr / (0.45 * fs)) ** 12)
        S = (SIM_TRX + t_sky) / (SIM_TRX + 6.0) * bandpass
        X = (self.rng.standard_normal(n) + 1j * self.rng.standard_normal(n)) * np.sqrt(S / 2.0)
        amp = 0.004 * 10 ** (self.gain / 20.0)
        if self.drift:
            amp *= math.sqrt(1.0 + SIM_GAIN_DRIFT * math.sin(2 * math.pi * (time.time() - self.t0) / 1200.0))
        x = np.fft.ifft(X) * np.sqrt(n) * amp
        t = (self.n0 + np.arange(n)) / fs
        x += 0.002 * amp * np.exp(2j * np.pi * 350e3 * t)          # internal spur
        if self.rng.random() < self.burst_prob:                     # interference burst (~1 ms)
            k0 = int(self.rng.integers(0, n - 3000))
            x[k0:k0 + 2400] += 3.0 * amp * (self.rng.standard_normal(2400) + 1j * self.rng.standard_normal(2400))
        if not self.offset_tuning:
            x += 0.012 - 0.008j                                      # LO leakage -> DC spike
        self.n0 += n
        x = np.clip(x.real, -1, 1) + 1j * np.clip(x.imag, -1, 1)
        now = time.time()                                            # pace like real hardware
        if self.t_next > now:
            time.sleep(self.t_next - now)
        self.t_next = max(self.t_next, now) + n / (fs * (1.0 + (SIM_PPM_ERROR - self.hw_ppm) * 1e-6))
        return x.astype(np.complex64)

    def close(self):
        pass


def list_rtl_devices():
    if librtlsdr is None:
        return []
    out = []
    try:
        for i in range(int(librtlsdr.rtlsdr_get_device_count())):
            try:
                name = librtlsdr.rtlsdr_get_device_name(i)
                name = name.decode(errors="ignore") if isinstance(name, bytes) else str(name)
            except Exception:
                name = "RTL-SDR"
            out.append(f"{i}: {name}")
    except Exception:
        pass
    return out


class Receiver(threading.Thread):
    def __init__(self, source, cfg, averager):
        super().__init__(daemon=True)
        self.source, self.cfg, self.avg = source, dict(cfg), averager
        self.cmds = queue.Queue()
        self.running = True
        self.error = None
        self.warnings = []
        self.lock = threading.Lock()
        self.live = None
        self.rows = collections.deque(maxlen=64)
        self.seq = 0
        self.taken = 0
        self.axis = None
        self.axis_id = 0
        self.clip = 0.0
        self.clip_max = 0.0             # worst ADC clipping since the last reset (checked by Cold/Hot)
        self.gains = None
        self.discard = 0
        self.notch_bins = 0
        self.rf_axis = None             # frequency axis of the fixed RF-view FFT
        self.rf_notch_bins = 0
        self.shift, self.wrap = 0, None
        self.rf_win = make_window(RF_WINDOW, RF_NFFT).astype(np.float32)
        self.rf_tail = np.empty(0, dtype=np.complex64)
        self.flag_sigma = 5.0
        self.reset_flags = False
        self.ppm_test = False           # True while the PPM test (sample clock vs PC clock) runs
        self.ppm_test_result = None
        self.tail = np.empty(0, dtype=np.complex64)
        self.hist = np.empty((0, N_SUBBANDS + 1))
        self.n_samples = 0
        self.wall0 = None
        self.iq_sink = None             # raw IQ recorder (Data & Export -> Save raw IQ); called with every block
        self.stream = hasattr(source, "start_stream")     # real stick: asynchronous reading
        self.q = queue.Queue(maxsize=STREAM_QUEUE_BLOCKS)
        self.dropped_blocks = 0
        self.streaming = False
        self.flag_log = collections.deque(maxlen=4000)   # (time, flagged spectra in the block) for "Flagged, 60 s"

    def flagged_last(self, seconds=60.0):
        t0 = time.time() - seconds
        return sum(n for t, n in list(self.flag_log) if t >= t0)

    def send(self, key, value):
        self.cmds.put((key, value))

    def signature(self):
        return tuple(self.cfg[k] for k in SIG_KEYS)

    def hop(self):
        return self.cfg["nfft"] // 2 if self.cfg["overlap"] else self.cfg["nfft"]

    def throughput(self):
        """Share of the samples that were processed (1.0 = none lost). With the USB stream the queue still
        holding blocks counts as processed-later, not lost."""
        if self.wall0 is None:
            return None
        dt = time.time() - self.wall0
        if dt <= 2.0:
            return None
        pending = self.q.qsize() * BLOCK_SAMPLES if self.stream else 0
        return (self.n_samples + pending) / (self.cfg["fs"] * dt)

    def _apply(self, k, v):
        self.cfg[k] = v
        s = self.source
        try:
            if k in ("target_hz", "offset_hz"):
                s.set_center(self.cfg["target_hz"] + self.cfg["offset_hz"])
            elif k == "fs":
                s.set_rate(v)
            elif k == "ppm":
                s.set_ppm(v)
            elif k == "gain":
                s.set_gain(v)
            elif k == "bias":
                s.set_bias(v)
            elif k == "offset_tuning":
                s.set_offset_tuning(v)
        except Exception as e:
            self.warnings.append(f"{k}: {e}")

    def _rebuild(self):
        c = self.cfg
        n = c["nfft"]
        self.win = make_window(c["window"], n).astype(np.float32)
        lo = c["target_hz"] + c["offset_hz"]
        sw_ppm = c["ppm"] - round(c["ppm"])     # fraction the hardware cannot do, corrected on the axis
        # LO offset: the hydrogen-line spectra are moved back by whole channels so that the centre frequency stays
        # in the middle; the axis keeps the exact frequency of every channel. Channels moved in from the other band
        # edge were never measured: NaN, not trusted. (The RF view is not moved: it shows the band as received.)
        self.shift = shift_bins(c["offset_hz"], c["fs"], n)
        axis = (lo + (np.arange(n) - n // 2 + self.shift) * c["fs"] / n) * (1.0 + sw_ppm * 1e-6)
        src = np.arange(n) + self.shift
        self.wrap = (src < 0) | (src >= n)
        use_notch = c["notch_hz"] > 0 and not c["offset_tuning"]     # offset tuning: no DC spike in the band
        self.notch_bins = int(round(c["notch_hz"] / (c["fs"] / n))) if use_notch else 0
        mask = trusted_mask(axis, self.signature())
        idx = np.flatnonzero(mask)
        self.valid_idx = idx
        self.sub_starts = np.linspace(0, len(idx), N_SUBBANDS + 1).astype(int)[:-1]
        # the band and sub-band means of every frame as one matrix product over the span of the trusted channels
        # (the same means as before, without copying the trusted channels of every frame)
        self.sub_w = None
        if len(idx):
            a, b = int(idx[0]), int(idx[-1]) + 1
            if self.wrap is None or not self.wrap[a:b].any():
                ends = np.append(self.sub_starts, len(idx))
                w = np.zeros((b - a, N_SUBBANDS + 1), dtype=np.float32)
                w[idx - a, 0] = 1.0 / len(idx)
                for k in range(N_SUBBANDS):
                    sel = idx[ends[k]:ends[k + 1]] - a
                    w[sel, k + 1] = 1.0 / max(len(sel), 1)
                self.sub_w, self.sub_span = w, (a, b)
        self.block = n * max(1, int(math.ceil(BLOCK_SAMPLES / n)))
        self.tail = np.empty(0, dtype=np.complex64)
        self.hist = np.empty((0, N_SUBBANDS + 1))
        rf_axis = (lo + (np.arange(RF_NFFT) - RF_NFFT // 2) * c["fs"] / RF_NFFT) * (1.0 + sw_ppm * 1e-6)
        self.rf_tail = np.empty(0, dtype=np.complex64)
        with self.lock:
            self.axis = axis
            self.rf_axis = rf_axis
            self.rf_notch_bins = int(round(c["notch_hz"] / (c["fs"] / RF_NFFT))) if use_notch else 0
            self.axis_id += 1
            self.live = None
            self.rows.clear()
        self.avg.sk_valid = mask.copy()
        self.avg.reset(axis, self.signature())
        self.discard = 1                      # let the tuner settle
        self.n_samples = 0
        self.wall0 = None

    # ---------------- asynchronous stream (real stick) ----------------
    def _on_block(self, u8, t, tm):
        """Background USB thread: only queue the raw bytes (all processing is done in the receiver thread)."""
        try:
            self.q.put_nowait((u8, t, tm))
        except queue.Full:
            self.dropped_blocks += 1          # processing is too slow for minutes: counted in the throughput

    def _start_stream(self):
        if self.stream and not self.streaming:
            self.source.start_stream(BLOCK_SAMPLES, self._on_block)
            self.streaming = True

    def _stop_stream(self):
        if self.streaming:
            self.source.stop_stream()
            self.streaming = False
        self._flush()

    def _flush(self):
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                return

    def _next_block(self):
        """(iq complex64, unix time at the end of the block, monotonic time) or None."""
        if not self.stream:
            iq = self.source.read(self.block)
            return iq, time.time(), time.monotonic(), None
        err = getattr(self.source, "stream_error", None)
        if err:
            raise RuntimeError("USB stream stopped: " + err)
        try:
            u8, t, tm = self.q.get(timeout=0.5)
        except queue.Empty:
            return None
        x = u8.astype(np.float32)
        x -= 127.5
        x /= 127.5
        iq = x[: len(x) // 2 * 2].view(np.complex64)      # interleaved I, Q float32 = complex64 (no copy)
        return iq, t, tm, u8

    def run(self):
        s, c = self.source, self.cfg
        try:
            s.open()
            self.gains = s.gains()
            s.set_rate(c["fs"])
            s.set_center(c["target_hz"] + c["offset_hz"])
            for k, fn in (("ppm", s.set_ppm), ("gain", s.set_gain), ("bias", s.set_bias),
                          ("offset_tuning", s.set_offset_tuning)):
                try:
                    fn(c[k])
                except Exception as e:
                    self.warnings.append(f"{k}: {e}")
            self._rebuild()
            self._start_stream()
            while self.running:
                changed = []
                while True:
                    try:
                        k, v = self.cmds.get_nowait()
                    except queue.Empty:
                        break
                    changed.append((k, v))
                if changed:
                    restart = self.streaming and any(k in ("fs", "offset_tuning") for k, _ in changed)
                    if restart:
                        self._stop_stream()         # the sample rate is not changed while the USB stream runs
                    for k, v in changed:
                        self._apply(k, v)
                    self._rebuild()
                    self._flush()                   # blocks still in the queue were taken with the old settings
                    if restart:
                        self._start_stream()
                if self.ppm_test:
                    self._run_ppm_test()
                    self._rebuild()
                    continue
                blk = self._next_block()
                if blk is None:
                    continue
                iq, t_end, _, u8 = blk
                if not self.running:
                    break
                if self.discard > 0:
                    self.discard -= 1
                    self.wall0 = t_end
                    continue
                self.n_samples += len(iq)
                sink = self.iq_sink
                if sink is not None:
                    try:
                        # the stick's own bytes and its count of dropped blocks (None: a simulated source makes
                        # its samples on demand, so its timing says nothing about lost samples)
                        sink(iq, t_end, u8, self.dropped_blocks if self.stream else None)
                    except Exception as e:
                        self.iq_sink = None
                        self.warnings.append(f"Raw IQ recording stopped: {e}")
                self._process(iq, t_end, u8)
        except Exception as e:
            if self.running:
                self.error = f"{type(e).__name__}: {e}"
        finally:
            try:
                self._stop_stream()
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass

    def _process(self, iq, t_end, u8=None):
        c = self.cfg
        n, hop, fs = c["nfft"], self.hop(), c["fs"]
        if u8 is not None and len(u8) == 2 * len(iq):
            # |(v - 127.5) / 127.5| > 0.97  <=>  v <= 3 or v >= 252 (the same test on the 8-bit values, faster)
            hit = _CLIP_LUT[u8]
            clip = float(np.count_nonzero(hit[0::2] | hit[1::2])) / len(iq)
        else:
            clip = float(np.mean((np.abs(iq.real) > 0.97) | (np.abs(iq.imag) > 0.97)))
        if c["dc_remove"] or c["offset_tuning"]:     # offset tuning always includes DC removal (residual DC)
            if u8 is not None:
                iq -= iq.mean()                       # own buffer of this block (stick stream): in place
            else:
                iq = iq - iq.mean()
        x = np.concatenate([self.tail, iq]) if len(self.tail) else iq
        t0 = t_end - len(x) / fs
        P = frame_powers(x, n, self.win, hop)
        nfr = len(P)
        self.tail = x[nfr * hop:].copy() if nfr else x.copy()
        if nfr == 0:
            return
        if self.notch_bins:
            notch_center(P, n // 2, self.notch_bins)
        if self.shift:
            P = np.roll(P, -self.shift, axis=1)
            P[:, self.wrap] = np.nan
        t = t0 + (np.arange(nfr) * hop + n / 2) / fs           # centre time of each frame (wall clock)
        # --- interference candidates: sudden power jumps in the whole band or in one of 16 sub-bands ---
        if self.reset_flags:
            self.reset_flags = False
            self.hist = np.empty((0, N_SUBBANDS + 1))
        if self.sub_w is not None:
            a, b = self.sub_span
            vals = (P[:, a:b] @ self.sub_w).astype(np.float64)
            tot = vals[:, 0].astype(np.float32)
        else:
            V = P[:, self.valid_idx]
            sub = np.add.reduceat(V, self.sub_starts, axis=1) / np.diff(np.append(self.sub_starts, V.shape[1]))
            tot = V.mean(axis=1)
            vals = np.column_stack([tot, sub])
        ref = self.hist if len(self.hist) >= 256 else np.vstack([self.hist, vals])
        med = np.median(ref, axis=0)
        mad = 1.4826 * np.median(np.abs(ref - med), axis=0)
        z = ((vals - med) / np.maximum(mad, 1e-30)).max(axis=1)
        flag = z > self.flag_sigma
        self.hist = np.vstack([self.hist, vals])[-FLAG_REF_FRAMES:]
        self.flag_log.append((t_end, int(np.count_nonzero(flag))))
        self.avg.add(P, t, tot, z, flag)
        # --- RF view: its own fixed FFT (RF_NFFT), independent of the hydrogen-line FFT above. Display only: it uses
        # the first RF_VIEW_SAMPLES of every block (enough for a smooth view, and a quarter of the processor time) ---
        xr = iq[:RF_VIEW_SAMPLES]
        nseg = len(xr) // RF_NFFT
        mean = None
        if nseg:
            Prf = frame_powers(xr[:nseg * RF_NFFT], RF_NFFT, self.rf_win, RF_NFFT)
            if self.rf_notch_bins:
                notch_center(Prf, RF_NFFT // 2, self.rf_notch_bins)
            mean = Prf.mean(axis=0, dtype=np.float64)
        with self.lock:
            self.clip = clip
            self.clip_max = max(self.clip_max, clip)
            if mean is not None:
                if self.live is None or self.live.shape != mean.shape:
                    self.live = mean
                else:
                    self.live += 0.35 * (mean - self.live)
                self.rows.append(mean)
                self.seq += 1

    def _run_ppm_test(self):
        """Like 'rtl_test -p': counts the samples the stick delivers and compares them with the PC clock.
        Nothing is processed meanwhile, so no samples are lost. The result is only as good as the PC clock."""
        s, fs = self.source, self.cfg["fs"]
        hw = int(round(self.cfg["ppm"]))
        n = 1 << 18
        if self.stream:
            self._flush()
            try:
                self.q.get(timeout=2.0)                 # let the USB stream settle
            except queue.Empty:
                pass
            drop0 = self.dropped_blocks
        else:
            s.read(n)
        t, cum = [], []
        total = 0
        t0 = time.monotonic()
        last = 0.0
        while self.running and self.ppm_test:
            if self.stream:
                try:
                    u8, _, now = self.q.get(timeout=1.0)    # arrival time of each USB block (monotonic clock)
                except queue.Empty:
                    continue
                if self.dropped_blocks != drop0:          # a lost block would bias the result: start again
                    drop0, t, cum, total, t0 = self.dropped_blocks, [], [], 0, now
                    continue
                total += len(u8) // 2
            else:
                total += len(s.read(n))
                now = time.monotonic()
            t.append(now - t0)
            cum.append(total)
            if now - last >= 2.0 and len(t) > 20:
                last = now
                tt, cc = np.array(t), np.array(cum, dtype=float)
                (rate, _), cov = np.polyfit(tt, cc, 1, cov=True)
                resid = (rate / fs - 1.0) * 1e6
                err = math.sqrt(cov[0, 0]) / fs * 1e6
                self.ppm_test_result = {"elapsed": tt[-1], "ppm": hw + resid, "err": err, "hw": hw}
        self.ppm_test = False

    def take_live(self):
        with self.lock:
            if self.live is None or self.seq == self.taken:
                return None
            self.taken = self.seq
            rows = list(self.rows)
            self.rows.clear()
            return self.axis_id, self.rf_axis, self.live.copy(), rows, self.clip, self.rf_notch_bins

