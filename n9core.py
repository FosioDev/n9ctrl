"""
n9core - talks to the AKG N9 Hybrid via USB dongle (Airoha RACE protocol).

Format (see docs/PROTOCOL.md):
  send   : HID report 0x06 = 06 <len+1> <destination> <RACE>
  read   : GET_REPORT 0x07 = 07 <len> <destination> <len bytes>   (continuous stream)
  destination: 0x00 = dongle, 0x80 = headset
  RACE   : 05 <type> <len u16le> <id u16le> <payload>
"""
import logging
import threading
import time

import hid

VID, PID = 0x0ECB, 0x20D9
HEADSET = 0x80
log = logging.getLogger("n9ctrl")

MODES = {"anc": 1, "ambient": 10, "off": 0}           # filter value reported by the headset
FILTER_NAME = {v: k for k, v in MODES.items()}


def race(rid, p=b""):
    n = 2 + len(p)
    return bytes([5, 0x5A, n & 255, n >> 8, rid & 255, rid >> 8]) + p


def hexs(b):
    return " ".join(f"{x:02X}" for x in b)


# ---------------------------------------------------------------- resources
# Each GET returns a frame f = 05 <type> <len2> <id2> <status> <data...>:
#   f[6] = status, f[7:] = data. The functions below translate the data into simple values.
def _n(f, k):
    return len(f) >= k


def _lt_voice(f):          # module 19: mic effect, sound effect, audio profile
    return {"mic_effect": f[9], "sound_effect": f[10], "audio_mode": f[11]} if _n(f, 12) and f[7] == 19 else {}


