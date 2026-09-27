"""Factor expression DSL: whitelist parsing, type checks, canonical form and evaluation."""

import ast
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from alphasieve.factors import derived
from alphasieve.factors.ops import COMMUTATIVE, OPS
from alphasieve.search_space import SearchSpace
from alphasieve.util import sha256_hex

MAX_EXPRESSION_LENGTH = 600
BINOPS = {ast.Add: "add", ast.Sub: "sub", ast.Mult: "mul", ast.Div: "div"}
COMPARES = {ast.Gt: "gt", ast.Lt: "lt", ast.GtE: "ge", ast.LtE: "le"}


@dataclass(frozen=True)
class Node:
    op: str
    args: tuple["Node", ...] = ()
    value: object = None


@dataclass
class DSLIssue:
    name: str
    message: str


class DSLError(Exception):
    def __init__(self, issues: list[DSLIssue]):
        super().__init__("; ".join(f"{i.name}: {i.message}" for i in issues))
        self.issues = issues


@dataclass
class CompiledFactor:
    node: Node
    canonical: str
    candidate_hash: str
    nodes: int
    depth: int
    terminals: set[str]
    max_window: int | None
    windows: list[int] = field(default_factory=list)


def _to_node(tree: ast.AST, issues: list[DSLIssue]) -> Node:
    if isinstance(tree, ast.Expression):
        return _to_node(tree.body, issues)
    if isinstance(tree, ast.Constant) and isinstance(tree.value, (int, float)) and not isinstance(tree.value, bool):
        return Node("const", value=float(tree.value))
    if isinstance(tree, ast.Name):
        if tree.id in OPS:
            issues.append(DSLIssue("syntax", f"{tree.id} is an operator and must be called"))
        return Node("term", value=tree.id)
    if isinstance(tree, ast.Call):
        if not isinstance(tree.func, ast.Name):
            issues.append(DSLIssue("syntax", "only plain function calls are allowed"))
            return Node("const", value=0.0)
        if tree.keywords:
            issues.append(DSLIssue("syntax", f"keyword arguments are not allowed in {tree.func.id}"))
        if tree.func.id not in OPS:
            issues.append(DSLIssue("unknown_function", f"{tree.func.id} is not an allowed operator"))
        return Node(tree.func.id, tuple(_to_node(a, issues) for a in tree.args))
    if isinstance(tree, ast.BinOp) and type(tree.op) in BINOPS:
        return Node(BINOPS[type(tree.op)], (_to_node(tree.left, issues), _to_node(tree.right, issues)))
    if isinstance(tree, ast.UnaryOp) and isinstance(tree.op, (ast.USub, ast.UAdd)):
        inner = _to_node(tree.operand, issues)
        return Node("neg", (inner,)) if isinstance(tree.op, ast.USub) else inner
    if isinstance(tree, ast.Compare) and len(tree.ops) == 1 and type(tree.ops[0]) in COMPARES:
        return Node(COMPARES[type(tree.ops[0])], (_to_node(tree.left, issues), _to_node(tree.comparators[0], issues)))
    issues.append(DSLIssue("syntax", f"disallowed syntax: {type(tree).__name__}"))
    return Node("const", value=0.0)


def _fold(node: Node) -> Node:
    if node.op in ("const", "term"):
        return node
    args = tuple(_fold(a) for a in node.args)
    spec = OPS.get(node.op)
    if spec and spec[2] == "elem" and args and all(a.op == "const" for a in args):
        with np.errstate(all="ignore"):
            value = float(spec[0](*[a.value for a in args]))
        return Node("const", value=value)
    if node.op in COMMUTATIVE:
        args = tuple(sorted(args, key=canonical_string))
    return Node(node.op, args)


def canonical_string(node: Node) -> str:
    if node.op == "const":
        return format(node.value, ".10g")
    if node.op == "term":
        return str(node.value)
    return f"{node.op}({','.join(canonical_string(a) for a in node.args)})"


