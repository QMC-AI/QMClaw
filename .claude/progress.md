# QMClaw 开发进度

## 2026-09-20: 统一量子测控工具集和 API

### 背景
当前 QMClaw 系统有三个服务可以调用测控实验命令：
- **agent_service** (3005): 直接调用 LabRAD，执行 `sq.*` 函数
- **qca_service** (3011): 结构化实验接口，基于 LangChain
- **hermes_service** (3012): 通用 Agent，需要扩展工具

**问题**：
1. 工具定义风格不同：agent 用自定义 `Tool` 类，qca 用 LangChain `@tool`
2. 参数格式不统一：agent 用代码字符串，qca 用结构化参数
3. 返回格式不一致：各服务返回格式各异

**目标**：统一工具集、统一 API、统一返回格式，支持日常测控实验场景。

### 架构
```
┌─────────────────────────────────────────────────────────────┐
│                    Unified Quantum Tools                      │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              ToolRegistry (Singleton)               │    │
│  │  ┌─────────────┐ ┌─────────────┐ ┌───────────┐ │    │
│  │  │RunCodeTool  │ │GetQubits   │ │Data Tools │ │    │
│  │  └─────────────┘ └─────────────┘ └───────────┘ │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
          ▲                ▲                ▲
          │                │                │
    ┌─────┴─────┐  ┌─────┴─────┐  ┌────┴────┐
    │agent_svc   │  │ qca_svc   │  │hermes_svc│
    │  adapter   │  │  adapter  │  │ MCP Tool │
    └───────────┘  └───────────┘  └──────────┘
```

### 已完成

#### Phase 1: 核心框架
- [x] `services/common/quantum_tools/__init__.py` - 统一导出
- [x] `services/common/quantum_tools/base.py` - 工具基类和统一返回格式
  - `ToolResult` - 统一返回格式
  - `ToolSchema` - 工具 schema
  - `ParameterSchema` - 参数 schema
  - `QuantumTool` - 抽象基类
  - `ExperimentResult` - 实验结果数据结构
  - `QubitInfo` - 量子比特信息
- [x] `services/common/quantum_tools/errors.py` - 错误码定义
  - `ErrorCode` 枚举
  - `QMClawError` 异常基类
  - `ValidationError`, `NotFoundError`, `TimeoutError`, `LabRADError` 等
- [x] `services/common/quantum_tools/registry.py` - 工具注册中心
  - `ToolRegistry` 类（每次实例化都是新实例，非单例）
  - `register_tool()` 注册工具
  - `execute()` 执行工具
  - `get_definitions()` 返回 OpenAI 格式定义
  - `get_stats()` 执行统计
- [x] `services/common/quantum_tools/types.py` - 数据类型定义

#### Phase 2: 核心工具实现
- [x] `services/common/quantum_tools/execution.py` - 实验执行工具
  - `RunCodeTool` - 直接执行代码
  - `RunExperimentTool` - 执行命名实验
  - `ListExperimentsTool` - 列出实验
  - `GetExperimentSchemaTool` - 获取实验 schema
- [x] `services/common/quantum_tools/qubit.py` - 量子比特工具
  - `GetQubitsTool` - 获取量子比特列表
  - `GetQubitParamsTool` - 获取量子比特参数
  - `SetQubitParamsTool` - 设置量子比特参数
- [x] `services/common/quantum_tools/data.py` - 数据查询工具
  - `ListHistoryTool` - 查询实验历史
  - `GetExperimentDataTool` - 获取实验数据
  - `GetArrayDataTool` - 获取数组数据
  - `GetStatsTool` - 获取统计数据

#### Phase 3: 服务适配（每个服务独立注册）
- [x] `services/agent_service/adapter.py` - Agent Service 适配器
  - `setup_agent_tools()` - 注册工具（直接调用 LabRAD）
  - `AgentServiceAdapter` 类
- [x] `services/agent_service/server.py` - 集成统一工具 + `/api/v1/` 路由
- [x] `services/qca_service/adapter.py` - QCA Service 适配器
  - `setup_qca_tools()` - 注册工具（HTTP 调用 quantum_service）
  - `QCAServiceAdapter` 类
- [x] `services/hermes_service/quantum_toolset.py` - Hermes MCP 工具服务器
  - `setup_hermes_tools()` - 注册工具（HTTP 调用 quantum_service）
  - `QuantumMCPServer` 类
- [x] `services/hermes_service/server.py` - 集成量子工具集 + `/quantum-tools` 端点

### 架构说明

