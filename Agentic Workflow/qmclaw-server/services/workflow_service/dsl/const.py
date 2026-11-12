"""dsl.const - IR v1 的常量表：op 清单、字段白名单、错误码、单位表。

这里刻意不 import server.py：dsl 包必须能被 `validate` 单独使用（不启动服务、
不连硬件），所以所有与执行体共享的"约定"都以常量形式放在这里，由 server.py
反向引用（`from .dsl.const import SAFE_FN_RE as _SAFE_FN_RE` 之类）。
"""

import re

IR_VERSION = 1

# ── 与 server.py 保持一致的校验正则 ────────────────────────────────────────────
# 注意：server.py 里的 _SAFE_FN_RE / _SAFE_QUBIT_RE / _SAFE_ARGNAME_RE 以本文件为准，
# 两边若不一致，以下测试会失败：dsl_selftest.py::test_regex_parity
SAFE_FN_RE = re.compile(r"^sq\.[a-z][a-z0-9_]*$")
SAFE_QUBIT_RE = re.compile(r"^[A-Za-z0-9_%\-]+$")
SAFE_ARGNAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
SAFE_WORKFLOW_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# analyze 节点的 command 白名单（原来原样转发给 :3004 的 exec，等于任意代码执行）
ANALYZE_CMD_RE = re.compile(r"^\s*qter\.fitData\(")

# ── op 清单 ───────────────────────────────────────────────────────────────────
OP_LET = "let"
OP_MEASURE = "measure"
OP_READ = "read"
OP_ANALYZE = "analyze"
OP_DECIDE = "decide"
OP_ASK = "ask"
OP_CLASSIFY = "classify"
OP_VLM = "vlm"
OP_ASSERT = "assert"
OP_SAY = "say"
OP_APPLY = "apply"
OP_REPEAT = "repeat"
OP_IF = "if"
OP_CALL = "call"

SIMPLE_OPS = (
    OP_LET, OP_MEASURE, OP_READ, OP_ANALYZE, OP_DECIDE, OP_ASK,
    OP_CLASSIFY, OP_VLM, OP_ASSERT, OP_SAY, OP_APPLY,
)
CONTROL_OPS = (OP_REPEAT, OP_IF)
ALL_OPS = SIMPLE_OPS + CONTROL_OPS + (OP_CALL,)

# IR op → 现有解释器的节点 type（None 表示由 IrExecutor 本地处理，不建节点）
OP_TO_NODE = {
    OP_LET: None,                 # 直接写 env，不建节点
    OP_MEASURE: "experiment",
    OP_READ: "contrast",
    OP_ANALYZE: "analyze",
    OP_DECIDE: "decision",
    OP_ASK: "llm",
    OP_CLASSIFY: "image_classification",
    OP_VLM: "image_analysis",
    OP_ASSERT: "quality_gate",
    OP_SAY: "print",              # channel=notify 时改成 notify
    OP_APPLY: "adjust_params",
    OP_REPEAT: None,
    OP_IF: None,
    OP_CALL: None,
}

OP_EFFECT = {
    OP_LET: "compute",
    OP_MEASURE: "write",
    OP_READ: "read",
    OP_ANALYZE: "compute",
    OP_DECIDE: "external",
    OP_ASK: "external",
    OP_CLASSIFY: "compute",
    OP_VLM: "external",
    OP_ASSERT: "compute",
    OP_SAY: "compute",
    OP_APPLY: "compute",
    OP_REPEAT: "compute",
    OP_IF: "compute",
    OP_CALL: "write",
}

# ── 字段白名单（S8：未知字段默认报错，防拼写错误静默失效） ──────────────────────
COMMON_FIELDS = frozenset({
    "op", "as", "when", "onError", "retries", "retryDelay",
    "timeout", "effect", "note", "export", "source",
})

OP_FIELDS = {
    OP_LET: {"value"},
    OP_MEASURE: {"fn", "qubit", "args", "params", "reportDataset"},
    OP_READ: {"kind", "dataset", "threshold", "qubit"},
    OP_ANALYZE: {"dataset", "command"},
    OP_DECIDE: {"from", "mode", "intent", "systemPrompt", "temperature", "maxTokens",
                "qubit", "choices"},
    OP_ASK: {"messages", "model", "temperature", "prompt", "system", "maxTokens",
             "question", "options", "blocking"},
    OP_CLASSIFY: {"image", "threshold", "labels"},
    OP_VLM: {"image", "family", "question"},
    OP_ASSERT: {"metric", "operator", "threshold", "value", "onFail", "tolerance"},
    OP_SAY: {"template", "message", "channel"},
    OP_APPLY: {"params"},
    OP_REPEAT: {"maxRounds", "stopWhen", "body"},
    OP_IF: {"cond", "then", "else"},
    OP_CALL: {"workflow", "args"},
}

# 结构语句的子块字段（遍历/规范化/校验共用）
OP_BODY_KEY = {
    OP_REPEAT: ("body",),
    OP_IF: ("then", "else"),
}

TOP_FIELDS = frozenset({
    "ir_version", "id", "meta", "env", "defaults", "budget", "pipeline", "context",
    # 执行策略头（与 budget 同级）：目前只有 readOnly，声明"这条流水线不该改任何参数"。
    "policy",
    # 目标契约（v1.3）：{objective, success{metric,operator,value}, maxRounds, onAchieved}。
    # 是语法糖，normalize 时 lowering 成 repeat + if/else + assert（见 dsl/goal.py）。
    "goal",
    # 写幅度上界（v1.3）：{"f10": 0.05, "PiGate.amp": 0.2}，供 dsl/manifest.py 生成写清单。
    # 缺失的键没有上界 ⇒ 不可代理放行（E_MANIFEST_UNBOUNDED）。
    "bounds",
    # ── IR v2 预留（见 docs/BACKEND_ADAPTERS.md）──────────────────────────────
    # 现在允许出现但不参与执行：写进 IR 也不会改变行为，只是让 v2 文档先能过校验。
    "backend", "device", "requirements",
})

