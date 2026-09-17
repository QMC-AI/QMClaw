# QmClaw - Quantum Measurement Control & Analysis

## 项目概述

QmClaw 是一个量子测量控制与分析系统，用于控制 LabRAD 实验设备、执行量子比特（qubit）校准实验，并进行数据分析。

## 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        Browser (Chrome)                          │
│                   http://localhost:3001                          │
└─────────────────────────────────────────────────────────────────┘
                              ↓ HTTP/API
┌─────────────────────────────────────────────────────────────────┐
│                    Next.js Frontend (:3001)                    │
│              React + TypeScript + Recharts                     │
│                  src/app/page.tsx                              │
└─────────────────────────────────────────────────────────────────┘
                              ↓ HTTP
┌─────────────────────────────────────────────────────────────────┐
│                   Express Server (:3002)                        │
│              src/index.ts (Node.js + tsx)                      │
│                                                                  │
│   Endpoints:                                                     │
│   - POST /job              提交实验任务                          │
│   - GET  /job/:id         查询任务状态                          │
│   - GET  /sessions         获取 DataVault 会话列表                │
│   - GET  /datasets         获取数据集列表                         │
│   - GET  /datasets/plot   生成历史数据图表                        │
│   - POST /workflow         提交工作流                            │
│   - GET  /health          健康检查                              │
└─────────────────────────────────────────────────────────────────┘
                              ↓ JSONL (stdin/stdout)
┌─────────────────────────────────────────────────────────────────┐
│              Python Subprocess (job_runner.py)                  │
│                                                                  │
│   - 启动时初始化 LabRAD 连接 (~20秒)                            │
│   - 主事件循环处理实验代码                                       │
│   - Flask 风格请求直接集成                                       │
│   - Matplotlib 绑图生成                                         │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                     LabRAD Server (:7682)                       │
│              量子测量仪器控制与数据采集                          │
└─────────────────────────────────────────────────────────────────┘
```

## 目录结构

```
D:\qmclaw\
├── README.md                    # 本文档
├── CLAUDE.md                    # 开发约定与进度记录
├── 一键启动.cmd                  # ⚡ 一键启动器（推荐）
├── start_manager.py             # Python 启动管理器
├── launch-all.cmd              # 备用启动器
│
├── qmclaw-server/              # Express 后端服务
│   ├── src/
│   │   ├── index.ts            # 主入口（Express 服务器）
│   │   ├── queue/              # 任务队列
│   │   ├── gateway/             # LabRAD 网关
│   │   │   ├── experiment.ts
│   │   │   ├── labrad.ts
│   │   │   ├── optimization.ts
│   │   │   ├── plot.ts
│   │   │   └── server_control.py  # 服务控制器
│   │   └── worker/             # Python 桥接
│   └── scripts/
│       ├── job_runner.py        # Python 子进程脚本
│       ├── check_services.py    # 服务状态检查
│       └── start_services.py    # 服务启动脚本
│
├── qmclaw-web/                  # Next.js 前端
│   ├── src/
│   │   ├── app/                # Next.js App Router
│   │   │   ├── page.tsx        # 主页面（仪表盘）
│   │   │   └── layout.tsx      # 布局
│   │   ├── components/         # React 组件
│   │   ├── lib/
│   │   │   ├── api.ts          # API 客户端
│   │   │   └── websocket.ts     # WebSocket 客户端
│   │   └── public/
│   │       └── plots/          # 生成的图表存储目录
│
└── measure_scripts/             # LabRAD 后端脚本
    ├── sq_workflow/            # 量子比特实验脚本
    └── measure_scripts.py       # 主测控脚本