```
┌─────────────────────────────────────────────────────────────────────┐
│                   智能体服务（通过 HTTP 调用）                      │
│                                                                  │
│  agent_service  ──────────────┐                                  │
│  qca_service    ────────────┼──> quantum_service (:3003)        │
│  hermes_service ──────────────┘                                  │
│                                                                  │
│  负责：自然语言理解、任务规划、工具调用                            │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ 直接调用
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   quantum_service                                 │
│  负责：LabRAD 连接、实验执行、量子比特管理                         │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   LabRAD / 硬件设备                               │
└─────────────────────────────────────────────────────────────────────┘
```

每个服务的 ToolRegistry 是独立实例，工具定义相同且都通过 HTTP 调用 quantum_service。

### 统一 API 端点

#### agent_service (3005)
```
GET  /api/v1/tools           - 列出所有工具
GET  /api/v1/tools/{name}   - 获取工具 schema
POST /api/v1/tools/{name}   - 执行工具
GET  /api/v1/qubits          - 获取量子比特
GET  /api/v1/experiments     - 列出实验
GET  /api/v1/health          - 健康检查
```

#### hermes_service (3012)
```
GET  /quantum-tools          - 列出量子工具
POST /quantum-tools/execute   - 执行量子工具
```

### 统一返回格式
```python
{
    "success": true,
    "data": {...},
    "error": {
        "code": "NOT_FOUND",
        "message": "Resource not found",
        "hint": "Call GET /qubits to see available"
    },
    "request_id": "req_abc123",
    "timestamp": "2026-09-20T14:30:45Z"
}
```

### 待验证
- [ ] 模块导入测试 (已通过)
- [ ] 服务启动测试
- [ ] 工具注册测试
- [ ] 跨服务 API 一致性测试
- [ ] hermes_service 量子工具集成测试

---

## 2026-09-17: Quantum/Analysis 服务降级模式

### 背景
当 LabRAD 或 lqcs 服务不可用时（如 lqcs 中的 Cython/Twisted 兼容性问题），quantum_service 和 analysis_service 应该能够优雅降级，显示本地缓存数据而不是直接崩溃。

### 目标
- 服务启动失败时不崩溃，进入降级模式
- 支持重连策略（指数退避）
- 最大重试次数后停止，显示降级状态
- 降级模式下返回本地缓存数据
- 前端显示降级状态和提示信息

### 架构
```
                    ┌─────────────────┐
                    │  Service Start  │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  Try Connect     │
                    │  LabRAD/lqcs     │
                    └────────┬────────┘
                             │
              ┌──────────────┴──────────────┐
              │                             │
       ┌──────▼──────┐              ┌───────▼──────┐
       │  Connected  │              │   Failed     │
       └──────┬──────┘              └───────┬──────┘
              │                             │
       ┌──────▼──────┐              ┌───────▼──────┐
       │  Healthy    │              │ Degraded Mode │
       │  Full Func  │              │ + Retry Timer│
       └─────────────┘              └──────┬───────┘
                                            │
                                    (max_attempts 次后停止)
                                            ▼
                                    ┌───────────────┐
                                    │  Permanent    │
                                    │  Degraded     │
                                    └───────────────┘
```

### 配置文件
```json
// config/fallback_config.json
{
  "quantum_service": {
    "enabled": true,
    "fallback_data_path": "data/fallback/quantum",
    "retry": {
      "max_attempts": 5,
      "base_delay": 2,
      "max_delay": 30
    }
  },
  "analysis_service": {
    "enabled": true,
    "fallback_data_path": "data/fallback/analysis",
    "retry": {...}
  }
}
```

### 已完成

#### 降级状态管理器
- [x] `services/common/fallback_manager.py` - FallbackManager 类
  - `ConnectionState` 枚举: DISCONNECTED, CONNECTING, CONNECTED, DEGRADED, DEGRADED_PERMANENT
  - 重连策略: 指数退避 (`base_delay * 2^attempt`)
  - 状态变更回调机制
  - 本地降级数据加载 (`get_fallback_data()`)
  - 定时器管理 (`start_reconnect_timer()`)
- [x] `services/common/__init__.py` - 导出 FallbackManager, ConnectionState

#### Quantum Service 降级
- [x] `services/quantum_service/server.py` 修改
  - 添加 `_fallback: FallbackManager` 成员
  - 修改 `before_start()`: 连接失败时进入降级模式，启动重连定时器
  - 添加 `_do_reconnect()`: 重连回调函数
  - 添加 `_load_fallback_data()`: 加载降级数据 (qubits.json, experiments.json)
  - 修改 `_ensure_connected()`: 处理降级状态
  - 修改 `_handle_health()`: 返回 `fallback_mode`, `fallback_state`, `fallback_message`
  - 修改 `_handle_connect()`: 支持手动重连
  - 修改 `_handle_qubits()`: 降级模式下返回本地数据
  - 修改 `_handle_experiments()`: 降级模式下返回本地数据
  - 修改 `_handle_execute()`: 降级模式下拒绝执行
  - 修改其他 handler: 添加降级状态检查
  - 添加 `_get_fallback_message()`: 生成用户友好的降级提示

