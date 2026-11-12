"""qwf_store - 服务端工作流存档（统一存 `.qwf` 文本）。

背景：画布此前把工作流存成"画布 JSON 快照"（节点 + 边 + 布局），
那种存档丢掉了 `.qwf` 里的语义信息（别名、note、timeout、budget、policy…），
而且没法直接拿去跑。现在统一改成 `.qwf`：

- 存档文件 = **语义**（IR 的文本形式），不是布局；
  布局由前端自动布局重算（`useAutoLayout`），同一张图每次算出来一样。
- 版本号与时间戳写在 `meta` 块里（`version` / `updatedAt`）。
  这两个字段是**易变字段**，不参与语义等价判定
  （见 `dsl/canvas_ir.py` 的 `_VOLATILE_META`）——否则每次保存都会被
  "改过了"的差异清单挡住。
- `examples/` 下的示例是**只读**的参考工作流，不进存档列表的 `saved` 分组。

本模块只碰文件系统，不依赖服务实例，方便单测。
"""

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .dsl import parse_qwf
from .dsl.textdsl.printer import ir_to_qwf

# qmclaw-server/（services/workflow_service/qwf_store.py → 上三级）
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
WORKFLOWS_DIR = ROOT_DIR / "data" / "workflows"
EXAMPLES_DIR = WORKFLOWS_DIR / "examples"

# 文件名安全：只留字母数字、下划线、连字符、点
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_id(raw: str) -> str:
    """把名字收拾成能当文件名的 id。"""
    name = _SAFE_ID_RE.sub("_", str(raw or "").strip()).strip("._")
    return name or "workflow"


def _meta_of(ir: Dict[str, Any]) -> Dict[str, Any]:
    meta = ir.get("meta")
    return meta if isinstance(meta, dict) else {}


def _file_info(path: Path, source: str) -> Dict[str, Any]:
    """一个 `.qwf` 文件 → 列表项。"""
    stat = path.stat()
    item: Dict[str, Any] = {
        "id": path.stem,
        "name": path.stem,
        "path": path.name,
        "source": source,
        "version": 1,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime)),
        "size": stat.st_size,
    }
    try:
        ir = parse_qwf(path.read_text(encoding="utf-8"))
        meta = _meta_of(ir)
        item.update({
            # 列表 id 用**文件名**（唯一）；`.qwf` 里的 workflow id 可能重复
            # （同一族工作流共用一个 id），单独放在 workflowId 里。
            "id": path.stem,
            "workflowId": ir.get("id") or path.stem,
            "title": meta.get("name") or path.stem,
            "description": meta.get("description") or "",
            "tags": meta.get("tags") or [],
            "version": int(meta.get("version") or 1),
            "nodeCount": len(ir.get("pipeline") or []),
            "ops": sorted({s.get("op") for s in ir.get("pipeline") or []
                           if isinstance(s, dict)}),
        })
    except Exception as exc:      # 语法错误也要列出来，别让整个列表挂掉
        item["error"] = str(exc)
    return item


def list_workflows(include_examples: bool = True) -> List[Dict[str, Any]]:
    """列出存档（`data/workflows/*.qwf`）与示例（`examples/*.qwf`）。"""
    items: List[Dict[str, Any]] = []
    if WORKFLOWS_DIR.exists():
        for path in sorted(WORKFLOWS_DIR.glob("*.qwf")):
            items.append(_file_info(path, "saved"))
    if include_examples and EXAMPLES_DIR.exists():
        for path in sorted(EXAMPLES_DIR.glob("*.qwf")):
            items.append(_file_info(path, "example"))
    return items


def path_for(wf_id: str) -> Path:
    """存档文件路径（只允许落在 `data/workflows` 下）。"""
    return WORKFLOWS_DIR / f"{safe_id(wf_id)}.qwf"


