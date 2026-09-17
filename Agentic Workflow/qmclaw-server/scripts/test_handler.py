import sys
import json
import os

# Set environment variables for API keys
os.environ["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY", "")

# Add paths
sys.path.insert(0, r"D:\QMClaw\Agentic Workflow\qmclaw-server\scripts")

# Test the handler directly
print("Importing job_runner...", file=sys.stderr, flush=True)

# Import job_runner module
import job_runner

print("job_runner imported, testing handle_flask_request...", file=sys.stderr, flush=True)

# Test quick_status (should be fast)
test_data = {"cid": "test123", "message": "test"}
result = job_runner.handle_flask_request("quick_status", test_data)
print(f"quick_status result: {result}", file=sys.stderr, flush=True)