#### Analysis Service 降级
- [x] `services/analysis_service/server.py` 修改
  - 添加 `_fallback: FallbackManager` 成员
  - 修改 `before_start()`: 连接失败时进入降级模式
  - 添加 `_do_reconnect()`: 重连回调函数
  - 添加 `_load_fallback_data()`: 加载降级数据 (datasets.json)
  - 修改 `_handle_health()`: 返回降级信息
  - 修改 `_handle_connect()`: 支持重连
  - 修改 `_handle_execute()`: 降级模式下拒绝执行
  - 修改 `_handle_datasets()`: 降级模式下返回本地数据
  - 添加 `_get_fallback_message()`: 生成降级提示

#### Gateway API 更新
- [x] `src/index.ts` 修改
  - 修改 `/hardware/status`: 返回 `fallback` 对象 (mode, state, message)
  - 修改 `/hardware/quick`: 返回 `fallback` 对象，更新 `labrad` 状态为 "degraded"
  - 添加日志过滤器标签: `[FallbackManager]`, `[quantum_service]`, `[analysis_service]`

#### 降级数据示例
- [x] `data/fallback/quantum/qubits.json` - 量子比特示例数据
- [x] `data/fallback/quantum/experiments.json` - 实验函数示例数据
- [x] `data/fallback/analysis/datasets.json` - 数据集示例数据

#### 配置文件
- [x] `config/fallback_config.json` - 降级模式配置

### 前端 API 响应格式
```typescript
// GET /hardware/quick
{
  labrad: "degraded",
  llm: "ready",
  ray: "via_quantum",
  datavault: "disconnected",
  fallback: {
    mode: true,
    state: "degraded",
    message: "LabRAD 连接中断，正在尝试重连（3/5）..."
  },
  message: "降级模式 - LabRAD 连接中断，正在尝试重连（3/5）..."
}

// GET /hardware/status
{
  overall: "degraded",
  timestamp: "...",
  services: {...},
  devices: {},
  issues: ["LabRAD not connected"],
  fallback: {
    mode: true,
    state: "degraded_permanent",
    message: "LabRAD 不可用（已停止重连）。最后错误：连接超时"
  }
}
```

### 待验证
- [ ] quantum_service 启动测试（正常模式和降级模式）
- [ ] analysis_service 启动测试
- [ ] 降级数据加载测试
- [ ] 重连机制测试（模拟 LabRAD 不可用）
- [ ] 前端降级状态显示测试
- [ ] 永久降级状态测试（max_attempts 次重连失败后）

---

## 2026-09-16: Workflow 历史记录微服务迁移

### 背景
系统已从 Legacy 模式迁移到微服务模式，但 workflow 历史记录功能仍在 Express 层。微服务重启会导致运行记录丢失。

### 目标
在 `workflow_service/server.py` 中实现历史记录功能，与现有的 TypeScript 版本功能对标。

### 架构
```
Browser → Express (:3002) → workflow_service (:3008)
                            → data/workflow-runs/*.json
```

### 已完成

#### workflow_service/server.py 增强
- [x] 添加数据目录配置 `DATA_DIR = ROOT_DIR / "data" / "workflow-runs"`
- [x] 添加 `_ensure_data_dir()` 确保目录存在
- [x] 添加历史记录数据类: `WorkflowRunNodeInput`, `WorkflowRunNodeOutput`, `WorkflowRunNode`, `WorkflowRun`
- [x] 添加数据锁 `_runs_lock = threading.Lock()`
- [x] 添加 CRUD 方法:
  - `_list_runs()` - 列出运行记录（支持 workflowId/workflowName 筛选）
  - `_get_run()` - 获取单条运行记录
  - `_save_run()` - 保存运行记录
  - `_delete_run()` - 删除运行记录
  - `_delete_runs_by_workflow()` - 删除某工作流的所有运行记录
  - `_get_stats()` - 获取工作流统计信息
- [x] 添加 `_persist_workflow_run()` - 将工作流运行结果持久化到磁盘
- [x] 修改 `_run_workflow_async()` 在工作流完成时调用持久化方法
- [x] 添加历史记录 API 路由处理函数:
  - `_handle_list_runs()` - GET /runs
  - `_handle_get_run()` - GET /runs/<id>
  - `_handle_delete_run()` - DELETE /runs/delete
  - `_handle_delete_runs_by_workflow()` - DELETE /runs/workflow/<workflowId>
  - `_handle_get_stats()` - GET /runs/stats/<workflowId>
- [x] 修改 `handle_request()` 添加新路由