def _check(node: Node, space: SearchSpace, issues: list[DSLIssue], windows: list[int]) -> str:
    """Return the kind of the node: 'series' or 'const'."""
    if node.op == "const":
        return "const"
    if node.op == "term":
        if node.value not in space.terminals:
            issues.append(DSLIssue("unknown_terminal", f"{node.value} is not an allowed terminal"))
        return "series"
    if node.op not in OPS:
        return "series"
    _, argspec, _ = OPS[node.op]
    if len(node.args) != len(argspec):
        issues.append(DSLIssue("arity", f"{node.op} expects {len(argspec)} arguments, got {len(node.args)}"))
        return "series"
    kinds = []
    for letter, arg in zip(argspec, node.args, strict=True):
        if letter == "w":
            ok = arg.op == "const" and float(arg.value).is_integer() and int(arg.value) in space.windows
            if not ok:
                shown = canonical_string(arg)
                issues.append(DSLIssue("window_not_allowed", f"{node.op} window {shown} not in {list(space.windows)}"))
            else:
                windows.append(int(arg.value))
            continue
        if letter == "c":
            if arg.op != "const":
                issues.append(DSLIssue("type_error", f"{node.op} expects a numeric constant"))
            elif node.op == "signed_power" and not 0.1 <= abs(arg.value) <= 5:
                issues.append(DSLIssue("type_error", f"{node.op} exponent must be within [0.1, 5]"))
            continue
        kind = _check(arg, space, issues, windows)
        if letter == "x" and kind != "series":
            issues.append(DSLIssue("type_error", f"{node.op} expects a series argument"))
        kinds.append(kind)
    return "series" if "series" in kinds or OPS[node.op][2] != "elem" else "const"


def _measure(node: Node) -> tuple[int, int, set[str]]:
    if node.op == "const":
        return 1, 1, set()
    if node.op == "term":
        return 1, 1, {node.value}
    total, depth, terms = 1, 0, set()
    for arg in node.args:
        n, d, t = _measure(arg)
        total += n
        depth = max(depth, d)
        terms |= t
    return total, depth + 1, terms


def compile_expression(expression: str, space: SearchSpace) -> CompiledFactor:
    issues: list[DSLIssue] = []
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise DSLError([DSLIssue("syntax", f"expression longer than {MAX_EXPRESSION_LENGTH} characters")])
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise DSLError([DSLIssue("syntax", f"cannot parse: {exc.msg}")]) from None
    node = _to_node(tree, issues)
    if issues:
        raise DSLError(issues)
    node = _fold(node)
    windows: list[int] = []
    kind = _check(node, space, issues, windows)
    if kind == "const":
        issues.append(DSLIssue("constant_expression", "expression does not depend on any data"))
    nodes, depth, terminals = _measure(node)
    if nodes > space.max_nodes:
        issues.append(DSLIssue("complexity_nodes", f"{nodes} nodes > {space.max_nodes}"))
    if depth > space.max_depth:
        issues.append(DSLIssue("complexity_depth", f"depth {depth} > {space.max_depth}"))
    if len(terminals) > space.max_terminals:
        issues.append(DSLIssue("complexity_terminals", f"{len(terminals)} terminals > {space.max_terminals}"))
    if issues:
        raise DSLError(issues)
    canonical = canonical_string(node)
    return CompiledFactor(
        node=node, canonical=canonical, candidate_hash=sha256_hex(canonical)[:16], nodes=nodes, depth=depth,
        terminals=terminals, max_window=max(windows) if windows else None, windows=sorted(set(windows)),
    )


class EvalContext:
    def __init__(self, panel):
        self.panel = panel
        self._groups = None
        self._size = None
        self._memo: dict[str, object] = {}

    def terminal(self, name: str) -> pd.DataFrame:
        return derived.terminal(self.panel, name)

    @property
    def groups(self) -> pd.Series:
        if self._groups is None:
            self._groups = self.panel.industry()
        return self._groups

    @property
    def size(self) -> pd.DataFrame:
        if self._size is None:
            self._size = np.log(self.panel.wide("circ_mv").where(self.panel.wide("circ_mv") > 0))
        return self._size


def evaluate_node(node: Node, ctx: EvalContext):
    key = canonical_string(node)
    if key in ctx._memo:
        return ctx._memo[key]
    if node.op == "const":
        result = node.value
    elif node.op == "term":
        result = ctx.terminal(node.value)
    else:
        fn, argspec, kind = OPS[node.op]
        args = []
        for letter, arg in zip(argspec, node.args, strict=True):
            args.append(int(arg.value) if letter == "w" else arg.value if letter == "c" else evaluate_node(arg, ctx))
        if kind == "group":
            result = fn(args[0], ctx.groups, ctx.size) if node.op == "cs_neutralize" else fn(args[0], ctx.groups)
        else:
            with np.errstate(all="ignore"):
                result = fn(*args)
        if isinstance(result, pd.DataFrame):
            result = result.replace([np.inf, -np.inf], np.nan)
    ctx._memo[key] = result
    return result


def evaluate(compiled: CompiledFactor, panel, ctx: EvalContext | None = None) -> pd.DataFrame:
    ctx = ctx or EvalContext(panel)
    result = evaluate_node(compiled.node, ctx)
    if not isinstance(result, pd.DataFrame):
        raise DSLError([DSLIssue("constant_expression", "expression evaluated to a constant")])
    return result.reindex(index=panel.dates, columns=panel.codes).astype(float)
