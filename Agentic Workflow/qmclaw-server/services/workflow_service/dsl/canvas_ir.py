"""dsl.canvas_ir - IR v1 ⇄ 画布节点图（前端画布与 `.qwf` 之间的桥）。

背景：画布此前只能吃 JSON 存档，`.qwf` 只能跑文本。现在要求
"`.qwf` 里的每个工作流都能在画布上显示，画布导入导出都用 `.qwf`"，
于是需要一个双向转换：

    .qwf 文本 ──parse──▶ IR ──ir_to_canvas──▶ 画布节点图 ──canvas_to_ir──▶ IR ──printer──▶ .qwf

设计约束：
- **不丢信息**。画布节点的 `config` 只放"画布能编辑的字段"，其余 IR 字段
  （`note` / `when` / `effect` / `onError` / `export` …）一律放进 `config` 一起带走；
  画布表达不了的 op（`repeat` / `if` / `call`）保留成**灰节点**，
  原始 IR 语句挂在节点的 `dsl.stmt` 上，导出时原样放回。
- **可判定等价**。`canvas_to_ir` 之后会和原 IR 逐字段比对，
  不等价就返回差异清单，由前端决定是否阻止导出（见 `ir_diff`）。
- 纯函数：不读文件、不连服务。

坐标系约定：
- 节点 `id` = IR 的 `as` 别名（没有别名时用 `n1`、`n2`…）；
  导出时 `as` 用节点 `dsl.as` 保留，因此别名不会被 `id` 的清洗规则改写。
- 边：`{id, source, target, data: {type}}`，`type` 取
  `dependency` / `condition-pass` / `condition-fail`（与前端 store 一致）。
  IR → 画布时只生成 `dependency` 边（顺序边 + 引用边），
  因为 `condition-*` 边在 `ir_from_canvas` 里会变成 `when` 守卫，
  无中生有的守卫会破坏往返等价。
"""

import copy
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .compile import CANVAS_TO_OP, node_type_for
from .const import OP_LET
from .helpers import to_ref
from .ir import canonical
from .textdsl.printer import ir_to_qwf

# 画布表达不了的 op → 灰节点
GRAY_NODE_TYPE = "dsl_unknown"
GRAY_OPS = ("repeat", "if", "call")

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

# IR 字段 ← 画布 config 键（双向一致的字段表；两处都用它，避免各写各的）
_OP_CONFIG_FIELDS: Dict[str, List[Tuple[str, str]]] = {
    "measure": [("fn", "fn"), ("qubit", "qubit"), ("args", "args"),
                ("params", "params"), ("timeout", "timeout"),
                ("retries", "retries"), ("reportDataset", "reportDataset")],
    "read": [("kind", "kind"), ("dataset", "datasetId"), ("threshold", "threshold"),
             ("qubit", "qubit")],
    "analyze": [("dataset", "datasetId"), ("command", "command")],
    "assert": [("metric", "metric"), ("operator", "operator"),
               ("threshold", "threshold"), ("value", "value"),
               ("tolerance", "tolerance")],
    "say": [("template", "message"), ("channel", "channel")],
    "apply": [("params", "params")],
    "decide": [("mode", "mode"), ("intent", "intentPrompt"),
               ("systemPrompt", "systemPrompt"), ("temperature", "temperature"),
               ("maxTokens", "maxTokens")],
    "ask": [("messages", "messages"), ("model", "model"),
            ("temperature", "temperature"), ("prompt", "prompt")],
    "classify": [("image", "imagePath"), ("threshold", "reviewThreshold"),
                 ("labels", "labels")],
    "vlm": [("image", "imagePath"), ("family", "experimentFamily")],
}

# 语句级公共字段（IR 键 = config 键）
# timeout / retries 是所有 op 都有的公共字段（IR COMMON_FIELDS），必须一起搬，
# 否则 .qwf 里的超时设置在画布往返后会丢，导出就不再语义等价。
_COMMON_CONFIG_FIELDS = ("note", "when", "effect", "onError", "retryDelay",
                         "export", "timeout", "retries")


# ── 小工具 ────────────────────────────────────────────────────────────────────
def _safe_id(raw: str, used: set) -> str:
    name = str(raw or "").strip()
    if not name or not _SAFE_ID_RE.match(name):
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "n"
    candidate = name
    suffix = 1
    while candidate in used:
        suffix += 1
        candidate = f"{name}_{suffix}"
    used.add(candidate)
    return candidate


