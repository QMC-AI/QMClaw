# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""QCA Service - Quantum Calibration Agent based on NVIDIA QCA Blueprint.

This service provides:
- Deep Agent with LangChain/LangGraph
- Experiment execution via QMClaw quantum_service
- Experiment history management (HDF5 + SQLite)
- Natural language experiment control
"""

import json
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

# =============================================================================
# CRITICAL: Setup paths BEFORE any other imports
# =============================================================================

# Get this service's directory
_SERVICE_DIR = Path(__file__).parent

# Add service directory to sys.path (ensures local imports work)
if str(_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICE_DIR))

# Add parent directory (qmclaw-server) to sys.path
_QMCLAW_SERVER_DIR = _SERVICE_DIR.parent.parent
if str(_QMCLAW_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_QMCLAW_SERVER_DIR))

# Add vendor QCA Blueprint to sys.path
# _SERVICE_DIR = .../Agentic Workflow/qmclaw-server/services/qca_service
# repo root = _SERVICE_DIR.parents[3] = D:\semester4\QMClaw
VENDOR_QCA_DIR = _SERVICE_DIR.parents[3] / "vendor" / "Quantum-Calibration-Agent-Blueprint"
if not VENDOR_QCA_DIR.exists():
    raise RuntimeError(
        f"vendor QCA not found: {VENDOR_QCA_DIR} — "
        r"copy deploy\vendor\Quantum-Calibration-Agent-Blueprint to QMClaw\vendor\ first"
    )
if str(VENDOR_QCA_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_QCA_DIR))

print(f"[QCA] === Service Starting ===")
print(f"[QCA] Python: {sys.executable}")
print(f"[QCA] CWD: {os.getcwd()}")
print(f"[QCA] Service dir: {_SERVICE_DIR}")
print(f"[QCA] QMClaw server dir: {_QMCLAW_SERVER_DIR}")
print(f"[QCA] Vendor dir: {VENDOR_QCA_DIR}")
print(f"[QCA] sys.path[0]: {sys.path[0]}")
print(f"[QCA] sys.path[1]: {sys.path[1] if len(sys.path) > 1 else 'N/A'}")

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Load environment from vendor directory
load_dotenv(VENDOR_QCA_DIR / ".env")

# Now import from vendor QCA Blueprint
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

# Try to import deepagents
try:
    from deepagents import create_deep_agent
    from deepagents.backends.local_shell import LocalShellBackend
    DEEP_AGENTS_AVAILABLE = True
    print("[QCA] Deep Agents framework loaded successfully")
except ImportError as e:
    print(f"[QCA] Warning: Deep Agents not available: {e}")
    print("[QCA] Falling back to simple chat mode")
    DEEP_AGENTS_AVAILABLE = False
    create_deep_agent = None
    LocalShellBackend = None

from core import storage, discovery
from core.workflow_validation import get_workflow_nodes
from prompt import load_system_prompt


# =============================================================================
# Directory Configuration
# =============================================================================

ROOT_DIR = Path(__file__).parent
VENDOR_DATA_DIR = VENDOR_QCA_DIR / "data"  # Use vendor's data directory
VENDOR_KNOWLEDGE_DIR = VENDOR_DATA_DIR / "knowledge"
VENDOR_SCRIPTS_DIR = VENDOR_QCA_DIR / "scripts"

# QMClaw specific directories
QMCLAW_SCRIPTS_DIR = ROOT_DIR / "scripts"  # Local scripts
QMCLAW_DATA_DIR = ROOT_DIR / "data"  # Local data

# Ensure directories exist
VENDOR_DATA_DIR.mkdir(parents=True, exist_ok=True)
VENDOR_KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
QMCLAW_DATA_DIR.mkdir(parents=True, exist_ok=True)


# =============================================================================
# LLM Configuration - Use MiniMax with direct HTTP calls
# =============================================================================

MINIMAX_API_KEY = os.environ.get("OPENAI_API_KEY", "") or os.environ.get("MINIMAX_API_KEY", "")
MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
MINIMAX_MODEL = "MiniMax-Text-01"

print(f"[QCA] API Key configured: {'Yes' if MINIMAX_API_KEY else 'No (empty)'}")
print(f"[QCA] API Key preview: {MINIMAX_API_KEY[:15]}..." if MINIMAX_API_KEY else "")


def _call_minimax(messages: list, temperature: float = 0.7) -> dict:
    """Direct HTTP call to MiniMax API."""
    import urllib.request
    import urllib.error

    payload = {
        "model": MINIMAX_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 2048,
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{MINIMAX_BASE_URL}/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {MINIMAX_API_KEY}",
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        print(f"[QCA] MiniMax API error: {e.code} - {error_body}")
        return {"error": f"API error {e.code}: {error_body}"}
    except Exception as e:
        print(f"[QCA] MiniMax request error: {e}")
        return {"error": str(e)}


def create_chat_model():
    """Create chat model - use MiniMax (legacy langchain approach)."""
    from langchain.chat_models import init_chat_model

    model_name = os.environ.get("QCA_MODEL", "minimax:MiniMax-Text-01")
    base_url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")

    print(f"[QCA] Using model: {model_name}")
    print(f"[QCA] Base URL: {base_url}")

    if model_name.startswith("minimax:"):
        model = model_name[8:]  # strip "minimax:" prefix
        return init_chat_model(
            model,
            model_provider="openai",
            base_url=base_url,
            api_key=MINIMAX_API_KEY,
        )
    else:
        return init_chat_model(model_name)


# =============================================================================
# Tool Loading - Use QMClaw-specific tools
# =============================================================================

def load_tools():
    """Load tools for the agent."""
    tools = []

    # Import our QMClaw-specific lab tool
    try:
        from tools.lab_tool import lab
        tools.append(lab)
        print("[QCA] Loaded lab tool")
    except ImportError as e:
        print(f"[QCA] Warning: Could not load lab tool: {e}")

    # Import run_experiment tool
    try:
        from tools.lab_tool import run_experiment
        tools.append(run_experiment)
        print("[QCA] Loaded run_experiment tool")
    except ImportError as e:
        print(f"[QCA] Warning: Could not load run_experiment tool: {e}")

    return tools


def create_agent():
    """Create the Deep Agent."""
    if not DEEP_AGENTS_AVAILABLE:
        print("[QCA] Deep Agents not available, skipping agent creation")
        return None, None, None

    backend = LocalShellBackend(
        root_dir=str(VENDOR_QCA_DIR),
        virtual_mode=True,
        inherit_env=True,
    )

    system_prompt = load_system_prompt()
    tools = load_tools()
    checkpointer = MemorySaver()

    chat_model = create_chat_model()

    agent = create_deep_agent(
        model=chat_model,
        backend=backend,
        tools=tools,
        system_prompt=system_prompt,
        checkpointer=checkpointer,
        interrupt_on={},
    )

    return agent, backend, checkpointer


# =============================================================================
# Simple Chat Handler (fallback when Deep Agent unavailable)
# =============================================================================

def _simple_chat(message: str) -> dict:
    """Simple chat handler using direct MiniMax API calls."""

    system_prompt = {
        "role": "system",
        "content": """你是一个量子校准智能体(QCA)。你需要通过工具来回答用户问题。

