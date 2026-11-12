"""dsl.textdsl.parser - `.qwf` 递归下降解析，直接产出 IR v1 字典。

语法（与 `docs/DSL_DESIGN.md` §6.1 一致）：

```qwf
workflow contrast_tuning {
  meta { name "Readout contrast tuning" tags [readout] }
  env  { qubit "q2ld3" }
  budget { max_wall 30m  max_writes 40 }

  measure baseline as c1 { fn sq.iqraw  qubit $env.qubit  timeout 120s  retries 1 }
  read    c1 as r1 { kind IQraw  threshold 0.2 }
  assert  r1.contrast >= 0.2 else halt  as g1

  repeat max 5 stop_when r5.contrast >= 0.7 {
    measure as c2 { fn sq.piamp  qubit $env.qubit }
    read    c2 as r5 { kind IQraw  threshold 0.4 }
  }

  if r5.contrast >= 0.7 {
    say "T1 = ${a1.metrics.T1_us} us"
  } else {
    say "contrast 未达标" channel notify
  }
}
```

设计约束：
- 解析器**只产 IR**，不做语义检查（那是 `validate.py` 的活）。
- 所有 `as` 名字都原样写进 IR；没写 `as` 的语句交给 `ir.assign_names()` 统一编号。
- 裸引用 `r1.contrast` 自动补成 `$r1.contrast`；`read c1` 自动补成 `$c1.dataset`。
"""

from typing import Any, Dict, List, Optional

from .lexer import DSLSyntaxError, Token, tokenize

# 字段别名：`.qwf` 里写得更像人话，IR 里用 camelCase
FIELD_ALIASES = {
    "system": "systemPrompt",
    "max_tokens": "maxTokens",
    "on_fail": "onFail",
    "on_error": "onError",
    "retry_delay": "retryDelay",
    "report_dataset": "reportDataset",
    "review_threshold": "threshold",
    "experiment_family": "family",
    "max_rounds": "maxRounds",
}

BUDGET_KEYS = {
    "max_wall": "maxWallSeconds",
    "max_writes": "maxWrites",
    "max_datasets": "maxDatasets",
    "max_llm": "maxLlmCalls",
    "max_scan_points": "maxScanPoints",
}

POLICY_KEYS = {
    "read_only": "readOnly",
}

DURATION_FACTORS = {"us": 1e-6, "ms": 1e-3, "s": 1.0, "m": 60.0, "h": 3600.0}

SIMPLE_STATEMENTS = ("measure", "read", "analyze", "decide", "ask", "classify", "vlm")