```

### 方式一：分别启动（开发模式）

手动启动服务
终端 1 - 后端 (端口 3002)


cd D:\qmclaw\qmclaw-server
& "C:\Program Files\AutoClaw\resources\node\node.exe" node_modules\tsx\dist\cli.mjs src\index.ts
终端 2 - 前端 (端口 3001)


cd D:\qmclaw\qmclaw-web
set PATH="C:\Program Files\AutoClaw\resources\node;%PATH%"
npm run dev
访问地址: http://localhost:3001


### 启动后访问

- 前端：http://localhost:3001
- 后端：http://localhost:3002

> **首次启动**：Python 子进程初始化 LabRAD 连接需要约 20 秒。

---

## 测控服务启动（LabRAD）

QmClaw 的 Web 界面依赖 LabRAD 测控服务。启动前请确保 LabRAD 已运行：

```powershell
python -c "from lqcs.servers_control import run_server_control; run_server_control()"
```

在弹出的 GUI 窗口中依次点击启动：
1. **labrad** - LabRAD 管理器
2. **datas** - 数据存储服务
3. **grapher** - 绘图服务
4. **registry editor** - 参数编辑器
5. **ray** - 分布式服务框架
6. **device manager** - 板卡服务
7. **uwave manager** - 微波源服务

## 环境要求

### 软件版本
- **Node.js**: v22.22.0（使用 AutoClaw 内置版本：`C:\Program Files\AutoClaw\resources\node\node.exe`）
- **Python**: 3.11（`C:\Users\lqcs\Programs\Python\Python311\python.exe`）
- **操作系统**: Windows 11 Pro

### Python 依赖
```
labrad          # LabRAD 通信库
lqms            # 量子测量系统（自定义）
matplotlib      # 绑图生成
numpy           # 数值计算
scipy           # 科学计算
openai          # LLM API 调用
```

### 环境变量（可选）
```powershell
$env:PORT = "3002"              # Express 服务器端口
$env:PYTHON_BIN = "C:\Users\lqcs\Programs\Python\Python311\python.exe"
$env:PLOTS_DIR = "D:\qmclaw\qmclaw-web\public\plots"
$env:OPENAI_API_KEY = "your-api-key"  # 用于工作流中的 LLM 决策
```

---

## 安装依赖

### 1. 安装 Node.js

下载并安装 Node.js (LTS 版本 v18+)：https://nodejs.org/

验证安装：
```bash
node -v
npm -v
```

> **Windows PowerShell 用户**：如果遇到脚本执行错误，以管理员身份运行：
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

### 2. 安装 Python 依赖

```bash
cd "d:\Documents\QMClaw\Agentic Workflow\qmclaw-server\python"
pip install -r requirements.txt
```

主要依赖：
- `labrad` — LabRAD 通信库
- `lqms` — 量子测量系统
- `matplotlib` — 绘图生成
- `numpy`, `scipy` — 数值计算

### 3. 安装 Node.js 依赖

**后端 (Express)：**
```bash
cd "d:\Documents\QMClaw\Agentic Workflow\qmclaw-server"
npm install
```

**前端 (Next.js)：**
```bash
cd "d:\Documents\QMClaw\Agentic Workflow\qmclaw-web"
npm install
```

如果 npm 安装慢，可以使用淘宝镜像：
```bash
npm config set registry https://registry.npmmirror.com
npm install
```

### 4. 配置环境变量

```bash
cd "d:\Documents\QMClaw\Agentic Workflow\qmclaw-server"
copy .env.example .env
```

编辑 `.env` 文件，填入你的 API Key（至少需要一个）：

```env
# LLM API Keys（至少填一个）
MINIMAX_API_KEY=your_minimax_api_key_here
MINIMAX_GROUP_ID=your_group_id_here

# 或使用其他 Provider
# OPENAI_API_KEY=sk-your-openai-key
# ANTHROPIC_API_KEY=sk-ant-your-key
# DEEPSEEK_API_KEY=sk-your-deepseek-key

# Python 路径（Windows）
PYTHON_BIN=C:\Users\你的用户名\Programs\Python\Python311\python.exe

# 图表输出目录
PLOTS_DIR=d:\Documents\QMClaw\Agentic Workflow\qmclaw-web\public\plots
```

### 5. 创建必要的目录

```bash
# 创建图表输出目录
mkdir -p "d:\Documents\QMClaw\Agentic Workflow\qmclaw-web\public\plots"

