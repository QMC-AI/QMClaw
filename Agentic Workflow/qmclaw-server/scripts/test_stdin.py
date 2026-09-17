"""
Test script to send a Flask request directly to Python subprocess stdin.
"""
import json
import sys

# Send a simple flask request
msg = {
    "type": "flask",
    "cid": "test123",
    "action": "quick_status",
    "data": {"cid": "test123"}
}

output = json.dumps(msg) + "\n"
sys.stdout.write(output)
sys.stdout.flush()

print(f"Sent: {output.strip()}", file=sys.stderr, flush=True)

# Read response
line = sys.stdin.readline()
print(f"Received: {line.strip()}", file=sys.stderr, flush=True)
sys.stdout.write(line)
sys.stdout.flush()
