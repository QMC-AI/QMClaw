# 开发进展日志（progress.md）

> **规则（用户约定）**：以后每次修改都要记录进本日志。  
> 记录粒度：一次有意义的改动 / 一个提交节点一条；写清**改了什么、为什么、涉及文件、验证结果**。  
> 配套：`docs/BACKLOG.md`（待办）、`docs/GIT_HISTORY.md`（回滚）、`docs/CHANGES_VS_SOURCE.md`（能力清单）。

---

## 格式

```
## YYYY-MM-DD  <简短标题>
- **范围**：feat / fix / docs / test / chore
- **改动**：做了什么（1-3 条）
- **文件**：主要路径
- **验证**：如何验证的 / 结果
- **提交**：hash（如有）
```

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
  - 另有 8 处脚本/网关的 `labrad_connect` 接线（gateway / workflow / scripts / backends）
- **验证**：
  - 单元测试 14/14 PASS
  - 活体只读回归 ALL CHECKS PASSED（13/13）：闸门关、`set_params`→`read_only`、离线模拟不受闸门限制
  - `check_labrad_link.py`：3 项 FAIL 均为预期缺口（无 manager / 无 `~/.lqcs` / connect 连不上）
- **提交**：`fe73642` → `5a0fc90` → `209f494` → `a1a4e5e` → `8079bf3` → `2bd4dac` → `7b65bd3`

---

## 2026-09-24  服务启动与验收

- **范围**：chore（用户授权代启服务）
- **改动**：启动 Python 微服务（3003–3011）、Express（3002）、Next.js（8081）；跑验收脚本
- **验证**：见上「活体只读回归」；前端 200；`quantum: mode=auto data_source=offline actuation=False`
- **备注**：默认仍遵守约定 3「服务启停由人控制」；本次为用户明确授权例外

---

## 2026-09-24  约定：每次修改必须记录日志

- **范围**：docs（约定）
- **改动**：用户要求「以后每次修改都要记录进日志」。本文件为唯一开发日志入口
- **文件**：`CLAUDE.md`、`.claude/progress.md`