# 创建数据目录
mkdir -p "d:\Documents\QMClaw\Agentic Workflow\qmclaw-server\data\workflows"
mkdir -p "d:\Documents\QMClaw\Agentic Workflow\qmclaw-server\data\templates"
```

---

## 快速启动

### 方式一：一键启动（推荐）

```powershell
cd "d:\Documents\QMClaw\Agentic Workflow"
.\一键启动.cmd
```

### 方式二：分别启动

**终端 1 — 后端 (端口 3002)：**
```bash
cd "d:\Documents\QMClaw\Agentic Workflow\qmclaw-server"
npm run dev
```

**终端 2 — 前端 (端口 3001)：**
```bash
cd "d:\Documents\QMClaw\Agentic Workflow\qmclaw-web"
npm run dev
```

### 启动后访问

- 前端：http://localhost:3001
- 后端：http://localhost:3002
- 健康检查：http://localhost:3002/health

> **首次启动**：Python 子进程初始化 LabRAD 连接需要约 20 秒，请耐心等待。

## 核心功能

### 1. 实验控制面板
- 选择量子比特（qubit）
- 选择实验类型（spectroscopy, s21, t1, ramsey_df 等）
- 运行实验并查看实时结果
- 测量指标（fidelity, T1, gate fidelity）

### 2. LabRAD DataVault 浏览器
- 浏览实验数据目录
- 查看历史数据集
- 生成并显示数据图表

### 3. 会话管理
- 切换 DataVault 会话
- 保存默认会话
- 自动恢复上次会话

### 4. 工作流引擎
支持的工作流节点类型：
- **experiment**: 执行量子比特实验
- **analyze**: 从前一个节点提取指标（支持实时/历史数据源）
- **quality_gate**: 基于指标的门控（pass/fail）
- **adjust_params**: 调整量子比特参数
- **decision**: LLM 驱动的决策节点（支持自定义输出变量名）
- **image_analysis**: 图像分析节点
- **print**: 打印日志节点
- **while**: 循环节点
- **parallel**: 并行执行节点
- **notify**: 通知节点
- **code**: 代码执行节点
- **context**: 上下文管理节点

### 5. 实验类型列表
| 名称 | 函数 | 说明 |
|------|------|------|
| spectroscopy | sq.spectroscopy | VNA 频谱扫描 |
| s21 | sq.s21 | 腔体 S21 频率扫描 |
| s21_dis | sq.s21_dis | S21 失真测量 |
| pulsed_spec | sq.pulsed_spec | 脉冲光谱 |
| iqraw | sq.iqraw | IQ 原始数据采集 |
| t1 | sq.t1 | T1 弛豫时间测量 |
| ramsey | sq.ramsey | Ramsey 干涉测量 |
| ramsey_df | sq.ramsey_df | Ramsey 退相干自由测量 |
| piamp | sq.piamp | Pi 脉冲幅度校准 |
| xeb | sq.xeb | 交叉熵基准测试 |
| single_shot | sq.single_shot | 单次读取保真度 |
| allxy | sq.allxy | AllXY 门表征 |
| swap | sq.swap | SWAP 门表征 |
| drag_calibrate | sq.drag_calibrate | DRAG 脉冲校准 |

### 6. 工作流高级功能

#### 历史数据分析 (Analyze Node)
Analyze 节点支持从 DataVault 查询历史实验数据进行统计分析：
- **实时模式 (Realtime)**: 分析上一个实验节点的输出
- **历史模式 (Historical)**: 从 DataVault 查询过去实验数据
  - 支持按量子比特、实验类型过滤
  - 支持按"最近 N 次"或"最近 N 天"筛选
  - 输出统计指标: mean, std, min, max, trend, latest

输出变量示例：
```
stats.{metric}.mean   # 均值
stats.{metric}.std    # 标准差
stats.{metric}.min    # 最小值
stats.{metric}.max    # 最大值
stats.{metric}.trend  # 趋势 (±%变化)
stats.{metric}.latest # 最新值
summary               # 文字总结
```

#### LLM 决策节点 (Decision Node)
Decision 节点支持自定义输出变量名：
- `symptomOutputVar`: 症状输出变量名（默认: `symptom`）
- `recommendationsOutputVar`: 建议输出变量名（默认: `recommendations`）
- `reasoningOutputVar`: 推理过程变量名（默认: `reasoning`）
- `symptomPromptVar`: 症状提取提示词变量名（默认: `symptom_prompt`）
- `recommendationsPromptVar`: 建议生成提示词变量名（默认: `recommendations_prompt`）

这些变量会自动初始化为空值（即使是空数组/对象），确保后续节点始终可以引用。

#### 工作流上下文变量
工作流支持在节点间传递上下文变量：
- 使用 `{{variableName}}` 语法引用变量
- 支持变量覆盖（adjust_params 节点）
- 支持实验节点参数化
- 支持决策节点上下文引用

## API 端点

### 实验任务
```
POST /job
  Body: { code: "python 代码字符串" }
  Response: { jobId: string, status: "pending" }

