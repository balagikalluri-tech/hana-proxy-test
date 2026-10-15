import os, json, socket, ssl, struct, threading, time, base64
import urllib.request, urllib.parse
from hdbcli import dbapi

# ── Environment Variables ─────────────────────────────────
# Grab the SOCKS proxy details directly out of your service key payload variables
BTP_SOCKS_HOST  = os.environ.get("CONN_PROXY_HOST", "://ondemand.com")
BTP_SOCKS_PORT  = int(os.environ.get("CONN_PROXY_PORT", "20004"))

CONN_TOKEN_URL  = os.environ.get("CONN_TOKEN_URL", "")
CONN_CLIENT_ID  = os.environ.get("CONN_CLIENT_ID", "")
CONN_CLIENT_SEC = os.environ.get("CONN_CLIENT_SECRET", "")
CONN_SUBACCOUNT = os.environ.get("CONN_SUBACCOUNT_ID", "")
HANA_HOST       = os.environ.get("HANA_HOST", "s4h-hana-virtual")
HANA_PORT       = int(os.environ.get("HANA_PORT", "30041"))
HANA_USER       = os.environ.get("HANA_USER", "")
HANA_PASS       = os.environ.get("HANA_PASSWORD", "")
LOCAL_PROXY_PORT = 20004

print("=" * 60)
print("  AI CORE → RAW SOCKS5 INTERCEPT → RISE HANA")
print("=" * 60)

# ── Step 1: Obtain Token ──────────────────────────────────
print("\nStep 1: Getting connectivity token...")
creds = base64.b64encode(f"{CONN_CLIENT_ID}:{CONN_CLIENT_SEC}".encode()).decode()
data  = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
req   = urllib.request.Request(
    CONN_TOKEN_URL, data=data,
    headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"}
)
with urllib.request.urlopen(req, timeout=30) as resp:
    TOKEN = json.loads(resp.read())["access_token"]
print(f"  Token obtained ✅ (length: {len(TOKEN)})")

# ── Step 2: Establish Direct BTP SOCKS Connection ──────────
def handle_socks5_direct(client_sock, token):
    tunnel_sock = None
    try:
        # Standard SOCKS5 greeting negotiation
        client_sock.recv(262)
        client_sock.send(b"\x05\x00") # No auth required locally

        # Read target information from hdbcli application packet
        data = client_sock.recv(262)
        
        # Connect safely to BTP proxy service via TLS wrapper
        ctx = ssl.create_default_context()
        raw_sock = socket.create_connection((BTP_SOCKS_HOST, BTP_SOCKS_PORT), timeout=30)
        tunnel_sock = ctx.wrap_socket(raw_sock, server_hostname=BTP_SOCKS_HOST)

        # Build proprietary SAP Proxy Header
        # This formats credentials correctly for "Other" landscape tokens
        sap_header = f"Proxy-Authorization: Bearer {token}\r\n"
        sap_header += f"SAP-Connectivity-SCC-Location_ID: SCC_BDC\r\n\r\n"
        
        # Initial SOCKS5 Handshake down to BTP Gateway
        tunnel_sock.sendall(b"\x05\x01\x00") # SOCKSv5, Connect, Reserved
        
        # Send target mapping data down to the proxy tunnel
        # Type 3 (Domain Name evaluation)
        target_payload = b"\x03" + struct.pack("B", len(HANA_HOST)) + HANA_HOST.encode() + struct.pack("!H", HANA_PORT)
        tunnel_sock.sendall(target_payload)
        
        # Inject the text authorization header immediately after target details
        tunnel_sock.sendall(sap_header.encode())

        # Read the SOCKS5 proxy verification response frame
        proxy_resp = tunnel_sock.recv(10)
        if not proxy_resp or proxy_resp[1] != 0x00:
            print(f"  [Tunnel] Connection rejected by BTP Proxy Status: {proxy_resp}")
            client_sock.send(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")
            client_sock.close()
            return

        # Tell hdbcli local wrapper connection setup is successful
        client_sock.send(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        print("  [Tunnel] SOCKS5 secure database pipe active ✅")

        # Two-way network bridging
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

        threading.Thread(target=c2t, daemon=True).start()
        threading.Thread(target=t2c, daemon=True).start()
        
        while not stop.is_set():
            time.sleep(0.5)

    except Exception as e:
        print(f"  [SOCKS5 Bridge Error]: {e}")
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
    print(f"Step 2: Monitoring Local Port 127.0.0.1:{LOCAL_PROXY_PORT} ✅")
    try:
        conn, _ = srv.accept()
        handle_socks5_direct(conn, TOKEN)
    except Exception as e:
        print(f"Proxy runtime exception: {e}")
    finally:
        srv.close()

proxy_thread = threading.Thread(target=run_local_proxy, daemon=True)
proxy_thread.start()
time.sleep(1)

# ── Step 3: Run Database Command ──────────────────────────
print("\nStep 3: Connecting via hdbcli wrapper...")
try:
    conn = dbapi.connect(
        address       = HANA_HOST,
        port          = HANA_PORT,
        user          = HANA_USER,
        password      = HANA_PASS,
        proxyHostname = "127.0.0.1",
        proxyPort     = LOCAL_PROXY_PORT
    )
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM SYS.DUMMY")
    row = cursor.fetchone()
    print(f"\n  HANA Response verified: {row} ✅")
    conn.close()
except Exception as e:
    print(f"  HANA Operation FAILED ❌: {e}")
    raise
