"""Same-LAN iPhone access for Local Chat.

Loopback (the PC itself) stays open as before. A phone on the Wi-Fi is a different
machine: it only reaches private IPv4 addresses we explicitly bind, and every chat,
model, search, and code-runner request from that peer needs a paired session.

The pairing code and the session token stay in process memory. They are not written
to disk, not put in URLs, and not logged by this module. Callers must not log the
return values of issue_code() or the raw token from open_session().

Ollama is not touched here. This module never binds 0.0.0.0 and never selects a
public address.
"""
import hmac
import os
import re
import secrets
import socket
import struct
import threading
import time

COOKIE = "lc_session"
CODE_TTL = 10 * 60
SESSION_TTL = 12 * 3600
MAX_CODE_FAILS = 5
MAX_IP_FAILS = 8
LOCK_WINDOW = 15 * 60
MAX_SESSIONS = 8
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
_CODE_RE = re.compile(r"[^A-Z0-9]")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{20,200}")
_LABEL_RE = re.compile(r"[\r\n\t]+")

# Substring markers for bridges, VPNs, tunnels, and container NICs. Short names
# such as "tun" / "tap" are prefixes only so they don't match "Ethernet".
_VIRTUAL_MARKERS = (
    "docker", "veth", "virbr", "tailscale", "zerotier", "bluetooth", "vmware",
    "virtualbox", "vbox", "vethernet", "hyper-v", "wsl", "tunnel", "teredo",
    "isatap", "npcap", "loopback", "cni", "flannel", "kube", "podman", "libvirt",
    "vpn", "singbox", "clash", "nordlynx", "hamachi",
)
_VIRTUAL_PREFIXES = ("tun", "tap", "wg", "zt", "br-")


class LanError(Exception):
    """Safe to show in the desktop UI. Do not put codes or tokens in the message."""


def is_virtual_interface(name):
    raw = (name or "").lower()
    n = raw.replace("_", "").replace(" ", "")
    if n in ("lo", "lo0"):
        return True
    if raw.startswith(_VIRTUAL_PREFIXES) or n.startswith(("docker", "veth", "virbr")):
        return True
    return any(marker in raw or marker.replace("-", "") in n for marker in _VIRTUAL_MARKERS)


def is_private_ipv4(ip):
    """RFC1918 only. Loopback, link-local, CGNAT (Tailscale), and public addresses are out."""
    try:
        n = struct.unpack("!I", socket.inet_aton(ip))[0]
    except OSError:
        return False
    if (n & 0xFF000000) == 0x0A000000:  # 10.0.0.0/8
        return True
    if (n & 0xFFF00000) == 0xAC100000:  # 172.16.0.0/12
        return True
    if (n & 0xFFFF0000) == 0xC0A80000:  # 192.168.0.0/16
        return True
    return False


def netmask_to_prefix(mask):
    try:
        n = struct.unpack("!I", socket.inet_aton(mask))[0]
    except OSError:
        return 24
    prefix = bin(n).count("1")
    if prefix < 8 or prefix > 30:
        return 24
    expect = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
    return prefix if n == expect else 24


def ip_in_subnet(ip, network, prefix):
    if not isinstance(prefix, int) or prefix < 8 or prefix > 30:
        return False
    try:
        ip_i = struct.unpack("!I", socket.inet_aton(ip))[0]
        net_i = struct.unpack("!I", socket.inet_aton(network))[0]
    except OSError:
        return False
    mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
    return (ip_i & mask) == (net_i & mask)


def normalize_code(value):
    return _CODE_RE.sub("", (value or "").upper())


def format_code(norm):
    return norm[:4] + "-" + norm[4:]


def clean_label(value):
    text = _LABEL_RE.sub(" ", str(value or ""))[:40].strip()
    return text or "iPhone"


def hash_token(token):
    import hashlib
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def const_eq(a, b):
    if not isinstance(a, str):
        a = "" if a is None else str(a)
    if not isinstance(b, str):
        b = "" if b is None else str(b)
    if len(a) != len(b):
        hmac.compare_digest(a.encode(), a.encode())
        return False
    return hmac.compare_digest(a.encode(), b.encode())


def cookie_value(header, name):
    if not header:
        return ""
    for part in header.split(";"):
        key, sep, val = part.strip().partition("=")
        if sep and key == name:
            return val.strip()
    return ""


def session_cookie(token, max_age):
    if not _TOKEN_RE.fullmatch(token or ""):
        raise LanError("bad token")
    return f"{COOKIE}={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={int(max_age)}"


