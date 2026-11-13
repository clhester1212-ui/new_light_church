"""Sony VISCA-over-IP control (UDP 52381) for the New Light PTZ cameras.

How Sony cameras answer: replies always go back to UDP port 52381 on the sender's
computer, no matter which port the command came from. Companion sends from random
ports and never listens on 52381, so this app owns 52381 for replies. One shared
socket is used for everything, guarded by a lock so only one inquiry waits at a time.
"""
from __future__ import annotations

import socket
import struct
import threading
import time

VISCA_PORT = 52381
_COMMAND = 0x0100
_INQUIRY = 0x0110
_CONTROL = 0x0200

# Speed limits for the SRG-300SE / SRG-X120 (pan 1-24, tilt 1-23, zoom 0-7).
PAN_MAX, TILT_MAX, ZOOM_MAX = 0x18, 0x17, 7


class _Transport:
    """One UDP socket on port 52381 shared by every camera."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind(("", VISCA_PORT))
            self.listening = True
        except OSError:
            # Someone else holds 52381: commands still work, inquiries won't get replies.
            self.sock.bind(("", 0))
            self.listening = False
        self.sock.settimeout(0.05)
        self.lock = threading.Lock()
        self.seq = 1
        self.reset_done: set[str] = set()

    def _next_seq(self) -> int:
        self.seq = (self.seq + 1) & 0xFFFFFFFF
        return self.seq

    def send(self, ip: str, ptype: int, payload: bytes) -> int:
        seq = self._next_seq()
        self.sock.sendto(struct.pack(">HHI", ptype, len(payload), seq) + payload, (ip, VISCA_PORT))
        return seq

    def drain(self, ip: str, wait: float) -> list[bytes]:
        """Collect replies from one camera for up to `wait` seconds."""
        out: list[bytes] = []
        end = time.monotonic() + wait
        while time.monotonic() < end:
            try:
                data, addr = self.sock.recvfrom(1024)
            except (socket.timeout, ConnectionResetError):
                if out:
                    break
                continue
            if addr[0] == ip and len(data) > 8:
                out.append(data[8:])
                if data[8:][1:2] == b"\x50":  # completion / inquiry answer
                    break
        return out

    def drain_ack(self, ip: str, wait: float) -> list[bytes]:
        """Collect replies until an ACK (90 4y) or error (90 6y). Completions (90 5y) are
        skipped over, because they may be left over from an earlier command."""
        out: list[bytes] = []
        end = time.monotonic() + wait
        while time.monotonic() < end:
            try:
                data, addr = self.sock.recvfrom(1024)
            except (socket.timeout, ConnectionResetError):
                continue
            if addr[0] == ip and len(data) > 9:
                msg = data[8:]
                out.append(msg)
                if msg[1] & 0xF0 in (0x40, 0x60):
                    break
        return out


_transport: _Transport | None = None
_transport_lock = threading.Lock()


def transport() -> _Transport:
    global _transport
    with _transport_lock:
        if _transport is None:
            _transport = _Transport()
        return _transport


def _nibbles(data: bytes, start: int, count: int = 4) -> int:
    v = 0
    for b in data[start:start + count]:
        v = (v << 4) | (b & 0x0F)
    return v


def _signed16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


class ViscaCamera:
    def __init__(self, ip: str):
        self.ip = ip

    # ---- commands (fire and forget; the camera acts even if we don't read the ACK) ----
    def _cmd(self, hex_or_bytes, tries: int = 4) -> bool:
        """Send a command and wait for the camera's ACK. If the camera says it is busy
        (buffer full / no free socket / not executable right now) or doesn't answer, try again.
        Returns True once the camera accepted it."""
        payload = bytes.fromhex(hex_or_bytes) if isinstance(hex_or_bytes, str) else hex_or_bytes
        t = transport()
        with t.lock:
            if self.ip not in t.reset_done:
                self.reset_sequence()
                t.reset_done.add(self.ip)
            for attempt in range(tries):
                t.send(self.ip, _COMMAND, payload)
                if not t.listening:
                    return True  # can't hear replies; best effort
                for reply in t.drain_ack(self.ip, 0.25):
                    kind = reply[1] & 0xF0
                    # Only a real ACK (90 4y) counts. A completion (90 5y) can belong to an
                    # EARLIER command (seen 2026-11-11: a pan's completion was mistaken for
                    # the next zoom's ACK, and the rejected zoom was never retried).
                    if kind == 0x40:
                        return True
                    if kind == 0x60 and len(reply) > 2 and reply[2] == 0x02:
                        return False  # syntax error: retrying won't help
                if attempt == 1:
                    self.reset_sequence()
                time.sleep(0.08)
            return False

    def pan_tilt(self, pan: int, tilt: int) -> bool:
        """Drive at signed speeds. pan>0 = right, tilt>0 = up, 0 = stop that axis."""
        ps = max(1, min(PAN_MAX, abs(pan)))
        ts = max(1, min(TILT_MAX, abs(tilt)))
        pd = 3 if pan == 0 else (2 if pan > 0 else 1)
        td = 3 if tilt == 0 else (1 if tilt > 0 else 2)
        return self._cmd(bytes([0x81, 0x01, 0x06, 0x01, ps, ts, pd, td, 0xFF]))

    def stop(self) -> None:
        # The camera drops a second command that arrives in the same instant,
        # so leave a short gap (seen 2026-11-11: zoom kept going after a stop).
        self.pan_tilt(0, 0)
        time.sleep(0.06)
        self.zoom(0)

    def zoom(self, speed: int) -> bool:
        """speed>0 = zoom in (tele), <0 = out (wide), 0 = stop. |speed| 1-7."""
        if speed == 0:
            return self._cmd("8101040700FF")
        return self.zoom_drive(1 if speed > 0 else -1, abs(speed))

    def zoom_drive(self, direction: int, p: int) -> bool:
        """Zoom in (direction>0) or out (<0) at Sony speed p = 0 (slowest) .. 7 (fastest)."""
        s = max(0, min(ZOOM_MAX, p))
        return self._cmd(bytes([0x81, 0x01, 0x04, 0x07, (0x20 if direction > 0 else 0x30) | s, 0xFF]))

    def zoom_position(self) -> int | None:
        r = self._inq("81090447FF", wait=0.4)
        return _nibbles(r, 2) if r and len(r) >= 7 else None

    def zoom_to(self, position: int) -> bool:
        p = max(0, min(0x4000, position))
        return self._cmd(bytes([0x81, 0x01, 0x04, 0x47, (p >> 12) & 0xF, (p >> 8) & 0xF, (p >> 4) & 0xF, p & 0xF, 0xFF]))

    def pan_tilt_to(self, pan: int, tilt: int, speed: int = 8) -> bool:
        """Move to an exact pan/tilt position (the numbers status() reports)."""
        def nib(v: int) -> list[int]:
            v &= 0xFFFF
            return [(v >> 12) & 0xF, (v >> 8) & 0xF, (v >> 4) & 0xF, v & 0xF]
        ps = max(1, min(PAN_MAX, speed))
        ts = max(1, min(TILT_MAX, speed))
        return self._cmd(bytes([0x81, 0x01, 0x06, 0x02, ps, ts, *nib(pan), *nib(tilt), 0xFF]))

    def recall_preset(self, number: int) -> bool:
        return self._cmd(bytes([0x81, 0x01, 0x04, 0x3F, 0x02, number & 0xFF, 0xFF]))

    def home(self) -> bool:
        return self._cmd("81010604FF")

    # ---- inquiries (need the reply on port 52381) ----
    def reset_sequence(self) -> None:
        """Sony cameras drop messages whose sequence number they don't expect (e.g. after
        this app restarts). A RESET control message clears that."""
        t = transport()
        t.send(self.ip, _CONTROL, b"\x01")
        t.drain(self.ip, 0.3)

    def _inq(self, hex_payload: str, wait: float = 0.8) -> bytes | None:
        t = transport()
        with t.lock:
            for attempt in range(2):
                t.send(self.ip, _INQUIRY, bytes.fromhex(hex_payload))
                for reply in t.drain(self.ip, wait):
                    if reply[:2] == b"\x90\x50":
                        return reply
                if attempt == 0:
                    self.reset_sequence()
        return None

    def pan_tilt_position(self) -> tuple[int, int] | None:
        r = self._inq("81090612FF", wait=0.4)
        if r and len(r) >= 11:
            return _signed16(_nibbles(r, 2)), _signed16(_nibbles(r, 6))
        return None

    def status(self) -> dict:
        """Power, zoom and pan/tilt position. Missing values mean no reply."""
        out: dict = {"online": False}
        r = self._inq("81090400FF")
        if r is None:
            return out
        out["online"] = True
        out["power"] = "on" if r[2] == 0x02 else "standby"
        r = self._inq("81090447FF")
        if r and len(r) >= 7:
            out["zoom"] = _nibbles(r, 2)
        r = self._inq("81090612FF")
        if r and len(r) >= 11:
            out["pan"] = _signed16(_nibbles(r, 2))
            out["tilt"] = _signed16(_nibbles(r, 6))
        return out

    def version(self) -> str | None:
        r = self._inq("81090002FF")
        if r and len(r) >= 10:
            return r[4:6].hex().upper()
        return None
