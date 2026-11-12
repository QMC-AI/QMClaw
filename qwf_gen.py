"""qwf_gen.py - 按「实验 + 参数」生成 .qwf（文本 DSL）文件。

约定（2026-11-05 起）：跑量子测控实验一律走 `.qwf` 路径，不再直接 `POST :3003/execute`
下发裸 `sq.*` 代码。本工具负责把"我想跑 sq.t1，zpa 固定 0"这种意图写成可解析、
可静态校验、可干跑的 `.qwf`。

参数写法（`--param` 可重复），统一落进 `.qwf` 的 `args { }` 块 ——
`measure` 的字段白名单只有 `fn / qubit / args / params / reportDataset`
（外加通用的 `as / timeout / note / retries / onError`），直接写在测量块里会报 `E_UNKNOWN_FIELD`：
    zpa=0.0                 标量
    delay=0,84000,6000      区间 → np.arange(0, 84000, 6000)
    states=[0,1]            列表字面量
    update=false            布尔
    gate=X                  字符串

## goal 模式（P0，2026-11-05）

`--goal-objective` 会写出**目标契约**。两种写法（`--goal-style`）：

1. `native`（默认，P1 起）：直接写 `goal` 顶层块，**服务端 `normalize()` 负责 lowering**
   成 `repeat + if/else + assert`；执行器零改动：

       goal {
         objective   "复现 q4ld3 的 iqraw 对比度"
         success { metric r1.contrast  operator >=  value 0.6 }
         max_rounds  3
         on_achieved "continue"
       }
       measure as c1 { ... }
       read    c1 as r1 { kind IQraw  threshold 0.4 }

2. `expanded`（P0 老写法）：手写展开低级节点，不依赖服务端 lowering：

    repeat max 3 stop_when r1.contrast >= 0.60 export r1 {
      measure as c1 { ... }
      read    c1 as r1 { kind IQraw  threshold 0.4 }
      say     "..."
    }
    assert r1.contrast >= 0.60 else halt as g1
    say     "GOAL reached: contrast=${r1.contrast} (>= 0.60)"

这正是 DSL 提案 §13.2 里 `goal {objective, success{...}, maxRounds}` 的 **lowering 结果**——
执行器完全不需要认识 `goal`，所以这一步零服务端改动。

语义要点（来自 `dsl/executor.py:423-451` `_exec_repeat`）：
- 每轮**开始**时清空体内绑定（S2：每轮重新绑定），但 `stop_when` 是在**本轮 body 跑完后**求值，
  所以它读得到**本轮**的 `$r1.*`，不需要读上一轮的值；
- 循环结束后，体内绑定默认被清掉 → 想让尾部的 `assert` 用到最后一轮的值，必须 `export r1`。

用法：
    python qwf_gen.py --fn sq.t1 --qubit q4ld3 --param zpa=0.0 \\\\
        --param delay=0,84000,6000 --timeout 120 --out t1_q4ld3.qwf
    python qwf_gen.py --fn sq.ramsey_t2 --qubit q4ld3 --read Ramsey \\\\
        --assert 'T2_us>=5' --out ramsey_q4ld3.qwf
    python qwf_gen.py --fn sq.iqraw --qubit q4ld3 \\\\
        --goal-objective "复现 q4ld3 的 iqraw 对比度" \\\\
        --success-metric contrast --success-op ">=" --success-value 0.6 \\\\
        --max-rounds 3 --out iqraw_goal_q4ld3.qwf
    python qwf_gen.py --help
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# 控制台编码修正：被自检 runner 用 runpy + StringIO 接管道时 stdout 没有 reconfigure，
# 不能让它成为 import 失败的原因（导入方只想要 qwf_gen 的函数）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

# 实验 → 可用的 read 指标。只有 iqraw（原始 IQ shots）能走 `read kind IQraw` ——
# 该节点按 discriminator 轴做投影统计，喂 T1/Ramsey 这类已归约成 P1 的数据集没意义。
READ_KIND = {
    "sq.iqraw": "IQraw",
}

NUM = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def _emit_value(raw: str) -> str:
    """把 CLI 上的一坨文本渲染成 .qwf 里合法的值写法。"""
    s = raw.strip()
    if s.lower() in ("true", "false"):
        return s.lower()
    if s.startswith("[") and s.endswith("]"):
        return s                      # 列表原样
    if "," in s and all(NUM.match(p.strip()) for p in s.split(",")):
        return "range " + " ".join(p.strip() for p in s.split(","))
    if NUM.match(s):
        return s
    return '"' + s.replace('"', '\\"') + '"'


def build(args) -> str:
    fn = args.fn
    qubit = args.qubit
    name = args.name or (fn.split(".")[-1] + "_" + qubit)

    lines: list[str] = []
    lines.append(f"# {name}.qwf — 由 qwf_gen.py 生成（{args.stamp}）")
    lines.append(f"# {fn} on {qubit}" + (f"  params: {args.param}" if args.param else ""))
    lines.append(f"workflow {name} {{")
    lines.append("  meta {")
    lines.append(f'    name        "{args.title or name}"')
    lines.append(f'    description "{fn} on {qubit}"')
    lines.append(f'    tags        ["single-qubit", "{fn.split(".")[-1]}"]')
    lines.append("  }")
    lines.append("")
    lines.append("  env {")
    lines.append(f'    qubit   "{qubit}"')
    if args.session:
        lines.append(f'    session "{args.session}"')
    lines.append("  }")
    lines.append("")
    lines.append("  budget {")
    lines.append(f"    max_wall   {args.max_wall}")
    lines.append(f"    max_writes {args.max_writes}")
    if args.goal_objective:
        lines.append(f"    max_datasets {max(1, int(args.max_rounds or 1))}")
    lines.append("  }")
    lines.append("")

    kind = args.read or READ_KIND.get(fn)
    metric = args.say or ("contrast" if kind else "")
    goal = bool(args.goal_objective)

    if goal and not kind:
        raise SystemExit("goal 模式需要 --read（要有一个可判定的指标）")
    if goal and not metric:
        raise SystemExit("goal 模式需要 --success-metric（缺省 contrast）")

    def _measure_block(indent: str) -> list[str]:
        out = [f"{indent}measure as c1 {{"]
        out.append(f"{indent}  fn      {fn}")
        out.append(f"{indent}  qubit   $env.qubit")
        out.append(f"{indent}  timeout {args.timeout}")
        if args.note:
            out.append(f'{indent}  note "{args.note}"')
        if args.param:
            out.append(f"{indent}  args {{")
            for p in args.param:
                if "=" not in p:
                    raise SystemExit(f"--param 需要 key=value 形式，收到 {p!r}")
                k, v = p.split("=", 1)
                out.append(f"{indent}    {k.strip()} {_emit_value(v)}")
            out.append(f"{indent}  }}")
        out.append(f"{indent}}}")
        return out

    def _read_block(indent: str) -> list[str]:
        thr = args.threshold
        return [f"{indent}read c1 as r1 {{ kind {kind}  threshold {thr} }}"]

    def _analyze_block(indent: str) -> list[str]:
        if not args.plot:
            return []
        return [f'{indent}analyze c1 as a1 {{ command "{args.plot}" }}']

    def _say_block(indent: str) -> list[str]:
        if metric:
            return [f'{indent}say "{qubit} {fn.split(".")[-1]} dataset=${{c1.dataset}} {metric}=${{r1.{metric}}}"']
        return [f'{indent}say "{qubit} {fn.split(".")[-1]} dataset=${{c1.dataset}}"']

    if goal:
        rounds = max(1, int(args.max_rounds or 1))
        op = args.success_op or ">="
        if args.success_value is None:
            raise SystemExit("goal 模式需要 --success-value")
        val = args.success_value
        if (args.goal_style or "native") == "native":
            # P1（2026-11-05）：直接写 goal 顶层块，由服务端 normalize() 做 lowering。
            # 执行器依然不认识 goal —— lowering 发生在服务端 IR 规范化阶段。
            lines.append(f"  # GOAL: {args.goal_objective}")
            lines.append("  goal {")
            lines.append(f'    objective   "{args.goal_objective}"')
            lines.append("    success {")
            lines.append(f"      metric     r1.{metric}")
            lines.append(f"      operator   {op}")
            lines.append(f"      value      {val}")
            lines.append("    }")
            lines.append(f"    max_rounds  {rounds}")
            lines.append('    on_achieved "continue"')
            lines.append("  }")
            lines.append("")
            lines.extend(_measure_block("  "))
            lines.append("")
            lines.extend(_read_block("  "))
            if args.plot:
                lines.append("")
                lines.extend(_analyze_block("  "))
            lines.append("")
            lines.extend(_say_block("  "))
            lines.append("}")
            return "\n".join(lines) + "\n"
        lines.append(f"  # GOAL: {args.goal_objective}")
        lines.append(f"  repeat max {rounds} stop_when r1.{metric} {op} {val} export r1 {{")
        lines.extend(_measure_block("    "))
        lines.extend(_read_block("    "))
        lines.extend(_analyze_block("    "))
        lines.extend(_say_block("    "))
        lines.append("  }")
        # 目标未达成 ≠ 运行错误：用 `else continue` 让门只记录不中断（DSL 的
        # `stmt.onFail == "continue"`，见 dsl/dryrun.py:131），再用 if/else 把
        # "达成 / 未达成"写进 say —— outcome 契约（P2）会把 g1.passed 结构化。
        lines.append(f"  if r1.{metric} {op} {val} {{")
        lines.append(f'    say "GOAL reached: {metric}=${{r1.{metric}}} ({op} {val})"')
        lines.append("  } else {")
        lines.append(f'    say "GOAL not reached: {metric}=${{r1.{metric}}} (need {op} {val})"')
        lines.append("  }")
        lines.append(f"  assert r1.{metric} {op} {val} else continue as g1")
        lines.append("}")
    else:
        lines.extend(_measure_block("  "))
        lines.append("")
        if kind:
            lines.extend(_read_block("  "))
            if args.assert_:
                lines.append(f"  assert {args.assert_} else halt as g1")
            lines.append("")
        lines.extend(_analyze_block("  "))
        if args.plot:
            lines.append("")
        lines.extend(_say_block("  "))
        lines.append("}")

    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fn", required=True, help="如 sq.t1 / sq.iqraw / sq.ramsey_t2")
    ap.add_argument("--qubit", required=True)
    ap.add_argument("--param", action="append", default=[], help="key=value，可重复")
    ap.add_argument("--read", default="", help="read 的 kind（缺省按实验自动选）")
    ap.add_argument("--threshold", default="0.4")
    ap.add_argument("--assert", dest="assert_", default="", help="如 T1_us>=20")
    ap.add_argument("--note", default="", help="节点注释（写进 .qwf 的 note）")
    ap.add_argument("--say", default="", help="say 里引用的指标名，缺省 contrast")
    ap.add_argument("--plot", default="qter.fitData(do_plot=True)")
    ap.add_argument("--no-plot", dest="plot", action="store_const", const="")
    ap.add_argument("--timeout", default="120")
    ap.add_argument("--max-wall", default="10m")
    ap.add_argument("--max-writes", default="4")
    ap.add_argument("--session", default="20251103_LD_SQ")
    ap.add_argument("--name", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--stamp", default="qwf_gen")
    # ── goal（P0）────────────────────────────────────────────────────────────
    ap.add_argument("--goal-objective", default="", help="目标描述；给了就进入 goal 模式")
    ap.add_argument("--success-metric", default="contrast", help="达成判据的指标名（缺省 contrast）")
    ap.add_argument("--success-op", default=">=", help="比较符：>= > <= < ==")
    ap.add_argument("--success-value", default=None, help="达成阈值（数值）")
    ap.add_argument("--max-rounds", default="1", help="goal 模式下最多迭代几轮")
    ap.add_argument("--goal-style", choices=["native", "expanded"], default="native",
                    help="native=写 goal 顶层块（服务端 lowering，推荐）；expanded=手写展开 repeat/if/assert（P0 老写法）")
    args = ap.parse_args()

    text = build(args)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"written: {args.out}  ({len(text)} chars)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
