# QMClaw Skills

可扩展的通用量子比特测控技能框架。

A scalable general-purpose framework for quantum measurement and control, providing reusable instruction bundles centered on `SKILL.md`.

---

<p align="center">
  <img src="assets/qmclaw-logo.png" alt="QMClaw" width="400"/>
</p>

<p align="center">
  <a href="https://github.com/YOUR_USERNAME/qmclaw-skills/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License">
  </a>
  <a href="https://github.com/YOUR_USERNAME/qmclaw-skills/stargazers">
    <img src="https://img.shields.io/github/stars/YOUR_USERNAME/qmclaw-skills" alt="Stars">
  </a>
  <a href="https://github.com/YOUR_USERNAME/qmclaw-skills/releases">
    <img src="https://img.shields.io/github/v/release/YOUR_USERNAME/qmclaw-skills" alt="Version">
  </a>
</p>

---

## 🌟 Features

| Feature | Description |
|---------|-------------|
| **QMClaw Framework Overview** | 六层执行栈、三层决策架构、多智能体模型 |
| **Three-Layer Decision** | L1 RuleEngine / L2 Learned Rules / L3 LLM Fallback |
| **Single-Qubit Calibration** | 15 步标准校准流程：S21 → Spectroscopy → Rabi → T1 |
| **Tool Scheduling** | 工具白名单、MCP 协议、集中式调度与分布式执行 |
| **Data Analysis** | T1/Rabi 拟合、XEB/RB 保真度、报告生成 |
| **Quality Assessment** | SNR、Visibility、T1、保真度等质量指标体系 |
| **Experiment Plotter** | 学术级可视化，支持 Nature/IEEE/APS/Springer 样式 |

---

## 📦 Skill Index

| Skill | Version | Purpose | Trigger Keywords |
|-------|---------|---------|------------------|
| [QMClaw框架概述](./skills/qmclaw-framework-overview/README.md) | v1.0 | 框架架构、六层栈、三层决策、多智能体 | "QMClaw", "框架介绍", "架构" |
| [QMClaw三层决策架构](./skills/qmclaw-decision-architecture/README.md) | v1.0 | L1/L2/L3 决策层、RuleEngine、规则引擎 | "决策", "RuleEngine", "规则" |
| [QMClaw单比特调校工作流](./skills/qmclaw-tuneup-workflow/README.md) | v1.0 | 15 步校准流程、状态机、Panel 批量校准 | "校准", "调校", "calibration", "tune-up" |
| [QMClaw工具调度与后端接口](./skills/qmclaw-tool-scheduling/README.md) | v1.0 | 工具白名单、MCP、Center Agent、Backend Adapter | "工具调度", "后端", "tool scheduling" |
| [QMClaw数据分析与质量评估](./skills/qmclaw-data-analysis/README.md) | v1.0 | T1/Rabi 拟合、XEB 保真度、质量指标 | "数据分析", "保真度", "T1", "quality" |
| [量子比特单比特校准标定](./skills/quantum-calibration/README.md) | v1.0 | 完整校准标定流程、参数管理、质量判定 | "校准", "标定", "单比特", "qubit" |
| [量子实验绘图与分析](./skills/quantum-experiment-plotter/README.md) | v1.0 | 一维/二维绘图、学术图表、数据拟合 | "绘图", "可视化", "plot", "figure" |

---

## 🚀 Quick Start

### 1. Installation

#### Claude Code

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/qmclaw-skills.git
cd qmclaw-skills

