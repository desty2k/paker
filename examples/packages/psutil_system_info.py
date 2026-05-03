"""Load psutil from an encrypted paker bundle and query system info.

Requires psutil installed for the dump step: pip install psutil
"""

import json
import os
import sys
import paker

KEY = os.urandom(32)

print("Dumping psutil (encrypted)...")
bundle = paker.dumps("psutil", key=KEY)
blob = json.dumps(bundle)
print(f"Bundle: {len(blob):,} bytes\n")

for name in list(sys.modules):
    if name.startswith("psutil"):
        del sys.modules[name]

with paker.loads(blob, key=KEY) as imp:
    import psutil

    print(f"CPU cores: {psutil.cpu_count()} logical, {psutil.cpu_count(logical=False)} physical")
    print(f"CPU usage: {psutil.cpu_percent(interval=0.5)}%")

    mem = psutil.virtual_memory()
    print(f"Memory: {mem.total / (1024**3):.1f} GB total, {mem.percent}% used")

    disk = psutil.disk_usage("/")
    print(f"Disk (/): {disk.total / (1024**3):.0f} GB total, {disk.percent}% used")

    net = psutil.net_io_counters()
    print(f"Network: {net.bytes_sent / (1024**2):.0f} MB sent, {net.bytes_recv / (1024**2):.0f} MB received")

    print(f"Processes: {len(psutil.pids())} running")
    print(f"Boot time: {__import__('datetime').datetime.fromtimestamp(psutil.boot_time())}")
