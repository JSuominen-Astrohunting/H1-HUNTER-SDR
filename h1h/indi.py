"""H1-HUNTER SDR - INDI client (Linux): reads the pointing of a telescope mount from an INDI server, for example the
one KStars / Ekos starts for an EQMod mount (Sky-Watcher HEQ5 / EQ6 ...). READ ONLY: the mount is never moved.

INDI is an XML protocol over TCP (default port 7624). The client sends <getProperties/>; the server answers with
def*Vector elements and later sends set*Vector updates. Only connections to this computer (localhost) are allowed.
"""
import socket
import threading
import time
import xml.etree.ElementTree as ET

DEFAULT_HOST, DEFAULT_PORT = "localhost", 7624
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
COORD_EOD, COORD_J2000 = "EQUATORIAL_EOD_COORD", "EQUATORIAL_COORD"


def parse_number(text):
    """INDI number: decimal or sexagesimal ('5:30:12.5', '-12 30 00')."""
    t = (text or "").strip()
    if not t:
        raise ValueError("empty number")
    if ":" in t or (" " in t and len(t.split()) in (2, 3)):
        parts = t.replace(":", " ").split()
        sign = -1.0 if parts[0].startswith("-") else 1.0
        vals = [abs(float(parts[0]))] + [float(p) for p in parts[1:]]
        out = vals[0] + (vals[1] / 60.0 if len(vals) > 1 else 0.0) + (vals[2] / 3600.0 if len(vals) > 2 else 0.0)
        return sign * out
    return float(t)


class IndiClient(threading.Thread):
    """Keeps the latest value of every number and switch property of every device."""

    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT):
        super().__init__(daemon=True)
        if host not in LOCAL_HOSTS:
            raise ValueError("Only an INDI server on this computer (localhost) is allowed: the program does not use "
                             "the network.")
        self.host, self.port = host, int(port)
        self.lock = threading.Lock()
        self.numbers = {}       # (device, property) -> {"values": {element: float}, "state": str, "t": time}
        self.switches = {}      # (device, property) -> {element: bool}
        self.sock = None
        self.running = True
        self.connected = False
        self.error = None

    def open(self, timeout=3.0):
        s = socket.create_connection((self.host, self.port), timeout=timeout)
        s.settimeout(1.0)
        s.sendall(b'<getProperties version="1.7"/>\n')
        self.sock = s
        self.connected = True
        self.start()

    def close(self):
        self.running = False
        try:
            if self.sock is not None:
                self.sock.close()
        except Exception:
            pass

    # ---------------- reading ----------------
    def run(self):
        parser = ET.XMLPullParser(events=("start", "end"))
        parser.feed("<indi>")                     # the stream has no root element: give it one
        root, depth = None, 0
        try:
            while self.running:
                try:
                    data = self.sock.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    break
                parser.feed(data.decode("utf-8", errors="replace"))
                for ev, el in parser.read_events():
                    if ev == "start":
                        if root is None:
                            root = el
                        depth += 1
                        continue
                    depth -= 1
                    if depth == 1:                # a whole message (direct child of the root) has arrived
                        self._on_message(el)
                        root.remove(el)           # keep memory flat during long observations
        except Exception as e:
            if self.running:
                self.error = f"{type(e).__name__}: {e}"
        finally:
            self.connected = False

    def _on_message(self, el):
        tag = el.tag
        dev, name = el.get("device"), el.get("name")
        if tag in ("defNumberVector", "setNumberVector"):
            vals = {}
            for c in el:
                if c.tag in ("defNumber", "oneNumber"):
                    try:
                        vals[c.get("name")] = parse_number(c.text)
                    except ValueError:
                        pass
            with self.lock:
                d = self.numbers.setdefault((dev, name), {"values": {}, "state": "Idle", "t": 0.0})
                d["values"].update(vals)
                d["state"] = el.get("state", d["state"])
                d["t"] = time.time()
        elif tag in ("defSwitchVector", "setSwitchVector"):
            vals = {c.get("name"): (c.text or "").strip() == "On" for c in el if c.tag in ("defSwitch", "oneSwitch")}
            with self.lock:
                self.switches.setdefault((dev, name), {}).update(vals)
        elif tag == "delProperty":
            with self.lock:
                for store in (self.numbers, self.switches):
                    for k in [k for k in store if k[0] == dev and (name is None or k[1] == name)]:
                        del store[k]

    # ---------------- what the program asks ----------------
    def telescopes(self):
        """Devices that publish equatorial coordinates (mounts)."""
        with self.lock:
            return sorted({d for (d, p) in self.numbers if p in (COORD_EOD, COORD_J2000)})

    def device_connected(self, device):
        with self.lock:
            sw = self.switches.get((device, "CONNECTION"))
        return True if sw is None else bool(sw.get("CONNECT"))

    def pointing(self, device):
        """(RA hours, Dec deg, jnow, busy, age_s) or None. Prefers JNow (EOD) coordinates, like the mount reports."""
        with self.lock:
            for prop, jnow in ((COORD_EOD, True), (COORD_J2000, False)):
                d = self.numbers.get((device, prop))
                if d and "RA" in d["values"] and "DEC" in d["values"]:
                    return (d["values"]["RA"], d["values"]["DEC"], jnow, d["state"] == "Busy", time.time() - d["t"])
        return None

    def site(self, device):
        """(lat, lon east -180..180, elevation m) from GEOGRAPHIC_COORD, or None."""
        with self.lock:
            d = self.numbers.get((device, "GEOGRAPHIC_COORD"))
            if not d:
                return None
            v = d["values"]
        if "LAT" not in v or "LONG" not in v:
            return None
        lon = v["LONG"] if v["LONG"] <= 180.0 else v["LONG"] - 360.0
        return v["LAT"], lon, v.get("ELEV", 0.0)


class IndiMount:
    """Looks like the ASCOM mount object the rest of the program uses (RightAscension, Declination, Slewing,
    EquatorialSystem, Name). Raises RuntimeError when the INDI server or the mount is not available."""

    def __init__(self, client, device):
        self.client, self.device = client, device
        self.Name = device

    def _p(self):
        c = self.client
        if not c.connected:
            raise RuntimeError("INDI server connection lost" + (f": {c.error}" if c.error else ""))
        if not c.device_connected(self.device):
            raise RuntimeError(f"{self.device} is disconnected in the INDI server (connect it in Ekos)")
        p = c.pointing(self.device)
        if p is None:
            raise RuntimeError(f"{self.device} reports no coordinates")
        return p

    @property
    def RightAscension(self):
        return self._p()[0]

    @property
    def Declination(self):
        return self._p()[1]

    @property
    def Slewing(self):
        return self._p()[3]

    @property
    def EquatorialSystem(self):
        return 1 if self._p()[2] else 2          # ASCOM: 1 = topocentric (JNow), 2 = J2000

    @property
    def Connected(self):
        return self.client.connected and self.client.device_connected(self.device)

    def disconnect(self):
        self.client.close()