#### BaseService 增强
- [x] 添加 `do_DELETE()` 方法支持 DELETE 请求

#### Express 网关增强 (serviceProxy.ts)
- [x] 修改 `proxyToService()` 支持 DELETE 方法
- [x] 添加工作流历史记录代理路由:
  - `GET /api/workflow-runs` → `/runs`
  - `GET /api/workflow-runs/stats/:workflowId` → `/runs/stats/:workflowId`
  - `GET /api/workflow-runs/:runId` → `/runs/:runId`
  - `DELETE /api/workflow-runs/:runId` → `/runs/delete`
  - `DELETE /api/workflow-runs/workflow/:workflowId` → `/runs/workflow/:workflowId`

#### Express index.ts 清理
- [x] 注释掉原有的 Express workflow-runs 路由（原直接使用 workflowRunService.ts）

### API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/runs` | 列出所有运行记录（支持 workflowId/workflowName 筛选） |
| GET | `/runs/<id>` | 获取单条运行记录 |
| DELETE | `/runs/delete` | 删除运行记录 (body: {runId}) |
| DELETE | `/runs/workflow/<workflowId>` | 删除某工作流的所有运行记录 |
| GET | `/runs/stats/<workflowId>` | 获取统计信息 |

### 待验证
- [ ] workflow_service 启动测试
- [ ] 运行 workflow 后检查 `data/workflow-runs/` 目录
- [ ] API 端点测试 (curl 或前端)
- [ ] 前端 Runs/History 页面功能测试

---

## 2026-09-14: Experiments 页面布局重构

### 背景
用户希望统一展示 COMMAND、PLOT COMMAND、ANALYZE COMMAND 三个命令，并整合结果展示区域。

### 设计方案
```
┌─────────────────────────────────────────────────────────────┐
│  ▶ COMMAND    sq.iqraw(q3ld4, do_plot=True)          [▼]  │
├─────────────────────────────────────────────────────────────┤
│  ▶ PLOT CMD   qter.fitData({exp_num})                [▼]  │
├─────────────────────────────────────────────────────────────┤
│  ▶ ANALYZE    qter.fitData({exp_num}, collect=True)  [▼] │
├─────────────────────────────────────────────────────────────┤
│  📋 RESULTS                                            │
│  ├── 📊 绘图结果 (Base64 图像 + 下载按钮)              │
│  └── 📈 分析结果 (metrics 网格 + stdout)              │
├─────────────────────────────────────────────────────────────┤
│  [⚡ Execute All] [▶ Run] [其他实验快捷按钮...]         │
└─────────────────────────────────────────────────────────────┘
```

### 颜色方案
- COMMAND: `#22c55e` (绿色) - 执行
- PLOT: `#3b82f6` (蓝色) - 绘图
- ANALYZE: `#f59e0b` (橙色) - 分析
- Execute All: `#8b5cf6` (紫色)

### 已完成
- [x] `CollapsibleCommand.tsx` - 可折叠命令组件
  - 折叠时显示一行命令文本
  - 展开时显示 textarea + Run/Save 按钮
  - 左侧边框颜色区分类型
  - Ctrl+Enter 运行快捷键
- [x] `UnifiedResults.tsx` - 统一结果展示组件
  - 整合绘图结果和分析结果
  - Base64 图像显示 + 下载按钮
  - Metrics 网格展示
  - stdout 可折叠显示
- [x] `page.tsx` - 重构 Experiments Tab
  - 移除 EditableCommand、PlotCommandEditor、AnalysisCommandEditor
  - 引入 CollapsibleCommand 和 UnifiedResults
  - 添加 "Execute All" 一键执行按钮
  - 状态管理更新 (isRunningCommand/Plot/Analyze, isSaving*)
- [x] `experiment_configs.json` - 补充所有实验的 defaultCommand

### 待测试
- [ ] 折叠/展开交互
- [ ] 命令编辑和运行
- [ ] Execute All 流程
- [ ] Base64 图像显示
- [ ] 分析结果展示

---

## 2026-09-12: 服务化架构重构 Phase 1 - 基础框架

### 背景
将 monolithic `job_runner.py` (4969行) 拆分为多个独立微服务，解决代码难以维护、功能耦合、Ray初始化冲突等问题。

### 目标架构
```
Express (:3002) → Python 微服务
├── LLM Service (:3006) - 多 Provider 统一接口
├── Quantum Service (:3003) - LabRAD 连接、实验执行
├── Analysis Service (:3004) - 数据分析、绘图
├── Agent Service (:3005) - QuantumAgent、ReAct 推理
├── Image Service (:3007) - PyTorch/ONNX 推理
└── Workflow Service (:3008) - 节点调度、依赖解析
```

### 已完成