class _Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0

    # ── 流操作 ──
    def peek(self, ahead: int = 0) -> Token:
        idx = min(self.pos + ahead, len(self.tokens) - 1)
        return self.tokens[idx]

    def advance(self) -> Token:
        tok = self.tokens[self.pos]
        if tok.kind != "EOF":
            self.pos += 1
        return tok

    def at(self, kind: str, value: Optional[str] = None) -> bool:
        tok = self.peek()
        return tok.kind == kind and (value is None or tok.value == value)

    def at_word(self, word: str) -> bool:
        return self.at("IDENT", word)

    def eat_word(self, word: str) -> bool:
        if self.at_word(word):
            self.advance()
            return True
        return False

    def expect(self, kind: str, value: Optional[str] = None) -> Token:
        tok = self.peek()
        if tok.kind != kind or (value is not None and tok.value != value):
            wanted = value or kind
            raise DSLSyntaxError(f"期望 {wanted}，实际是 {tok.value!r}", tok.line, tok.col)
        return self.advance()

    def expect_word(self, word: str) -> Token:
        return self.expect("IDENT", word)

    def error(self, message: str) -> DSLSyntaxError:
        tok = self.peek()
        return DSLSyntaxError(message, tok.line, tok.col)

    # ── 入口 ──
    def parse_workflow(self) -> Dict[str, Any]:
        self.expect_word("workflow")
        ident = self.peek()
        if ident.kind not in ("IDENT", "STRING"):
            raise self.error("workflow 后面需要工作流 id")
        workflow_id = _unquote(self.advance().value)
        ir: Dict[str, Any] = {"ir_version": 1, "id": workflow_id, "pipeline": []}
        self.expect("PUNCT", "{")
        while not self.at("PUNCT", "}"):
            if self.at("EOF"):
                raise self.error("workflow 块没有闭合")
            word = self.peek().value if self.at("IDENT") else ""
            if word in ("meta", "env", "budget", "defaults", "context", "policy", "bounds"):
                self.advance()
                ir[word] = self.parse_block(mapping=word)
            elif word == "goal":
                # v1.3 目标契约块：语法糖，normalize 时 lowering 成 repeat+if+assert
                self.advance()
                ir["goal"] = self.parse_goal()
            else:
                ir["pipeline"].append(self.parse_statement())
        self.expect("PUNCT", "}")
        return ir

    # ── 块 ──
    def parse_block(self, mapping: str = "") -> Any:
        self.expect("PUNCT", "{")
        out: Dict[str, Any] = {}
        while not self.at("PUNCT", "}"):
            if self.at("EOF"):
                raise self.error("块没有闭合")
            key_tok = self.peek()
            if key_tok.kind != "IDENT":
                raise self.error(f"块里需要字段名，实际是 {key_tok.value!r}")
            self.advance()
            key = key_tok.value
            if self.at_word("range"):
                # 扫描区间：`freq range 6.5 7.1 0.001` → {"$arange": [6.5, 7.1, 0.001]}
                # 对应官方流程里的 np.arange(start, stop, step)；见 docs/TUNEUP_FLOW.md 第 4 节。
                self.advance()
                nums: List[Any] = []
                while len(nums) < 3 and self.at("NUMBER"):
                    nums.append(_number(self.advance().value))
                if len(nums) < 2:
                    raise self.error("range 需要 2 或 3 个数值：range 起点 终点 [步长]")
                out[_alias(key)] = {"$arange": nums}
                continue
            if mapping == "budget":
                key = BUDGET_KEYS.get(key, key)
                out[key] = self.parse_value(duration=True)
            elif mapping == "policy":
                key = POLICY_KEYS.get(key, key)
                out[key] = self.parse_value()
            elif mapping == "meta":
                out[key] = self.parse_value()
            elif mapping == "bounds":
                # 写幅度上界：{"f10": 0.05, "PiGate.amp": 0.2}
                # 键名保持原样（可能带点，如 PiGate.amp），值必须是数值。
                val = self.parse_value()
                if isinstance(val, bool) or not isinstance(val, (int, float)):
                    raise self.error(
                        f"bounds.{key} 需要数值幅度上界，实际是 {val!r}")
                out[key] = val
            else:
                out[_alias(key)] = self.parse_value()
        self.expect("PUNCT", "}")
        return out

    # ── goal 块（v1.3 目标契约）──
    GOAL_KEYS = ("objective", "success", "max_rounds", "on_achieved")
    SUCCESS_KEYS = ("metric", "operator", "value", "tolerance", "criteria", "level")

    def parse_goal(self) -> Dict[str, Any]:
        """`goal { objective "..." success { metric r1.contrast operator >= value 0.6 } max_rounds 3 }`

        解析只负责把 `.qwf` 写法翻译成 IR 形状；语义检查在 `validate.py`。
        """
        self.expect("PUNCT", "{")
        out: Dict[str, Any] = {}
        while not self.at("PUNCT", "}"):
            if self.at("EOF"):
                raise self.error("goal 块没有闭合")
            key_tok = self.peek()
            if key_tok.kind != "IDENT":
                raise self.error(f"goal 块里需要字段名，实际是 {key_tok.value!r}")
            self.advance()
            key = key_tok.value
            if key == "success":
                out["success"] = self.parse_success()
            elif key == "objective":
                out["objective"] = self.parse_value()
            elif key in ("max_rounds", "maxRounds"):
                out["maxRounds"] = self.parse_value()
            elif key in ("on_achieved", "onAchieved"):
                out["onAchieved"] = self.parse_value()
            else:
                raise self.error(
                    f"goal 块不认识字段 {key!r}（可用：{' / '.join(self.GOAL_KEYS)}）")
        self.expect("PUNCT", "}")
        return out

    def parse_success(self) -> Dict[str, Any]:
        """`success { metric r1.contrast operator >= value 0.6 tolerance 0.02 }`"""
        self.expect("PUNCT", "{")
        out: Dict[str, Any] = {}
        while not self.at("PUNCT", "}"):
            if self.at("EOF"):
                raise self.error("success 块没有闭合")
            key_tok = self.peek()
            if key_tok.kind != "IDENT":
                raise self.error(f"success 块里需要字段名，实际是 {key_tok.value!r}")
            self.advance()
            key = key_tok.value
            if key == "operator":
                tok = self.peek()
                if tok.kind == "OP":
                    self.advance()
                    out["operator"] = tok.value
                elif tok.kind in ("IDENT", "STRING"):
                    self.advance()
                    out["operator"] = _unquote(tok.value)
                else:
                    raise self.error(f"success.operator 需要比较符号，实际是 {tok.value!r}")
            elif key == "metric":
                tok = self.peek()
                if tok.kind in ("REF", "IDENT"):
                    self.advance()
                    out["metric"] = _ref_value(tok.value)
                else:
                    raise self.error(f"success.metric 需要引用，实际是 {tok.value!r}")
            elif key in ("value", "threshold"):
                out["value"] = self.parse_value()
            elif key == "tolerance":
                out["tolerance"] = self.parse_value()
            elif key in ("criteria", "level"):
                # 判据引用：阈值可取自 Metric Registry（config/lab_criteria.json）
                out[key] = self.parse_value()
            else:
                raise self.error(
                    f"success 块不认识字段 {key!r}（可用：{' / '.join(self.SUCCESS_KEYS)}）")
        self.expect("PUNCT", "}")
        return out

    def parse_value(self, duration: bool = False) -> Any:
        tok = self.peek()
        if tok.kind == "STRING":
            self.advance()
            return _unquote(tok.value)
        if tok.kind == "NUMBER":
            self.advance()
            return _number(tok.value, duration=duration)
        if tok.kind == "REF":
            self.advance()
            return tok.value
        if tok.kind == "PUNCT" and tok.value == "[":
            return self.parse_list()
        if tok.kind == "PUNCT" and tok.value == "{":
            return self.parse_block()
        if tok.kind == "IDENT":
            self.advance()
            return _ident_value(tok.value)
        raise self.error(f"无法解析的值 {tok.value!r}")

    def parse_list(self) -> List[Any]:
        self.expect("PUNCT", "[")
        items: List[Any] = []
        while not self.at("PUNCT", "]"):
            items.append(self.parse_value())
            if not self.eat_punct(","):
                break
        self.expect("PUNCT", "]")
        return items

    def eat_punct(self, ch: str) -> bool:
        if self.at("PUNCT", ch):
            self.advance()
            return True
        return False

    # ── 语句 ──
    def parse_statement(self) -> Dict[str, Any]:
        """解析一条语句，并记下它在源文件里的位置。

        `source` 不参与语义与哈希（`ir.canonical` 会剥掉它），只用于把校验错误
        指回 `.qwf` 的行号——否则文本前端报错时用户还得自己数行。
        """
        tok = self.peek()
        stmt = self._parse_statement()
        if isinstance(stmt, dict) and tok is not None:
            stmt.setdefault("source", {
                "line": getattr(tok, "line", None),
                "col": getattr(tok, "col", None),
            })
        return stmt

    def _parse_statement(self) -> Dict[str, Any]:
        tok = self.peek()
        if tok.kind != "IDENT":
            raise self.error(f"语句必须以关键字开头，实际是 {tok.value!r}")
        word = tok.value

        if word == "let":
            self.advance()
            name = self.expect("IDENT").value
            self.expect("PUNCT", "=")
            return {"op": "let", "as": name, "value": self.parse_value()}

        if word == "say":
            self.advance()
            stmt: Dict[str, Any] = {"op": "say", "template": self.parse_value()}
            if self.eat_word("channel"):
                stmt["channel"] = self.expect("IDENT").value
            if self.eat_word("as"):
                stmt["as"] = self.expect("IDENT").value
            return stmt

        if word == "apply":
            self.advance()
            stmt = {"op": "apply", "params": self.parse_block()}
            if self.eat_word("as"):
                stmt["as"] = self.expect("IDENT").value
            return stmt

        if word == "assert":
            self.advance()
            stmt = {"op": "assert"}
            stmt.update(self.parse_compare())
            if self.eat_word("else"):
                stmt["onFail"] = self.expect("IDENT").value
            if self.eat_word("as"):
                stmt["as"] = self.expect("IDENT").value
            return stmt

        if word == "repeat":
            self.advance()
            self.expect_word("max")
            max_rounds = self.expect("NUMBER").value
            stmt = {"op": "repeat", "maxRounds": int(float(max_rounds))}
            if self.eat_word("stop_when"):
                spec = self.parse_compare()
                stmt["stopWhen"] = {"metric": spec["metric"], "op": spec["operator"],
                                    "value": spec.get("threshold")}
            if self.eat_word("export"):
                names = [self.expect("IDENT").value]
                while self.eat_punct(","):
                    names.append(self.expect("IDENT").value)
                stmt["export"] = names
            if self.eat_word("as"):
                stmt["as"] = self.expect("IDENT").value
            stmt["body"] = self.parse_statement_block()
            return stmt

        if word == "if":
            self.advance()
            cond = self.parse_condition()
            stmt = {"op": "if", "cond": cond, "then": self.parse_statement_block(), "else": []}
            if self.eat_word("else"):
                stmt["else"] = self.parse_statement_block()
            return stmt

        if word in SIMPLE_STATEMENTS:
            return self.parse_simple()

        raise self.error(f"未知语句关键字 {word!r}")

    def parse_statement_block(self) -> List[Dict[str, Any]]:
        self.expect("PUNCT", "{")
        stmts: List[Dict[str, Any]] = []
        while not self.at("PUNCT", "}"):
            if self.at("EOF"):
                raise self.error("语句块没有闭合")
            stmts.append(self.parse_statement())
        self.expect("PUNCT", "}")
        return stmts

    def _opt_as(self, stmt: Dict[str, Any]) -> None:
        # `as` 可省略：缺省名由 ir.assign_names() 按文档顺序补成 s1,s2,…
        if self.at_word("as"):
            self.advance()
            stmt["as"] = self.expect("IDENT").value

    def parse_simple(self) -> Dict[str, Any]:
        word = self.advance().value
        stmt: Dict[str, Any] = {"op": word}

        if word == "measure":
            if self.at("IDENT") and not self.at_word("as"):
                stmt["note"] = self.advance().value
            self._opt_as(stmt)
            fields = self.parse_block()
            stmt.update(fields)
            return stmt

        if word in ("read", "analyze", "classify", "vlm"):
            stmt["dataset" if word in ("read", "analyze") else "image"] = self.parse_dataset_ref()
            self._opt_as(stmt)
            stmt.update(self.parse_block())
            return stmt

        # decide / ask
        # decide 的主值是 intent；ask 的主值是 question（目标歧义澄清要问什么）。
        stmt["intent" if word == "decide" else "question"] = self.parse_value()
        self._opt_as(stmt)
        stmt.update(self.parse_block())
        return stmt

    # ── 条件 / 比较 ──
    def parse_condition(self) -> Any:
        if self.at("REF") or self.at("IDENT"):
            if self.peek(1).kind != "OP":
                tok = self.advance()
                return _ref_value(tok.value)
        return self.parse_compare()

    def parse_compare(self) -> Dict[str, Any]:
        tok = self.peek()
        if tok.kind not in ("REF", "IDENT", "NUMBER"):
            raise self.error(f"比较左侧需要引用或数值，实际是 {tok.value!r}")
        self.advance()
        left = _ref_value(tok.value) if tok.kind != "NUMBER" else _number(tok.value)
        op_tok = self.expect("OP")
        right_tok = self.peek()
        if right_tok.kind == "NUMBER":
            self.advance()
            right: Any = _number(right_tok.value)
        elif right_tok.kind in ("REF", "IDENT"):
            self.advance()
            right = _ref_value(right_tok.value)
        elif right_tok.kind == "STRING":
            self.advance()
            right = _unquote(right_tok.value)
        else:
            raise self.error(f"比较右侧无法解析：{right_tok.value!r}")
        return {"metric": left, "operator": op_tok.value, "threshold": right}

    def parse_dataset_ref(self) -> Any:
        tok = self.peek()
        if tok.kind == "NUMBER":
            self.advance()
            return _number(tok.value)
        if tok.kind in ("REF", "IDENT"):
            self.advance()
            value = _ref_value(tok.value)
            if "." not in value:
                value = f"${value}.dataset"
            elif not value.startswith("$"):
                value = "$" + value
            return value
        raise self.error(f"这里需要数据集引用，实际是 {tok.value!r}")