可用工具：
- run_experiment: 执行量子实验（参数：experiment_name, params）
- lab: 查询实验信息、历史和数据（参数：action, experiment_name, experiment_id, array_name等）

可用实验：
- t1_measurement: 测量T1弛豫时间
- ramsey_measurement: 测量Ramsey振荡
- spectroscopy: 进行光谱测量

当用户要求运行实验时，使用run_experiment工具。
当用户询问历史或参数时，使用lab工具。
请用中文回答。"""
    }

    user_message = {
        "role": "user",
        "content": message
    }

    response = _call_minimax([system_prompt, user_message], temperature=0.7)

    if "error" in response:
        return {"content": f"Error: {response['error']}", "type": "error"}

    try:
        content = response["choices"][0]["message"]["content"]
        return {"content": content, "type": "chat"}
    except (KeyError, IndexError) as e:
        return {"content": f"Unexpected response format: {response}", "type": "error"}


# =============================================================================
# Global Agent Instance
# =============================================================================

_agent = None
_backend = None
_checkpointer = None
_agent_initialized = False


def _init_agent_background():
    """Initialize agent in background thread."""
    global _agent, _backend, _checkpointer, _agent_initialized
    import time
    try:
        _agent, _backend, _checkpointer = create_agent()
        _agent_initialized = True
        print("[QCA] Deep Agent initialized successfully")
    except Exception as e:
        print(f"[QCA] Warning: Agent init error (will use fallback): {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize agent on startup (non-blocking)."""
    global _agent, _backend, _checkpointer, _agent_initialized
    print("[QCA] Service starting...")
    print(f"[QCA] Vendor directory: {VENDOR_QCA_DIR}")
    print(f"[QCA] Scripts directory: {VENDOR_SCRIPTS_DIR}")
    print(f"[QCA] Data directory: {VENDOR_DATA_DIR}")

    # Start agent initialization in a background thread
    import threading
    init_thread = threading.Thread(target=_init_agent_background, daemon=True)
    init_thread.start()
    print("[QCA] Agent initialization started in background thread...")

    yield

    print("[QCA] Service shutting down...")