#### 服务基础设施
- [x] `services/base/base_service.py` - 服务基类
  - `_safe_print()` - UTF-8 编码安全打印
  - `BaseService` - 抽象服务类
  - `ServiceConfig` - 配置数据类
  - `ServiceStatus` - 状态枚举
  - `run_service()` - 便捷运行函数
- [x] `services/base/service_registry.py` - 服务注册机制
- [x] `services/base/__init__.py` - 导出
- [x] `services/common/` - 公共模块
  - `config.py` - 配置管理
  - `logging.py` - 统一日志
  - `exceptions.py` - 异常定义

#### LLM 服务
- [x] `services/llm_service/server.py` - LLM 推理服务
  - `/chat` - 聊天完成（MiniMax/OpenAI/DeepSeek）
  - `/models` - 模型列表
  - `/stats` - 统计信息
  - `/health` - 健康检查
- [x] `services/llm_service/__init__.py`
- [x] 已测试可用

#### 测控执行服务
- [x] `services/quantum_service/labrad_client.py` - LabRAD 客户端
  - `LabRADClient` 类
  - LabRAD 连接管理
  - Qubit 生成和管理
  - Session 切换
  - 实验模块 (`sq.*`) 访问
- [x] `services/quantum_service/server.py` - 测控执行服务
  - `/health` - 健康检查
  - `/connect` - 触发连接
  - `/status` - 连接状态
  - `/qubits` - 量子比特列表
  - `/experiments` - 实验函数列表
  - `/execute` - 执行实验代码
  - `/switch_session` - 切换会话
- [x] `services/quantum_service/__init__.py`
- [x] `services/__init__.py` - 更新导出

#### 配置和脚本
- [x] `config/services.json` - 服务配置
- [x] `scripts/start_all_services.py` - 批量启动脚本

#### 数据分析服务
- [x] `services/analysis_service/server.py` - 数据分析服务
  - `/health` - 健康检查
  - `/plot` - 绘制最新数据集
  - `/plot/historical` - 绘制历史数据集
  - `/stats` - 数据统计
  - `/analyze` - 数据分析 (basic/peak/fit)
  - `/datasets` - 数据集列表
  - `/load` - 加载数据集
- [x] `services/analysis_service/__init__.py`
- [x] `services/__init__.py` - 更新导出

#### Agent 服务
- [x] `services/agent_service/server.py` - Agent 服务
  - `/health` - 健康检查
  - `/chat` - ReAct 推理对话
  - `/chat/stream` - 流式对话
  - `/tasks` - 任务列表
  - `/tasks/status` - 任务状态
  - `/tools` - 工具列表
  - 内置工具: run_experiment, get_qubits, list_experiments
- [x] `services/agent_service/__init__.py`

#### Image 服务
- [x] `services/image_service/server.py` - 图像服务
  - `/health` - 健康检查
  - `/classify/single` - 单张图像分类
  - `/classify/folder` - 批量图像分类
  - `/train` - 模型训练
  - `/model/info` - 模型信息
  - `/training/status` - 训练状态
  - 支持 PyTorch/ONNX 后端
- [x] `services/image_service/__init__.py`

#### Workflow 服务
- [x] `services/workflow_service/server.py` - 工作流服务
  - `/health` - 健康检查
  - `/workflows` - 工作流列表
  - `/workflows/create` - 创建工作流
  - `/workflows/run` - 运行工作流
  - `/workflows/status` - 工作流状态
  - `/workflows/cancel` - 取消工作流
  - 节点类型: experiment, analysis, llm
  - 拓扑排序依赖解析
- [x] `services/workflow_service/__init__.py`

#### 任务队列服务
- [x] `services/task_queue/server.py` - 任务队列服务
  - `/health` - 健康检查
  - `/submit` - 提交任务
  - `/status` - 任务状态
  - `/list` - 任务列表
  - `/cancel` - 取消任务
  - `/retry` - 重试任务
  - `/stats` - 统计信息
  - `/poll` - 轮询获取任务（工作者）
  - `/complete` - 标记任务完成
  - 任务类型: experiment, analysis, agent_chat, workflow, image_classify, general
  - 优先级队列 (1-10)
  - 重试机制 (可配置 max_retries)
  - Redis 后端支持（可选）
- [x] `services/task_queue/__init__.py`
- [x] `config/services.json` - 添加 task_queue 服务配置
- [x] `scripts/start_all_services.py` - 更新

#### 服务注册表更新
- [x] `services/__init__.py` - 更新导出所有服务

### Phase 1 完成 - 服务增强
- [x] 增强 Quantum Service 新增端点：
  - `GET /sessions` - 获取会话列表
  - `GET /session_tree?max_depth=5` - 获取目录树
  - `GET /qubit/params?name=xxx` - 获取量子比特参数
  - `POST /qubit/set_params` - 设置量子比特参数
  - `GET /datasets?path=xxx` - 获取数据集列表
