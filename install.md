# Installation & Usage Guide

---

## 1. Environment Requirements

| Requirement | Version | Note |
|-------------|---------|------|
| Python | 3.10+ | Both Client and Server |
| Git | any | For cloning the repository |
| Docker & Docker Compose | — | Optional, for self-hosted MCP server |
| An AI Agent | Claude Code / Codex / other | For skill-based interaction |

**OS**: Windows / macOS / Linux

---

## 2. Installation

### 2.1 Clone the Repository

```bash
git clone https://github.com/YOUR_USERNAME/qmclaw-skills.git
cd qmclaw-skills
```

### 2.2 Option A — Install as Skills (No Python packaging needed)

QMClaw Skills are reusable instruction bundles centred on `SKILL.md`.
Copy the whole folder, not only `SKILL.md`, because many skills depend on `_shared/`, scripts, or README context.

#### Claude Code

**Manual Local-Skill Installation:**

```bash
mkdir -p ~/.claude/skills
cp -R skills/_shared ~/.claude/skills/
cp -R skills/qmclaw-* ~/.claude/skills/
cp -R skills/quantum-* ~/.claude/skills/
```

**Update after pulling new changes:**

```bash
git pull
cp -R skills/_shared ~/.claude/skills/
cp -R skills/qmclaw-* ~/.claude/skills/
cp -R skills/quantum-* ~/.claude/skills/
```

Restart Claude Code so newly added skills are picked up.

#### Codex

**Manual Local-Skill Installation:**

```bash
mkdir -p ~/.codex/skills
cp -R skills/_shared ~/.codex/skills/
for d in skills/qmclaw-* skills/quantum-*; do
  cp -R "$d" ~/.codex/skills/
done
```

Restart Codex after installation.

#### Other Agents or Manual Use

Copy the whole `skills/` directory into your prompt library or project.
Preserve `SKILL.md`, `README.md`, `static/`, scripts, assets, and `skills/_shared/` together.

### 2.3 Option B — Use as a Python Package (For programmatic access)

```bash
# Core dependencies only (numpy)
pip install -e .

# With data analysis support (scipy)
pip install -e ".[analysis]"

# With plotting support (matplotlib, plotly, opencv)
pip install -e ".[plot]"

# With MCP protocol support (swiftmcp)
pip install -e ".[mcp]"

# All features
pip install -e ".[full]"
```

---

## 3. Configuration

### 3.1 MCP Configuration (`.mcp.json`)

If you want to use the MCP protocol-based experiment tools, create or edit `.mcp.json`:

```json
{
    "mcpServers": {
        "quantum-service": {
            "command": "npx",
            "args": [
                "-y",
                "@modelcontextprotocol/server-http",
                "http://127.0.0.1:8008/mcp"
            ]
        }
    }
}
```

Adjust `http://127.0.0.1:8008/mcp` to point to your MCP server.

### 3.2 Quantum Control System Connection

If your skills need to connect to a quantum control system:

```python
import sys
# Add your workflow directory path here
WORKFLOW_DIR = r"/path/to/your/sq_workflow"  # Linux/Mac
# WORKFLOW_DIR = r"D:\path\to\sq_workflow"  # Windows
sys.path.insert(0, WORKFLOW_DIR)

import labrad
from lqms.pyle.workflow import switchSession
import sq

# Connect
cxn = labrad.connect(host='localhost', port=7682)
s = switchSession(cxn, user='YOUR_USERNAME')
qobj = s.YOUR_QUBIT_NAME  # e.g., 'q11ld4'
```

> **Note:** The quantum control system setup varies by lab. Consult your system administrator for the correct connection parameters and workflow directory path.

---

## 4. Usage

### 4.1 In AI Agents (Natural Language)

After installation, invoke skills naturally:

**Claude Code:**
```
执行单比特校准工作流。
如何进行 T1 拟合和 XEB 保真度计算？
QMClaw 的决策架构是什么？
```

**Codex:**
```
Introduce the QMClaw framework architecture
Execute the single-qubit calibration workflow
Calculate T1 fitting and XEB fidelity
```

### 4.2 In Python (Programmatic Access)

```python
import sys
sys.path.insert(0, "path/to/qmclaw-skills")

from new_ctrl.task import call_interface, get_data
from new_templates import S21_template, rabi_template, t1_template

# ① Execute S21 experiment
tid = call_interface(
    workflow=S21_template,
    qubits=["Q0"],
    frequency_start=-40e6,
    frequency_end=40e6,
    frequency_sample_num=101,
)

# ② Retrieve experiment data
data = get_data(tid)
print("S21 data:", data["data"]["s21"])

# ③ Execute Rabi experiment
tid_rabi = call_interface(
    workflow=rabi_template,
    qubits=["Q0"],
    drive_amp=[0.01, 0.05, 0.1],
)
data_rabi = get_data(tid_rabi)
print("Rabi data:", data_rabi["data"]["population"])

# ④ Execute T1 experiment
tid_t1 = call_interface(
    workflow=t1_template,
    qubits=["Q0"],
    delay=[0, 1e-6, 5e-6, 10e-6, 20e-6],
)
data_t1 = get_data(tid_t1)
print("T1 data:", data_t1["data"]["population"])
```

### 4.3 Using the MCP Server Directly (Without AI Agent)

