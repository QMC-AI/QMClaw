"""Smoke-test the imports QMClaw needs, against whichever interpreter runs this.

Usage (must be run by the target env's python):
    <env>\\python.exe -B check_env_imports.py

Prints PASS / FAIL / MISSING per module group so you can see exactly what a
given environment still lacks before starting the services.
"""
import importlib
import sys

GROUPS = {
    "科学计算 / 数据分析": [
        "numpy", "scipy", "matplotlib", "pandas", "h5py", "sympy",
        "plotly", "sklearn", "seaborn", "PIL", "cv2", "tqdm",
        "networkx", "dill", "psutil", "colorama", "configparser",
        "func_timeout", "adjustText", "protobuf", "cssselect", "future",
        "quaternion", "tenpy",
    ],
    "LabRAD 测控栈": [
        "labrad", "twisted", "OpenSSL", "service_identity",
        "win32api", "win32con", "win32gui", "win32ui", "pywintypes",
        "PyQt6", "qtpy",
        "pyvisa", "apscheduler", "stim", "qutip", "cvxpy",
    ],
    # Lab-specific wheels shipped in deploy/*.whl
    "LQCS / LQMS（deploy 轮子）": [
        "lqcs", "lqms", "envelopes",
    ],
    "Web / API 服务": [
        "fastapi", "uvicorn", "starlette", "pydantic", "httpx", "anyio",
        "socketio", "mcp", "sse_starlette", "dotenv", "yaml", "jinja2",
    ],
    "LLM / Agent 框架": [
        "openai", "anthropic", "langchain", "langchain_core", "langgraph",
        "langchain_openai", "langchain_anthropic", "langchain_mcp_adapters",
        "tiktoken", "tavily", "deepagents",
    ],
    "vendor 子模块（需先安装 deploy\\vendor）": {
        "hermes-agent": ["hermes_cli", "run_agent", "agent", "toolsets", "model_tools", "acp_adapter"],
        "QubitClient": ["qubitclient"],
        "Quantum-Calibration-Agent-Blueprint": ["cli", "server", "core", "tools", "vlm", "prompt"],
    },
    "分布式 / 深度学习": [
        "ray", "torch",
    ],
    "可选功能（缺了只影响对应服务）": [
        "redis",       # task_queue Redis 后端
        "docx",        # measure_scripts 的 docx 报告导出
        "onnxruntime", # image_service ONNX 后端
        "torchvision", # image_service Torch 后端
        "swiftmcp",    # 根 MCP 工具
    ],
}

# modules that legitimately cannot exist: lab-local code, not shipped by deploy
LOCAL_ONLY = {"sq", "pylab"}


def probe(name):
    try:
        mod = importlib.import_module(name)
    except ImportError as e:
        if "No module named" in str(e):
            return "MISSING", str(e)
        return "FAIL", f"{type(e).__name__}: {e}"
    except Exception as e:
        return "FAIL", f"{type(e).__name__}: {e}"
    ver = getattr(mod, "__version__", None)
    return "PASS", str(ver) if ver else ""


totals = {"PASS": 0, "FAIL": 0, "MISSING": 0}
for group, mods in GROUPS.items():
    print(f"\n{'=' * 70}\n{group}\n{'=' * 70}")
    if isinstance(mods, dict):
        for sub, submods in mods.items():
            print(f"  -- {sub} --")
            for m in submods:
                st, info = probe(m)
                totals[st] += 1
                print(f"    {st:8} {m:28} {info}")
    else:
        for m in mods:
            st, info = probe(m)
            totals[st] += 1
            print(f"  {st:8} {m:28} {info}")

print(f"\n{'=' * 70}")
print(f"汇总: PASS={totals['PASS']}  FAIL={totals['FAIL']}  MISSING={totals['MISSING']}")
print(f"{'=' * 70}")