- [x] 增强 LabRAD Client 添加属性：
  - `name`, `host`, `port`, `dv` 属性
- [x] 更新 Express 网关 - 添加新路由代理
- [x] 更新 TypeScript 客户端 - 添加新方法
- [x] 修复 TypeScript 错误 (PromiseRejectedResult.reason)

### Phase 2 完成 - 客户端代理
- [x] 创建 `services/client_proxy.py` - Python 客户端代理模块
  - QuantumProxy, AnalysisProxy, LLMProxy, AgentProxy
  - WorkflowProxy, TaskQueueProxy
  - check_service_health(), get_all_health()

### Phase 3 完成 - 前端API同步
- [x] 更新 Express 网关挂载路径: `app.use('/api', createServiceProxy())`
- [x] 更新前端 `api.ts` 使用新的微服务路径结构
- [x] 路径结构 (微服务架构一目了然):

```
/api/
├── /llm/*           → LLM Service (:3006)
├── /quantum/*       → Quantum Service (:3003)
├── /analysis/*      → Analysis Service (:3004)
├── /agent/*        → Agent Service (:3005)
├── /image/*        → Image Service (:3007)
├── /workflow/*      → Workflow Service (:3008)
├── /tasks/*        → Task Queue Service (:3009)
└── /hermes/*       → Hermes Agent (job_runner.py)
```

### 自动启动微服务
- [x] 在 job_runner.py 中添加 `ServiceManager` 类
- [x] 当 `QMCLAW_USE_SERVICES=true` 时自动启动所有微服务
- [x] 使用 `atexit` 注册退出时停止所有服务
- [x] 使用 `signal` 处理 SIGINT/SIGTERM 正确停止服务

### Express 网关路径设计原则
- 简单: 一个 Router 处理所有代理路由
- 可靠: 统一超时、错误处理、响应格式
- 易维护: 路由按服务分组，清晰明了
- 可扩展: 新增服务只需添加配置和路由

### Express 网关重构 (已完成)
- [x] 重构 `src/services/serviceProxy.ts`
  - 类型定义 (ServiceConfig)
  - 配置层 (SERVICES, 超时配置)
  - 核心代理 (`proxyToService`)
  - 路由按服务分组
  - 健康检查 (`/services/health`)
  - 导出类型和工具函数

### 使用方法
```bash
# 默认模式 (不启用服务)
python scripts/job_runner.py

# 启用服务模式
export QMCLAW_USE_SERVICES=true
python scripts/job_runner.py
```

### 已完成 - 服务测试
- [x] 所有服务可独立启动
- [x] LLM 服务健康检查 ✅
- [x] LLM 服务模型列表 ✅
- [x] TaskQueue 服务 ✅
- [x] Analysis 服务 ✅
- [x] Workflow 服务 ✅
- [x] Quantum 服务 ✅
- [x] 创建 `__main__.py` 支持 `python -m services.<name>`

### 已完成 - Express 网关集成
- [x] `src/services/serviceProxy.ts` - 统一服务代理
  - `/api/llm/*` - LLM 服务代理
  - `/api/quantum/*` - 测控服务代理
  - `/api/analysis/*` - 分析服务代理
  - `/api/agent/*` - Agent 服务代理
  - `/api/image/*` - 图像服务代理
  - `/api/workflow/*` - 工作流服务代理
  - `/api/tasks/*` - 任务队列代理
  - `/api/services/health` - 所有服务健康检查汇总
- [x] `src/services/apiClients.ts` - TypeScript 服务客户端
  - llmClient, quantumClient, analysisClient
  - agentClient, imageClient, workflowClient
  - taskQueueClient, healthClient
- [x] `src/index.ts` - 挂载服务代理中间件

---

## 2026-09-07: Hermes-Agent 集成

### 目标
- 将 Hermes-Agent 作为独立 Tab 页面集成到 QMClaw Web UI
- 先独立运行，后续考虑与现有 Agent 页面融合

### 架构
```
Browser → Express (:3002) → Python subprocess → Hermes AIAgent
```

### 已完成
- [x] `scripts/hermes_runner.py` - Hermes Runner 模块
  - `HermesRunner` 类 - AIAgent 封装
  - `QMClawMemoryProvider` - 会话隔离内存提供者
  - `QMClawToolWrapper` - QMClaw 量子控制工具包装器
  - `_run_hermes_chat()` - 非流式处理
  - `_run_hermes_chat_stream()` - SSE 流式处理
  - **自动模型检测** - 根据模型名自动选择 provider/base_url
    - MiniMax: `https://api.minimax.chat/v1`
    - Anthropic: `https://api.anthropic.com/v1`
    - OpenAI: `https://api.openai.com/v1`
    - DeepSeek: `https://api.deepseek.com/v1`
    - 默认: OpenRouter
