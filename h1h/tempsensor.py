"""H1-HUNTER SDR - optional air temperature sensor for the Hot load temperature. READ ONLY.

Sources:
  DS18B20 on a Raspberry Pi (1-Wire, GPIO 4): read from /sys/bus/w1/devices/28-*/ (needs dtoverlay=w1-gpio).
  USB serial: any device that prints the temperature in degrees Celsius as text lines, e.g. an Arduino with a
              DS18B20 ("5.31" or "T=5.31 C" per line, 9600 baud). The first number on the line is used.
  ASCOM (Windows): ObservingConditions.Temperature (weather stations, cloud sensors) or Focuser.Temperature.
  INDI (Linux):    WEATHER_PARAMETERS / WEATHER_TEMPERATURE (weather drivers) or FOCUS_TEMPERATURE (focusers),
                   from an INDI server on this computer.
Every source keeps its latest value with its time; a value older than MAX_AGE_S is not used. The program never
uses the network (INDI: localhost only).
"""
import glob
import os
import re
import threading
import time

MAX_AGE_S = 300.0
T_MIN_C, T_MAX_C = -60.0, 60.0
W1_GLOB = "/sys/bus/w1/devices/28-*"
_NUM = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def plausible(c):
    return c is not None and T_MIN_C <= c <= T_MAX_C


class _Base:
    kind = ""

    def __init__(self):
        self.value, self.t, self.error = None, 0.0, None

    def latest(self):
        """(celsius, age_s) or None."""
        if self.value is None:
            return None
        return self.value, time.time() - self.t

    def poll(self):
        """Called from the GUI thread (needed by ASCOM / COM); threaded sources do nothing here."""

    def close(self):
        pass

    def _set(self, c):
        if plausible(c):
            self.value, self.t, self.error = float(c), time.time(), None
        else:
            self.error = f"implausible value {c}"


class _Threaded(_Base):
    period = 10.0

    def __init__(self):
        super().__init__()
        self.running = True
        self.th = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self.th.start()
        return self

    def _loop(self):
        while self.running:
            try:
                self._read_once()
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"
            for _ in range(int(self.period * 10)):
                if not self.running:
                    return
                time.sleep(0.1)

    def close(self):
        self.running = False


# ---------------- DS18B20 (Raspberry Pi 1-Wire) ----------------
def ds18b20_devices():
    return sorted(glob.glob(W1_GLOB))


def read_ds18b20(path):
    """Degrees C from one DS18B20 sysfs folder (newer kernels: 'temperature'; older: 'w1_slave' with CRC check)."""
    t_file = os.path.join(path, "temperature")
    if os.path.isfile(t_file):
        with open(t_file) as fh:
            return int(fh.read().strip()) / 1000.0
    with open(os.path.join(path, "w1_slave")) as fh:
        lines = fh.read().splitlines()
    if len(lines) < 2 or not lines[0].strip().endswith("YES") or "t=" not in lines[1]:
        raise ValueError("CRC error or no reading")
    return int(lines[1].split("t=")[1]) / 1000.0


class Ds18b20(_Threaded):
    kind = "DS18B20"

    def __init__(self, path=None):
        super().__init__()
        devs = ds18b20_devices()
        if path is None:
            if not devs:
                raise RuntimeError("No DS18B20 found (1-Wire: add 'dtoverlay=w1-gpio' to /boot/firmware/config.txt "
                                   "and restart).")
            path = devs[0]
        self.path = path
        self.name = f"DS18B20 {os.path.basename(path)}"

    def _read_once(self):
        self._set(read_ds18b20(self.path))


# ---------------- USB serial text sensor ----------------
def parse_line(line):
    """First plausible number of a text line, or None ('5.31', 'T=5.31 C', 'temp: -2,5')."""
    for m in _NUM.finditer(line or ""):
        try:
            c = float(m.group(0).replace(",", "."))
        except ValueError:
            continue
        if plausible(c):
            return c
    return None