def clear_cookie():
    return f"{COOKIE}=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"


def address_url(ip, port):
    return f"http://{ip}:{int(port)}/"


# ---------------------------------------------------------------- interfaces
def _ioctl_ipv4(name, request):
    import fcntl
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        packed = struct.pack("256s", name.encode("utf-8")[:15])
        try:
            out = fcntl.ioctl(sock.fileno(), request, packed)
        except OSError:
            return ""
        return socket.inet_ntoa(out[20:24])
    finally:
        sock.close()


def _linux_default_iface():
    try:
        with open("/proc/net/route", encoding="ascii", errors="replace") as fh:
            next(fh, None)
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "00000000":
                    return parts[0]
    except OSError:
        return ""
    return ""


def _linux_private_ipv4():
    root = "/sys/class/net"
    if not os.path.isdir(root):
        return []
    SIOCGIFADDR = 0x8915
    SIOCGIFNETMASK = 0x891B
    found = []
    for name in sorted(os.listdir(root)):
        if name == "lo" or is_virtual_interface(name):
            continue
        state_path = os.path.join(root, name, "operstate")
        try:
            with open(state_path, encoding="ascii", errors="replace") as fh:
                state = fh.read().strip().lower()
        except OSError:
            state = ""
        if state == "down":
            continue
        ip = _ioctl_ipv4(name, SIOCGIFADDR)
        mask = _ioctl_ipv4(name, SIOCGIFNETMASK)
        if not ip or not is_private_ipv4(ip):
            continue
        found.append({
            "ip": ip,
            "prefix": netmask_to_prefix(mask) if mask else 24,
            "name": name[:64],
            "preferred": False,
        })
    preferred = _linux_default_iface()
    for row in found:
        if row["name"] == preferred:
            row["preferred"] = True
    if found and not any(row["preferred"] for row in found):
        found[0]["preferred"] = True
    return found


