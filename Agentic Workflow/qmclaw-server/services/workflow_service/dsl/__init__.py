"""`services.workflow_service.dsl` - 工作流领域专用语言。

三个前端，一个中间表示（IR v1），一个执行器：

    文本 `.qwf` ──┐
    Python eDSL ──┼──▶ IR (dict) ──▶ validate ──▶ IrExecutor ──▶ 现有 WorkflowService
    画布 JSON ────┘

对外只需要记住四个函数：

    ir = parse_qwf(text)                    # 或 edsl.workflow(...).to_ir()
    ir = normalize(ir)                       # 补默认值 + 自动命名
    ir, diags = validate(ir)                 # 语义校验（纯函数，不连服务）
    result = run_ir(service, ir, context)    # 真正执行

设计文档见 `docs/DSL_DESIGN.md`。
"""

from .budget import Budget, BudgetExceeded
from . import capabilities, quantities
from .backends import BackendAdapter, LqcsAdapter, get_adapter, list_backends, register
from .compile import CANVAS_TO_OP, build_node, ir_from_canvas, node_type_for
from .canvas_ir import (
    GRAY_NODE_TYPE,
    canvas_to_ir,
    canvas_to_qwf,
    ir_diff,
    ir_equivalent,
    ir_to_canvas,
    stability_diff,
)
from .dryrun import DryRunProvider
from .edsl import WorkflowBuilder, workflow
from .executor import Cancelled, Halt, IrExecutor, UndefinedRef, run_ir, wrap_result
from .ir import assign_names, canonical, ir_hash, iter_all_statements, normalize, validate_structure
from .lint import lint_ir, lint_text
from .plan import format_plan, plan_checks, plan_ir
from .textdsl import DSLSyntaxError, parse_file as parse_qwf_file, parse as parse_qwf
from .textdsl.printer import ir_to_qwf, print_qwf, stmt_to_qwf
from .validate import Scope, format_diags, parse_ref, refs_in, validate

__all__ = [
    # 校验 / 规范化
    "normalize", "assign_names", "canonical", "ir_hash",
    "validate_structure", "validate", "format_diags", "iter_all_statements",
    "plan_ir", "format_plan", "plan_checks",
    "Scope", "parse_ref", "refs_in",
    # 前端
    "parse_qwf", "parse_qwf_file", "DSLSyntaxError",
    "lint_ir", "lint_text",
    "workflow", "WorkflowBuilder",
    "ir_from_canvas", "build_node", "node_type_for", "CANVAS_TO_OP",
    # `.qwf` ⇄ 画布（IR 是中表示）
    "ir_to_qwf", "print_qwf", "stmt_to_qwf",
    "ir_to_canvas", "canvas_to_ir", "canvas_to_qwf",
    "ir_equivalent", "ir_diff", "GRAY_NODE_TYPE", "stability_diff",
    # 执行
    "IrExecutor", "run_ir", "wrap_result",
    "Halt", "Cancelled", "UndefinedRef",
    "Budget", "BudgetExceeded", "DryRunProvider",
    # 后端预留（IR v2）
    "capabilities", "quantities",
    "BackendAdapter", "LqcsAdapter", "get_adapter", "list_backends", "register",
]