```python
from new_ctrl.task import call_interface, get_data, query_param, update_param
from new_templates import S21_template

# Execute experiment
tid = call_interface(
    workflow=S21_template,
    qubits=["Q0"],
    frequency_start=-40e6,
    frequency_end=40e6,
)

# Get results
data = get_data(tid)

# Query and update parameters
update_param("Q0.frequency", 6.5e9)
value = query_param("Q0.frequency")
print(f"Frequency: {value}")
```

---

## 5. Skill Index

| Skill | Purpose | Trigger Keywords |
|-------|---------|------------------|
| `qmclaw-framework-overview` | Framework architecture, six-layer stack | "QMClaw", "框架介绍", "架构" |
| `qmclaw-decision-architecture` | L1/L2/L3 decision layers, RuleEngine | "决策", "RuleEngine", "规则" |
| `qmclaw-tuneup-workflow` | 15-step calibration, state machine, Panel batch | "校准", "调校", "calibration", "tune-up" |
| `qmclaw-tool-scheduling` | Tool whitelist, MCP, Center Agent, Backend Adapter | "工具调度", "后端", "tool scheduling" |
| `qmclaw-data-analysis` | T1/Rabi fitting, XEB/RB fidelity, quality metrics | "数据分析", "保真度", "T1", "quality" |
| `quantum-calibration` | Complete single-qubit calibration and labeling | "校准", "标定", "单比特", "qubit" |
| `quantum-experiment-plotter` | 1D/2D plotting, academic figures, data fitting | "绘图", "可视化", "plot", "figure" |

---

## 6. Verify Installation

### 6.1 Verify Skills

After installation, restart your AI agent and verify the skills are loaded:

```
QMClaw 的框架架构是什么？
```

If the skill responds correctly, the installation is successful.

### 6.2 Verify Python Package

```bash
# Run the smoke test suite
python tests/test_smoke.py
```

Expected output:
```
  RUN  test_import_new_ctrl
  ✅ PASS  test_import_new_ctrl
  RUN  test_import_new_ctrl_tools
  ✅ PASS  test_import_new_ctrl_tools
  ...
  ==================================================
    结果: 7 passed, 0 failed, 7 total
  ==================================================
```

### 6.3 Verify MCP Server (Optional)

If you have started the MCP server (via `swiftmcp` or docker-compose), verify it is reachable:

```bash
curl -s http://127.0.0.1:8008/mcp | head -c 100
```

---

## 7. Troubleshooting

### Skills not appearing in the agent

1. Check that `SKILL.md` is directly inside the skill folder, not in a subdirectory.
2. Ensure `skills/_shared/` is copied alongside the skill folders.
3. Restart the agent session after installation.
4. Verify the folder names match exactly (no extra prefixes).

### `ModuleNotFoundError: No module named 'new_ctrl'`

The Python package is not installed. Choose one:

```bash
# Option 1: Install as editable package
pip install -e .

# Option 2: Add the project directory to PYTHONPATH
export PYTHONPATH="$(pwd):$PYTHONPATH"
```

### `ModuleNotFoundError: No module named 'swiftmcp'`

The MCP protocol support package is not installed. Choose one:

```bash
# Option 1: Install the [mcp] extra
pip install -e ".[mcp]"

# Option 2: Install swiftmcp directly
pip install swiftmcp

# Option 3: Use the official MCP Python SDK instead
pip install mcp>=1.0
```

### `ModuleNotFoundError: No module named 'matplotlib'`

The plotting support package is not installed. Choose one:

```bash
# Option 1: Install the [plot] extra
pip install -e ".[plot]"

# Option 2: Install matplotlib directly
pip install matplotlib
```

### Chinese text display issues

If Chinese characters appear as boxes, ensure your terminal and editor support UTF-8 encoding:

```bash
# Set UTF-8 encoding
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8
```

### MCP server connection issues

If the MCP server is not reachable:

```bash
# Check if the server is running
curl -s http://127.0.0.1:8008/mcp

# If not running, start it
python mcp_tools_new.py  # or docker-compose up -d
```

---

## 8. Updating

To update QMClaw Skills to the latest version:

```bash
cd qmclaw-skills
git pull

# Re-install skills
cp -R skills/_shared ~/.claude/skills/
cp -R skills/qmclaw-* ~/.claude/skills/
cp -R skills/quantum-* ~/.claude/skills/

# Re-install Python package (if used)
pip install -e ".[full]"
```

Restart your AI agent after updating.

---

## 9. Uninstalling

### Remove Skills

```bash
# Remove all QMClaw skills from Claude Code
rm -rf ~/.claude/skills/qmclaw-*
rm -rf ~/.claude/skills/quantum-*
# Optionally remove shared content
rm -rf ~/.claude/skills/_shared
```

### Remove Python Package

```bash
pip uninstall qmclaw
```

---

## 10. Support

- **GitHub Issues**: [https://github.com/YOUR_USERNAME/qmclaw-skills/issues](https://github.com/YOUR_USERNAME/qmclaw-skills/issues)
- **Documentation**: See `skills/*/README.md` for individual skill documentation
- **Contributing**: See `CONTRIBUTING.md` for guidelines on adding new skills

---

> **Tip**: QMClaw Skills include a Mock implementation in `new_ctrl/task.py`, which allows you to test and demo the full workflow without actual quantum hardware.
