"""dsl.textdsl.lexer - `.qwf` 的词法分析（手写，零依赖）。

为什么不用 lark：实验室机可能离线，且失败信息必须是"第几行第几列 + 期望什么"，
手写 90 行就能做到，比引一个 C 扩展依赖更稳。
"""

import re
from dataclasses import dataclass
from typing import List

TOKEN_RE = re.compile(
    r"""
      (?P<WS>\s+)
    | (?P<COMMENT>\#[^\n]*)
    | (?P<STRING>"(?:[^"\\]|\\.)*")
    | (?P<NUMBER>-?\d+(?:\.\d+)?(?:us|ms|s|m|h)?(?![A-Za-z0-9_]))
    | (?P<REF>\$[A-Za-z_][A-Za-z0-9_.]*)
    | (?P<IDENT>[A-Za-z_][A-Za-z0-9_.]*)
    | (?P<OP>>=|<=|==|!=|>|<)
    | (?P<PUNCT>[{}\[\],=])
    """,
    re.VERBOSE,
)

KIND_WS = "WS"


@dataclass
class Token:
    kind: str
    value: str
    line: int
    col: int

    def __repr__(self) -> str:
        return f"Token({self.kind}, {self.value!r}, {self.line}:{self.col})"


class DSLSyntaxError(Exception):
    def __init__(self, message: str, line: int = 0, col: int = 0):
        self.message = message
        self.line = line
        self.col = col
        super().__init__(str(self))

    def __str__(self) -> str:
        if self.line:
            return f"line {self.line}:{self.col}: {self.message}"
        return self.message


def tokenize(text: str) -> List[Token]:
    tokens: List[Token] = []
    line, line_start = 1, 0
    pos = 0
    while pos < len(text):
        match = TOKEN_RE.match(text, pos)
        if not match:
            col = pos - line_start + 1
            raise DSLSyntaxError(f"无法识别的字符 {text[pos]!r}", line, col)
        kind = match.lastgroup
        value = match.group()
        if kind != KIND_WS and kind != "COMMENT":
            tokens.append(Token(kind, value, line, pos - line_start + 1))
        if "\n" in value:
            line += value.count("\n")
            line_start = pos + value.rindex("\n") + 1
        pos = match.end()
    tokens.append(Token("EOF", "", line, pos - line_start + 1))
    return tokens