app = FastAPI(title="QCA Service - Quantum Calibration Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)


# =============================================================================
# Request/Response Models
# =============================================================================

class ChatRequest(BaseModel):
    message: str
    thread_id: Optional[str] = None


class RunExperimentRequest(BaseModel):
    experiment_name: str
    params: Optional[dict] = None
    notes: str = ""


# =============================================================================
# Health Endpoints
# =============================================================================

@app.get("/health")
async def health():
    """Health check - always returns quickly."""
    return {
        "status": "healthy",
        "service": "qca_service",
        "vendor_dir": str(VENDOR_QCA_DIR),
        "scripts_dir": str(VENDOR_SCRIPTS_DIR),
        "data_dir": str(VENDOR_DATA_DIR),
        "deep_agents_available": DEEP_AGENTS_AVAILABLE,
        "agent_initialized": _agent_initialized,
        "api_key_configured": bool(MINIMAX_API_KEY),
    }


# =============================================================================
# Experiment Endpoints
# =============================================================================

@app.get("/capabilities")
async def get_capabilities():
    """List all available experiments."""
    experiments = discovery.discover_experiments(VENDOR_SCRIPTS_DIR)
    return {
        "experiments": [
            {
                "name": exp.name,
                "description": exp.description,
                "parameters": [
                    {
                        "name": p.name,
                        "type": p.type,
                        "default": p.default,
                        "range": list(p.range) if p.range else None,
                        "required": p.required,
                    }
                    for p in exp.parameters
                ],
                "module_path": exp.module_path,
            }
            for exp in experiments
        ]
    }


@app.get("/schema/{name}")
async def get_schema(name: str):
    """Get experiment schema."""
    schema = discovery.get_experiment_schema(name, VENDOR_SCRIPTS_DIR)
    if not schema:
        available = discovery.discover_experiments(VENDOR_SCRIPTS_DIR)
        raise HTTPException(
            status_code=404,
            detail={
                "error": f"Experiment '{name}' not found",
                "available_experiments": [e.name for e in available],
            },
        )

    return {
        "name": schema.name,
        "description": schema.description,
        "parameters": [
            {
                "name": p.name,
                "type": p.type,
                "required": p.required,
                "default": p.default,
                "range": list(p.range) if p.range else None,
            }
            for p in schema.parameters
        ],
        "module_path": schema.module_path,
    }


# =============================================================================
# History Endpoints
# =============================================================================

@app.get("/history")
async def get_history(last: Optional[int] = None, type: Optional[str] = None):
    """List experiment history."""
    experiments = storage.search_experiments(VENDOR_DATA_DIR, type=type, last=last)
    return {
        "count": len(experiments),
        "experiments": [
            {
                "id": e["id"],
                "type": e["type"],
                "target": e.get("target"),
                "timestamp": e["timestamp"],
                "status": e["status"],
            }
            for e in experiments
        ],
    }


@app.get("/history/{experiment_id}")
async def get_history_detail(experiment_id: str):
    """Get experiment details."""
    experiment = storage.load_experiment(experiment_id, VENDOR_DATA_DIR)
    if not experiment:
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_id}' not found")
    return experiment.to_dict()


