#!/usr/bin/env python3
"""Tiny HTTP/CONNECT proxy that forwards to an authenticated SOCKS5 upstream.

Why: Chromium (and therefore patchright/Playwright) cannot authenticate to a
SOCKS5 proxy. Most residential proxy providers only expose SOCKS5 with
user/password, so we run this bridge next to the browser and point everything
at ``http://127.0.0.1:<port>``.

Usage:
    python scripts/proxy_bridge.py --listen 127.0.0.1:8899 \
        --upstream socks5://user:pass@host:port

Only the standard library is used. DNS is resolved by the upstream proxy.
"""
import argparse
import socket
import socketserver
import struct
import sys
import threading
import urllib.parse


def log(*a):
    print("[proxy]", *a, flush=True)


def apply_map(host_map, host):
    """Rewrite host via a {name: ip} table (case-insensitive, exact match).

    Cloudflare's Turnstile attestation hosts (brunhild/hagen) publish AAAA
    records only, so clients without IPv6 cannot resolve them. Mapping the
    CONNECT target to the corresponding IPv4 anycast address fixes that.
    The TLS SNI/Host header is left untouched.
    """
    if not host_map:
        return host
    return host_map.get(host.lower(), host)


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("upstream closed")
        buf += chunk
    return buf


def socks5_connect(upstream, host, port, timeout=30):
    """Open a tunnel to host:port through socks5://user:pass@host:port."""
    s = socket.create_connection((upstream["host"], upstream["port"]), timeout)
    s.settimeout(timeout)
    if upstream.get("user"):
        s.sendall(b"\x05\x02\x00\x02")
    else:
        s.sendall(b"\x05\x01\x00")
    ver, method = recv_exact(s, 2)
    if ver != 5:
        raise ConnectionError(f"bad socks version {ver}")
    if method == 2:
        user = upstream["user"].encode()
        pw = (upstream.get("password") or "").encode()
        s.sendall(b"\x01" + bytes([len(user)]) + user + bytes([len(pw)]) + pw)
        _, status = recv_exact(s, 2)
        if status != 0:
            raise ConnectionError("socks5 auth failed")
    elif method != 0:
        raise ConnectionError(f"no acceptable auth method ({method})")

    addr = host.encode("idna") if not host.isascii() else host.encode()
    s.sendall(b"\x05\x01\x00\x03" + bytes([len(addr)]) + addr + struct.pack(">H", port))
    ver, rep, _rsv, atyp = recv_exact(s, 4)
    if rep != 0:
        raise ConnectionError(f"socks5 connect failed (rep={rep})")
    if atyp == 1:
        recv_exact(s, 4)
    elif atyp == 3:
        recv_exact(s, recv_exact(s, 1)[0])
    elif atyp == 4:
        recv_exact(s, 16)
    recv_exact(s, 2)
    s.settimeout(None)
    return s


def relay(a, b):
    """Copy bytes both ways until either side closes."""

    def pump(src, dst):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    t = threading.Thread(target=pump, args=(b, a), daemon=True)
    t.start()
    pump(a, b)
    t.join(timeout=5)


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        upstream = self.server.upstream
        client = self.request
        client.settimeout(60)
        try:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = client.recv(4096)
                if not chunk:
                    return
                head += chunk
                if len(head) > 65536:
                    return
            request_line = head.split(b"\r\n", 1)[0].decode("latin-1")
            parts = request_line.split()
            if len(parts) < 2:
                return
            method, target = parts[0], parts[1]

            if method.upper() == "CONNECT":
                host, _, port = target.rpartition(":")
                host = apply_map(self.server.host_map, host.strip("[]"))
                remote = socks5_connect(upstream, host, int(port))
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                # anything already buffered after the headers belongs to the tunnel
                rest = head.split(b"\r\n\r\n", 1)[1]
                if rest:
                    remote.sendall(rest)
                relay(client, remote)
                remote.close()
            else:
                url = urllib.parse.urlsplit(target)
                host = url.hostname
                port = url.port or (443 if url.scheme == "https" else 80)
                if not host:
                    host_header = [
                        l.split(":", 1)[1].strip()
                        for l in head.split(b"\r\n")[1:]
                        if l.lower().startswith(b"host:")
                    ]
                    host = host_header[0].split(":")[0] if host_header else ""
                    port = 80
                remote = socks5_connect(
                    upstream, apply_map(self.server.host_map, host), port)
                path = url.path or "/"
                if url.query:
                    path += "?" + url.query
                new_head = request_line.replace(target, path, 1).encode("latin-1")
                new_head = new_head.replace(b"Proxy-Connection:", b"Connection:")
                lines = head.split(b"\r\n\r\n", 1)[0].split(b"\r\n")
                rebuilt = [new_head] + [
                    l for l in lines[1:] if not l.lower().startswith(b"proxy-")
                ]
                remote.sendall(b"\r\n".join(rebuilt) + b"\r\n\r\n" +
                               head.split(b"\r\n\r\n", 1)[1])
                relay(client, remote)
                remote.close()
        except Exception as e:  # noqa: BLE001
            log(f"error: {type(e).__name__}: {e}")
            try:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            except OSError:
                pass


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="127.0.0.1:8899")
    ap.add_argument("--upstream", required=True,
                    help="socks5://user:pass@host:port")
    ap.add_argument("--map", default="",
                    help="comma list of host=ip overrides, e.g. "
                         "brunhild.challenges.cloudflare.com=104.18.17.146")
    args = ap.parse_args()

    u = urllib.parse.urlsplit(args.upstream)
    upstream = {
        "host": u.hostname,
        "port": u.port or 1080,
        "user": urllib.parse.unquote(u.username or ""),
        "password": urllib.parse.unquote(u.password or ""),
    }
    host, _, port = args.listen.rpartition(":")
    server = Server((host or "127.0.0.1", int(port)), Handler)
    server.upstream = upstream
    host_map = {}
    for pair in args.map.split(","):
        pair = pair.strip()
        if pair and "=" in pair:
            name, ip = pair.split("=", 1)
            host_map[name.strip().lower()] = ip.strip()
    server.host_map = host_map
    log(f"listening on {args.listen} -> socks5 {upstream['host']}:{upstream['port']}"
        + (f" (map={host_map})" if host_map else ""))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
