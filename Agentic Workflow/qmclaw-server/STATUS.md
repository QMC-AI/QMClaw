# QMClaw 项目状态

## 当前状态

![Architecture](https://img.shields.io/badge/architecture-microservices-blue)
![Status](https://img.shields.io/badge/status-in%20development-green)

## 架构概览

```
┌─────────────────────────────────────────────────────────────────┐
│                      前端 (Browser / :3001)                      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Express API 网关 (:3002)                         │
│              统一入口 · 路由代理 · 跨域处理                        │
└─────────────────────────────────────────────────────────────────┘
        │              │              │              │
        ▼              ▼              ▼              ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│   quantum   │  │  analysis   │  │    agent    │  │     llm     │
│   (:3003)   │  │   (:3004)  │  │   (:3005)   │  │   (:3006)   │
│  LabRAD连接  │  │  DataLab   │  │  ReAct推理  │  │  多Provider │
│  实验执行    │  │  绘图分析  │  │  Hermes助手 │  │  LLM推理    │
└─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘
                                                      │
                                                      ▼
                                              ┌─────────────┐
                                              │    image    │
                                              │   (:3007)   │
                                              │ PyTorch推理 │
                                              │ 图像分类    │
                                              └─────────────┘
```

## 服务状态

| 服务 | 端口 | 功能 | 状态 | 说明 |
|------|------|------|------|------|
| quantum | 3003 | LabRAD 连接、实验执行、Qubit 管理、DataVault | ✅ 完成 | 核心测控服务 |
| analysis | 3004 | DataLab 绘图、统计分析 | ✅ 完成 | 数据分析 |
| agent | 3005 | QuantumAgent、ReAct 推理、工具注册 | ✅ 完成 | Agent 推理 |
| llm | 3006 | 多 Provider 统一接口 | ✅ 完成 | LLM 推理 |
| image | 3007 | PyTorch/ONNX 推理、模型训练 | ✅ 完成 | 图像分类 |
| workflow | 3008 | 节点调度、依赖解析 | ⏳ 待实现 | 工作流编排 |
| task_queue | 3009 | 任务调度、重试机制 | ⏳ 待实现 | 任务队列 |

## 已迁移功能

### Express 网关 (微服务模式)

- [x] `/api/experiments/*` → analysis 服务
- [x] `/api/classify/*` → image 服务
- [x] `/api/agent/*` → agent 服务
- [x] `/api/hermes/*` → hermes 服务
- [x] `/sessions/*` → quantum 服务
- [x] `/qubits/*` → quantum 服务
- [x] `/datasets/*` → quantum 服务
- [x] `/hardware/*` → quantum 服务

### 前端组件

- [x] JobsPanel → 量子服务 API
- [x] DatasetBrowser → 量子服务 API
- [x] SessionManager → 量子服务 API

## 待完成

### 微服务实现

- [ ] workflow_service 完整实现
- [ ] task_queue_service 完整实现
- [ ] 服务注册与发现机制
- [ ] 服务健康检查聚合
- [ ] 断路器保护机制完善

### 代码清理

- [ ] legacy 代码移除 (job_runner.py)
- [ ] 统一日志格式
- [ ] 错误处理规范化
- [ ] API 文档生成 (OpenAPI)

### 运维

- [ ] Docker Compose 部署
- [ ] 服务监控 (Prometheus)
- [ ] 日志聚合 (ELK)
- [ ] 单元测试覆盖

## 已知问题

| 问题 | 状态 | 说明 |
|------|------|------|
| Ray Device Manager actor 初始化 | ⚠️ | 需确保 Ray 集群正确启动 |
| LabRAD 连接稳定性 | ⚠️ | 长时运行需心跳机制 |
| legacy/job_runner.py 仍存在 | 🔨 | 迁移后待删除 |

## 技术栈

| 层级 | 技术 | 版本 |
|------|------|------|
| 前端 | Next.js + React | 14+ |
| 网关 | Express + TypeScript | 5+ |
| 测控 | Python + LabRAD | 3.x |
| 数据分析 | Python + DataLab | - |
| Agent | Python + ReAct | - |
| LLM | Python + 多 Provider | - |
| 图像 | Python + PyTorch | 2.x |

## 配置

```bash
# 微服务模式 (默认)
QMCLAW_USE_SERVICES=true

# Legacy 模式 (不推荐)
QMCLAW_USE_SERVICES=false
```

配置文件: `config/services.json`

---

*最后更新: 2026-09-15*
