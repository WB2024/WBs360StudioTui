"""Quick smoke-test: start the web server thread and verify port 8360 opens."""
import asyncio
import socket
import sys
import threading
import time
from pathlib import Path


def _web_server_thread(host: str, port: int) -> None:
    from textual_serve.server import Server

    cmd = f"{sys.executable} {Path('main.py').resolve()}"
    server = Server(cmd, host=host, port=port, title="x360tm")
    try:
        asyncio.run(server.serve())
    except Exception as e:
        print(f"Server error: {e}", flush=True)


t = threading.Thread(target=_web_server_thread, args=("0.0.0.0", 8360), daemon=True)
t.start()

print("Waiting 4s for server to bind...", flush=True)
time.sleep(4)

s = socket.socket()
s.settimeout(2)
try:
    s.connect(("127.0.0.1", 8360))
    print("SUCCESS — port 8360 is open, web server running!", flush=True)
    s.close()
except Exception as e:
    print(f"FAIL — port check: {e}", flush=True)
