"""Using paker from the command line."""

# paker has a CLI for dumping, loading, and listing modules.
#
# Dump a package to JSON:
#   paker dump requests --indent 4
#   paker dump requests -O requests_bundle.json
#   paker dump requests -C  # compile to bytecode
#
# List modules in a JSON file:
#   paker list requests_bundle.json
#
# Recreate files from JSON:
#   paker load requests_bundle.json -O ./output/
#
# Show version:
#   paker -V

import subprocess
import sys

print("=== paker CLI examples ===\n")

print("$ paker -V")
result = subprocess.run(
    [sys.executable, "-m", "paker", "-V"],
    capture_output=True,
    text=True,
)
print(f"  {result.stdout.strip()}\n")

print("$ paker dump json --indent 2 -O /tmp/json_bundle.json")
result = subprocess.run(
    [sys.executable, "-m", "paker", "dump", "json", "--indent", "2", "-O", "/tmp/json_bundle.json"],
    capture_output=True,
    text=True,
)
print(f"  {'OK' if result.returncode == 0 else result.stderr.strip()}\n")

print("$ paker list /tmp/json_bundle.json")
result = subprocess.run(
    [sys.executable, "-m", "paker", "list", "/tmp/json_bundle.json"],
    capture_output=True,
    text=True,
)
for line in result.stdout.strip().split("\n"):
    print(f"  {line}")
