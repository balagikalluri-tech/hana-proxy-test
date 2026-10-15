import os, json, socket, ssl, struct, threading, time, base64
import urllib.request, urllib.parse
import websocket
from hdbcli import dbapi

# ── Environment Variables ─────────────────────────────────
CONN_SVC_URL     = os.environ.get("CONN_SVC_URL", "")
CONN_TUNNEL_PATH = os.environ.get("CONN_TUNNEL_PATH", "/api/v1/tunnel")
CONN_TOKEN_URL   = os.environ.get("CONN_TOKEN_URL", "")
CONN_CLIENT_ID   = os.environ.get("CONN_CLIENT_ID", "")
CONN_CLIENT_SEC  = os.environ.get("CONN_CLIENT_SECRET", "")
CONN_SUBACCOUNT  = os.environ.get("CONN_SUBACCOUNT_ID", "")
CONN_SUBDOMAIN   = os.environ.get("CONN_SUBDOMAIN", "")
HANA_HOST        = os.environ.get("HANA_HOST", "s4h-hana-virtual")
HANA_PORT        = int(os.environ.get("HANA_PORT", "30041"))
HANA_USER        = os.environ.get("HANA_USER", "")
HANA_PASS        = os.environ.get("HANA_PASSWORD", "")
LOCAL_PROXY_PORT = 20004
PROXY_HOST       = CONN_SVC_URL.replace("https://", "").replace("http://", "").rstrip("/")

print("=" * 60)
print("  AI CORE → CONNECTIVITY TUNNEL → CLOUD CONNECTOR → HANA")
print("=" * 60)
print(f"\nConnectivity SVC : {CONN_SVC_URL}")
print(f"HANA Target      : {HANA_HOST}:{HANA_PORT}")
print(f"Subaccount ID    : {CONN_SUBACCOUNT}")

# ── Step 1: Reachability check ────────────────────────────
print(f"\n[Check] {PROXY_HOST}:443 reachability...")
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(10)
    result = s.connect_ex((PROXY_HOST, 443))
    s.close()
    print(f"  Result: {'REACHABLE ✅' if result == 0 else f'NOT REACHABLE ❌ (code: {result})'}")
    if result != 0:
        raise SystemExit(1)
except Exception as e:
    print(f"  Check failed: {e}")
    raise SystemExit(1)

# ── Step 2: Get OAuth token ───────────────────────────────
print(f"\nStep 1: Getting connectivity service token...")
try:
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
except Exception as e:
    print(f"  Token FAILED ❌: {e}")
    raise

# ── Step 3: Local SOCKS5 proxy → WebSocket tunnel ─────────
def handle_socks5(client_sock, token):
    try:
        # SOCKS5 handshake — accept any auth method
        client_sock.recv(262)
        client_sock.send(b"\x05\x00")  # no auth

        # Read SOCKS5 CONNECT request
        data = client_sock.recv(262)
        atyp = data[3]
        if atyp == 0x03:  # domain name
            ln       = data[4]
            tgt_host = data[5:5+ln].decode()
            tgt_port = struct.unpack("!H", data[5+ln:7+ln])[0]
        elif atyp == 0x01:  # IPv4
            tgt_host = socket.inet_ntoa(data[4:8])
            tgt_port = struct.unpack("!H", data[8:10])[0]
        else:
            client_sock.close(); return

        print(f"\n  [Tunnel] Connecting {tgt_host}:{tgt_port} via connectivity service...")

        # Create WebSocket tunnel via SAP Connectivity Service
        tunnel_url = f"wss://{PROXY_HOST}{CONN_TUNNEL_PATH}"
        ws = websocket.create_connection(
            tunnel_url,
            header={
                "Authorization":                   f"Bearer {token}",
                "SAP-Connectivity-Authentication": f"Bearer {token}",
                "SAP-Connectivity-SubAccount-ID":  CONN_SUBACCOUNT,
                "SAP-Connectivity-Target-Host":    f"{tgt_host}:{tgt_port}",
            },
            timeout=30,
            sslopt={"cert_reqs": ssl.CERT_REQUIRED}
        )

        # Send SOCKS5 success response
        client_sock.send(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        print(f"  [Tunnel] WebSocket tunnel established ✅")

        # Bridge: client ↔ WebSocket tunnel
        stop_event = threading.Event()

        def client_to_tunnel():
            try:
                while not stop_event.is_set():
                    d = client_sock.recv(4096)
                    if not d: break
                    ws.send_binary(d)
            except: pass
            finally: stop_event.set()

        def tunnel_to_client():
            try:
                while not stop_event.is_set():
                    d = ws.recv()
                    if d is None: break
                    if isinstance(d, str): d = d.encode()
                    client_sock.sendall(d)
            except: pass
            finally: stop_event.set()

        t1 = threading.Thread(target=client_to_tunnel, daemon=True)
        t2 = threading.Thread(target=tunnel_to_client, daemon=True)
        t1.start(); t2.start()
        t1.join(); t2.join()

    except Exception as e:
        print(f"  [Tunnel] Error: {e}")
        try: client_sock.send(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")
        except: pass
    finally:
        try: client_sock.close()
        except: pass

def run_local_proxy():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", LOCAL_PROXY_PORT))
    srv.listen(5)
    srv.settimeout(60)
    print(f"\nStep 2: Local SOCKS5 proxy listening on 127.0.0.1:{LOCAL_PROXY_PORT} ✅")
    try:
        conn, _ = srv.accept()
        handle_socks5(conn, TOKEN)
    except socket.timeout:
        print("  [Proxy] Timeout waiting for connection")
    finally:
        srv.close()

proxy_thread = threading.Thread(target=run_local_proxy, daemon=True)
proxy_thread.start()
time.sleep(1)

# ── Step 4: HANA connection via local proxy ───────────────
print(f"\nStep 3: Connecting to HANA via local proxy → tunnel...")
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
    print(f"  HANA response: {row} ✅")
    print("\n" + "=" * 60)
    print("  SUCCESS ✅")
    print("  AI Core → WebSocket → Cloud Connector → RISE HANA")
    print("=" * 60)
    conn.close()
except Exception as e:
    print(f"  HANA connection FAILED ❌: {e}")
    raise

proxy_thread.join(timeout=5)
