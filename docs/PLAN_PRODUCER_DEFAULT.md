# `/execute` 默认 producer 化：契约方案（阶段 16 提案）

> 状态：**待用户确认**。阶段 15（异步执行补齐 RunRecord）已落地并提交 `35a488e`，
> 本文只描述"把异步设为默认"这一步要动的契约面。
> 相关：`docs/ARCHITECTURE_ROADMAP.md` §2.1 / §4、`docs/OPERATIONS.md` §3.18。

## 1. 为什么还要一步

阶段 15 之后，异步执行已经**可审计**（queued → 完成两条同 `run_id`，读侧去重）。
但默认仍是同步：

- 同步请求会占住一个 HTTP 连接直到通道 lane 跑完（长实验 = 几分钟），
  客户端超时/SIGKILL 后服务端照跑，调用方拿不到 `job_id` 去"放弃"或"继续等"；
- Express 代理与前端都按"请求返回即结果"写，长实验只能靠加大超时；
- 生产者的语义应该是"提交即返回、结果另取"，这也是 §2.1 的原始目标。

## 2. 目标契约（`POST :3003/execute`）

| 请求 | 行为 |
|---|---|
| `{"code": ...}`（**默认**） | 立即返回 `202`-语义：`{status:"queued", job_id, run_id, trace_id, poll, queue_position}` |
| `{"code": ..., "wait": true}` | 旧同步行为（等 lane 跑完返回结果），供脚本/调试**显式**选择 |
| `{"code": ..., "async": true}` | 同默认（保留兼容） |
| `{"code": ..., "async": false}` | 同 `wait:true`（保留兼容） |

- 失败语义不变：写闸门/治理/卡死/降级这些**在提交时就能判定**的错误仍然同步返回
  （`read_only` / `stuck` / `degraded` / `not_connected` / `task_required`），
  不会先回 `queued` 再失败 —— 否则调用方要区分"排队后被拒"与"运行时失败"。
- `GET /jobs/{id}` 回包补 `run_id`、`trace_id`（阶段 15 已存到 job 上）。
- 兼容开关：`config/fallback_config.json` 的 `quantum_service.actuation.execute_default`
  = `"sync"` | `"async"`（默认 `"async"`），环境变量 `QMCLAW_EXECUTE_DEFAULT` 优先；
  出问题时改回 `"sync"` 即恢复旧行为，**不用回滚代码**。

## 3. 消费方改造清单（同一批提交，缺一不可）

| 位置 | 现在 | 改成 |
|---|---|---|
| `services/workflow_service/dsl/backends/lqcs.py` | 直连 `/execute` 同步等结果 | 提交后按 `poll` 轮询 `/jobs/{id}`，把完成结果当同步结果用；超时用 `POST /jobs/{id}/abandon` |
| `services/agent_service/adapter.py:279` | 同步等结果 | 同上（封装成一个 `execute_async()` 小工具，三处复用） |
| `services/client_proxy.py:118` | 同步 | 同上（默认带 `wait:true` 也可以，但长实验不该走它） |
| `Agentic Workflow/qmclaw-server/src/index.ts` 的 execute 代理 | 透传同步 | 透传 `wait` 字段即可；前端不需要立即改（它走 workflow/agent） |
| `qmclaw-web/src/app/page.tsx` 的"直接执行"按钮 | 等结果 | 有 job 就显示"排队/运行中"并轮询；**这一项需要前端一起改，属于独立授权** |

## 4. 分两步走（建议）

- **16a（纯后端，本方案主体）**：`execute_default` 开关 + 默认 async + 三个 Python 消费方
  改造 + `GET /jobs/{id}` 补字段 + 自检 `_phase16_check.py`（桩服务模拟 queued→完成）。
  验收：离线自检 + `:3003` 重启后跑一次 `wait:true` 与一次默认请求各一条 iqraw（约 3 s）。
- **16b（前端契约，需单独确认）**：前端"直接执行"改成排队 + 轮询 UI。

## 5. 风险与回退

| 风险 | 处置 |
|---|---|
| 消费方漏改 → 拿到 `{status:"queued"}` 当结果用 | 三个 Python 消费方在同一提交里改完；`_phase16_check.py` 扫源码里所有 `/execute` 调用点必须带 `wait` 或轮询 |
| 前端旧代码把 `queued` 当失败 | 前端目前只经 workflow/agent，`page.tsx` 的直发按钮在 16b 一起改；16a 期间它走的是 agent（已改造） |
| 长实验 + 客户端重启 → 结果没人取 | 审计记录已落盘（阶段 15），`GET /qubit/runs` 仍可查 |
| 需要回退 | `execute_default="sync"`（配置热生效） |

## 6. 验证清单

1. `POST /execute {"code":"sq.iqraw(q4ld3, do_plot=False, update=False)"}` → 立刻 `< 1 s` 返回 `queued`；
   `GET /jobs/{job}` 轮询到 `completed`，`run_id` 在 `/qubit/runs` 里能看到**唯一一条**记录且带 `dataset_ids`。
2. `POST /execute {..., "wait": true}` → 旧行为（约 3 s 返回 success）。
3. 写闸门关掉时：默认请求**同步**返回 `read_only`（不是 queued）。
4. 离线自检 36 套全绿。
