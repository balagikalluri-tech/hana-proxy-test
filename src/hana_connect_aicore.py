import os, json
import urllib.request

CF_BRIDGE_URL = os.environ.get("CF_BRIDGE_URL", "")
BRIDGE_API_KEY = os.environ.get("BRIDGE_API_KEY", "")

print("=" * 60)
print("  AI CORE → CF BRIDGE → CLOUD CONNECTOR → RISE HANA")
print("=" * 60)
print(f"\nBridge URL: {CF_BRIDGE_URL}")

# Step 1 — Health check
print("\n[Check] Bridge reachability...")
req = urllib.request.Request(f"{CF_BRIDGE_URL}/health")
with urllib.request.urlopen(req, timeout=15) as resp:
    health = json.loads(resp.read())
print(f"  Bridge: {health['status']} ✅")

# Step 2 — HANA connection test
print("\nStep 1: Testing HANA connection via CF Bridge...")
req = urllib.request.Request(
    f"{CF_BRIDGE_URL}/hana/test",
    headers={"X-API-Key": BRIDGE_API_KEY}
)
with urllib.request.urlopen(req, timeout=30) as resp:
    result = json.loads(resp.read())

print(f"  Status   : {result['status']} ✅")
print(f"  HANA Time: {result['hana_time']}")
print(f"  Database : {result['database']}")
print(f"  Host     : {result['host']}")

# Step 3 — Test query
print("\nStep 2: Running test SQL query...")
body = json.dumps({"sql": "SELECT TOP 5 TABLE_NAME FROM TABLES WHERE SCHEMA_NAME = 'SYS'"}).encode()
req = urllib.request.Request(
    f"{CF_BRIDGE_URL}/hana/query",
    data=body,
    headers={"X-API-Key": BRIDGE_API_KEY,
             "Content-Type": "application/json"}
)
with urllib.request.urlopen(req, timeout=30) as resp:
    qresult = json.loads(resp.read())

print(f"  Rows returned: {qresult['count']}")
for row in qresult['rows']:
    print(f"    {row}")

print("\n" + "=" * 60)
print("  SUCCESS ✅")
print("  AI Core → CF Bridge → Cloud Connector → RISE HANA")
print("=" * 60)
