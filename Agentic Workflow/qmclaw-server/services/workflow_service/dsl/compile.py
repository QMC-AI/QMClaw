"""dsl.compile - 语句 → 现有解释器节点（以及画布节点图 → IR）。

这里是整个 DSL 的"适配层"，只做形状转换，不做执行。之所以集中在一处，是因为
现有 14 种节点的配置键五花八门（`datasetId` vs `dataset`、`imagePath` vs `image`、
`intentPrompt` vs `intent`），散落在执行器里迟早对不上。
"""

from typing import Any, Callable, Dict, List, Optional

from .const import (
    OP_ANALYZE,
    OP_APPLY,
    OP_ASK,
    OP_ASSERT,
    OP_CLASSIFY,
    OP_DECIDE,
    OP_LET,
    OP_MEASURE,
    OP_READ,
    OP_SAY,
    OP_VLM,
    OP_TO_NODE,
)

Identity = Callable[[Any], Any]


def _identity(value: Any) -> Any:
    return value


def build_node(stmt: Dict[str, Any], name: str,
               resolve: Identity = _identity,
               env: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """把一条 IR 语句编译成 {type, config, depends}。

    resolve: 把 `$ref` 解成具体值（运行期由 IrExecutor 提供；编译期用恒等）。
    env: 环境绑定，用于 `qubit` 缺省时取 `$env.qubit`。
    """
    op = stmt.get("op")
    env = env or {}
    if op == OP_LET:
        return None

    base: Dict[str, Any] = {}
    for key in ("timeout", "retries", "retryDelay"):
        if stmt.get(key) is not None:
            base[key] = stmt[key]

    if op == OP_MEASURE:
        args = stmt.get("args")
        if args is None:
            args = stmt.get("params")
        config = dict(base)
        config.update({
            "fn": stmt.get("fn"),
            "qubit": resolve(stmt.get("qubit", "$env.qubit")),
            "params": resolve(args or {}),
            "do_plot": False,          # 服务进程里画图会把微服务卡死（见 :664 注释）
            "reportDataset": stmt.get("reportDataset", True),
        })
        return {"type": "experiment", "config": config,
                "depends": _ref_names(stmt, ("qubit", "args", "params"))}

    if op == OP_READ:
        config = dict(base)
        config.update({
            "datasetId": _as_dataset(resolve(stmt.get("dataset"))),
            "qubit": resolve(stmt.get("qubit", "$env.qubit")),
            "kind": stmt.get("kind", "IQraw"),
            "threshold": stmt.get("threshold", 0.2),
        })
        return {"type": "contrast", "config": config,
                "depends": _ref_names(stmt, ("dataset", "qubit"))}

    if op == OP_ANALYZE:
        config = dict(base)
        command = str(stmt.get("command") or "qter.fitData(do_plot=False)")
        config.update({
            "datasetId": _as_dataset(resolve(stmt.get("dataset"))),
            "command": command,
        })
        return {"type": "analyze", "config": config, "depends": _ref_names(stmt, ("dataset",))}

    if op == OP_ASSERT:
        metric = stmt.get("metric")
        left = resolve(metric) if isinstance(metric, str) and metric.startswith("$") else resolve(
            stmt.get("value"))
        right = resolve(stmt.get("threshold", stmt.get("value")))
        config = dict(base)
        config.update({
            "metric": _metric_label(metric),
            "value": left,
            "threshold": right,
            "operator": stmt.get("operator", ">="),
            "failWorkflow": stmt.get("onFail", "halt") != "continue",
        })
        return {"type": "quality_gate", "config": config, "depends": _ref_names(stmt, ("metric",))}

    if op == OP_SAY:
        channel = stmt.get("channel", "log")
        # 自由文本要先解析 `${...}` / `$ref`：legacy 的 print/notify 只认 `{{}}`，
        # 不解析就会把 "hello ${n}" 原样打出来。
        template = resolve(stmt.get("template", stmt.get("message", "")))
        node_type = "notify" if channel == "notify" else "print"
        return {"type": node_type, "config": dict(base, message=template), "depends": []}

    if op == OP_APPLY:
        return {"type": "adjust_params",
                "config": dict(base, params=resolve(stmt.get("params") or {})),
                "depends": []}

    if op == OP_DECIDE:
        config = dict(base)
        config.update({
            "mode": stmt.get("mode", "analysis"),
            "intentPrompt": resolve(stmt.get("intent", stmt.get("intentPrompt", ""))),
            "systemPrompt": resolve(stmt.get("systemPrompt", "")),
            "temperature": stmt.get("temperature", 0.3),
            "maxTokens": stmt.get("maxTokens", 1200),
            "choices": resolve(stmt.get("choices") or []),
        })
        return {"type": "decision", "config": config, "depends": _from_names(stmt)}

    if op == OP_ASK:
        question = resolve(stmt.get("question") or "")
        options = resolve(stmt.get("options") or [])
        if question:
            # `ask` 带 question → 目标歧义澄清，直接向人提问（needs_human）。
            config = dict(base)
            config.update({
                "question": question,
                "options": options,
                "blocking": bool(stmt.get("blocking", True)),
            })
            return {"type": "ask", "config": config,
                    "depends": _ref_names(stmt, ("question",))}
        config = dict(base)
        config.update({
            "messages": resolve(stmt.get("messages") or (
                [{"role": "user", "content": stmt.get("prompt", "")}]
                if stmt.get("prompt") else []
            )),
            "model": stmt.get("model", "minimax"),
            "temperature": stmt.get("temperature", 0.7),
        })
        return {"type": "llm", "config": config, "depends": _ref_names(stmt, ("messages", "prompt"))}

    if op == OP_CLASSIFY:
        config = dict(base)
        config.update({
            "imagePath": resolve(stmt.get("image")),
            "reviewThreshold": stmt.get("threshold", 0.75),
        })
        return {"type": "image_classification", "config": config,
                "depends": _ref_names(stmt, ("image",))}

    if op == OP_VLM:
        config = dict(base)
        config.update({
            "imagePath": resolve(stmt.get("image")),
            "experimentFamily": stmt.get("family", "t1"),
        })
        return {"type": "image_analysis", "config": config, "depends": _ref_names(stmt, ("image",))}

    raise ValueError(f"unsupported op for node compilation: {op!r}")


def _metric_label(metric: Any) -> str:
    """`$r1.contrast` → "contrast"；裸名原样返回。用于 quality_gate 的展示与 metrics 键。"""
    from .validate import parse_ref

    if isinstance(metric, str):
        parsed = parse_ref(metric)
        if parsed is not None:
            return parsed[1][-1] if parsed[1] else parsed[0]
        return metric
    return "value"


def _as_dataset(value: Any) -> Any:
    """contrast/analyze 需要 int（analyze 内部再 str()）；`$ref` 已解成 int 时原样返回。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return value
    return value


def _ref_names(stmt: Dict[str, Any], fields) -> List[str]:
    """这条语句依赖了哪些绑定（用于 node.depends，让 print 汇总 / gate 回溯有得可用）。"""
    from .validate import refs_in  # 局部 import，避免循环

    names: List[str] = []
    for field in fields:
        for name, _path in refs_in(stmt.get(field)):
            if name != "env" and name not in names:
                names.append(name)
    return names


def _from_names(stmt: Dict[str, Any]) -> List[str]:
    from .validate import parse_ref

    out: List[str] = []
    for item in stmt.get("from") or []:
        parsed = parse_ref(item) if isinstance(item, str) else None
        if parsed and parsed[0] != "env" and parsed[0] not in out:
            out.append(parsed[0])
    return out


# ── 画布节点图 → IR ──────────────────────────────────────────────────────────
CANVAS_TO_OP = {
    "experiment": OP_MEASURE,
    "contrast": OP_READ,
    "analyze": OP_ANALYZE,
    "analysis": OP_ANALYZE,
    "decision": OP_DECIDE,
    "llm": OP_ASK,
    "image_classification": OP_CLASSIFY,
    "image_analysis": OP_VLM,
    "quality_gate": OP_ASSERT,
    "print": OP_SAY,
    "notify": OP_SAY,
    "adjust_params": OP_APPLY,
}


def ir_from_canvas(name: str, nodes: List[Dict[str, Any]],
                   context: Optional[Dict[str, Any]] = None,
                   settings: Optional[Dict[str, Any]] = None,
                   edges: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """画布提交的 {id,type,depends,conditions,config}[] → IR。

    规则见设计文档 §7.1：
    - 按 depends 拓扑排序（`depends` 只是顺序信息，不进 IR）
    - `{{x}}` → `$x`（能解析的转成引用；`{{nodes.c1.contrast}}` 这类保持原样并留 warning）
    - `conditions` → `when` 守卫（pass/fail 分别映射 `$x.ok` / `not $x.ok`）

    前端提交时已经把 `edges` 折成 `depends`/`conditions`；但保存到
    `data/workflows/*.json` 的文件里依赖住在 `edges` 里，所以这里也接受
    `edges`，缺 `depends` 时据此补齐（否则读存档文件会得到一张无边图）。
    """
    from .helpers import to_ref

    settings = settings or {}
    context = context or {}
    if edges:
        nodes = _merge_edges(nodes, edges)
    by_id = {n.get("id"): n for n in nodes}
    order = _topo(nodes, by_id)
    pipeline: List[Dict[str, Any]] = []

    for node in order:
        node_type = node.get("type")
        if node_type in ("context",):
            cfg = node.get("config") or {}
            for key, value in (cfg.get("variables") or {}).items():
                pipeline.append({"op": OP_LET, "as": key, "value": to_ref(value)})
            continue
        if node_type in ("while", "parallel"):
            raise ValueError(
                f"节点 {node.get('id')} 的类型 {node_type} 在 IR v1 中不存在；"
                "循环请用 repeat（画布暂不支持，请用文本 DSL /eDSL）"
            )
        if node_type == "code":
            raise ValueError(f"节点 {node.get('id')}: code 永不进 IR（任意代码执行不是工作流语言的一部分）")
        op = CANVAS_TO_OP.get(node_type)
        if op is None:
            raise ValueError(f"节点 {node.get('id')}: 未知类型 {node_type!r}")

        stmt: Dict[str, Any] = {"op": op, "as": _safe_name(node.get("id") or "n"),
                                "source": {"kind": "canvas", "nodeId": node.get("id")}}
        cfg = {k: to_ref(v) for k, v in (node.get("config") or {}).items()}
        stmt.update(_canvas_fields(op, cfg, by_id, node))

        when = _when_guard(node.get("conditions") or [])
        if when is not None:
            stmt["when"] = when
        pipeline.append(stmt)

    ir = {
        "ir_version": 1,
        "id": _safe_name(name or "canvas_workflow"),
        "meta": {"name": settings.get("description") or name or "canvas workflow",
                 "source": {"kind": "canvas"}},
        "env": {},
        "context": context,
        "defaults": {"nodeTimeout": settings.get("nodeTimeout", 300)},
        "budget": _canvas_budget(nodes, settings),
        "pipeline": pipeline,
    }
    declared_policy = settings.get("policy")
    if isinstance(declared_policy, dict) and declared_policy.get("readOnly") is not None:
        ir["policy"] = {"readOnly": bool(declared_policy.get("readOnly"))}
    repeat = settings.get("repeat")
    if isinstance(repeat, dict) and repeat.get("maxRounds"):
        stop = repeat.get("stopWhen")
        stop_when = None
        if isinstance(stop, dict) and stop.get("metric"):
            stop_when = {"metric": to_ref(stop.get("metric")),
                         "operator": stop.get("op") or stop.get("operator") or ">=",
                         "value": stop.get("threshold", stop.get("value"))}
        ir["pipeline"] = [{
            "op": "repeat",
            "maxRounds": int(repeat["maxRounds"]),
            "stopWhen": stop_when,
            "body": pipeline,
        }]
    return ir


def _merge_edges(nodes: List[Dict[str, Any]],
                 edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把存档文件里的 `edges` 折回每个节点的 `depends` / `conditions`（不改入参）。"""
    merged = [dict(n) for n in nodes]
    by_id = {n.get("id"): n for n in merged}
    for node in merged:
        node["depends"] = list(node.get("depends") or [])
        node["conditions"] = list(node.get("conditions") or [])
    for edge in edges:
        src, dst = edge.get("source"), edge.get("target")
        if not src or not dst or dst not in by_id:
            continue
        target = by_id[dst]
        kind = str(edge.get("type") or "dependency")
        if kind.startswith("condition"):
            on = "pass" if kind.endswith("pass") else "fail"
            target["conditions"].append({"from": src, "on": on})
        else:
            if src not in target["depends"]:
                target["depends"].append(src)
    return merged


def _upstream_dataset(node: Dict[str, Any], by_id: Dict[str, Any]) -> Optional[str]:
    """沿 depends 向上找第一个产出 dataset 的节点，返回 `$<id>.dataset`。

    画布上 `contrast` / `analyze` 不写 datasetId，执行期由服务端回溯 depends；
    IR 里必须写成显式引用，否则静态校验看不见数据来源。
    """
    seen = set()
    queue = list(node.get("depends") or [])
    while queue:
        dep_id = queue.pop(0)
        if not dep_id or dep_id in seen:
            continue
        seen.add(dep_id)
        dep = by_id.get(dep_id) or {}
        if dep.get("type") in ("experiment", "experiment_batch"):
            return f"${_safe_name(dep_id)}.dataset"
        queue.extend(dep.get("depends") or [])
    return None


def _canvas_fields(op: str, cfg: Dict[str, Any], by_id: Dict[str, Any],
                   node: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if op == OP_MEASURE:
        out["fn"] = cfg.get("fn", "sq.iqraw")
        out["qubit"] = cfg.get("qubit", "$env.qubit")
        if cfg.get("params") or cfg.get("args"):
            out["args"] = cfg.get("args") or cfg.get("params")
        if cfg.get("timeout"):
            out["timeout"] = cfg["timeout"]
        if cfg.get("retries"):
            out["retries"] = cfg["retries"]
    elif op == OP_READ:
        out["kind"] = cfg.get("kind", "IQraw")
        ds = cfg.get("datasetId", cfg.get("dataset"))
        if ds not in (None, ""):
            out["dataset"] = ds
        else:
            # 画布上的 contrast 节点没有显式 datasetId，运行时靠 depends 回溯；
            # 编译成 IR 时必须落成显式引用，IR 才是自描述且可静态校验的。
            upstream = _upstream_dataset(node, by_id)
            if upstream:
                out["dataset"] = upstream
        out["threshold"] = cfg.get("threshold", 0.2)
        out["qubit"] = cfg.get("qubit", "$env.qubit")
    elif op == OP_ANALYZE:
        if cfg.get("datasetId", cfg.get("dataset")):
            out["dataset"] = cfg.get("datasetId", cfg.get("dataset"))
        else:
            upstream = _upstream_dataset(node, by_id)
            if upstream:
                out["dataset"] = upstream
        out["command"] = cfg.get("command", "qter.fitData(do_plot=False)")
    elif op == OP_ASSERT:
        label = cfg.get("metric", "contrast")
        deps = [d for d in (node.get("depends") or []) if d]
        # 画布上的 gate 只有指标名（contrast），要绑到上游节点才有可比较的值
        out["metric"] = f"${_safe_name(deps[0])}.{label}" if deps and "." not in str(label) else label
        out["operator"] = cfg.get("operator", ">=")
        out["threshold"] = cfg.get("threshold", 0.4)
        out["onFail"] = "halt" if cfg.get("failWorkflow", True) else "continue"
    elif op == OP_SAY:
        out["template"] = cfg.get("message", cfg.get("text", ""))
        out["channel"] = "notify" if node.get("type") == "notify" else "log"
    elif op == OP_APPLY:
        out["params"] = cfg.get("params") or {}
    elif op == OP_DECIDE:
        out["mode"] = cfg.get("mode", "analysis")
        out["intent"] = cfg.get("intentPrompt", cfg.get("intent", ""))
        out["systemPrompt"] = cfg.get("systemPrompt", "")
        out["from"] = [f"${d}" for d in (node.get("depends") or [])]
    elif op == OP_ASK:
        out["messages"] = cfg.get("messages") or []
        out["model"] = cfg.get("model", "minimax")
    elif op == OP_CLASSIFY:
        out["image"] = cfg.get("imagePath", cfg.get("image", ""))
        out["threshold"] = cfg.get("reviewThreshold", 0.75)
    elif op == OP_VLM:
        out["image"] = cfg.get("imagePath", cfg.get("image", ""))
        out["family"] = cfg.get("experimentFamily", cfg.get("family", "t1"))
    return out


def _when_guard(conditions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """多个条件 → `when` 相与。单条件时退化成裸引用，方便阅读。"""
    parts = []
    for cond in conditions:
        src = cond.get("from")
        if not src:
            continue
        parts.append(f"${src}.ok" if cond.get("on") == "pass" else f"!${src}.ok")
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return {"allOf": parts}


def _topo(nodes: List[Dict[str, Any]], by_id: Dict[str, Any]) -> List[Dict[str, Any]]:
    order: List[Dict[str, Any]] = []
    visiting, done = set(), set()

    def visit(node, stack):
        nid = node.get("id")
        if nid in done:
            return
        if nid in visiting:
            raise ValueError(f"画布存在环：{' → '.join(stack + [str(nid)])}")
        visiting.add(nid)
        for dep in node.get("depends") or []:
            dep_node = by_id.get(dep)
            if dep_node is not None:
                visit(dep_node, stack + [str(nid)])
        visiting.discard(nid)
        done.add(nid)
        order.append(node)

    for n in nodes:
        visit(n, [])
    return order


def _safe_name(raw: str) -> str:
    import re

    name = re.sub(r"[^a-z0-9_]", "_", str(raw).strip().lower())
    if not name or not name[0].isalpha():
        name = "n_" + name
    return name


def _count_canvas_writes(nodes: List[Dict[str, Any]]) -> int:
    return sum(1 for n in nodes if n.get("type") == "experiment")


def _canvas_budget(nodes: List[Dict[str, Any]], settings: Dict[str, Any]) -> Dict[str, Any]:
    """画布存档的预算头：默认按"实验节点数"估 maxWrites，允许 settings.budget 覆盖。

    画布 UI 目前没有预算输入框，但存档文件与 API 调用方可以写
    `settings.budget = {maxWrites, maxWallSeconds, maxDatasets, maxLlmCalls,
    maxScanPoints}`。不给时行为与以前完全一致（只按节点数估 maxWrites）。
    """
    declared = settings.get("budget")
    declared = declared if isinstance(declared, dict) else {}
    budget: Dict[str, Any] = {
        "maxWrites": declared.get("maxWrites", _count_canvas_writes(nodes) or None),
    }
    for key in ("maxWallSeconds", "maxDatasets", "maxLlmCalls", "maxScanPoints"):
        if declared.get(key) is not None:
            budget[key] = declared[key]
    return budget


def node_type_for(op: str, stmt: Dict[str, Any]) -> Optional[str]:
    if op == OP_SAY and stmt.get("channel") == "notify":
        return "notify"
    return OP_TO_NODE.get(op)