- [x] `src/index.ts` - Express 路由
  - `POST /api/hermes/chat` - Hermes 聊天端点
  - `POST /api/hermes/chat/stream` - SSE 流式端点
  - `GET /api/hermes/models` - 可用模型列表（含 MiniMax M2.7）
- [x] `scripts/job_runner.py` - 后端动作处理
  - `hermes_chat` action handler
  - `hermes_chat_stream` action handler
- [x] `src/lib/api.ts` - 前端 API
  - `hermesChat()` - 非流式调用
  - `hermesChatStream()` - async generator SSE 流式处理
  - `hermesGetModels()` - 获取模型列表
- [x] `src/components/HermesChatPanel.tsx` - 前端 UI
  - 模型选择器（默认 MiniMax M2.7）
  - Toolset 切换 (web/vision/terminal/computer_use)
  - 流式响应显示
  - Thinking 块显示
  - 工具调用显示
- [x] `src/app/page.tsx` - Tab 集成
  - 添加 "hermes" Tab
  - 导入 HermesChatPanel 组件
  - 添加 Tab 按钮和内容渲染

### 模型配置
| 模型 | Provider | Base URL | 默认 |
|------|----------|----------|------|
| MiniMax-M2.7 | minimax | https://api.minimax.chat/v1 | ✅ |
| Claude Sonnet 4.6 | anthropic | https://api.anthropic.com/v1 | |
| Claude Opus 4.8 | anthropic | https://api.anthropic.com/v1 | |
| GPT-4o | openai | https://api.openai.com/v1 | |
| DeepSeek Chat | deepseek | https://api.deepseek.com/v1 | |

### 安全配置
- 默认禁用 terminal 和 computer_use toolsets
- 只启用 web 和 vision
- 会话隔离 (每会话独立 MemoryProvider)

### 模型配置集成
- [x] Hermes 使用 `model_configs.json` 系统配置
- [x] 自动从 `qmclaw-server/config/model_configs.json` 加载
- [x] 移除硬编码模型配置，handler 直接传递 model 参数
- [x] 支持 provider 自动检测和 API key 环境变量映射
- [x] 未指定模型时自动选择第一个 enabled 模型

### 待测试
- [ ] 后端服务启动测试
- [ ] Hermes Tab 页面功能测试
- [ ] 与现有 Agent 页面融合测试

---

## 2026-09-07: Agent 对话非阻塞修复

### 问题
- Agent 对话任务阻塞主进程，导致其他 API 请求超时
- SSE 流式传输端点 Promise 不resolve

### 解决方案
- 简化前端：`agent_chat` 使用非阻塞模式
- 任务通过 Python 后台线程 `_backend_queue` 执行，不阻塞 Express

### 已完成
- [x] `AgentChatPanel.tsx` - 移除复杂 SSE 处理，改用 `api.agentChat()`
- [x] `agent_chat` 通过 Python 后台队列执行，不阻塞主进程
- [x] README.md 添加 TODO 章节，记录 SSE 流式处理待办

### 待办（已记录到 README.md TODO）
- [ ] SSE 流式处理支持 - Python 后台线程 stdout 通信问题待解决

---

## 2026-09-04: 前端页面重构 - 统一左侧边栏

### 架构调整
- Tab 从 8 个精简为 4 个：experiments, workflow, agent, images
- jobs, services, agent-tools, memory 不再独立成 Tab
- services 状态移到 Header
- memory 作为 agent Tab 内的视图
- agent-tools 作为 agent Tab 内的子面板

### 已完成
- [x] Tab 定义更新为 4 个 (experiments | workflow | agent | images)
- [x] 移除 jobs, services, agent-tools, memory 独立 Tab
- [x] Header 添加 Services 状态区 (LabRAD/Ray/DataVault 状态点)
- [x] Sidebar 动态渲染框架
  - experiments: JobManager
  - workflow: WorkflowHistorySidebar
  - agent: ChatHistorySidebar
  - images: 空
- [x] Qubit 选择器添加搜索、参数设置⚙和删除✕按钮
- [x] Qubit 选择器只在非 images tab 显示
- [x] MemoryPanel.tsx 修复 TypeScript 错误 (colSpan)
- [x] ChatHistorySidebar.tsx 组件 (聊天会话历史列表)
- [x] WorkflowHistorySidebar.tsx 组件 (工作流历史)
- [x] AgentChatPanel.tsx 视图切换 (Chat | Memory | Tools)