# Install skills
mkdir -p ~/.claude/skills
cp -R skills/* ~/.claude/skills/
```

#### Manual Installation

Copy the `skills/` directory to your agent's skill directory:

```bash
# For Claude Code
~/.claude/skills/

# For Codex
~/.codex/skills/

# For other agents
<your-agent-skill-dir>/
```

### 2. Usage

After installation, invoke skills naturally:

```
Use the QMClaw framework overview skill to introduce the architecture.
执行单比特校准工作流。
如何进行 T1 拟合和 XEB 保真度计算？
```

### 3. Example: Single-Qubit Calibration

```python
import sys
# Add your workflow directory path here
WORKFLOW_DIR = '/path/to/your/sq_workflow'  # Linux/Mac
# WORKFLOW_DIR = r'D:\path\to\sq_workflow'  # Windows
sys.path.insert(0, WORKFLOW_DIR)

import labrad
from lqms.pyle.workflow import switchSession
import sq
import numpy as np

# Connect to quantum control system
cxn = labrad.connect()
s = switchSession(cxn, user='YOUR_USER')
qobj = s.YOUR_QUBIT

# Execute 15-step calibration workflow
print("[1/15] S21 scan...")
sq.s21(qobj, update=False)

print("[4/15] Spectroscopy...")
sq.spectroscopy(qobj, freq=np.arange(2.7, 2.9, 0.001))

# ... continue with remaining steps
print("[15/15] T1 measurement...")
sq.t1(qobj, zpa=0)
```

> **Note:** Adjust `WORKFLOW_DIR` and connection parameters according to your lab's setup.

---

## 📁 Project Structure

```
qmclaw-skills/
├── README.md                    # This file
├── LICENSE                      # MIT License
├── install.md                   # Installation guide
├── CONTRIBUTING.md              # Contribution guidelines
├── .gitignore                   # Git ignore patterns
│
├── assets/                      # Project assets
│   └── qmclaw-logo.png          # Logo
│
├── skills/                      # Skills directory
│   ├── _shared/                 # Shared content
│   │   ├── core/
│   │   │   ├── quantum-params.md
│   │   │   ├── quality-standards.md
│   │   │   └── terminology.md
│   │   └── README.md
│   │
│   ├── qmclaw-framework-overview/       # Framework architecture
│   │   ├── SKILL.md
│   │   └── README.md
│   │
│   ├── qmclaw-decision-architecture/    # Three-layer decision
│   ├── qmclaw-tuneup-workflow/          # 15-step calibration
│   ├── qmclaw-tool-scheduling/          # Tool scheduling
│   ├── qmclaw-data-analysis/            # Data analysis
│   ├── quantum-calibration/             # Single-qubit calibration
│   └── quantum-experiment-plotter/      # Visualization
│
└── docs/                        # Documentation (optional)
```

---

## 🏗️ Architecture

### Six-Layer Execution Stack

```
┌─────────────────────────────────────────────────────────────┐
│  L6: Natural Language Interface    │  User commands         │
├─────────────────────────────────────────────────────────────┤
│  L5: Analysis & Report             │  Data fitting          │
├─────────────────────────────────────────────────────────────┤
│  L4: Workflow Orchestration        │  Workflow execution    │
├─────────────────────────────────────────────────────────────┤
│  L3: Tool Scheduling               │  Tool dispatch         │
├─────────────────────────────────────────────────────────────┤
│  L2: Knowledge & Rules             │  RAG, rule learning    │
├─────────────────────────────────────────────────────────────┤
│  L1: Hardware Interface            │  Instruments/simulators│
└─────────────────────────────────────────────────────────────┘
```

### Three-Layer Decision Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  L3: LLM Fallback Layer                                    │
│  ├── Use case: New problems, no rule match                 │
│  ├── Speed: ~300ms                                         │
│  └── Cost: API cost                                        │
├─────────────────────────────────────────────────────────────┤
│  L2: Learned Rules Layer                                   │
│  ├── Source: L3 success case promotion                     │
│  ├── Speed: <1ms                                           │
│  └── Cost: $0                                              │
├─────────────────────────────────────────────────────────────┤
│  L1: RuleEngine Layer                                      │
│  ├── Content: Expert knowledge, physics rules              │
│  ├── Speed: <1μs                                           │
│  └── Cost: $0                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 📚 Documentation

| Document | Description |
|----------|-------------|
| [README](./README.md) | This file - project overview |
| [install.md](./install.md) | Detailed installation guide |
| [skills/*/README.md](skills/) | Individual skill documentation |
| [SKILL.md](./skills/*/SKILL.md) | Skill specifications |

---

## 🤝 Contributing

Contributions are welcome! Please follow these steps:

1. **Fork** the repository
2. **Create** a feature branch (`git checkout -b feature/amazing-feature`)
3. **Commit** your changes (`git commit -m 'Add amazing feature'`)
4. **Push** to the branch (`git push origin feature/amazing-feature`)
5. **Open** a Pull Request

### Adding a New Skill

See [CONTRIBUTING.md](./CONTRIBUTING.md) for detailed guidelines.

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgments

- QMClaw framework developed by the QMClaw team
- Inspired by [nature-skills](https://github.com/Yuan1z0825/nature-skills)
- Built for the quantum computing community

---

## 📧 Contact

- **GitHub Issues**: [Issues](https://github.com/YOUR_USERNAME/qmclaw-skills/issues)

---

<p align="center">
  Made with ❤️ for the quantum computing community
</p>