def _ref_names(value: Any) -> List[str]:
    """从一个值里收集 `$name` 引用里的 name（不递归进 dict 的键）。"""
    names: List[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            if node.startswith("$"):
                head = node[1:].split(".")[0].split("[")[0]
                if head and head != "env" and head not in names:
                    names.append(head)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for item in node.values():
                walk(item)

    walk(value)
    return names


def _stmt_refs(stmt: Dict[str, Any]) -> List[str]:
    """一条语句引用了哪些绑定（`as` 别名）。"""
    names: List[str] = []
    for key, value in stmt.items():
        if key in ("op", "as", "source", "body", "then", "else"):
            continue
        for name in _ref_names(value):
            if name not in names:
                names.append(name)
    return names


# ── IR → 画布 ────────────────────────────────────────────────────────────────
def _config_for(op: str, stmt: Dict[str, Any]) -> Dict[str, Any]:
    """IR 语句 → 画布 config（只放画布能编辑的字段）。"""
    config: Dict[str, Any] = {}
    for ir_key, cfg_key in _OP_CONFIG_FIELDS.get(op, []):
        if ir_key in stmt and stmt[ir_key] is not None:
            config[cfg_key] = copy.deepcopy(stmt[ir_key])
    for key in _COMMON_CONFIG_FIELDS:
        if key in stmt and stmt[key] is not None:
            config[key] = copy.deepcopy(stmt[key])
    if op == "assert":
        # 画布上 quality_gate 用布尔 failWorkflow，IR 用 onFail；两个都留一份。
        on_fail = stmt.get("onFail")
        config["failWorkflow"] = on_fail != "continue" if on_fail else True
    if op == "say":
        config.setdefault("message", stmt.get("template", stmt.get("message", "")))
    return config


def ir_to_canvas(ir: Dict[str, Any]) -> Dict[str, Any]:
    """IR v1 → 画布节点图。"""
    pipeline: List[Dict[str, Any]] = list(ir.get("pipeline") or [])
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    used_ids: set = set()
    alias_to_id: Dict[str, str] = {}
    unsupported: List[str] = []

    # let 语句合并成一个 context 节点（画布的 context 节点就是"一组变量"）
    lets = [s for s in pipeline if s.get("op") == OP_LET]
    let_node_id = None
    if lets:
        let_node_id = _safe_id("context", used_ids)
        variables = {}
        for stmt in lets:
            variables[str(stmt.get("as"))] = copy.deepcopy(stmt.get("value"))
        nodes.append({
            "id": let_node_id,
            "type": "context",
            "position": {"x": 0, "y": 0},
            "label": "context",
            "config": {"variables": variables},
            "dsl": {"op": OP_LET, "as": None, "stmt": None, "merged": True},
            "depends": [],
            "conditions": [],
        })

    ordered: List[Tuple[Dict[str, Any], str]] = []
    for stmt in pipeline:
        if stmt.get("op") == OP_LET:
            continue
        op = stmt.get("op", "")
        alias = stmt.get("as") or ""
        node_id = _safe_id(alias or f"n{len(ordered) + 1}", used_ids)
        alias_to_id[alias] = node_id if alias else node_id
        node_type = node_type_for(op, stmt)
        if node_type is None:
            node_type = GRAY_NODE_TYPE
            unsupported.append(op)
        depends = [alias_to_id[name] for name in _stmt_refs(stmt)
                   if name in alias_to_id]
        node = {
            "id": node_id,
            "type": node_type,
            "position": {"x": 0, "y": 140 * len(nodes)},
            "label": alias or op,
            "config": _config_for(op, stmt),
            "dsl": {"op": op, "as": alias or None, "stmt": copy.deepcopy(stmt)},
            "depends": depends,
            "conditions": [],
        }
        if node_type == GRAY_NODE_TYPE:
            node["config"] = {}
            node["label"] = f"{op}（画布暂不支持）"
        nodes.append(node)
        ordered.append((stmt, node_id))

    # 顺序边（保持 pipeline 顺序）+ 引用边（数据依赖）
    edge_index = 0
    prev_id = let_node_id
    for stmt, node_id in ordered:
        if prev_id and prev_id != node_id:
            edges.append({"id": f"e{edge_index}", "source": prev_id,
                          "target": node_id, "data": {"type": "dependency"}})
            edge_index += 1
        for dep in node["depends"]:
            if dep != node_id:
                edges.append({"id": f"e{edge_index}", "source": dep,
                              "target": node_id, "data": {"type": "dependency"}})
                edge_index += 1
        prev_id = node_id

    workflow = {
        "meta": copy.deepcopy(ir.get("meta") or {}),
        "env": copy.deepcopy(ir.get("env") or {}),
        "budget": copy.deepcopy(ir.get("budget") or {}),
        "policy": copy.deepcopy(ir.get("policy") or {}),
        "defaults": copy.deepcopy(ir.get("defaults") or {}),
        "context": copy.deepcopy(ir.get("context") or {}),
    }
    return {
        "id": ir.get("id"),
        "name": (ir.get("meta") or {}).get("name") or ir.get("id"),
        "workflow": workflow,
        "nodes": nodes,
        "edges": edges,
        "context": copy.deepcopy(ir.get("context") or {}),
        "ir": copy.deepcopy(ir),
        "qwf": ir_to_qwf(ir),
        "unsupported": sorted(set(unsupported)),
    }


# ── 画布 → IR ────────────────────────────────────────────────────────────────
def _topo_order(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_id = {n.get("id"): n for n in nodes}
    order: List[Dict[str, Any]] = []
    done: set = set()

    def visit(node: Dict[str, Any], stack: List[str]) -> None:
        nid = node.get("id")
        if nid in done:
            return
        if nid in stack:
            raise ValueError(f"画布存在环：{' → '.join(stack + [str(nid)])}")
        for dep in node.get("depends") or []:
            dep_node = by_id.get(dep)
            if dep_node is not None:
                visit(dep_node, stack + [nid])
        done.add(nid)
        order.append(node)

    for node in nodes:
        visit(node, [])
    return order


def _stmt_from_config(op: str, cfg: Dict[str, Any], node: Dict[str, Any]) -> Dict[str, Any]:
    """画布 config → IR 语句（只写 config 里真实存在的键）。"""
    stmt: Dict[str, Any] = {"op": op}
    for ir_key, cfg_key in _OP_CONFIG_FIELDS.get(op, []):
        if cfg_key in cfg and cfg[cfg_key] is not None:
            # 画布里的 `{{qubit}}` 写法要变成 IR 的 `$qubit`（否则执行期
            # 会把 "{{qubit}}" 当成字面量 qubit 名传给机器）。
            stmt[ir_key] = to_ref(copy.deepcopy(cfg[cfg_key]))
    for key in _COMMON_CONFIG_FIELDS:
        if key in cfg and cfg[key] is not None:
            stmt[key] = to_ref(copy.deepcopy(cfg[key]))
    if op == "assert":
        original = ((node.get("dsl") or {}).get("stmt") or {}).get("onFail")
        on_fail = cfg.get("onFail")
        fail_workflow = cfg.get("failWorkflow")
        if on_fail is not None:
            implied = "continue" if fail_workflow is False else "halt"
            if fail_workflow is not None and fail_workflow is not (on_fail != "continue"):
                on_fail = implied          # 画布上的开关被改动过 → 以开关为准
            stmt["onFail"] = on_fail
        elif fail_workflow is False:
            stmt["onFail"] = "continue"
        elif original is not None:
            stmt["onFail"] = original
    if op == "say":
        if "template" not in stmt and "message" in cfg:
            stmt["template"] = cfg["message"]
        if "channel" not in stmt and cfg.get("channel") is None:
            if node.get("type") == "notify":
                stmt["channel"] = "notify"
    if op == "decide":
        depends = node.get("depends") or []
        if depends:
            stmt["from"] = [f"${d}" for d in depends]
    return stmt


def _keep_unmapped_fields(op: str, stmt: Dict[str, Any],
                          preserved: Dict[str, Any]) -> Dict[str, Any]:
    """保留画布**编辑不到**的 IR 字段（防止导出时丢语义）。

    规则：
    - 字段表（`_OP_CONFIG_FIELDS`）和公共字段（`_COMMON_CONFIG_FIELDS`）
      一律以画布 `config` 为准 —— 用户在画布上删掉了就是删掉了；
    - 其余字段（例如某些 op 的扩展字段）画布上没有对应控件，
      从原始语句 `dsl.stmt` 原样带走。
    """
    if not preserved:
        return stmt
    mapped = {ir_key for ir_key, _ in _OP_CONFIG_FIELDS.get(op, [])}
    for key, value in preserved.items():
        if key in ("op", "as", "source"):
            continue
        if key in stmt or key in mapped or key in _COMMON_CONFIG_FIELDS:
            continue
        stmt[key] = copy.deepcopy(value)
    return stmt


def _merge_edges(nodes: List[Dict[str, Any]],
                edges: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """把边折回每个节点的 `depends`（存档/前端给的都是边，不是 depends）。

    只折 `dependency` 边：`condition-*` 边在 IR 里是 `when` 守卫，
    而画布节点没有"通过/失败"两个出口，折进来会凭空改变语义。
    """
    merged = [dict(n) for n in nodes]
    if not edges:
        for node in merged:
            node.setdefault("depends", [])
        return merged
    by_id = {n.get("id"): n for n in merged}
    for node in merged:
        node["depends"] = list(node.get("depends") or [])
    for edge in edges:
        src, dst = edge.get("source"), edge.get("target")
        if not src or not dst or dst not in by_id:
            continue
        kind = str(edge.get("type") or edge.get("data", {}).get("type") or "dependency")
        if kind.startswith("condition"):
            continue
        if src not in by_id[dst]["depends"]:
            by_id[dst]["depends"].append(src)
    return merged


def _alias_of(node: Dict[str, Any]) -> str:
    """节点在 IR 里的绑定名（`as` 别名，没有就用节点 id）。"""
    dsl = node.get("dsl") or {}
    return str(dsl.get("as") or node.get("id") or "")


def _upstream_dataset_ref(node: Dict[str, Any],
                          by_id: Dict[str, Any]) -> Optional[str]:
    """沿 depends 往上找第一个产出 dataset 的节点，返回 `$<alias>.dataset`。"""
    seen: set = set()
    queue = list(node.get("depends") or [])
    while queue:
        dep_id = queue.pop(0)
        if not dep_id or dep_id in seen:
            continue
        seen.add(dep_id)
        dep = by_id.get(dep_id) or {}
        dsl = dep.get("dsl") or {}
        if dep.get("type") == "experiment" or dsl.get("op") == "measure":
            alias = _alias_of(dep)
            if alias:
                return f"${alias}.dataset"
        queue.extend(dep.get("depends") or [])
    return None


def canvas_to_ir(doc: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """画布节点图 → (IR, 错误列表)。"""
    errors: List[str] = []
    nodes = list(doc.get("nodes") or [])
    nodes = _merge_edges(nodes, doc.get("edges"))
    try:
        order = _topo_order(nodes)
    except ValueError as exc:
        return {}, [str(exc)]

    pipeline: List[Dict[str, Any]] = []
    by_id = {n.get("id"): n for n in nodes}
    for node in order:
        node_type = node.get("type")
        cfg = node.get("config") or {}
        dsl = node.get("dsl") or {}
        if node_type == "context":
            for name, value in (cfg.get("variables") or {}).items():
                pipeline.append({"op": OP_LET, "as": name,
                                 "value": to_ref(copy.deepcopy(value))})
            continue
        op = CANVAS_TO_OP.get(node_type)
        if op is None:
            preserved = dsl.get("stmt")
            if preserved:
                stmt = copy.deepcopy(preserved)
            else:
                errors.append(f"节点 {node.get('id')}（{node_type}）没有可还原的 DSL 语句")
                continue
            if dsl.get("as"):
                stmt["as"] = dsl["as"]
            pipeline.append(stmt)
            continue
        stmt = _stmt_from_config(op, cfg, node)
        stmt = _keep_unmapped_fields(op, stmt, dsl.get("stmt") or {})
        alias = dsl.get("as")
        if alias:
            stmt["as"] = alias
        elif not dsl.get("stmt"):
            # 画布上新画的节点没有原始语句，用节点 id 当绑定名
            stmt["as"] = node.get("id")
        if op in ("read", "analyze") and not str(stmt.get("dataset") or "").strip():
            # 画布上的 contrast / analyze 不写 datasetId，执行期由服务端回溯
            # 依赖；IR 里必须写成显式引用，否则静态校验看不见数据来源。
            ref = _upstream_dataset_ref(node, by_id)
            if ref:
                stmt["dataset"] = ref
        pipeline.append(stmt)

    workflow = doc.get("workflow") or {}
    ir: Dict[str, Any] = {
        "ir_version": 1,
        "id": str(doc.get("id") or doc.get("name") or "workflow"),
        "meta": copy.deepcopy(workflow.get("meta") or {}),
        "env": copy.deepcopy(workflow.get("env") or {}),
        "context": copy.deepcopy(doc.get("context") or workflow.get("context") or {}),
        "defaults": copy.deepcopy(workflow.get("defaults") or {}),
        "budget": copy.deepcopy(workflow.get("budget") or {}),
        "pipeline": pipeline,
    }
    policy = workflow.get("policy")
    if isinstance(policy, dict) and policy:
        ir["policy"] = copy.deepcopy(policy)
    for key in ("meta", "env", "context", "defaults", "budget"):
        if not ir[key]:
            ir.pop(key)
    return ir, errors


# ── 等价检查 ─────────────────────────────────────────────────────────────────
def _numeric_equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 1e-9
    return a == b


def _strip(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k != "source"}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


# `meta` 里不参与语义判定的字段：版本号与时间戳每次保存都会变，
# 但它们不代表"工作流被改过"。
_VOLATILE_META = {"version", "updatedAt"}


def ir_equivalent(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    """两个 IR 是否语义等价（数值上 4 == 4.0 视为相等）。"""
    return not ir_diff(left, right)


def ir_diff(left: Dict[str, Any], right: Dict[str, Any]) -> List[str]:
    """逐字段比对两个 IR，返回人类可读的差异清单（空 = 等价）。"""
    diffs: List[str] = []

    def walk(a: Any, b: Any, path: str) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                if path == "$.meta" and key in _VOLATILE_META:
                    continue
                if key not in a:
                    diffs.append(f"{path}.{key}: 缺失 → {json.dumps(b[key], ensure_ascii=False)}")
                elif key not in b:
                    diffs.append(f"{path}.{key}: {json.dumps(a[key], ensure_ascii=False)} → 缺失")
                else:
                    walk(a[key], b[key], f"{path}.{key}")
            return
        if isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                diffs.append(f"{path}: 长度 {len(a)} → {len(b)}")
            for index in range(min(len(a), len(b))):
                walk(a[index], b[index], f"{path}[{index}]")
            return
        if not _numeric_equal(a, b):
            diffs.append(f"{path}: {json.dumps(a, ensure_ascii=False)} → "
                         f"{json.dumps(b, ensure_ascii=False)}")

    walk(_strip(left or {}), _strip(right or {}), "$")
    return diffs


def _normalize_for_compare(ir: Dict[str, Any]) -> Dict[str, Any]:
    """比较前的归一化：`let` 只是绑定，位置不影响语义。

    画布把所有 `let` 合并成一个 context 节点（放在最前），所以
    "画布 → IR → 画布 → IR" 两次 IR 的 `let` 位置可能不同 —— 比较前
    把 `let` 提到最前并按别名排序，避免误报。
    """
    normalized = copy.deepcopy(ir or {})
    pipeline = list(normalized.get("pipeline") or [])
    lets = sorted((s for s in pipeline if s.get("op") == OP_LET),
                  key=lambda s: str(s.get("as")))
    rest = [s for s in pipeline if s.get("op") != OP_LET]
    normalized["pipeline"] = lets + rest
    return normalized


def stability_diff(ir: Dict[str, Any]) -> List[str]:
    """画布 → IR → 画布 → IR 的稳定性检查。

    返回差异清单（空 = 稳定）。不稳定说明"这份 IR 用画布表达不出来"，
    导出会丢语义 —— 前端据此阻止导出。
    """
    try:
        canvas = ir_to_canvas(ir)
        again, errors = canvas_to_ir(canvas)
    except Exception as exc:                      # noqa: BLE001 - 兜底，别让导出崩掉
        return [f"IR→画布→IR 往返失败：{exc}"]
    if errors:
        return list(errors)
    return ir_diff(_normalize_for_compare(ir), _normalize_for_compare(again))


def canvas_to_qwf(doc: Dict[str, Any],
                  base_ir: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """画布 → `.qwf` 文本 + 等价性判定。

    两个"等价"概念，含义不同：

    - `stable` / `unstableDiff`：**导出保真度**。画布 → IR → 画布 → IR
      必须回到同一个 IR；不等价说明画布表达不了这份 IR，导出就会丢语义，
      前端应阻止导出。
    - `equivalent` / `diff`：相对 `base_ir`（画布打开时的原始 IR）的改动。
      用户正常编辑画布必然产生差异，这里只作提示，不阻止导出。
    """
    ir, errors = canvas_to_ir(doc)
    result: Dict[str, Any] = {"ok": not errors, "errors": errors}
    if errors:
        return result
    result["ir"] = ir
    result["qwf"] = ir_to_qwf(ir)
    result["hash"] = canonical(ir)
    unstable = stability_diff(ir)
    result["stable"] = not unstable
    result["unstableDiff"] = unstable
    if base_ir:
        diffs = ir_diff(base_ir, ir)
        result["equivalent"] = not diffs
        result["diff"] = diffs
        result["changed"] = bool(diffs)
    else:
        result["equivalent"] = True
        result["diff"] = []
        result["changed"] = False
    return result


__all__ = [
    "GRAY_NODE_TYPE", "GRAY_OPS",
    "ir_to_canvas", "canvas_to_ir", "canvas_to_qwf",
    "ir_equivalent", "ir_diff", "stability_diff",
]