### 待测试
- [ ] 前端页面测试（Tab 切换、Sidebar 渲染）
- [ ] Qubit 选择器功能测试
- [ ] Agent Tab 内视图切换测试

### 技术细节
- Sidebar 宽度: 220px
- Qubit 选择器固定在 Sidebar 底部
- ChatHistorySidebar 使用 api.listChatSessions() / api.deleteChatSession()
- WorkflowHistorySidebar 使用 api.listWorkflowRuns()

---

## 2026-09-03: 记忆与反思系统 MVP

### 已完成
- [x] `scripts/memory_store.py` - 记忆存储模块 (Episode + Skill CRUD)
- [x] `scripts/reflection_engine.py` - 反思引擎 (LLM驱动的反思分析)
- [x] `scripts/job_runner.py` 集成
  - `_get_memory_store()` / `_get_reflection_engine()` 延迟导入
  - `_run_agent_chat_with_memory()` 带记忆召回和反思的任务执行
  - `recall_memory` 工具注册
  - 记忆上下文注入到 ReAct prompt
  - 8个记忆 API handlers
- [x] `src/index.ts` - Express 路由 (8个 /api/agent/memory/* 端点)
- [x] `src/lib/api.ts` - 前端 API 客户端 (8个 memory* 方法)
- [x] `src/components/MemoryPanel.tsx` - 记忆中心 UI (3个 tab: 任务记录/技能库/统计)
- [x] `src/app/page.tsx` - 集成 MemoryPanel tab

### 待测试
- [ ] 后端服务启动测试
- [ ] 前端页面测试
- [ ] 完整任务执行 -> 记忆存储 -> 反思流程测试

### 技术细节
- 存储: JSON文件 (`config/memory/episodes/` 和 `config/memory/skills/`)
- 反思触发: 任务完成后自动调用 `reflect_after_task()`
- 技能提取: 反思报告建议创建时自动生成
- 记忆召回: 任务开始前 `recall_for_task()` 匹配相关技能和历史


---

## 2026-09-24  C 组真机接通（只读联调）落地

- **范围**：feat / fix / test / docs（分支 `feat/c-real-machine`，7 个提交）
- **改动**：
  1. LabRAD 连接参数外置：`get_labrad_params` / `labrad_connect` / `get_labrad_paths`，10 处 `labrad.connect()` 全部替换
  2. 修复离线↔在线切换：懒连接、清 `DEGRADED_PERMANENT`、`/mode` 真实返回 `connected`；`default_mode: auto`
  3. 写闸门 `actuation`（默认全关）+ `/health` `/status` 增补 `data_source` / `actuation_allowed` / `labrad_host:port`
  4. 执行超时标记 `stuck` 并拒绝后续下发（BACKLOG B4）
  5. agent 在线图源切换 + 工具层 `read_only` hint 透传
  6. 14 个单元测试 + `check_readonly_regression.py` + `check_labrad_link.py`
  7. `docs/REAL_MACHINE.md` 运维手册；CLAUDE.md 写闸门约定
- **文件**：
  - `services/common/config.py`、`quantum_service/{server,labrad_client}.py`、`analysis_service/server.py`
  - `agent_service/{adapter,server}.py`、`common/quantum_tools/execution.py`
  - `config/{system,fallback_config}.json`、`tests/test_real_machine_unit.py`
  - `docs/{REAL_MACHINE.md,deploy_diff_tools/check_labrad_link.py,BACKLOG.md,REAL_MACHINE_PLAN.md}`
  - 另有 gateway / workflow / scripts / backends 的 `labrad_connect` 接线
- **验证**：
  - 单元测试 14/14 PASS
  - 活体只读回归 ALL CHECKS PASSED（13/13）：闸门关、`set_params`→`read_only`、离线模拟不受闸门限制
  - `check_labrad_link.py`：3 项 FAIL 均为预期缺口（无 manager / 无 `~/.lqcs` / connect 连不上）
- **提交**：`fe73642` → `5a0fc90` → `209f494` → `a1a4e5e` → `8079bf3` → `2bd4dac` → `7b65bd3`

## 2026-09-24  服务启动与验收

- **范围**：chore（用户授权代启服务）
- **改动**：启动 Python 微服务（3003–3011）、Express（3002）、Next.js（8081）；跑验收脚本
- **验证**：活体只读回归 13/13 PASS；前端 200；`quantum: mode=auto data_source=offline actuation=False`
- **备注**：默认仍遵守约定 3「服务启停由人控制」；本次为用户明确授权例外

## 2026-09-24  约定：每次修改必须记录日志

- **范围**：docs（约定）
- **改动**：用户要求「以后每次修改都要记录进日志」。本文件为开发日志入口，**每次修改后追加一条**
- **文件**：`CLAUDE.md` 约定 5、`.claude/progress.md`