def _default_route_ipv4():
    """Source address the kernel would use for an off-link UDP socket. No packet is sent."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        ip = sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        sock.close()
    return ip if is_private_ipv4(ip) else ""


def _adapter_layout(ptr_size):
    """Byte offsets into IP_ADAPTER_ADDRESSES / IP_ADAPTER_UNICAST_ADDRESS.

    The first member is a ULONGLONG alignment union, so `Next` is at byte 8 on
    both 32-bit and 64-bit Windows. SOCKET_ADDRESS is pointer-aligned, which
    pads it to 16 bytes on 64-bit and puts OnLinkPrefixLength at byte 56.
    """
    if ptr_size == 8:
        return {"next": 8, "unicast": 24, "friendly": 72, "uni_next": 8, "uni_addr": 16, "uni_prefix": 56}
    if ptr_size == 4:
        return {"next": 8, "unicast": 16, "friendly": 40, "uni_next": 8, "uni_addr": 12, "uni_prefix": 44}
    return None


def _ptr(blob, offset, ptr_size):
    fmt = "<Q" if ptr_size == 8 else "<I"
    if offset < 0 or offset + struct.calcsize(fmt) > len(blob):
        return 0
    return struct.unpack_from(fmt, blob, offset)[0]


def _in_blob(ptr, base, length):
    if not ptr:
        return None
    off = ptr - base
    if off < 0 or off >= length:
        return None
    return off


def _wide(blob, offset, limit=64):
    chars = []
    for _ in range(limit):
        if offset + 2 > len(blob):
            break
        code = struct.unpack_from("<H", blob, offset)[0]
        offset += 2
        if code == 0:
            break
        chars.append(chr(code))
    return "".join(chars)


def parse_windows_adapters(blob, base, ptr_size=8):
    """Parse a GetAdaptersAddresses buffer. Pointers are absolute addresses into `blob`."""
    layout = _adapter_layout(ptr_size)
    if not layout or not blob:
        return []
    found = []
    cursor = base
    seen = set()
    for _ in range(64):
        off = _in_blob(cursor, base, len(blob))
        if off is None or off in seen:
            break
        seen.add(off)
        name = ""
        name_ptr = _ptr(blob, off + layout["friendly"], ptr_size)
        name_off = _in_blob(name_ptr, base, len(blob))
        if name_off is not None:
            name = _wide(blob, name_off)
        uni = _ptr(blob, off + layout["unicast"], ptr_size)
        if not is_virtual_interface(name):
            uni_seen = set()
            while uni and len(uni_seen) < 16:
                uni_off = _in_blob(uni, base, len(blob))
                if uni_off is None or uni_off in uni_seen:
                    break
                uni_seen.add(uni_off)
                if uni_off + layout["uni_prefix"] >= len(blob):
                    break
                prefix = blob[uni_off + layout["uni_prefix"]]
                sockaddr = _ptr(blob, uni_off + layout["uni_addr"], ptr_size)
                sock_off = _in_blob(sockaddr, base, len(blob))
                ip = ""
                if sock_off is not None and sock_off + 8 <= len(blob):
                    family = struct.unpack_from("<H", blob, sock_off)[0]
                    if family == 2:  # AF_INET
                        try:
                            ip = socket.inet_ntoa(blob[sock_off + 4:sock_off + 8])
                        except OSError:
                            ip = ""
                if ip and is_private_ipv4(ip):
                    if not isinstance(prefix, int) or prefix < 8 or prefix > 30:
                        prefix = 24
                    found.append({
                        "ip": ip, "prefix": int(prefix), "name": (name or "Ethernet")[:64], "preferred": False,
                    })
                uni = _ptr(blob, uni_off + layout["uni_next"], ptr_size)
        cursor = _ptr(blob, off + layout["next"], ptr_size)
        if not cursor:
            break
    uniq, seen_ips = [], set()
    for row in found:
        if row["ip"] in seen_ips:
            continue
        seen_ips.add(row["ip"])
        uniq.append(row)
    return uniq


def _windows_private_ipv4():
    """IPv4 unicast addresses via GetAdaptersAddresses, then the default-route fallback."""
    import ctypes
    from ctypes import wintypes

    ptr_size = ctypes.sizeof(ctypes.c_void_p)
    if ptr_size not in (4, 8):
        return []
    iphlpapi = ctypes.windll.iphlpapi
    size = wintypes.ULONG(16 * 1024)
    flags = 0x0002 | 0x0004 | 0x0008  # skip anycast, multicast, dns
    buf = None
    for _ in range(4):
        buf = ctypes.create_string_buffer(size.value)
        rc = iphlpapi.GetAdaptersAddresses(
            ctypes.c_ulong(2), ctypes.c_ulong(flags), None,
            ctypes.cast(buf, ctypes.c_void_p), ctypes.byref(size))
        if rc == 0:
            break
        if rc != 111:  # ERROR_BUFFER_OVERFLOW
            return []
        buf = None
    if buf is None:
        return []
    found = parse_windows_adapters(buf.raw, ctypes.addressof(buf), ptr_size)
    preferred = _default_route_ipv4()
    for row in found:
        if row["ip"] == preferred:
            row["preferred"] = True
    if found and not any(row["preferred"] for row in found):
        found[0]["preferred"] = True
    return found


def _fallback_private_ipv4():
    ip = _default_route_ipv4()
    if not ip:
        return []
    return [{"ip": ip, "prefix": 24, "name": "network", "preferred": True, "prefix_assumed": True}]


def list_private_ipv4():
    """Private IPv4 addresses on real adapters. Empty when the PC is not on a LAN."""
    if os.name == "nt":
        try:
            found = _windows_private_ipv4()
        except Exception:
            found = []
        return found or _fallback_private_ipv4()
    if os.path.isdir("/sys/class/net"):
        return _linux_private_ipv4()
    return _fallback_private_ipv4()


# ---------------------------------------------------------------- gate
class LanGate:
    def __init__(self):
        self.lock = threading.Lock()
        self.enabled = False
        self.bindings = []  # {ip, prefix, name, port, preferred}
        self.warnings = []
        self.code = None  # {norm, display, expires, failures}
        self.code_burned = False
        self.sessions = {}  # sid -> record
        self.ip_fails = {}

    def clear(self):
        with self.lock:
            self.enabled = False
            self.bindings = []
            self.warnings = []
            self.code = None
            self.code_burned = False
            self.sessions = {}
            self.ip_fails = {}

    def set_listening(self, bindings, warnings):
        with self.lock:
            self.enabled = True
            self.bindings = list(bindings)
            self.warnings = list(warnings)

    def host_headers(self):
        with self.lock:
            return {f"{b['ip']}:{int(b['port'])}" for b in self.bindings}

    def peer_allowed(self, ip):
        if ip in ("127.0.0.1", "::1"):
            return True
        with self.lock:
            bindings = list(self.bindings)
        return any(ip_in_subnet(ip, b["ip"], int(b["prefix"])) for b in bindings)

    def _ip_locked(self, ip, now):
        stamps = [t for t in self.ip_fails.get(ip, []) if now - t < LOCK_WINDOW]
        self.ip_fails[ip] = stamps
        if not stamps:
            self.ip_fails.pop(ip, None)
        return len(stamps) >= MAX_IP_FAILS

    def _note_fail(self, ip, now):
        stamps = [t for t in self.ip_fails.get(ip, []) if now - t < LOCK_WINDOW]
        stamps.append(now)
        self.ip_fails[ip] = stamps[-MAX_IP_FAILS * 2:]

    def issue_code(self):
        raw = "".join(secrets.choice(ALPHABET) for _ in range(8))
        display = format_code(raw)
        with self.lock:
            self.code = {"norm": raw, "display": display, "expires": time.time() + CODE_TTL, "failures": 0}
            self.code_burned = False
            expires_in = CODE_TTL
        return {"code": display, "expires_in": expires_in}

    def current_code(self):
        """Plain code for the loopback UI only. Caller must not log it."""
        with self.lock:
            code = self.code
            if not code or time.time() > code["expires"]:
                return None
            return {"code": code["display"], "expires_in": max(0, int(code["expires"] - time.time()))}

    def check_code(self, given, peer_ip):
        """Return 'ok', 'bad', 'expired', or 'locked'. Does not issue a session."""
        now = time.time()
        with self.lock:
            if self._ip_locked(peer_ip, now):
                return "locked"
            code = self.code
            if not code or now > code["expires"]:
                self.code = None
                return "expired"
            norm = normalize_code(given)
            if len(norm) > 64 or not const_eq(norm, code["norm"]):
                code["failures"] += 1
                self._note_fail(peer_ip, now)
                if code["failures"] >= MAX_CODE_FAILS:
                    self.code = None
                    self.code_burned = True
                return "bad"
            self.code = None
            self.code_burned = False
            self.ip_fails.pop(peer_ip, None)
            return "ok"

    def open_session(self, peer_ip, label):
        token = secrets.token_urlsafe(32)
        token_hash = hash_token(token)
        sid = secrets.token_hex(4)
        now = time.time()
        record = {
            "sid": sid,
            "token_hash": token_hash,
            "label": clean_label(label),
            "ip": peer_ip,
            "created": now,
            "last": now,
            "expires": now + SESSION_TTL,
        }
        with self.lock:
            if len(self.sessions) >= MAX_SESSIONS:
                return None, "too_many"
            self.sessions[sid] = record
        return token, record

    def session_ok(self, cookie_header, peer_ip):
        token = cookie_value(cookie_header, COOKIE)
        if not token or not _TOKEN_RE.fullmatch(token):
            return False
        digest = hash_token(token)
        now = time.time()
        with self.lock:
            match = None
            for record in self.sessions.values():
                if const_eq(record["token_hash"], digest):
                    match = record
                    break
            if match is None:
                const_eq(digest, digest)
                return False
            if now > match["expires"]:
                self.sessions.pop(match["sid"], None)
                return False
            if match["ip"] != peer_ip:
                # Wrong address (stolen cookie, or the phone's DHCP lease moved). Drop it.
                self.sessions.pop(match["sid"], None)
                return False
            match["last"] = now
            return True

    def revoke_cookie(self, cookie_header):
        token = cookie_value(cookie_header, COOKIE)
        if not token:
            return False
        digest = hash_token(token)
        with self.lock:
            for sid, record in list(self.sessions.items()):
                if const_eq(record["token_hash"], digest):
                    del self.sessions[sid]
                    return True
        return False

    def revoke(self, sid=None, all_sessions=False):
        with self.lock:
            if all_sessions:
                self.sessions.clear()
                return 0
            if sid and sid in self.sessions:
                del self.sessions[sid]
                return 1
        return 0

    def public_sessions(self):
        with self.lock:
            rows = []
            for record in self.sessions.values():
                rows.append({
                    "id": record["sid"],
                    "label": record["label"],
                    "ip": record["ip"],
                    "created": int(record["created"] * 1000),
                    "last": int(record["last"] * 1000),
                })
        rows.sort(key=lambda row: row["last"], reverse=True)
        return rows

    def snapshot(self):
        with self.lock:
            code = self.code
            expires_in = 0
            if code and time.time() <= code["expires"]:
                expires_in = max(0, int(code["expires"] - time.time()))
            else:
                code = None
            return {
                "enabled": self.enabled,
                "bindings": list(self.bindings),
                "warnings": list(self.warnings),
                "has_code": bool(code),
                "code_expires_in": expires_in,
                "code_burned": self.code_burned and not code,
                "sessions": len(self.sessions),
            }


GATE = LanGate()
