# 真机接通说明（只读联调）

> 本文是运维/联调手册。设计见 `docs/REAL_MACHINE_PLAN.md`，待办登记见 `docs/BACKLOG.md` C 组。
> **当前阶段：只读联调。写闸门默认关闭，禁止向真机下发实验或改参。**

## 一、拓扑

```
[本机 QMClaw]  ──TCP 7682──►  [远程测控机 LabRAD manager]
     │                              │
     │                              ├─ DataVault
     │                              ├─ lqms / lqcs
     │                              └─ Ray cluster（可选）
     └─ 10 个微服务 + Next.js 前端
```

本机是**客户端**，不托管 LabRAD manager（因此不需要本机 Java，除非做协议层本地验证）。

## 二、配置键怎么填

### 2.1 LabRAD 连接（`config/system.json`）

```json
"lqcs": {
  "connection": { "host": "<测控机 IP 或主机名>", "port": 7682, "timeout": 30 },
  "paths": {
    "measure_scripts": null,
    "backend_dir": null
  }
}
```

| 键 | 说明 | 覆盖顺序 |
|---|---|---|
| `connection.host` | 测控机地址 | `LABRADHOST` env → 本键 → `localhost` |
| `connection.port` | LabRAD manager 端口 | `LABRADPORT` env → 本键 → `7682` |
| `connection.timeout` | 连接超时（秒） | `LABRADTIMEOUT` env → 本键 → `30` |
| `paths.measure_scripts` | 实验脚本目录 | `MEASURE_SCRIPTS` env → 本键 → 按仓库根推导 |
| `paths.backend_dir` | 后端目录 | `BACKEND_DIR` env → 本键 → 向上搜索 `measure_scripts` |

临时切地址不必改文件：

```powershell
$env:LABRADHOST = "10.0.0.5"
$env:LABRADPORT = "7682"
```

### 2.2 数据来源模式（`config/fallback_config.json`）

```json
"quantum_service": {
  "offline_mode": { "default_mode": "auto" },
  "actuation": { "allow_execute": false, "allow_set_params": false }
}
```

| 模式 | 含义 |
|---|---|
| `online` | 强制 LabRAD |
| `offline` | 强制离线数据 |
| `auto`（默认） | LabRAD 可达则在线，否则干净回落离线 |

运行时切换：`POST /mode` `{"mode": "online"|"offline"|"auto"}`。  
响应带 `connected` 与失败原因，**不再静默假成功**。

### 2.3 写闸门（`actuation`）

| 键 | 管什么 | env 覆盖 |
|---|---|---|
| `allow_execute` | `POST /execute`、agent 实验工具 | `QMCLAW_ALLOW_EXECUTE=1` |
| `allow_set_params` | `POST /qubit/set_params` | `QMCLAW_ALLOW_SET_PARAMS=1` |

**默认全 `false`。** 服务端强制，不依赖 LLM 自觉。离线模拟不受闸门限制（不碰硬件）。

### 2.4 其它前置

| 项 | 说明 |
|---|---|
| `~/.lqcs` | lqcs 按用户配置（拓扑真相来源）。**优先从测控机整目录拷贝** |
| TEMP 可写 | lqms `.pyd` 导入需要，否则在线分支启动即失败 |
| DataVault 会话 | `config/session.json` 的路径必须在**远程** DataVault 上存在 |
| lqms/lqcs 版本 | 以测控机为准；本机保持 `lqms 0.3.1`（见 BACKLOG E1） |

## 三、启动顺序

1. 确认测控机 LabRAD manager 已运行，`~/.lqcs` 已就位
2. 填好 `system.json` 的 `connection`（或设 env）
3. **人工启动**后端与前端（agent 不代启）：
   - `python -m scripts.start_all_services --all`（或按现有 run-dev.cmd）
   - Next.js `:8081`
4. 自检：

```powershell
D:\semester4\deploy\qmclaw\qmclaw\python.exe docs\deploy_diff_tools\check_labrad_link.py
D:\semester4\deploy\qmclaw\qmclaw\python.exe "Agentic Workflow\qmclaw-server\scripts\check_readonly_regression.py"
```

5. 看 `/health` 是否带 `data_source: "labrad"`、`actuation_allowed: false`

## 四、从只读到可写的提权步骤（须人工执行并记录）

> 每次打开写权限都是**可审计事件**。禁止在未确认现场安全时打开。

1. **完成 BACKLOG B4 相关确认**：上一次执行若曾 timeout，先确认现场已安全
2. **确认硬件归属**：当前联调窗口内只有你有权下发指令
3. 翻开闸门（二选一，推荐改配置文件以便留痕）：
   - `fallback_config.json` → `quantum_service.actuation.allow_execute: true`（`allow_set_params` 同理）
   - 或临时 `$env:QMCLAW_ALLOW_EXECUTE = "1"`
4. **重启受影响的 Python 服务**（不热重载）
5. 在 `docs/BACKLOG.md` 或实验日志里**记录**：时间、操作人、打开范围、预计关闭时间
6. 用完立即改回 `false` 并重启服务

## 五、故障排查

| 症状 | 可能原因 | 处理 |
|---|---|---|
| `/mode` 切 online 后 `connected: false` | host/port 错、防火墙、manager 未起 | 看响应 `error`；跑 `check_labrad_link.py` 看卡在哪一步 |
| TCP 通但 `labrad.connect` 失败 | TLS 模式不匹配、密码 | 对齐测控机 manager 配置 |
| `initialize` 卡在 `switchSession` | DataVault 会话不存在 | 改 `session.json` 为远程已存在的路径 |
| `FileNotFoundError: temporary directory` | TEMP 不可写 | 修好 TEMP 再启动 |
| `~/.lqcs` 缺失 / `PermissionError` | 未从测控机拷贝 | 整目录拷到用户主目录 |
| `/execute` 返回 `read_only` | **预期**（闸门关闭） | 只读阶段忽略；需写时按第四节提权 |
| `/execute` 返回 `stuck` | 上次超时后工作线程可能仍在跑 | 确认现场安全后 `POST /connect {"reset_stuck": true}` 或重启服务 |
| 离线结果与改前不一致 | 误动了离线路径 | 用 `git revert` 回退对应提交 |

## 六、健康字段速查

`GET /health` 与 `GET /status` 关键字段：

| 字段 | 含义 |
|---|---|
| `mode` | online / offline / auto |
| `data_source` | `labrad` / `offline` / `fallback` —— **数据来自哪里** |
| `labrad_connected` | 是否已连上 LabRAD |
| `labrad_host` / `labrad_port` | 实际连接目标 |
| `actuation_allowed` | 是否允许任意写 |
| `allow_execute` / `allow_set_params` | 两个独立闸门 |
| `stuck` / `stuck_reason` | 是否因超时卡死 |
| `ray_ready` | Ray/DeviceManager 是否就绪（只读不依赖） |

## 七、明确不在本期

- 不打开写权限、不跑真机实验
- 不改离线模拟器、不升级 `lqms`
- 无硬件时无法验证真实比特参数与数据质量（需硬件恢复后单独验收）
