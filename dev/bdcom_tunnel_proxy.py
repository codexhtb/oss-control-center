"""Unix socket to host-loopback SSH tunnel bridge for the isolated dev API."""

import os
import select
import signal
import socket
import socketserver
import stat
import threading
from pathlib import Path

SOCKET_PATH = Path("/run/bdcom/bdcom.sock")
TUNNEL_ADDRESS = ("127.0.0.1", 13306)


class TunnelHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        try:
            upstream = socket.create_connection(TUNNEL_ADDRESS, timeout=3)
        except OSError:
            return
        with upstream:
            upstream.settimeout(8)
            self.request.settimeout(8)
            while True:
                readable, _, _ = select.select([self.request, upstream], [], [], 10)
                for source in readable:
                    target = upstream if source is self.request else self.request
                    try:
                        data = source.recv(65536)
                        if not data:
                            return
                        target.sendall(data)
                    except OSError:
                        return


class TunnelServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    request_queue_size = 8
    allow_reuse_address = False


def main() -> None:
    os.umask(0o177)
    if SOCKET_PATH.is_symlink():
        raise RuntimeError("BDCOM socket path must not be a symlink")
    if SOCKET_PATH.exists():
        if not stat.S_ISSOCK(SOCKET_PATH.stat().st_mode):
            raise RuntimeError("BDCOM socket path is occupied")
        SOCKET_PATH.unlink()
    with TunnelServer(str(SOCKET_PATH), TunnelHandler) as server:
        SOCKET_PATH.chmod(0o600)
        signal.signal(
            signal.SIGTERM,
            lambda *_: threading.Thread(target=server.shutdown, daemon=True).start(),
        )
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            SOCKET_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