# ── 值转换 ──
def _alias(key: str) -> str:
    return FIELD_ALIASES.get(key, key)


def _unquote(text: str) -> str:
    if not (text.startswith('"') and text.endswith('"')):
        return text
    body = text[1:-1]
    return (body.replace("\\n", "\n").replace("\\t", "\t")
            .replace('\\"', '"').replace("\\\\", "\\"))


def _number(text: str, duration: bool = False) -> Any:
    for unit, factor in DURATION_FACTORS.items():
        if text.endswith(unit):
            raw = text[: -len(unit)]
            value = float(raw) * factor
            return int(value) if float(value).is_integer() else round(value, 6)
    value = float(text)
    return int(value) if value.is_integer() else value


def _ident_value(text: str) -> Any:
    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered == "none":
        return None
    return _ref_value(text)


def _ref_value(text: str) -> Any:
    """裸 `r1.contrast` → `$r1.contrast`；`sq.t1` 这类函数名保持原样。"""
    if text.startswith("$"):
        return text
    if "." in text and not text.startswith("sq."):
        return "$" + text
    return text


def parse(text: str) -> Dict[str, Any]:
    """`.qwf` 文本 → IR 字典（未 normalize、未 validate）。"""
    return _Parser(tokenize(text)).parse_workflow()


def parse_file(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return parse(handle.read())