def read_workflow(wf_id: str) -> Optional[Dict[str, Any]]:
    """读一个存档/示例：返回 {id,name,text,ir,canvas?}。找不到返回 None。"""
    target = path_for(wf_id)
    if not target.exists():
        candidate = EXAMPLES_DIR / f"{safe_id(wf_id)}.qwf"
        if candidate.exists():
            target = candidate
        else:
            return None
    text = target.read_text(encoding="utf-8")
    ir = parse_qwf(text)
    meta = _meta_of(ir)
    return {
        "id": target.stem,
        "workflowId": ir.get("id") or target.stem,
        "name": meta.get("name") or target.stem,
        "text": text,
        "ir": ir,
        "path": str(target),
        "source": "example" if target.parent == EXAMPLES_DIR else "saved",
    }


def write_workflow(wf_id: str, ir: Dict[str, Any]) -> Dict[str, Any]:
    """把 IR 写成 `.qwf` 存档（自动递增 `meta.version`、刷新 `meta.updatedAt`）。"""
    WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    target = path_for(wf_id)
    version = 0                      # 第一次存 → version 1
    if target.exists():
        try:
            old = parse_qwf(target.read_text(encoding="utf-8"))
            version = int(_meta_of(old).get("version") or 1)
        except Exception:
            version = 1
    meta = dict(_meta_of(ir))
    meta["version"] = version + 1
    meta["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    ir = dict(ir)
    ir["meta"] = meta
    text = ir_to_qwf(ir)
    target.write_text(text, encoding="utf-8")
    return {
        "ok": True,
        "id": target.stem,
        "name": meta.get("name") or target.stem,
        "path": str(target),
        "version": meta["version"],
        "updatedAt": meta["updatedAt"],
        "qwf": text,
    }


def delete_workflow(wf_id: str) -> Dict[str, Any]:
    """删存档（示例是只读的，不给删）。"""
    target = path_for(wf_id)
    if not target.exists():
        return {"ok": False, "error": f"找不到存档：{wf_id}"}
    if target.parent == EXAMPLES_DIR:
        return {"ok": False, "error": "示例工作流是只读的，不能删除"}
    target.unlink()
    return {"ok": True, "id": target.stem}


def migrate_json_archives() -> List[Dict[str, Any]]:
    """迁移老存档：把 `data/workflows/*.json`（画布快照）转成 `.qwf`。

    老 JSON 里只有节点与边，没有 `.qwf` 语义（别名/note/timeout…），
    所以这里走"画布 → IR → `.qwf`"的官方通道，转换失败就如实报告，
    不做静默丢弃。迁移完把老 JSON 改名成 `.migrated.json`（留档不删）。
    """
    from .dsl.canvas_ir import canvas_to_qwf

    results: List[Dict[str, Any]] = []
    if not WORKFLOWS_DIR.exists():
        return results
    for path in sorted(WORKFLOWS_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            results.append({"file": path.name, "ok": False, "error": str(exc)})
            continue
        nodes = payload.get("nodes") or []
        edges = payload.get("edges") or []
        settings = payload.get("settings") or {}
        meta = {
            "name": payload.get("name") or path.stem,
        }
        if settings.get("description"):
            meta["description"] = settings["description"]
        if settings.get("source"):
            meta["source"] = settings["source"]
        if settings.get("qubit"):
            meta["qubit"] = settings["qubit"]
        doc = {
            "id": payload.get("id") or path.stem,
            "name": payload.get("name") or path.stem,
            "nodes": nodes,
            "edges": edges,
            "context": payload.get("context") or {},
            "workflow": {"meta": meta},
        }
        try:
            result = canvas_to_qwf(doc)
        except Exception as exc:
            results.append({"file": path.name, "ok": False, "error": str(exc)})
            continue
        if not result.get("ok"):
            results.append({"file": path.name, "ok": False,
                            "error": "; ".join(result.get("errors") or ["转换失败"])})
            continue
        saved = write_workflow(path.stem, result["ir"])
        path.rename(path.with_suffix(".migrated.json"))
        results.append({"file": path.name, "ok": True, "id": saved["id"],
                        "path": saved["path"]})
    return results


__all__ = [
    "WORKFLOWS_DIR", "EXAMPLES_DIR", "safe_id",
    "list_workflows", "read_workflow", "write_workflow", "delete_workflow",
    "migrate_json_archives",
]