def _lt_autooff(f):      # module 1: enabled (u16), time in seconds (u16)
    if not _n(f, 13) or f[7] != 1:
        return {}
    return {"auto_off": {"on": bool(f[9] | (f[10] << 8)), "min": (f[11] | (f[12] << 8)) // 60}}


def _one(key):          # one byte at f[7]
    return lambda f: {key: f[7]} if _n(f, 8) and f[6] == 0 else {}


def _pair(key, a, b):   # two bytes at f[7], f[8]
    return lambda f: {key: {a: f[7], b: f[8]}} if _n(f, 9) and f[6] == 0 else {}


def _lt_vp(f):           # voice prompt: module echo in f[6:8], status f[8], value f[9]
    return {"voice_prompt": f[9]} if _n(f, 10) and f[6] == 1 and f[8] == 0 else {}


def _lt_source(f):        # active source: f[7] 0=BT 1=dongle 2=P2, f[8] P2 on, f[9] dongle paired
    if not _n(f, 8) or f[6] != 0:
        return {}
    r = {"source": f[7]}
    if _n(f, 10):
        r.update(source_aux=f[8], source_dongle=f[9])
    return r


def _lt_adapt(f):        # adaptive ANC (environment detection): f[7]=3, f[8]=enabled
    return {"anc_adapt": f[8]} if _n(f, 9) and f[6] == 0 and f[7] == 3 else {}


def _lt_autocomp(f):     # automatic compensation: headset stores 0 = enabled
    return {"auto_comp": 1 - f[14]} if _n(f, 15) and f[6] == 0 and f[7] == 13 else {}


def _lt_anc_level(f):    # module 5: f[9] = active filter (1 = ANC), f[10:12] = level
    return {"anc_level": f[10] | (f[11] << 8)} if _n(f, 12) and f[6] == 5 and f[9] == 1 else {}


def _lt_amb_level(f):    # module 7: f[9:11] = ambient level
    return {"amb_level": f[9] | (f[10] << 8)} if _n(f, 11) and f[6] == 7 else {}


GETS = [
    (0x0E17, b"\x03\x02", _lt_adapt),
    (0x0E26, bytes([0, 13, 0, 0]), _lt_autocomp),
    (0x0901, b"\x05\x00", _lt_anc_level),
    (0x0901, b"\x07\x00", _lt_amb_level),
    (0x2C83, b"\x13\x00", _lt_voice),
    (0x2C83, b"\x01\x00", _lt_autooff),
    (0x0013, b"", _pair("audio3d", "on", "type")),
    (0x0014, b"", _one("safesound")),
    (0x0053, b"", _one("chat_opt")),
    (0x0051, b"", _one("limiter")),
    (0x0058, b"", _one("eq_bass")),
    (0x0060, b"", _pair("smart_talk", "on", "latency")),
    (0x0075, b"", _one("hires")),
    (0x0064, b"", _one("mic_gain")),
    (0x0037, b"", _pair("balance", "on", "level")),
    (0x0055, b"", _one("source")),
    (0x0901, b"\x01\x00", _lt_vp),
]


def _bt(v):
    return 1 if v else 0


# SETs: key -> function(value, current) -> (RACE id, payload, function that checks the response)
def _ok0(f):
    return _n(f, 7) and f[6] == 0


def _ok_vp(f):
    return _n(f, 9) and f[6] == 1 and f[8] == 0


SETS = {
    "anc_adapt": lambda v, a: (0x0E17, bytes([3, _bt(v)]), _ok0),
    "auto_comp": lambda v, a: (0x0E13, bytes([5, 1 - _bt(v)]), _ok0),
    "anc_level": lambda v, a: (0x0E06, bytes([0, 30]) + int(v).to_bytes(2, "little"), _ok0),
    "amb_level": lambda v, a: (0x0E06, bytes([0, 30]) + int(v).to_bytes(2, "little"), _ok0),
    "balance": lambda v, a: (0x0036, bytes([_bt(v["on"]), int(v["level"])]), _ok0),
    "sound_effect": lambda v, a: (0x2C82, bytes([19, 0, int(v)]), _ok0),
    "mic_effect": lambda v, a: (0x2C82, bytes([19, 0, int(v)]), _ok0),
    "audio_mode": lambda v, a: (0x2C82, bytes([19, 0, int(v)]), _ok0),
    "auto_off": lambda v, a: (0x2C82, bytes([1, 0, _bt(v["on"]), 0]) + int(v["min"] * 60).to_bytes(2, "little"), _ok0),
    "audio3d": lambda v, a: (0x0012, bytes([_bt(v["on"]), int(v["type"])]), _ok0),
    "safesound": lambda v, a: (0x0015, bytes([_bt(v)]), _ok0),
    "chat_opt": lambda v, a: (0x0052, bytes([_bt(v)]), _ok0),
    "limiter": lambda v, a: (0x0050, bytes([_bt(v)]), _ok0),
    "eq_bass": lambda v, a: (0x0059, bytes([_bt(v)]), _ok0),
    "smart_talk": lambda v, a: (0x0061, bytes([_bt(v["on"]), int(v["latency"])]), _ok0),
    "source": lambda v, a: (0x0054, bytes([int(v)]), _ok0),
    "hires": lambda v, a: (0x0076, bytes([_bt(v)]), _ok0),
    "voice_prompt": lambda v, a: (0x0900, bytes([1, 0, _bt(v)]), _ok_vp),
}


class N9:
    """Maintains the connection to the dongle, reconnects automatically, and publishes state."""

    id = "n9"
    name = "AKG N9 Hybrid"
    connection = "USB 2.4 GHz dongle"
    resources = ["battery", "mode", "settings"]

    def __init__(self, on_change):
        self.on_change = on_change          # function(state: dict)
        self.dev = None
        self.io = threading.Lock()        # HID access
        self.cmd = threading.Lock()       # one request at a time
        self.bufs = {}
        self.listeners = []
        self.state = {"dongle": False, "headset": False, "battery": None, "mode": None, "updated": None, "rec": {}}
        self.window_open = False        # window open -> poll more often
        self.wake = threading.Event()  # force an immediate poll
        self.stop = False
        self._rec_timer = None
        self._pending = set()                # ids we are waiting for (responses, not notifications)
        threading.Thread(target=self._reader, daemon=True, name="reader").start()
        threading.Thread(target=self._watcher, daemon=True, name="watcher").start()

    # ------------------------------------------------------------ connection
    def _open(self):
        for i in hid.enumerate(VID, PID):
            if i["usage_page"] == 0xFF13 and "&Col02" in i["path"].decode(errors="ignore"):
                d = hid.device()
                d.open_path(i["path"])
                return d
        return None

    def _close(self):
        with self.io:
            if self.dev:
                try:
                    self.dev.close()
                except Exception:
                    pass
            self.dev = None
            self.bufs.clear()

    def _publish(self, **changes):
        new = dict(self.state, **changes)
        if new != self.state:
            self.state = new
            try:
                self.on_change(dict(new))
            except Exception:
                log.exception("on_change")

    # ------------------------------------------------------------ reading
    def _reader(self):
        while not self.stop:
            if not self.dev:
                time.sleep(0.2)
                continue
            try:
                with self.io:
                    r = bytes(self.dev.get_input_report(0x07, 62)) if self.dev else b""
            except Exception as e:
                log.warning("dongle disappeared: %s", e)
                self._close()
                self._publish(dongle=False, headset=False)
                continue
            if len(r) >= 3 and r[1] > 0:
                target = r[2]
                self.bufs[target] = self._split(target, self.bufs.get(target, b"") + r[3:3 + r[1]])
            else:
                time.sleep(0.03)

    def _split(self, target, buf):
        while True:
            i = buf.find(b"\x05")
            if i < 0:
                return b""
            if len(buf) - i < 6:
                return buf[i:]
            if buf[i + 1] not in (0x5A, 0x5B, 0x5C, 0x5D):
                buf = buf[i + 1:]
                continue
            n = buf[i + 2] | (buf[i + 3] << 8)
            if n > 600:
                buf = buf[i + 1:]
                continue
            if len(buf) - i < 4 + n:
                return buf[i:]
            f = bytes(buf[i:i + 4 + n])
            buf = buf[i + 4 + n:]
            if f[4:6] == b"\x92\x0f":      # firmware debug log
                continue
            self._frame(target, f)

    def _frame(self, target, f):
        for cb in list(self.listeners):
            cb(target, f)
        rid = f[4] | (f[5] << 8)
        # the headset notifies by itself when the mode changes (e.g. physical button)
        if target == HEADSET and rid == 0x0901 and f[1] == 0x5D and len(f) >= 10:
            log.info("noise notification 0x0901: %s", hexs(f[6:]))
            self._publish(headset=True, mode=FILTER_NAME.get(f[9], f"filter {f[9]}"))
        # spontaneous notifications from the headset (changes made by the phone app, etc.)
        if target == HEADSET and f[1] in (0x5C, 0x5D) and rid != 0x0CD6:
            if rid != 0x0901 and rid not in self._pending:
                log.info("headset notification 0x%04X type=%02X: %s", rid, f[1], hexs(f[6:]))
            for gid, _pay, translator in GETS:
                if gid == rid:
                    try:
                        new = translator(f)
                    except Exception:
                        new = {}
                    if new:
                        rec = dict(self.state.get("rec") or {})
                        rec.update(new)
                        self._publish(rec=rec)
            if rid not in self._pending:
                self._reload_soon()  # spontaneous headset notification: recheck everything soon

    def _reload_soon(self, delay=1.2):
        """Batches several consecutive notifications and reloads resources once (real-time update)."""
        t = self._rec_timer
        if t:
            t.cancel()
        self._rec_timer = threading.Timer(delay, self._reload_bg)
        self._rec_timer.daemon = True
        self._rec_timer.start()

    def _reload_bg(self):
        try:
            if self.dev and self.state.get("headset") and not self.stop:
                self.read_resources()
        except Exception:
            log.exception("reload")

    # ------------------------------------------------------------ requests
    def _send(self, target, pkt):
        rep = (bytes([0x06, len(pkt) + 1, target]) + pkt).ljust(62, b"\x00")
        with self.io:
            if not self.dev:
                raise OSError("no dongle")
            self.dev.write(rep)

    def request(self, pkt, rid, type_=None, timeout=2.5, attempts=2):
        with self.cmd:
            for _ in range(attempts):
                ev, box = threading.Event(), []

                def cb(target, f):
                    if target == HEADSET and (f[4] | (f[5] << 8)) == rid and (type_ is None or f[1] == type_):
                        box.append(f)
                        ev.set()
                self.listeners.append(cb)
                self._pending.add(rid)
                try:
                    self._send(HEADSET, pkt)
                    ev.wait(timeout)
                except OSError:
                    return None
                finally:
                    self.listeners.remove(cb)
                    time.sleep(0.05)
                    self._pending.discard(rid)
                if box:
                    return box[0]
            return None

    def read_battery(self):
        f = self.request(race(0x0CD6, b"\x00"), 0x0CD6, type_=0x5D)
        return f[8] if f and len(f) >= 9 else None

    def read_mode(self):
        f = self.request(race(0x0901, b"\x05\x00"), 0x0901)
        if f and len(f) >= 10:
            return FILTER_NAME.get(f[9], f"filter {f[9]}")
        return None

    def set_mode(self, mode):
        if mode == "off":
            pkt = race(0x0E06, b"\x00\x0B")
        elif mode == "anc":
            pkt = race(0x0E06, bytes([0, 0x0A, 1, 0]))
        elif mode == "ambient":
            pkt = race(0x0E06, bytes([0, 0x0A, 10, 4]))
        else:
            raise ValueError(mode)
        f = self.request(pkt, 0x0E06)
        ok = bool(f) and len(f) >= 7 and f[6] == 0
        log.info("set_mode %s -> %s", mode, hexs(f) if f else "no response")
        if ok:
            self._publish(headset=True, mode=mode)
        threading.Timer(0.8, self.update).start()   # confirm with the headset
        return ok

    # ------------------------------------------------------------ resources
    def read_resources(self):
        """Asks the headset for the state of all features and publishes the result."""
        rec = dict(self.state.get("rec") or {})
        for rid, pay, translator in GETS:
            if self.stop or not self.dev:
                break
            f = self.request(race(rid, pay), rid, timeout=1.5, attempts=2)
            if not f:
                continue
            try:
                rec.update(translator(bytes(f)))
            except Exception:
                log.exception("translate 0x%04X", rid)
        self._publish(rec=rec)

    def set_resource(self, key, value):
        """Sends a change, checks the response, and reads the value back."""
        build = SETS.get(key)
        if not build or not self.dev:
            return False
        current = (self.state.get("rec") or {}).get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            value = dict(current, **value)
        rid, pay, check = build(value, current)
        f = self.request(race(rid, pay), rid, timeout=2.5, attempts=1)
        ok = bool(f) and check(bytes(f))
        log.info("set %s=%s -> %s", key, value, hexs(f) if f else "no response")
        if key == "source":
            # when switching to Bluetooth the headset leaves the dongle: cannot re-read, just publish
            if ok:
                self._publish(rec=dict(self.state.get("rec") or {}, source=int(value)))
            return ok
        self.read_resources()
        new = (self.state.get("rec") or {}).get(key)
        if ok and new is not None:
            want = value
            if isinstance(want, dict):
                ok = all(new.get(k) == (_bt(v) if k == "on" else int(v)) for k, v in want.items())
            else:
                ok = new == (_bt(want) if key in ("safesound", "chat_opt", "limiter", "eq_bass", "hires", "voice_prompt", "anc_adapt", "auto_comp") else int(want))
        return ok

    # ------------------------------------------------------------ loop
    def update(self, battery=False):
        if not self.dev:
            return
        mode = self.read_mode()
        changes = {"headset": mode is not None}
        if mode is not None:
            changes["mode"] = mode
            changes["updated"] = time.strftime("%H:%M")
        if battery or self.state["battery"] is None:
            b = self.read_battery()
            if b is not None:
                changes["battery"] = b
        self._publish(**changes)

    def _watcher(self):
        last_bat = last_rec = 0
        while not self.stop:
            if not self.dev:
                try:
                    d = self._open()
                except Exception:
                    d = None
                if d:
                    with self.io:
                        self.dev = d
                    log.info("dongle connected")
                    self._publish(dongle=True)
                    time.sleep(0.8)            # drain the old queue
                    last_bat = 0
                else:
                    self._publish(dongle=False, headset=False)
                    time.sleep(3)
                    continue
            now = time.time()
            try:
                self.update(battery=now - last_bat > 120)
                if now - last_bat > 120:
                    last_bat = now
                if self.state["headset"] and (not self.state["rec"] or (self.window_open and now - last_rec > 8)):
                    self.read_resources()
                    last_rec = now
            except Exception:
                log.exception("update")
            self.wake.wait(4 if self.window_open else 20)
            self.wake.clear()

    def close(self):
        self.stop = True
        self._close()