@app.get("/history/{experiment_id}/arrays")
async def get_arrays(experiment_id: str):
    """List arrays in experiment."""
    arrays = storage.list_arrays(experiment_id, VENDOR_DATA_DIR)
    if arrays is None:
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_id}' not found")
    return {"arrays": arrays}


@app.get("/history/{experiment_id}/array/{array_name}")
async def get_array(
    experiment_id: str,
    array_name: str,
    start: Optional[int] = None,
    end: Optional[int] = None,
):
    """Get array data."""
    data = storage.get_array(experiment_id, array_name, VENDOR_DATA_DIR, start=start, end=end)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Array '{array_name}' not found")
    return {"array": array_name, "data": data, "length": len(data)}


# =============================================================================
# Workflow Endpoints
# =============================================================================

@app.get("/workflows")
async def list_workflows():
    """List saved workflows."""
    workflows_dir = VENDOR_DATA_DIR / "workflows"
    workflows_dir.mkdir(exist_ok=True)

    workflows = []
    for wf_dir in workflows_dir.iterdir():
        if wf_dir.is_dir():
            workflow_file = wf_dir / "workflow.json"
            if workflow_file.exists():
                try:
                    wf_data = json.loads(workflow_file.read_text())
                    workflows.append({
                        "id": wf_dir.name,
                        "name": wf_data.get("name", wf_dir.name),
                        "status": wf_data.get("status", "unknown"),
                    })
                except Exception:
                    pass

    return {"workflows": workflows}


# =============================================================================
# Chat Endpoint (Deep Agent powered)
# =============================================================================

@app.post("/chat")
async def chat(req: ChatRequest):
    """Deep Agent powered chat endpoint.

    Uses the vendor's Deep Agent with LangGraph for:
    - Natural language understanding
    - Tool execution (run_experiment, lab, etc.)
    - Multi-turn conversation with memory
    """
    message = req.message.strip()
    thread_id = req.thread_id or f"thread_{uuid.uuid4().hex[:12]}"

    print(f"[QCA Chat] thread={thread_id}, message={message[:100]}...")

    # Use Deep Agent if available, otherwise fallback to simple chat
    if _agent is not None and DEEP_AGENTS_AVAILABLE:
        try:
            # Run the agent
            config = {"configurable": {"thread_id": thread_id}}

            response = await _agent.ainvoke(
                {"messages": [HumanMessage(content=message)]},
                config=config,
            )

            # Extract response
            messages = response.get("messages", [])
            assistant_messages = [m for m in messages if isinstance(m, AIMessage)]

            if assistant_messages:
                last_msg = assistant_messages[-1]
                content = last_msg.content
                tool_calls = getattr(last_msg, "tool_calls", None)

                return {
                    "thread_id": thread_id,
                    "content": content,
                    "type": "agent",
                    "tool_calls": [
                        {"name": tc["name"], "args": tc["args"]}
                        for tc in (tool_calls or [])
                    ],
                }
            else:
                return {
                    "thread_id": thread_id,
                    "content": "No response from agent",
                    "type": "error",
                }

        except Exception as e:
            print(f"[QCA Chat] Error: {e}")
            import traceback
            traceback.print_exc()
            return {
                "thread_id": thread_id,
                "error": str(e),
                "type": "error",
            }
    else:
        # Use simple chat fallback
        print("[QCA Chat] Using simple chat fallback")
        try:
            result = _simple_chat(message)
            return {
                "thread_id": thread_id,
                "content": result.get("content", "No response"),
                "type": result.get("type", "chat"),
            }
        except Exception as e:
            print(f"[QCA Chat] Simple chat error: {e}")
            return {
                "thread_id": thread_id,
                "error": str(e),
                "type": "error",
            }


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 3011))
    host = os.environ.get("HOST", "0.0.0.0")

    print(f"[QCA] Starting QCA Service on {host}:{port}")
    print(f"[QCA] Vendor: {VENDOR_QCA_DIR}")
    uvicorn.run(app, host=host, port=port)