GET /job/:jobId
  Response: { status, stdout, stderr, plotPath?, completedAt }

DELETE /job/:jobId
  Response: { jobId, status: "cancelled" }
```

### DataVault
```
GET /sessions
  Response: { current: {...}, sessions: [...] }

POST /sessions/switch
  Body: { path: ["", "LQHL", "test"] }
  Response: { success: true, path: [...] }

GET /datasets?path=LQHL/test/20260324
  Response: { path, groups: [...], datasets: [...] }

GET /datasets/plot?name=xxx&path=xxx
  Response: PNG 图片
```

### 工作流
```
POST /workflow
  Body: { name, nodes: [...], context: {...} }
  Response: { workflowId, status }

GET /workflow/:id
  Response: { status, nodes: {...}, context: {...} }

GET /workflow-runs?workflowId=xxx&workflowName=xxx
  Response: [{ id, workflowId, workflowName, status, nodes: [...], ... }]

DELETE /workflow-runs/:id
  Response: { success: true }
```

### 服务控制
```
GET /server/status
  Response: { labrad: {...}, ray: {...}, services: {...}, overall: string }

POST /server/start
  Response: { success: boolean, stdout: string, stderr: string }
```

## 数据流程

### 实验执行流程
1. 用户在前端选择量子比特和实验类型
2. 前端调用 `POST /job` 提交实验
3. Express 将代码通过 stdin 发送给 Python 子进程
4. Python 子进程执行实验代码
5. 实验结果通过 stdout 返回（JSONL 格式）
6. Express 解析结果并存储
7. 前端轮询 `GET /job/:id` 获取结果
8. 实验图表保存到 `public/plots/` 目录

### Flask 风格请求处理
Express 向 Python 子进程发送 JSONL 格式消息：
```json
{"type": "flask", "cid": "abc123", "action": "datasets", "data": {"path": "LQHL/test"}}
```
Python 处理后返回结果：
```json
{"cid": "abc123", "action": "datasets", "data": {"path": "...", "groups": [], "datasets": [...]}}
```

## 故障排查

### 端口占用
```powershell
# 查看端口占用
Get-NetTCPConnection -LocalPort 3002

# 停止进程
Stop-Process -Id <PID> -Force
```

### Python 子进程无响应
Python 子进程初始化需要约 20 秒（LabRAD 连接）。如果启动失败，检查：
1. LabRAD 服务器是否运行（端口 7682）
2. `D:\qmclaw\measure_scripts\sq_workflow\backend.py` 是否存在
3. `lqms` 库是否正确安装

### 图表显示问题
检查 `D:\qmclaw\qmclaw-web\public\plots\` 目录是否存在且可写。

## 开发注意事项

1. **Express 服务器**（port 3002）由 AutoClaw 启动时管理，PID 39252 是 AutoClaw 内置进程，无法直接终止。

2. **Python 子进程**使用 JSONL 协议通信，必须在每条消息后加 `\n`，并使用 `flush=True`。

3. **Flask 请求处理**在 Python 主线程中同步执行，避免 stdin/stdout 缓冲问题。

4. **LabRAD 连接**是共享资源，实验执行期间不能同时进行 DataVault 操作。

## 相关文档

- LabRAD: https://github.com/labrad/labrad
- Next.js: https://nextjs.org/
- Matplotlib: https://matplotlib.org/