class SerialSensor(_Threaded):
    kind = "USB serial"
    period = 0.0

    def __init__(self, port, baud=9600):
        super().__init__()
        import serial
        self.port = port
        self.name = f"serial {port}"
        self.ser = serial.Serial(port, baud, timeout=1.0)

    def _loop(self):
        buf = b""
        while self.running:
            try:
                data = self.ser.read(256)
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"
                time.sleep(1.0)
                continue
            if not data:
                continue
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                c = parse_line(line.decode("ascii", errors="replace"))
                if c is not None:
                    self._set(c)
            buf = buf[-512:]

    def close(self):
        self.running = False
        try:
            self.ser.close()
        except Exception:
            pass


def probe_serial(ports, seconds=2.5):
    """First port that prints plausible temperature lines: (port, celsius) or None. Each port is opened read only."""
    import serial
    for port in ports:
        try:
            s = serial.Serial(port, 9600, timeout=0.5)
        except Exception:
            continue
        try:
            t0, buf = time.time(), b""
            while time.time() - t0 < seconds:
                buf += s.read(256)
                for line in buf.split(b"\n")[:-1]:
                    c = parse_line(line.decode("ascii", errors="replace"))
                    if c is not None:
                        return port, c
        except Exception:
            pass
        finally:
            try:
                s.close()
            except Exception:
                pass
    return None


def serial_ports():
    try:
        from serial.tools import list_ports
        return [p.device for p in list_ports.comports()]
    except Exception:
        return []


# ---------------- ASCOM (Windows) ----------------
class AscomSensor(_Base):
    kind = "ASCOM"

    def __init__(self, driver_id, device_type):
        super().__init__()
        import win32com.client
        self.driver_id, self.device_type = driver_id, device_type
        self.dev = win32com.client.Dispatch(driver_id)
        self.dev.Connected = True
        try:
            nm = str(self.dev.Name)
        except Exception:
            nm = driver_id
        self.name = f"ASCOM {device_type}: {nm}"
        self.t_read = 0.0
        self.poll(force=True)

    def poll(self, force=False):
        if not force and time.time() - self.t_read < 10.0:
            return
        self.t_read = time.time()
        try:
            self._set(float(self.dev.Temperature))
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"

    def close(self):
        try:
            self.dev.Connected = False
        except Exception:
            pass


def ascom_choose(device_type):
    """ASCOM Chooser for ObservingConditions or Focuser; returns the driver id or ''."""
    import win32com.client
    ch = win32com.client.Dispatch("ASCOM.Utilities.Chooser")
    ch.DeviceType = device_type
    return ch.Choose("") or ""


# ---------------- INDI (Linux) ----------------
INDI_TEMP_PROPS = (("WEATHER_PARAMETERS", "WEATHER_TEMPERATURE"), ("FOCUS_TEMPERATURE", "TEMPERATURE"))


def indi_temperature_devices(client):
    """[(device, property, element)] that publish a temperature."""
    out = []
    with client.lock:
        for (dev, prop), d in client.numbers.items():
            for p, el in INDI_TEMP_PROPS:
                if prop == p and el in d["values"]:
                    out.append((dev, p, el))
    return sorted(out)


class IndiSensor(_Base):
    kind = "INDI"

    def __init__(self, client, device, prop, element):
        super().__init__()
        self.client, self.device, self.prop, self.element = client, device, prop, element
        self.name = f"INDI {device}"

    def latest(self):
        with self.client.lock:
            d = self.client.numbers.get((self.device, self.prop))
            if not d or self.element not in d["values"]:
                return None
            c, t = d["values"][self.element], d["t"]
        if not plausible(c):
            return None
        # the INDI server holds the current value (weather drivers send it only when it changes)
        return float(c), (0.0 if self.client.connected else time.time() - t)

    def close(self):
        self.client.close()