POLICY_FIELDS = frozenset({"readOnly"})

BUDGET_FIELDS = frozenset({"maxWallSeconds", "maxWrites", "maxDatasets", "maxLlmCalls",
                           "maxScanPoints"})

DEFAULT_BUDGET = {
    "maxWallSeconds": 3600,
    "maxWrites": None,       # 真机模式必填（见 S7 / E_BUDGET_REQUIRED）
    "maxLlmCalls": 20,
    # 单条实验允许的最大扫描点数。None = 未声明（不拦），但执行时仍会估算并告警。
    # 机器级硬上限另见环境变量 QMCLAW_MAX_SCAN_POINTS。
    "maxScanPoints": None,
}

# ── goal 块（v1.3 目标契约） ──────────────────────────────────────────────────
# 顶层 `goal` 是语法糖，normalize 时 lowering 成 repeat + if/else + assert
# （见 dsl/goal.py 与 docs/DSL_DESIGN.md §13.2）。
GOAL_FIELDS = frozenset({"objective", "success", "maxRounds", "onAchieved"})
GOAL_SUCCESS_FIELDS = frozenset({"metric", "operator", "value", "tolerance", "criteria", "level"})

# ── 比较运算符 ────────────────────────────────────────────────────────────────
OPS = {
    ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: abs(float(a) - float(b)) < 1e-9,
    "!=": lambda a, b: abs(float(a) - float(b)) >= 1e-9,
}

# ── 单位表（S3 量纲校验） ─────────────────────────────────────────────────────
# 键名永不改；单位由后缀声明，`assert` 时两侧必须同量纲。
UNIT_SUFFIXES = {
    "_us": "time",
    "_ns": "time",
    "_ms": "time",
    "_ghz": "freq",
    "_mhz": "freq",
    "_hz": "freq",
    "_deg": "angle",
}
DIMENSIONLESS = {
    "contrast", "snr", "fidelity", "f0", "f1", "r_squared",
    "separation", "pi_amp", "confidence", "visible", "success_rate",
}

DURATION_UNITS = {"s": 1.0, "ms": 1e-3, "m": 60.0, "min": 60.0, "h": 3600.0}

# ── dry-run 合成值 ────────────────────────────────────────────────────────────
# 只用于 --dry-run synthetic：让控制流能走通，不代表真机预期值。
FN_METRICS = {
    "sq.iqraw": {"contrast": 0.72, "snr": 3.1},
    "sq.piamp": {"pi_amp": 0.41},
    "sq.pidf": {},
    "sq.alpha": {"alpha": 0.83},
    "sq.t1": {"T1_us": 62.0, "R_squared": 0.97},
    "sq.ramsey": {"T2star_us": 8.4, "R_squared": 0.95},
    "sq.echo": {"T2_us": 12.1},
    "sq.rabi": {"pi_amp": 0.42},
    "sq.spec": {"f10_ghz": 4.812},
}

# ── 错误码（附录 B） ─────────────────────────────────────────────────────────
ERROR_CODES = {
    "E_IR_VERSION": "ir_version 不支持",
    "E_UNKNOWN_FIELD": "语句含未知字段",
    "E_UNKNOWN_OP": "未知 op",
    "E_DUPLICATE_BINDING": "同一作用域内 as 重名",
    "E_UNDEFINED_REF": "引用未绑定或引用后续语句",
    "E_TYPE_MISMATCH": "引用字段类型不适用于比较",
    "E_DIMENSION": "比较两侧量纲不一致",
    "E_BUDGET_REQUIRED": "含 write 但未声明 maxWrites",
    "E_UNREACHABLE": "分支常量假 / maxRounds 非法",
    "E_BAD_FN": "fn 不匹配 ^sq\\.[a-z][a-z0-9_]*$",
    "E_BAD_QUBIT": "qubit 不匹配 ^[A-Za-z0-9_%-]+$",
    "E_BAD_COMMAND": "analyze 的 command 不在白名单",
    "E_NOT_IMPLEMENTED": "本版本不执行该 op",
    "E_LEGACY_INTERP": "仍含 {{}} 旧式插值（警告）",
    "E_BAD_SYNTAX": "文本 DSL 语法错误",
    "E_SCHEMA": "IR 结构不合法",
    # ── 后端可移植性（v2 预留）────────────────────────────────────────────────
    "E_CAPABILITY_MISSING": "目标后端未声明 IR 要求的能力",
    "E_VENDOR_LEAK": "IR 中出现厂商符号（v2 为 error，v1 记为 warning W_VENDOR_LEAK）",
    "E_SCAN_POINTS": "budget.maxScanPoints 不是正数，或单条实验估算点数超过该预算",
    # ── 目标契约与授权（v1.3）────────────────────────────────────────────────
    "E_GOAL_OBJECTIVE": "goal 块字段非法或 objective 为空",
    "E_GOAL_METRIC": "goal.success 的 metric/operator/value 非法",
    "E_GOAL_BUDGET_MISMATCH": "goal.maxRounds 放大后超出 budget（采集次数或写次数）",
    "E_MANIFEST_UNBOUNDED": "动态分支的写集合不可穷举，无法生成写清单",
}

# ── 节点状态（与 server.NodeStatus 对齐，避免 dsl 依赖 server） ─────────────────
ST_PENDING = "pending"
ST_RUNNING = "running"
ST_COMPLETED = "completed"
ST_FAILED = "failed"
ST_CANCELLED = "cancelled"
ST_SKIPPED = "skipped"
