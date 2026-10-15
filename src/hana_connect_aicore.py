import os, json, socket, ssl, struct, threading, time, base64
import urllib.request, urllib.parse
from hdbcli import dbapi

# ── Environment Variables ─────────────────────────────────
CONN_SVC_URL    = os.environ.get("CONN_SVC_URL", "")
CONN_TOKEN_URL  = os.environ.get("CONN_TOKEN_URL", "")
CONN_CLIENT_ID  = os.environ.get("CONN_CLIENT_ID", "")
CONN_CLIENT_SEC = os.environ.get("CONN_CLIENT_SECRET", "")
CONN_SUBACCOUNT = os.environ.get("CONN_SUBACCOUNT_ID", "")
HANA_HOST       = os.environ.get("HANA_HOST", "s4h-hana-virtual")
HANA_PORT       = int(os.environ.get("HANA_PORT", "30041"))
HANA_USER       = os.environ.get("HANA_USER", "")
HANA_PASS       = os.environ.get("HANA_PASSWORD", "")
LOCAL_PROXY_PORT = 20004
PROXY_HOST       = CONN_SVC_URL.replace("https://","").replace("http://","").rstrip("/")

print("=" * 60)
print("  AI CORE → HTTP CONNECT → CLOUD CONNECTOR → HANA")
print("=" * 60)
print(f"\nConnectivity SVC : {CONN_SVC_URL}")
print(f"HANA Target      : {HANA_HOST}:{HANA_PORT}")
print(f"Subaccount ID    : {CONN_SUBACCOUNT}")

# ── Step 1: Reachability ──────────────────────────────────
print(f"\n[Check] {PROXY_HOST}:443 reachability...")
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(10)
result = s.connect_ex((PROXY_HOST, 443))
s.close()
print(f"  Result: {'REACHABLE ✅' if result == 0 else f'NOT REACHABLE ❌ (code: {result})'}")
if result != 0:
    raise SystemExit(1)

# ── Step 2: Get OAuth token ───────────────────────────────
print(f"\nStep 1: Getting connectivity token...")
creds = base64.b64encode(f"{CONN_CLIENT_ID}:{CONN_CLIENT_SEC}".encode()).decode()
data  = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
req   = urllib.request.Request(
    CONN_TOKEN_URL, data=data,
    headers={"Authorization": f"Basic {creds}",
             "Content-Type": "application/x-www-form-urlencoded"}
)
with urllib.request.urlopen(req, timeout=30) as resp:
    TOKEN = json.loads(resp.read())["access_token"]
print(f"  Token obtained ✅ (length: {len(TOKEN)})")

# ── Step 3: HTTP CONNECT tunnel function ──────────────────
def create_http_connect_tunnel(tgt_host, tgt_port, token):
    """Create HTTP CONNECT tunnel via SAP Connectivity Service"""
    print(f"\n  [Tunnel] HTTP CONNECT → {tgt_host}:{tgt_port}")

    ctx = ssl.create_default_context()
    raw = socket.create_connection((PROXY_HOST, 443), timeout=30)
    tun = ctx.wrap_socket(raw, server_hostname=PROXY_HOST)

    # HTTP CONNECT request with SAP auth headers
    req = (
        f"CONNECT {tgt_host}:{tgt_port} HTTP/1.1\r\n"
        f"Host: {tgt_host}:{tgt_port}\r\n"
        f"Proxy-Authorization: Bearer {token}\r\n"
        f"SAP-Connectivity-Authentication: Bearer {token}\r\n"
        f"SAP-Connectivity-SubAccount-ID: {CONN_SUBACCOUNT}\r\n"
        f"SAP-Connectivity-SCC-Location-ID: SCC_BDC\r\n"
        f"\r\n"
    )
    )
    tun.sendall(req.encode())

    # Read response
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = tun.recv(4096)
        if not chunk: break
        resp += chunk

    status_line = resp.decode(errors="replace").split("\r\n")[0]
    print(f"  [Tunnel] Server response: {status_line}")

    if "200" not in status_line:
        tun.close()
        raise Exception(f"HTTP CONNECT rejected: {status_line}")

    print(f"  [Tunnel] Tunnel established ✅")
    return tun

# ── Step 4: Local SOCKS5 proxy ────────────────────────────
def handle_socks5(client_sock, token):
    tunnel_sock = None
    try:
        # SOCKS5 auth negotiation
        client_sock.recv(262)
        client_sock.send(b"\x05\x00")

        # SOCKS5 CONNECT request
        data = client_sock.recv(262)
        atyp = data[3]
        if atyp == 0x03:
            ln       = data[4]
            tgt_host = data[5:5+ln].decode()
            tgt_port = struct.unpack("!H", data[5+ln:7+ln])[0]
        elif atyp == 0x01:
            tgt_host = socket.inet_ntoa(data[4:8])
            tgt_port = struct.unpack("!H", data[8:10])[0]
        else:
            client_sock.close(); return

        try:
            tunnel_sock = create_http_connect_tunnel(tgt_host, tgt_port, token)
            # SOCKS5 success reply
            client_sock.send(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        except Exception as e:
            print(f"  [Tunnel] Failed: {e}")
            client_sock.send(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")
            client_sock.close(); return

        # Bridge client ↔ tunnel
        stop = threading.Event()

        def c2t():
            try:
                while not stop.is_set():
                    d = client_sock.recv(4096)
                    if not d: break
                    tunnel_sock.sendall(d)
            except: pass
            finally: stop.set()

        def t2c():
            try:
                while not stop.is_set():
                    d = tunnel_sock.recv(4096)
                    if not d: break
                    client_sock.sendall(d)
            except: pass
            finally: stop.set()

        t1 = threading.Thread(target=c2t, daemon=True)
        t2 = threading.Thread(target=t2c, daemon=True)
        t1.start(); t2.start()
        t1.join(); t2.join()

    except Exception as e:
        print(f"  [SOCKS5] Error: {e}")
    finally:
        try: client_sock.close()
        except: pass
        if tunnel_sock:
            try: tunnel_sock.close()
            except: pass

def run_local_proxy():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", LOCAL_PROXY_PORT))
    srv.listen(5)
    srv.settimeout(60)
    print(f"\nStep 2: Local SOCKS5 proxy on 127.0.0.1:{LOCAL_PROXY_PORT} ✅")
    try:
        conn, _ = srv.accept()
        handle_socks5(conn, TOKEN)
    except socket.timeout:
        print("  [Proxy] Timeout — no connection received")
    finally:
        srv.close()

proxy_thread = threading.Thread(target=run_local_proxy, daemon=True)
proxy_thread.start()
time.sleep(1)

# ── Step 5: HANA connection ───────────────────────────────
print(f"\nStep 3: Connecting HANA via proxy → HTTP CONNECT tunnel...")
try:
    conn = dbapi.connect(
        address       = HANA_HOST,
        port          = HANA_PORT,
        user          = HANA_USER,
        password      = HANA_PASS,
        proxyHostname = "127.0.0.1",
        proxyPort     = LOCAL_PROXY_PORT,
        proxyUsername = TOKEN,
        proxyPassword = ""
    )
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM SYS.DUMMY")
    row = cursor.fetchone()
    print(f"\n  HANA response: {row} ✅")
    print("\n" + "=" * 60)
    print("  SUCCESS ✅")
    print("  AI Core → HTTP CONNECT → Cloud Connector → RISE HANA")
    print("=" * 60)
    conn.close()
except Exception as e:
    print(f"  HANA FAILED ❌: {e}")
    raise

proxy_thread.join(timeout=5)
