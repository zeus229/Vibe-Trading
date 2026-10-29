"""Per-role validation: what each declared figure has to survive.

The model declares what each measurement-shaped number IS (spec §2); this
module checks that declaration against ledger evidence (spec §4). It reads no
prose word: roles come from the model, shape from :mod:`figures`.
"""

from __future__ import annotations

import ast
import json
from decimal import Decimal, ROUND_HALF_UP
import math
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from src.agent.grounding.derived import validate_operands

from src.agent.grounding.identity import (
    _CANONICAL_SYMBOL_RE,
    _normalize_symbol,
    _scan_symbols,
)
from src.agent.grounding import identity_checks  # noqa: F401  (registers declared checks)
from src.agent.grounding import figures_checks  # noqa: F401  (registers declared checks)
from src.agent.grounding.registry import GROUNDING_CHECKS, grounding_check
from src.agent.grounding.evidence import (
    EvidenceRecord,
    _record_matches_entity,
    _is_metadata_count_leaf,
    _is_number,
    _is_price_kind,
    _keyed_metric_map_kind,
    _metric_kind_for_path,
    _price_field_for_path,
    _timestamp_matches_claim_date,
    tail_risk_identity,
)
from src.agent.grounding.figures import (
    ROUNDED_BAND,
    Declaration,
    Figure,
    FiguresBlock,
    _lines_with_offsets,
    segment_bounds,
)

import re

#: An answer that relabels a locked listed identity as private contradicts the
#: resolver, which is an identity finding rather than a figure finding.
_PRIVATE_ASSERTION_RE = re.compile(
    r"(?:\b(?:is|remains|still)\s+(?:an?\s+)?(?:private company|privately held)\b|"
    r"\bnot publicly traded\b|\bunlisted company\b|"
    r"(?:是|仍是|属于)(?:一家)?(?:私人|私营|非上市)公司|未上市|没有上市)",
    re.IGNORECASE,
)

# Positive wording for a market quote. Currency alone is not sufficient: EPS,
# revenue, net income, and other financial amounts are also currency-marked.
_MARKET_PRICE_CONTEXT_RE = re.compile(
    r"(?:"
    r"\bprice\s+target\b|\btarget\s+price\b|"
    r"\bentry\s+price\b|\bbuy(?:ing)?\s+price\b|\bpurchase\s+price\b|"
    r"\bclosing\s+price\b|\bopening\s+price\b|"
    r"\bclos(?:e|ed)\s+at\b|\bopen(?:ed)?\s+at\b|"
    r"\bintraday\s+high\b|\bintraday\s+low\b|"
    r"\bprice\s+support\b|\bprice\s+resistance\b|"
    r"precio\s+de\s+cierre|precio\s+de\s+apertura|precio\s+objetivo|"
    r"precio\s+de\s+entrada|precio\s+de\s+compra|"
    r"cerr[oó]\s+en|abri[oó]\s+en|"
    r"cotizaci[oó]n|cotiz[oó]\b|"
    r"m[aá]ximo\s+intradiario|m[ií]nimo\s+intradiario|"
    r"soporte\s+de\s+precio|resistencia\s+de\s+precio|"
    r"nivel\s+de\s+soporte|nivel\s+de\s+resistencia|"
    r"开盘价|收盘价|最高价|最低价|现价|目标价|止损价|买入价|入场价|支撑位|阻力位|报价"
    r")",
    re.IGNORECASE,
)

def _is_asistente_casa_tool(tool: Any) -> bool:
    """Match adapter tools with or without MCP's transport prefix."""
    name = str(tool or "").casefold()
    if name.startswith("mcp_"):
        name = name[4:]
    return name.startswith("asistente_casa_")


# Loader ids are ASCII but the answer follows the user's language, so a source
# is surfaced by any alias ("数据来源：腾讯财经" for ``tencent``).
_SOURCE_ALIASES = {
    "akshare": ("akshare", "ak share"),
    "baostock": ("baostock",),
    "binance": ("binance", "币安"),
    "ccxt": ("ccxt",),
    "eastmoney": ("eastmoney", "东方财富", "东财"),
    "futu": ("futu", "富途"),
    "mootdx": ("mootdx", "通达信"),
    "okx": ("okx", "欧易"),
    "pykrx": ("pykrx", "krx"),
    "sina": ("sina", "新浪"),
    "stooq": ("stooq",),
    "tencent": ("tencent", "腾讯"),
    "tushare": ("tushare",),
    "yahoo": ("yahoo", "雅虎"),
    "yfinance": ("yfinance", "yahoo", "雅虎"),
}

_CURRENCY_ALIASES = {
    "USD": ("usd", "us$", "美元", "美金"),
    # ¥ is also the yen sign, but ``_infer_currency`` maps no venue to JPY;
    # adding a JPY venue means revisiting this entry.
    "CNY": ("cny", "cnh", "rmb", "人民币", "¥", "￥"),
    "HKD": ("hkd", "hk$", "港元", "港币"),
    "KRW": ("krw", "韩元", "韩圜"),
    "INR": ("inr", "印度卢比", "卢比"),
    "CAD": ("cad", "c$", "加元", "加拿大元"),
    "GBP": ("gbp", "£", "英镑"),
    "VND": ("vnd", "₫", "越南盾"),
    "BRL": ("brl", "r$", "real brasileiro", "reais", "巴西雷亚尔"),
    "ARS": ("ars", "ar$", "阿根廷比索"),
}

# "元" counts as CNY only when no other currency's character precedes it
# (港元/美元/日元), or a Hong Kong listing would satisfy a CNY requirement.
_OTHER_CURRENCY_PREFIXES = "港美日欧韩台新加澳"

#: Relative band a value must fall in to count as matching evidence.
_TOLERANCE = 0.005

#: Most exact refs a correction lists for a ref whose call id names nothing.
_MAX_FIELD_REF_CANDIDATES = 5

# a.0.b and a[0].b name the same list element; evidence paths are
# emitted with brackets, so refs are compared in that spelling.
_DOTTED_INDEX_RE = re.compile(r"(?<=\w)\.(\d+)(?=\.|\[|$)")
_INDEX_RE = re.compile(r"\[\d+\]")
_MAX_INDEXED_REF_CANDIDATES = 12
_MAX_CONTAINER_REF_CANDIDATES = 5
_MAX_CALL_REF_CANDIDATES = 5

# Table-row labels that unambiguously describe an aggregate rather than an
# instrument. This is intentionally narrow: company/name-shaped labels such as
# "Acme Corp" never qualify merely because their declaration is unscoped.
_SUMMARY_ROW_LABEL_RE = re.compile(
    r"^(?:"
    r"total(?:\s+(?:conjunto|combined|general|portfolio))?|"
    r"subtotal|"
    r"sum(?:\s+(?:of\s+the\s+)?(?:top\s+)?\w+)?|"
    r"suma(?:\s+de\s+(?:las|los)\s+\w+)?|"
    r"aggregate|agregado|combined|conjunto"
    r")$",
    re.IGNORECASE,
)


def _index_normalized(path: str) -> str:
    """Spell dotted collection indices with brackets."""
    return _DOTTED_INDEX_RE.sub(r"[\1]", path)

#: A plain integer is read as a price only for an instrument quoted in the
#: thousands (600519.SH, an index, BTC). Below that, a prose integer is a window,
#: a horizon or a count ("20 日均线", "200-day") and stays unchecked.
_INTEGER_PRICE_FLOOR = 1000.0

#: Price fields a rejected prose figure is pointed at, in order (#1433).
_CITABLE_FIELDS = ("close", "price", "adj_close")


_TABLE_CELL_REF = re.compile(r"rows\[(\d+)\]\[(\d+)\]")


def _backtest_summary_alias(field: str) -> str | None:
    """Map a ``summary.*`` path of the backtest result to its run-card path."""
    if field == "summary.initial_cash":
        return "backtest.initial_cash"
    for prefix in ("metrics.", "structured_metrics."):
        if field.startswith("summary." + prefix):
            return field[len("summary."):]
    return None


@dataclass(frozen=True)
class ValidationResult:
    """Final-answer grounding decision.

    ``released_text`` is the draft without its declaration block, which is a
    contract with the gate and never reaches the user.

    ``passed_figures`` names the measured figures the gate checked and let
    through, as written, so the correction prompt can tell the model what to
    keep, not only what to fix.
    """

    valid: bool
    issues: list[dict[str, Any]] = field(default_factory=list)
    released_text: str = ""
    passed_figures: tuple[str, ...] = ()
    # Exact observed declarations bound to clean visible occurrences. This is
    # retained for the bounded correction contract, which must preserve both
    # the number and its source reference.
    passed_claims: tuple[dict[str, Any], ...] = ()


def _close(value: float, target: float) -> bool:
    """Whether two values agree inside the evidence tolerance."""
    return abs(value - target) <= max(abs(target) * _TOLERANCE, 1e-9)


def _close_any(value: float, targets: Iterable[float]) -> bool:
    """Whether ``value`` agrees with any of ``targets``."""
    return any(_close(value, target) for target in targets)


def _nearest(value: float, targets: Iterable[float], limit: int = 3) -> list[float]:
    """The observed values closest to a rejected figure.

    Args:
        value: The rejected figure's value.
        targets: Every value the relevant evidence pool holds.
        limit: How many to name.

    Returns:
        Up to ``limit`` distinct observed values, closest first.
    """
    unique = sorted({float(target) for target in targets}, key=lambda item: (abs(item - value), item))
    return unique[:limit]


def _written_half_unit(text: str) -> float:
    """Half a unit of the last digit a figure was written with ("37%" -> 0.5)."""
    body = text.strip().rstrip("%％").strip()
    decimals = len(body.split(".", 1)[1]) if "." in body else 0
    return 0.5 * 10.0 ** (-decimals)


def _explicit_sign(text: str) -> int:
    """``1`` or ``-1`` when a figure was written with a sign ("+36.8%"), else ``0``."""
    head = text.strip()[:1]
    return 1 if head == "+" else -1 if head == "-" else 0


def _is_percent_field(name: str) -> bool:
    """Whether a field name says its value is already in percentage points."""
    lowered = name.lower()
    return name == "document.ratio" or "percent" in lowered or "pct" in lowered or lowered.endswith("%")


def _is_plain_count(figure: Figure) -> bool:
    """Whether a figure can be a count or a parameter the model chose.

    A weight, threshold, window, probability or multiplier is unchecked; a
    figure with a currency mark is a price or an amount and is checked as
    observed. A price column is refused before this is asked.
    """
    return not figure.currency


def _note_tokens(note: str) -> set[str]:
    """Citation fragments in a note: CJK character pairs and ASCII words of four letters or more.

    Pairs, because Chinese is not space-separated: "财报毛利率桥" is visible in
    "毛利率下降" the way "gross margin bridge" is visible in "gross margin".
    Four letters, because "the" or "and" would make any English line visible.

    Args:
        note: A declaration's free-text note.

    Returns:
        The casefolded fragments; digits, spaces and punctuation separate runs.
    """
    tokens: set[str] = set()
    run, kind = "", ""
    for char in note.casefold() + " ":
        if "㐀" <= char <= "鿿":
            current = "cjk"
        elif char.isascii() and char.isalpha():
            current = "ascii"
        else:
            current = ""
        if current != kind:
            if kind == "cjk" and len(run) >= 2:
                tokens.update(run[index : index + 2] for index in range(len(run) - 1))
            elif kind == "ascii" and len(run) >= 4:
                tokens.add(run)
            run, kind = "", current
        if current:
            run += char
    return tokens


def _strip_sign(node: ast.AST) -> ast.AST:
    """The operand under any unary ``+`` or ``-``."""
    while isinstance(node, ast.UnaryOp):
        node = node.operand
    return node


def _is_sum(node: ast.AST) -> bool:
    """Whether a node is a binary ``+`` or ``-``."""
    return isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub))


def _is_unit_factor(node: ast.AST) -> bool:
    """Whether a sum is ``1 ± c ± …`` over constants: a percentage change as a factor."""
    node = _strip_sign(node)
    if not _is_sum(node):
        return False
    while _is_sum(node):
        if not isinstance(_strip_sign(node.right), ast.Constant):
            return False
        node = _strip_sign(node.left)
    return isinstance(node, ast.Constant) and node.value == 1


def _unanchored_term(tree: ast.Expression, observed: Callable[[float], bool]) -> bool:
    """Whether a formula adds or subtracts a term holding no observed operand.

    Multiplicative constants are free, so two forms are not offsets: ``1 ± c``
    used as a factor ("0.666 × (1 − 0.03)") and the 1 beside a quotient
    ("0.666 / 1.053 − 1"). Neither reaches a value a free multiplier could not.
    Both are recognised by structure: near a price of 1 the constant 1 is itself
    within tolerance of an observation.

    Args:
        tree: The parsed formula.
        observed: Whether an operand is a value this session observed.

    Returns:
        True when some added or subtracted term is unanchored.
    """

    exponents = {
        id(item.right)
        for item in ast.walk(tree)
        if isinstance(item, ast.BinOp) and isinstance(item.op, ast.Pow)
    }

    def signed_constants(item: ast.AST, sign: float = 1.0) -> list[float]:
        if id(item) in exponents:
            return []
        if isinstance(item, ast.UnaryOp) and isinstance(item.op, (ast.UAdd, ast.USub)):
            next_sign = -sign if isinstance(item.op, ast.USub) else sign
            return signed_constants(item.operand, next_sign)
        if isinstance(item, ast.Constant) and _is_number(item.value):
            return [sign * float(item.value)]
        values: list[float] = []
        for child in ast.iter_child_nodes(item):
            values.extend(signed_constants(child, sign))
        return values

    def anchored(node: ast.AST) -> bool:
        return any(observed(value) for value in signed_constants(node))

    def visit(node: ast.AST, factor: bool) -> bool:
        node = _strip_sign(node)
        if not isinstance(node, ast.BinOp):
            return False
        if not _is_sum(node):
            return visit(node.left, True) or visit(node.right, True)
        if factor and _is_unit_factor(node):
            return False
        for raw_side, raw_other in ((node.left, node.right), (node.right, node.left)):
            side, other = _strip_sign(raw_side), _strip_sign(raw_other)
            unit_beside_ratio = (
                isinstance(side, ast.Constant)
                and side.value == 1
                and isinstance(other, ast.BinOp)
                and isinstance(other.op, ast.Div)
            )
            zero_identity = (
                isinstance(side, ast.Constant)
                and _is_number(side.value)
                and float(side.value) == 0.0
            )
            if (
                not _is_sum(side)
                and not unit_beside_ratio
                and not zero_identity
                and not anchored(raw_side)
            ):
                return True
        return visit(node.left, False) or visit(node.right, False)

    return visit(tree.body, False)


def _evaluate_formula(
    expression: str,
    *,
    operand_transform: Callable[[float], float] | None = None,
) -> tuple[float, list[float], ast.Expression] | None:
    """Evaluate a numeric ``+ - * /`` expression without executing code.

    Args:
        expression: An arithmetic run, possibly using ``× ÷ −`` and commas.
        operand_transform: Optional unit conversion for numeric operands;
            recorded input magnitudes and power exponents remain unchanged.

    Returns:
        ``(result, operands, parsed tree)``, or None when the run is not a
        well-formed expression over at least two numeric operands.
    """
    normalized = (
        expression.replace("×", "*")
        .replace("✕", "*")
        .replace("÷", "/")
        .replace("−", "-")
        .replace("–", "-")
        .replace("（", "(")
        .replace("）", ")")
        .replace(",", "")
        .replace("²", "**2")
        .replace("³", "**3")
        .replace("^", "**")
        .strip()
    )
    # "12.87% − 11.36%" is 0.1287 − 0.1136: an operand written as a percent is
    # the fraction the evidence holds, and "0.666 × (1 − 3%)" means 0.97.
    normalized = _PERCENT_OPERAND_RE.sub(
        lambda match: format(float(match.group(1)) / 100.0, ".12g"), normalized
    ).replace("%", "").replace("％", "")
    if not normalized:
        return None
    try:
        tree = ast.parse(normalized, mode="eval")
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return None
    inputs: list[float] = []

    def visit(node: ast.AST, *, unit_one: bool = False, factor: bool = False) -> float:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and _is_number(node.value):
            # Preserve decimal syntax for exact coefficient normalization;
            # rebuilding it from an evaluated float loses cancellations.
            setattr(node, "_grounding_literal", ast.get_source_segment(normalized, node))
            value = float(node.value)
            inputs.append(value)
            return operand_transform(value) if operand_transform and not (unit_one and value == 1) else value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            if isinstance(node.op, ast.UAdd):
                return value
            if isinstance(node.operand, ast.Constant) and inputs:
                inputs[-1] = -value
            return -value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            # A square or a cube (HHI is a sum of squared weights). The
            # exponent is part of the operator, not an operand.
            if not _is_small_exponent(node.right):
                raise ValueError("unsupported exponent")
            return visit(node.left) ** int(node.right.value)
        if isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)
        ):
            multiplicative = isinstance(node.op, (ast.Mult, ast.Div))
            unit_factor = factor and _is_unit_factor(node)
            left = visit(node.left, factor=multiplicative, unit_one=(
                multiplicative or unit_factor or (isinstance(node.right, ast.BinOp) and isinstance(node.right.op, ast.Div))
            ))
            right = visit(node.right, factor=multiplicative, unit_one=(
                multiplicative or unit_factor or (isinstance(node.left, ast.BinOp) and isinstance(node.left.op, ast.Div))
            ))
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if right == 0:
                raise ValueError("division by zero")
            return left / right
        raise ValueError("unsupported formula")

    try:
        value = visit(tree)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None
    if len(inputs) < 2 or not math.isfinite(value):
        return None
    return value, inputs, tree


#: A number written with a percent sign inside a formula.
_PERCENT_OPERAND_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[%％]")


def _is_small_exponent(node: ast.AST) -> bool:
    """Whether a power's exponent is a literal 2 or 3."""
    return isinstance(node, ast.Constant) and node.value in (2, 3) and not isinstance(node.value, bool)


#: A bracketed aside holding a word: "（收益差，约−1.17pp）", "(portfolio)".
_ANNOTATION_RE = re.compile(r"[（(\[][^（）()\[\]]*?(?:[\u3400-\u9fff]|[A-Za-z]{2})[^（）()\[\]]*[）)\]]")

#: A label or unit written against a number: a CJK run, or an ASCII word of
#: two letters or more ("Sharpe", "RP", "pp"). One letter is kept, so "1e3"
#: stays a number and "5 x 3" stays unreadable rather than becoming "5 3".
_LABEL_RE = re.compile(r"[\u3400-\u9fff]+|[A-Za-z]{2,}")


def _without_labels(text: str) -> str:
    """A note with its words removed, so the arithmetic between them can be read.

    "等权Sharpe 0.692 − 风险平价 0.651" is the arithmetic "0.692 − 0.651";
    an aside in brackets goes whole, because the number inside it
    ("约−1.17pp") is a restatement of the result, not an operand. Nothing is
    read from the words themselves.
    """
    return _LABEL_RE.sub(" ", _ANNOTATION_RE.sub(" ", text))


def _formula_in_note(note: str) -> tuple[float, list[float], ast.Expression] | None:
    """Find the derivation a note states.

    The whole note is tried first, then each segment between result separators
    ("0.666 × 0.97 = 0.646"); separators are punctuation, not vocabulary.
    An ASCII colon instead delimits descriptive labels and one complete numeric
    expression; labels cannot contain arithmetic, and the suffix loses no words.

    Args:
        note: The declaration's free-text note.

    Returns:
        ``(result, operands, parsed tree)`` for the first parseable segment, or None.
    """
    # An explicit ASCII label separator requires a complete arithmetic suffix.
    # Do not strip trailing words or choose between formulas in the description.
    if ":" in note:
        *labels, expression = note.split(":")
        if any(
            not label.strip()
            or re.search(r"[+*/×✕÷−²³^]|\d\s*[-–]", label)
            or _formula_in_note(label) is not None
            for label in labels
        ):
            return None
        if not re.fullmatch(r"[\d\s.eE+*/()%％,×✕÷−–（）²³^\-]+", expression):
            return None
        return _evaluate_formula(expression)
    # A note with an ASCII colon returned above, so no separator list below needs it.
    candidates = [note]
    parts = [note]
    for separator in ("≈", "≒", "＝", "=", "→", "->"):
        parts = [piece for part in parts for piece in part.split(separator)]
    # A formula followed by its explanation ("(a − b) / b，自高点回撤"). A bare
    # "," is not split: it groups thousands inside a formula.
    for separator in ("，", "；", "：", "; ", ", "):
        parts = [piece for part in parts for piece in part.split(separator)]
    candidates.extend(part for part in parts if part.strip())
    # Only once the note as written fails: its words removed, whole and in parts.
    unlabelled = _without_labels(note)
    for separator in ("≈", "≒", "＝", "=", "→", "->", "，", "；", "：", "; ", ", "):
        unlabelled = " \n ".join(unlabelled.split(separator))
    candidates.extend(part for part in unlabelled.split(" \n ") if part.strip())
    for candidate in candidates:
        evaluated = _evaluate_formula(candidate)
        if evaluated is not None:
            return evaluated
    return None


def _has_explicit_percent_scale(note: str) -> bool:
    """Whether a percentage formula explicitly converts a fraction by 100."""
    return bool(re.search(r"(?:×|✕|\*)\s*100(?:\.0+)?\b", note))


def _has_trailing_percent_factor(note: str) -> bool:
    """Whether the note's outer operation is the literal ``* 100`` unit factor.

    Upstream's ``_derivation`` strips exactly this factor and compares the
    remaining fraction as percentage points, so the result is already a fraction.
    """
    evaluated = _formula_in_note(note)
    if evaluated is None:
        return False
    body = evaluated[2].body
    return (
        isinstance(body, ast.BinOp)
        and isinstance(body.op, ast.Mult)
        and isinstance(body.right, ast.Constant)
        and _is_number(body.right.value)
        and body.right.value == 100
    )


class _PolicyMixin:
    """Policy behaviour of :class:`GroundingLedger`."""

    def _validate_identity(self, content: str) -> list[dict[str, Any]]:
        """Validate aggregate state and listed/private contradictions."""
        return GROUNDING_CHECKS.run_phase("identity", self, content)

    def _validate_figures(
        self,
        content: str,
        block: FiguresBlock,
        figures: Sequence[Figure],
    ) -> list[dict[str, Any]]:
        """Check every measurement-shaped number against its declared role.

        Args:
            content: The candidate answer.
            block: Its parsed declaration block.
            figures: Every number located in its prose.

        Returns:
            One issue per figure that its role does not survive, plus one per
            malformed declaration line and the provenance findings.
        """
        issues: list[dict[str, Any]] = GROUNDING_CHECKS.run_phase("declarations", self, content)
        records = self._comparable_price_records()
        # Symbol resolution is broader than price comparison. A session may hold
        # quotes for the primary listing and only non-price evidence for another
        # explicitly named instrument (for example issuer fundamentals). If claim
        # identity only sees price-comparable records, that second symbol becomes
        # invisible and its referenced evidence is later filtered under the
        # primary symbol. Keep numeric matching fail-closed, but let any observed
        # numeric evidence contribute its explicit symbol to claim resolution.
        symbol_records = [
            record
            for record in self._evidence
            if record.status == "observed"
            and record.value is not None
            and record.symbol
        ]
        # Preserve the existing price-derived document fallback. A report may
        # mention a secondary instrument only for fundamentals; that should make
        # explicit claims about the secondary resolvable without making otherwise
        # unattributed primary-listing figures ambiguous.
        document_symbol = self._symbol_for_claim(content, records)
        positions = _lines_with_offsets(content)
        line_symbols = [
            self._symbol_for_claim(line, symbol_records) for line, _ in positions
        ]
        declared_observed = {
            declaration.value
            for declaration in block.declarations
            if declaration.role == "observed"
        }
        # Multipliers a declared derivation uses: a count equal to one is that factor.
        derived_constants = [
            operand
            for declaration in block.declarations
            if declaration.role == "derived"
            for evaluated in [_formula_in_note(declaration.note)]
            if evaluated is not None
            for operand in evaluated[1]
        ]
        declaration_by_span = self._declarations_by_span(
            block, figures, content=content, line_symbols=line_symbols,
            symbol_records=symbol_records,
        )

        # The hidden declaration alone cannot turn invented data into a free
        # coefficient. A chosen parameter needs a count declaration AND its
        # value and label in the visible answer. No prose vocabulary decides
        # its role; the same label-matching contract already protects citations.
        scalar_parameters: list[float] = []
        for parameter in figures:
            declared = block.match(parameter.value, parameter.percent, parameter.digits)
            if (
                declared is None
                or declared.role != "count"
                or parameter.shape not in ("measured", "bare")
                or parameter.currency
                or parameter.column
            ):
                continue
            line = positions[parameter.line][0] if 0 <= parameter.line < len(positions) else ""
            # Citation tokens cover labels embedded in ordinary prose. Also
            # accept a complete label in any script, including a short symbol
            # such as "w"; it must not match a letter inside an unrelated word.
            label = declared.note.strip()
            label_visible = bool(label) and (
                bool(_note_tokens(label) & _note_tokens(line))
                or re.search(
                    r"(?<![^\W\d_])" + re.escape(label) + r"(?![^\W\d_])",
                    line,
                    re.IGNORECASE,
                ) is not None
            )
            if label_visible:
                scalar_parameters.append(parameter.value / 100 if parameter.percent else parameter.value)
        checked_price = False
        for figure in figures:
            if figure.shape not in ("measured", "bare"):
                continue
            declaration = declaration_by_span.get((figure.start, figure.end))
            symbol = self._figure_symbol(
                content,
                figure,
                declaration,
                line_symbols,
                document_symbol,
                symbol_records,
            )
            if figure.shape == "bare" and not self._poses_as_price(
                figure, self._price_band(symbol, records)
            ):
                continue
            if declaration is not None:
                found = GROUNDING_CHECKS.run(
                    "figure-symbol", self, content, figure, declaration, symbol, line_symbols, symbol_records,
                )
                if found:
                    issues.extend(found)
                    continue
            if declaration is None:
                found = GROUNDING_CHECKS.run(
                    "undeclared-figure", self, content, figure, symbol, records, block
                )
                issues.extend(found)
                # An undeclared figure rejected at the declaration boundary did
                # not trigger provenance in the original walk.
                if not any(issue["code"] == "figure_undeclared" for issue in found):
                    checked_price = True
                continue
            role = declaration.role
            found = GROUNDING_CHECKS.run("figure-price-column", self, figure, role, symbol)
            if found:
                issues.extend(found)
                continue
            if role == "count":
                posing = (
                    _is_plain_count(figure)
                    and self._poses_as_price(figure, self._price_band(symbol, records))
                    and not _close_any(figure.value, derived_constants)
                )
                if not _is_plain_count(figure) or posing:
                    checked_price = True
                    issues.extend(
                        GROUNDING_CHECKS.run("figure-count", self,
                            figure,
                            declaration,
                            symbol,
                            records,
                            why=(
                                "it sits in the instrument's observed price range and no "
                                "declared derivation uses it"
                                if posing
                                else "a count cannot carry a currency mark"
                            ),
                        )
                    )
                continue
            if role == "cited":
                line = (
                    positions[figure.line][0]
                    if 0 <= figure.line < len(positions)
                    else ""
                )
                issues.extend(
                    GROUNDING_CHECKS.run("figure-cited", self, figure, declaration, symbol, declared_observed, line)
                )
                continue
            checked_price = True
            if role == "derived":
                issues.extend(
                    GROUNDING_CHECKS.run("figure-derived", self, figure, declaration, symbol, records, scalar_parameters)
                )
                continue
            if role == "proposed":
                issues.extend(
                    GROUNDING_CHECKS.run("figure-proposed", self, figure, declaration, symbol, records, scalar_parameters)
                )
                continue
            issues.extend(GROUNDING_CHECKS.run("figure-observed", self, figure, declaration, symbol, records))
        market_records = self._price_records()
        if checked_price and market_records:
            issues.extend(self._validate_price_provenance(content, market_records))

        # Keep upstream issue codes/reasons intact while restoring additive semantic
        # metadata needed by bounded recovery and selective redaction. Attach it only
        # to numeric figure issues; other issue classes remain explicitly unclassified.
        figures_by_span = {(figure.start, figure.end): figure for figure in figures}
        for issue in issues:
            if issue.get("code") not in {"numeric_claim_unavailable", "numeric_claim_conflict"}:
                continue
            span = issue.get("span")
            if not isinstance(span, (list, tuple)) or len(span) != 2:
                continue
            figure = figures_by_span.get((span[0], span[1]))
            if figure is None:
                continue
            declaration = declaration_by_span.get((figure.start, figure.end))
            issue.update(
                percent=figure.percent,
                currency=figure.currency,
                market_price=self._figure_is_market_price(content, figure, declaration),
            )
        # Producers supply hints; only this boundary may authorize a derived
        # preservation obligation. It sees the original declaration, so a calc
        # cannot silently fill omitted refs or replace explicit derived intent.
        for issue in issues:
            span = issue.get("span")
            figure = figures_by_span.get(tuple(span)) if isinstance(span, (list, tuple)) else None
            if figure is None:
                continue
            declaration = declaration_by_span.get((figure.start, figure.end))
            if declaration is None and issue.get("role") == "derived":
                candidates = [item for item in block.declarations
                              if item.role == "derived" and item in block.compatible_declarations(
                                  figure.value, item.percent, figure.digits)]
                declaration = candidates[0] if len(candidates) == 1 else None
            written = self._written_symbol(content, figure, line_symbols, symbol_records)
            symbol = written if written in {record.symbol for record in symbol_records} else issue.get("symbol")
            if declaration is None and issue.get("role") != "derived" and not figure.percent and _has_ratio_display_context(content, figure):
                displays = [item for item in block.compatible_declarations(
                    figure.value, True, figure.digits) if item.role == "observed"]
                if len(displays) == 1 and not figure.currency and not figure.column:
                    declaration = self._ratio_display_declaration(figure, displays[0])
                    if declaration is not None:
                        issue["role"] = "derived"
            self._authorize_derived_repair(
                issue, figure, declaration, symbol, records, block.declarations)
        return issues

    def _authorize_derived_repair(
        self, issue: dict[str, Any], figure: Figure,
        declaration: Declaration | None, symbol: str | None,
        records: Sequence[EvidenceRecord], declarations: Sequence[Declaration] = (),
    ) -> None:
        """Turn a candidate into a preservation promise only after full proof.

        Structured derived intent is authoritative: association can repair its
        refs, but a deterministic calculation cannot supply missing operands.
        Undeclared calc and canonical replacement candidates use this same proof.
        """
        derived = issue.get("role") == "derived" or (
            declaration is not None and declaration.role == "derived"
        )
        replacement = issue.get("replacement_role") == "derived"
        if not (derived or replacement or issue.get("derive_formula")):
            return
        proof: dict[str, Any] = {}
        target = figure
        candidate = declaration if derived else None
        if replacement:
            target = replace(figure, value=issue["replacement_value"],
                             text=issue["replacement_text"], digits=issue["replacement_digits"])
            candidate = Declaration(-1, target.text, target.value, target.percent, "derived",
                                    issue["replacement_formula"], "; ".join(issue["replacement_refs"]))
        elif candidate is None and not derived and issue.get("derive_formula"):
            candidate = Declaration(-1, figure.text, figure.value, figure.percent, "derived",
                                    issue["derive_formula"], "; ".join(issue["derive_operand_refs"]))
        if candidate is not None and not figure.column:
            proof = self._derived_ref_repair_metadata(target, candidate, symbol, records, declarations)
            if proof:
                repaired = replace(candidate, percent=target.percent, note=proof["derive_formula"],
                                   ref="; ".join(proof["derive_operand_refs"]))
                if self._check_derived(target, repaired, symbol, records):
                    proof = {}
        # Remove every claim-preservation hint before publishing the decision.
        # Observed candidates cannot downgrade a rejected derived declaration.
        for key in list(issue):
            if key.startswith(("derive_", "replacement_")) or key in {
                "derived_repair_verified", "proven_ref_repair_candidates",
                "exact_ref_repair_candidate", "aggregate_ref_candidate",
                "aggregate_ref_candidates", "entity_ref_symbol", "field_ref_candidates",
            }:
                issue.pop(key)
        if proof:
            issue.update(proof, derived_repair_verified=True)
            if replacement:
                issue.update(replacement_role="derived", replacement_value=target.value,
                             replacement_text=target.text, replacement_digits=target.digits,
                             replacement_formula=proof["derive_formula"],
                             replacement_refs=proof["derive_operand_refs"])
                issue.pop("derive_formula", None)
                issue.pop("derive_operand_refs", None)

    def _declarations_by_span(
        self,
        block: FiguresBlock,
        figures: Sequence[Figure],
        *,
        content: str = "",
        line_symbols: Sequence[str | None] = (),
        symbol_records: Sequence[EvidenceRecord] = (),
    ) -> dict[tuple[int, int], Declaration | None]:
        """Bind prose figures to declarations without cross-wiring duplicates.

        Declarations that are the *only* compatible declaration for any prose
        figure are reserved first.  Ambiguous figures then prefer compatible
        declarations that are not already reserved/used.  This prevents a
        rounded declaration from being borrowed by a later exact figure when
        that declaration already uniquely belongs to another claim (for
        example CER 0.2250% followed by cash 0.23%).

        Reuse remains allowed when there is genuinely no alternative, so one
        declaration can still ground repeated prose mentions of the same claim.
        """
        relevant = [
            figure for figure in figures if figure.shape in ("measured", "bare")
        ]
        matches_by_span: dict[tuple[int, int], tuple[Declaration, ...]] = {}
        reserved: set[int] = set()

        for figure in relevant:
            matches = block.compatible_declarations(
                figure.value, figure.percent, figure.digits
            )
            if not matches and figure.percent:
                proven_unitless: list[Declaration] = []
                for declaration in block.declarations:
                    if self._unitless_contribution_display_match(
                        declaration,
                        figure,
                        content,
                        line_symbols,
                        symbol_records,
                    ):
                        # This is a label-only association. The visible unit
                        # is accepted only after the exact observed contribution
                        # ref proves the same numeric value without scaling.
                        proven_unitless.append(
                            replace(declaration, percent=figure.percent)
                        )
                matches = tuple(proven_unitless)
            matches_by_span[(figure.start, figure.end)] = matches
            if len(matches) == 1:
                reserved.add(matches[0].index)

        resolved: dict[tuple[int, int], Declaration | None] = {}
        used_ambiguous: set[int] = set()
        for figure in relevant:
            span = (figure.start, figure.end)
            matches = matches_by_span[span]
            if not matches:
                resolved[span] = None
                continue
            if len(matches) == 1:
                resolved[span] = matches[0]
                continue

            available = [
                item
                for item in matches
                if item.index not in reserved and item.index not in used_ambiguous
            ]
            # Equal displayed values can belong to different instruments.
            # When a table row names its entity, bind to the exact-ref
            # declaration whose observed value belongs to that entity.
            written_symbol = (
                self._written_symbol(
                    content, figure, line_symbols, symbol_records, include_line=False
                )
                if content else figure.symbol
            )
            if written_symbol:
                entity_matches = [
                    item for item in matches
                    if self._entity_exact_ref_match(item.ref, figure) == written_symbol
                ]
                if len(entity_matches) == 1:
                    available = entity_matches
            if not available:
                available = [
                    item for item in matches if item.index not in used_ambiguous
                ]
            if not available:
                # Multiple-compatible occurrences are one-to-one. Reuse is
                # only safe in the single-compatible case handled above.
                resolved[span] = None
                continue

            chosen = available[0]
            resolved[span] = chosen
            used_ambiguous.add(chosen.index)

        return resolved

    def _unitless_contribution_display_match(
        self,
        declaration: Declaration,
        figure: Figure,
        content: str,
        line_symbols: Sequence[str | None],
        symbol_records: Sequence[EvidenceRecord],
    ) -> bool:
        """Prove that a unitless declaration labels this exact contribution.

        A visible pp/bp/bps mark can supply the display unit omitted from a
        numeric declaration only when its exact entity ref names one observed
        contribution and both the declaration and evidence equal the rendered
        value at its written precision. No ratio/percent scaling is attempted.
        """
        if (
            not figure.percent
            or not declaration.unitless_numeric
            or declaration.percent
            or declaration.role != "observed"
            or not self._within_written_precision(
                figure, figure.value, declaration.value
            )
        ):
            return False

        symbol = self._written_symbol(
            content,
            figure,
            line_symbols,
            symbol_records,
            include_line=False,
        ) or figure.symbol
        if not symbol:
            return False

        refs = [part.strip() for part in re.split(r"[,;]", declaration.ref) if part.strip()]
        if len(refs) != 1 or "::" not in refs[0]:
            return False
        call_id, field = (part.strip() for part in refs[0].split("::", 1))
        if not call_id or not field or _metric_kind_for_path(field) != "contribution":
            return False

        scoped = self._referenced(refs[0], symbol, figure)
        if scoped is None:
            return False
        records, metric_values = scoped
        if len(records) != 1 or metric_values:
            return False
        record = records[0]
        canonical_call_id = self._canonical_session_call_id(call_id)
        return bool(
            record.status == "observed"
            and record.unit == "ratio"
            and record.identity_scope == "entity"
            and record.symbol == symbol
            and record.call_id == canonical_call_id
            and _index_normalized(record.field) == _index_normalized(field)
            and _metric_kind_for_path(record.field) == "contribution"
            and record.value is not None
            and self._within_written_precision(
                figure, figure.value, float(record.value)
            )
        )

    @grounding_check(name="figure-symbol", code="numeric_claim_conflict",
                     description="A declaration cannot move a figure to another instrument.")
    def _check_figure_symbol(self, content, figure, declaration, symbol, line_symbols, symbol_records):
        exact_observation_ref = bool(
            declaration is not None
            and declaration.role == "observed"
            and "::" in declaration.ref
        )
        written = self._written_symbol(
            content, figure, line_symbols, symbol_records,
            include_line=not exact_observation_ref,
        )
        if written and symbol and written != symbol:
            # A declaration names where a number came from; it cannot
            # move a figure the sentence attaches to another instrument.
            return [
                self._figure_issue(
                    "numeric_claim_conflict",
                    figure,
                    declaration.role,
                    symbol,
                    "symbol_mismatch",
                    f"is declared for {symbol} but the answer writes it about {written}",
                )
            ]
        return []

    @grounding_check(name="undeclared-figure", code="figure_undeclared",
                     description="An undeclared measurement must be observed in this session.", additional_codes=('numeric_claim_conflict', 'numeric_claim_unavailable'))
    def _check_undeclared_figure(self, content, figure, symbol, records, block):
        # A shape mismatch must not erase an existing derived intent.
        # Association is only a candidate: repair still proves every ref
        # and the complete formula before requiring preservation.
        derived_candidates = [
            item for item in block.declarations
            if item.role == "derived" and item in block.compatible_declarations(
                figure.value, item.percent, figure.digits
            )
        ]
        if derived_candidates:
            repair = self._derived_ref_repair_metadata(
                figure, derived_candidates[0], symbol, records
            ) if len(derived_candidates) == 1 else {}
            return [self._figure_issue(
                "figure_undeclared", figure, "derived", symbol, "undeclared",
                "has no compatible derived declaration in its written unit; "
                "repair the complete derivation or remove it",
                **repair,
            )]
        found = self._check_observed(figure, None, symbol, records)
        # Repair suggestions describe possible evidence, not an independent
        # reason to reject a value already verified as observed. A declaration
        # in another unit remains a separate assertion that needs a proof.
        other_unit = _declared_in_other_unit(block, figure) if block.present else None
        structured_observation = any(
            record.status == "observed" and record.value is not None
            and _metric_kind_for_path(record.field) is not None
            and (
                record.identity_scope == "entity"
                or (
                    record.identity_scope == "aggregate"
                    and "portfolio_" in record.field.casefold()
                )
                or any(token in record.field.casefold() for token in (
                    "correlation_matrix", "covariance_matrix"
                ))
            )
            and self._kind_fits(record, figure)
            and self._matches_evidence(
                figure, [float(record.value)], [float(record.value)]
            )
            and _record_matches_entity(record, symbol)
            for record in self._evidence
        )
        ratio_scale_without_context = (
            any(
                record.status == "observed" and record.unit == "ratio"
                and record.value is not None
                and self._strict_written_match(figure, float(record.value)) == (True, True)
                for record in self._evidence
            )
            and not _has_ratio_display_context(content, figure)
        )
        if block.present and (found or other_unit or structured_observation):
            entity_repair = self._undeclared_entity_repair_metadata(figure)
            structured_repair = self._structured_metric_repair_metadata(figure, symbol)
            # A same-number reference is not proof of a ratio-to-percent
            # conversion. Only the separately verified derived-repair path
            # may authorize preservation when the declared unit differs.
            repair_metadata = (
                {} if other_unit or ratio_scale_without_context else {
                    **entity_repair,
                    **structured_repair,
                    **self._deterministic_derive_metadata(figure),
                }
            )
            return [
                self._figure_issue(
                    "figure_undeclared",
                    figure,
                    None,
                    symbol,
                    "undeclared",
                    "is not declared in the figures block and is not an observed "
                    "value; declare it as observed / derived / proposed / cited / "
                    "count, or remove it",
                    **(
                        {
                            "declared_as": other_unit.value_text,
                            "declared_sign_differs": (other_unit.value < 0) != (figure.value < 0),
                        }
                        if other_unit
                        else {}
                    ),
                    **repair_metadata,
                )
            ]
        return found

    @grounding_check(name="figure-price-column", code="numeric_claim_conflict",
                     description="An OHLC column can contain only observed prints.")
    def _check_figure_column(self, figure, role, symbol):
        if figure.column and role != "observed":
            return [
                self._figure_issue(
                    "numeric_claim_conflict",
                    figure,
                    role,
                    symbol,
                    "cited_as_observed" if role == "cited" else "role_in_price_column",
                    f"is declared {role}, but it sits in the {figure.column} column, "
                    "which holds observed prints only",
                )
            ]
        return []

    @staticmethod
    def _figure_issue(
        code: str,
        figure: Figure,
        role: str | None,
        symbol: str | None,
        reason: str,
        message: str,
        **extra: Any,
    ) -> dict[str, Any]:
        """Build one figure-scoped issue with value, role, span, symbol and reason."""
        issue = {
            "code": code,
            "value": figure.text,
            "role": role,
            "span": [figure.start, figure.end],
            "symbol": symbol,
            "reason": reason,
            "claim": figure.text,
            "message": f"{figure.text} {message}.",
            "figure_value": figure.value,
            "figure_percent": figure.percent,
            "figure_currency": figure.currency,
            "figure_digits": figure.digits,
        }
        issue.update(extra)
        return issue

    def _figure_symbol(
        self,
        content: str,
        figure: Figure,
        declaration: Declaration | None,
        line_symbols: Sequence[str | None],
        document_symbol: str | None,
        records: Sequence[EvidenceRecord],
    ) -> str | None:
        """Resolve which instrument a figure is about (spec §4).

        Order: the declaration's ``ref``/``note``, the table row's symbol column,
        the figure's punctuation segment, its line, then the whole answer. The
        segment precedes the line because a comparison report names both
        instruments on one header line and then writes about one of them.
        """
        if self._matrix_cell_records(figure, records):
            # A correlation cell has two instrument axes and is portfolio
            # aggregate evidence; the row key is not its sole entity.
            return None
        if (
            declaration is not None
            and declaration.role == "observed"
            and "::" in declaration.ref
        ):
            # A descriptive note such as "efectivo" must not be parsed as a
            # ticker unless the answer itself attaches a known entity to the
            # figure. The exact field ref remains the evidence authority.
            note_symbol = self._symbol_for_claim(declaration.note, records)
            declared = note_symbol
            written = self._written_symbol(
                content, figure, line_symbols, records, include_line=False
            )
            if (
                written
                and figure.symbol
                and self._summary_row_is_unscoped(
                    figure.symbol, declaration, figure
                )
            ):
                written = None
            # A statement segment that names an entity is still checked below
            # against the declaration. When it is unscoped, the exact source
            # ref owns identity; a different claim elsewhere on this line does
            # not attach itself to this figure.
            return declared or written
        if declaration is not None:
            declared = self._symbol_for_claim(
                f"{declaration.ref} {declaration.note}", records
            )
            if declared:
                return declared
        written = self._written_symbol(content, figure, line_symbols, records)
        derived_multi_entity = bool(
            declaration is not None
            and self._derived_multi_entity_refs_are_unscoped(
                declaration, figure
            )
        )
        if declaration is not None:
            if (
                written
                and figure.symbol
                and self._summary_row_is_unscoped(
                    figure.symbol, declaration, figure
                )
            ):
                written = None
            elif written and derived_multi_entity:
                written = None
        if derived_multi_entity:
            return None
        return written or document_symbol

    def _matrix_cell_records(
        self, figure: Figure, records: Sequence[EvidenceRecord]
    ) -> list[EvidenceRecord]:
        if not figure.symbol or not figure.table_column_label:
            return []
        row = _normalize_symbol(figure.symbol)
        column = _normalize_symbol(figure.table_column_label)
        matches = []
        source_records = [record for record in records if "correlation_matrix" in record.field.casefold()]
        if not source_records:
            source_records = self._evidence
        for record in source_records:
            parts = record.field.split(".")
            index = next(
                (i for i, part in enumerate(parts)
                 if part.casefold() in {"correlation_matrix", "correlation", "covariance_matrix"}),
                None,
            )
            if index is None or len(parts) < index + 3:
                continue
            if (_normalize_symbol(parts[index + 1]), _normalize_symbol(parts[index + 2])) == (row, column):
                matches.append(record)
        return matches

    def _summary_row_is_unscoped(
        self,
        row_label: str,
        declaration: Declaration,
        figure: Figure,
    ) -> bool:
        """Whether an explicit aggregate-row label should stay unscoped.

        The label itself must be unmistakably aggregate (Total/Subtotal/Sum,
        including the Spanish forms used in portfolio tables). This prevents a
        company/name-shaped row such as ``Acme Corp`` from borrowing aggregate
        evidence.

        The declaration may already be an exact ref, or it may be a tool-name
        ref that can be repaired to exact call refs. In either case at least one
        value-compatible source must resolve exclusively to aggregate/unknown,
        symbol-less evidence.
        """
        label = re.sub(r"[*_`]+", "", str(row_label)).strip()
        if not label or not _SUMMARY_ROW_LABEL_RE.fullmatch(label):
            return False

        if declaration.role == "derived":
            derivation = self._derivation(
                declaration, None, self._evidence, money=figure.currency,
                percent=figure.percent,
            )
            if (
                not isinstance(derivation, str)
                and derivation is not None
                and self._result_matches(figure, derivation[0], declaration.note)
            ):
                return True
            if self._deterministic_derive_metadata(figure):
                return True

        refs = [declaration.ref]
        refs.extend(
            self._tool_field_ref_candidates(declaration.ref, None, figure)
        )

        for ref in dict.fromkeys(refs):
            scoped = self._referenced(ref, None, figure)
            if scoped is None:
                continue
            records, metric_values = scoped
            if metric_values:
                return True
            matching = [
                record
                for record in records
                if record.value is not None
                and not record.symbol
                and record.identity_scope in {"aggregate", "unknown"}
                and self._matches_evidence(
                    figure,
                    [float(record.value)],
                    [float(record.value)],
                )
            ]
            if matching:
                return True
        return False

    def _derived_multi_entity_refs_are_unscoped(
        self,
        declaration: Declaration,
        figure: Figure,
    ) -> bool:
        """Whether a derived declaration is intrinsically multi-entity.

        Every exact source ref must resolve, and the resolved evidence must
        contain at least two distinct entity symbols. In that case the derived
        claim cannot inherit one incidental ticker merely because that ticker
        appears elsewhere on the same prose line.
        """
        if declaration.role != "derived":
            return False
        refs = [
            part.strip()
            for part in re.split(r"[,;]", declaration.ref or "")
            if part.strip()
        ]
        if len(refs) < 2:
            return False

        symbols: set[str] = set()
        for ref in refs:
            scoped = self._referenced(ref, None, figure)
            if scoped is None:
                return False
            records, metric_values = scoped
            if metric_values and not records:
                # A symbol-less metric ref does not prove multi-entity scope.
                return False
            if not records:
                return False
            ref_symbols = {record.symbol for record in records if record.symbol}
            if not ref_symbols:
                return False
            symbols.update(str(symbol) for symbol in ref_symbols)
        return len(symbols) >= 2

    @staticmethod
    def _figure_is_market_price(
        content: str, figure: Figure, declaration: Declaration | None
    ) -> bool:
        """Classify quote context positively; currency by itself is not a price."""
        if figure.percent:
            return False
        if figure.column:
            return _price_field_for_path(figure.column) is not None
        left, right = segment_bounds(content, figure.start, figure.end)
        return bool(
            _MARKET_PRICE_CONTEXT_RE.search(content[left:right])
            or (declaration is not None and _MARKET_PRICE_CONTEXT_RE.search(declaration.note))
        )

    def _written_symbol(
        self,
        content: str,
        figure: Figure,
        line_symbols: Sequence[str | None],
        records: Sequence[EvidenceRecord],
        *,
        include_line: bool = True,
    ) -> str | None:
        """The instrument the answer's own text attaches a figure to, if one.

        The table row's symbol column, then the figure's punctuation segment,
        then its line; the whole-answer fallback is left to the caller.
        """
        if figure.symbol:
            # "000001.SZ 平安银行": the cell names its instrument beside a name.
            written = _scan_symbols(figure.symbol)
            if len(written) == 1:
                return next(iter(written))
            normalized = _normalize_symbol(figure.symbol)
            if normalized:
                return normalized
        left, right = segment_bounds(content, figure.start, figure.end)
        segment_symbol = self._symbol_for_claim(content[left:right], records)
        if segment_symbol:
            return segment_symbol
        if (
            include_line
            and 0 <= figure.line < len(line_symbols)
            and line_symbols[figure.line]
        ):
            return line_symbols[figure.line]
        return None

    def _has_asistente_casa_evidence(self) -> bool:
        """Whether this run contains evidence from the Asistente Casa adapter."""
        return any(
            record.tool == "financial_rigor"
            or _is_asistente_casa_tool(record.tool)
            for record in self._evidence
        ) or any(
            entry.get("tool") == "financial_rigor"
            or _is_asistente_casa_tool(entry.get("tool"))
            for entry in self._analysis_metrics
        )

    def _referenced(
        self,
        ref: str,
        symbol: str | None,
        figure: Figure | None,
        *,
        pool_ambiguous: bool = False,
    ) -> tuple[list[EvidenceRecord], list[float]] | None:
        """Resolve one exact ref or a complete comma/semicolon-separated set.

        Multi-source derivations are common in the Asistente Casa adapter and
        are also used by upstream's backtest comparison guidance. Every named
        source must resolve; a missing source never borrows a matching value
        from the wider evidence pool.
        """
        keys = [key.strip() for key in re.split(r"[,;]", ref or "") if key.strip()]
        if not keys:
            return None
        resolved = [
            self._referenced_one(key, symbol, figure, pool_ambiguous=pool_ambiguous)
            for key in keys
        ]
        if len(keys) == 1:
            result = resolved[0]
            if result is None and self._has_asistente_casa_evidence():
                normalized = _normalize_symbol(keys[0])
                known_symbols = {r.symbol for r in self._evidence if r.symbol}
                if normalized not in known_symbols:
                    return [], []
            return result
        if any(result is None or (not result[0] and not result[1]) for result in resolved):
            return [], []
        return (
            [record for result in resolved for record in result[0]],
            [value for result in resolved for value in result[1]],
        )

    def _referenced_one(
        self,
        ref: str,
        symbol: str | None,
        figure: Figure | None,
        *,
        pool_ambiguous: bool = False,
    ) -> tuple[list[EvidenceRecord], list[float]] | None:
        """The evidence named by an exact field, call+field, one call, or one tool.

        An exact evidence-field ref is accepted only when that field occurs in
        one call. When it repeats across calls, ``call_id::field`` is the
        unambiguous tightest scope. Otherwise a ``ref`` naming a call id or a
        tool name keeps the existing call/tool scope, and the only one that can
        ground a non-price figure (revenue, IC, volume). A backtest's output is
        also named by its run directory or file (:meth:`_artifact_scope`).
        Records of another symbol are dropped when the figure's symbol is
        known; a currency-marked figure keeps only money-denominated records, a
        percent only the others, less metadata counts.

        Args:
            ref: The declaration's ``ref``.
            symbol: The figure's resolved symbol, or None.
            figure: The figure whose shape narrows the kind, or None for the
                operands of a derivation.
            pool_ambiguous: Pool a field ref's sources even when they disagree.
                Only a derivation's anchors ask for it: an operand from either
                run is still an observation.

        Returns:
            ``(records, metric values)``, or None when ``ref`` names no field,
            call, tool, or backtest output.
        """
        key = (ref or "").strip()
        if not key:
            return None

        # A composite ref names one exact field from one exact call, or from
        # one backtest's output (``rp::sharpe``). This is the unambiguous form
        # when the same analysis field appears in more than one tool call.
        if "::" in key:
            call_id, field = (part.strip() for part in key.split("::", 1))
            call_id = self._canonical_session_call_id(call_id)
            if not call_id or not field:
                return [], []
            # ``call::rows[i][j]`` names one cell of a table that call showed
            # the model; only cells recorded as evidence resolve (never a date,
            # symbol or price column, nor a row that was not shown).
            cell = _TABLE_CELL_REF.fullmatch(field)
            if cell is not None:
                record = self._table_cells.get((call_id, int(cell[1]), int(cell[2])))
                return ([record], []) if record is not None else ([], [])
            # The backtest tool returns its run card as ``summary``
            # (backtest_summary.build_backtest_summary), so the same values
            # are recorded under the run card's own paths.
            if call_id in self._backtest_scopes:
                field = _backtest_summary_alias(field) or field
            records, entries = self._field_sources(field, symbol)
            by_call = [record for record in records if record.call_id == call_id]
            metrics = [float(entry["value"]) for entry in entries if entry.get("call_id") == call_id]
            # Not a call: ``rp::sharpe`` names a field of one backtest's output,
            # ``rp::target_positions.csv`` or ``backtest::rp`` the output itself.
            records = by_call if by_call or metrics else self._artifact_scope(key) or []
        else:
            records, entries = self._field_sources(key, symbol)
            artifact_scope = None if records or entries else self._artifact_scope(key)
            if records or entries:
                if not pool_ambiguous and self._ambiguous_field_sources(key, symbol):
                    # Calls disagree on this field, so the ref cannot say
                    # which value it quotes (VaR at 95% vs 99%). Fail closed
                    # rather than pool them; the issue names the calls.
                    return [], []
                metrics = [float(entry["value"]) for entry in entries]
            elif artifact_scope is not None:
                # A backtest's run directory or one of its files: everything
                # that backtest observed there, and nothing another run did.
                records, metrics = artifact_scope, []
            else:
                records = [
                    record
                    for record in self._evidence
                    if key in (record.call_id, record.tool)
                    and record.status == "observed"
                    and record.value is not None
                ]
                metrics = [
                    float(entry["value"])
                    for entry in self._analysis_entries(symbol)
                    if key in (entry.get("call_id"), entry.get("tool"))
                    and entry.get("value") is not None
                ]
                if not records and not metrics:
                    # Preserve the legacy loose-ref contract: a ref such as a
                    # symbol that names no field/call/tool falls back to the
                    # ordinary evidence path. Ambiguous or composite field
                    # refs return earlier as an explicit empty scope instead.
                    return None
        if symbol:
            records = [
                record for record in records if _record_matches_entity(record, symbol)
            ]
        if figure is not None and figure.column:
            # A table cell quotes its own column, not whatever else the call returned.
            records = [record for record in records if record.field == figure.column]
            metrics = []
        if figure is not None and figure.percent:
            records = [
                record
                for record in records
                if not _is_price_kind(record)
                and not _is_metadata_count_leaf(record.field)
                and record.unit != "count"
                and (record.source != "document" or record.field == "document.ratio")
            ]
        elif figure is not None and figure.currency:
            # Documents often put the unit in a table header. An explicitly
            # referenced numeric cell can be restated with that unit; it is
            # still matched to this exact source and written precision. Do not
            # promote it to a price globally or admit ratios as amounts. Known
            # currency identity is checked by _check_observed below.
            records = [
                record for record in records
                if _is_price_kind(record)
                or (record.source == "document" and record.field == "document.figure")
            ]
            metrics = []
        return records, metrics

    def _analysis_entries(self, symbol: str | None) -> list[dict[str, Any]]:
        """Scope analysis metrics to their own item, retaining legacy aggregates."""
        call_symbols: dict[str, set[str]] = {}
        for record in self._evidence:
            if record.symbol and record.status == "observed":
                call_symbols.setdefault(record.call_id, set()).add(record.symbol)
        selected = []
        for entry in self._analysis_metrics:
            if entry.get("instrument_scoped"):
                # A symbol-keyed map belongs to the instrument its key names;
                # a correlation cell has two axes and no single owner.
                if entry.get("symbol") == symbol:
                    selected.append(entry)
                continue
            scope = entry.get("identity_scope")
            if scope in {"unknown", "conflict"}:
                continue
            if not symbol:
                selected.append(entry)
            elif scope == "entity":
                if entry.get("symbol") == symbol:
                    selected.append(entry)
            elif scope == "aggregate":
                continue
            elif not call_symbols.get(entry.get("call_id")) or call_symbols[entry["call_id"]] == {symbol}:
                selected.append(entry)
        return selected

    def _field_sources(
        self, field: str, symbol: str | None
    ) -> tuple[list[EvidenceRecord], list[dict[str, Any]]]:
        """Observed values of the evidence field ``field`` names, for ``symbol``.

        ``field`` is a full path (``data.tail_risk.var_95``) or its trailing
        part (``var_95``, ``tail_risk.var_95``): a short ref used to fall
        through to the whole evidence pool, where a VaR 99% claim quoting the
        95% value found its match (#1444 review). Another symbol's records are
        dropped here, before any count of calls, so two instruments' closes do
        not make ``close`` ambiguous.
        """
        wanted = _index_normalized(field)

        def named(path: Any) -> bool:
            if not isinstance(path, str):
                return False
            path = _index_normalized(path)
            return path == wanted or path.endswith("." + wanted)

        records = [
            record
            for record in self._evidence
            if named(record.field)
            and record.status == "observed"
            and record.value is not None
            and (not symbol or not record.symbol or record.symbol == symbol)
        ]
        entries = [
            entry
            for entry in self._analysis_entries(symbol)
            if named(entry.get("field")) and entry.get("value") is not None
        ]
        return records, entries

    def _ambiguous_field_sources(self, field: str, symbol: str | None) -> list[str]:
        """The ``call::path`` sources a field-only ref cannot choose between.

        Ambiguous means more than one source AND more than one value among
        them: two runs of one call returning the same number leave nothing to
        choose. A source is a call and its full path, except that one backtest
        is one source (:meth:`_ref_source`). Each is written as the ref that
        names it.

        Returns:
            Sorted refs, or an empty list when the ref is exact.
        """
        if not field or "::" in field:
            return []
        records, entries = self._field_sources(field, symbol)
        sources = {
            (*self._ref_source(record.call_id, record.field, record.scope), float(record.value))
            for record in records
        }
        sources |= {
            (
                *self._ref_source(str(entry.get("call_id")), str(entry.get("field")), None),
                float(entry["value"]),
            )
            for entry in entries
        }
        if len({identity for identity, *_ in sources}) < 2 or len({value for *_, value in sources}) < 2:
            return []
        return sorted({label for _, label, _ in sources})

    def _ref_source(
        self, call_id: str, field: str, scope: str | None
    ) -> tuple[tuple[str, str], str]:
        """Which source one evidence value belongs to, and the ref that names it.

        One backtest is one observation: its run card, metrics file and risk
        X-ray each hold a ``max_drawdown``, and a ref matching several of them
        still names that one run. Two backtests are two sources, and so is one
        directory run twice (the earlier call keeps its own identity). A
        backtest's value is named through its run directory (``rp::sharpe``),
        which the model chose; anything else by call id and full path.

        Args:
            call_id: The call that returned the value.
            field: Its full evidence path.
            scope: The backtest run directory of a backtest output record.

        Returns:
            ``(identity, ref label)``.
        """
        if scope is None:
            owner = self._backtest_scopes.get(call_id)
            if owner is not None and self._scope_latest.get(owner) == call_id:
                scope = owner
        if scope is None:
            return (call_id, field), f"{call_id}::{field}"
        return ("scope", scope), f"{scope}::{field}" if scope else f"{call_id}::{field}"

    def _artifact_scope(self, key: str) -> list[EvidenceRecord] | None:
        """The backtest output a ref names by run directory or file, or None.

        Backtest output is named the way the model named the run: its run
        directory, which the model chose (``rp``, ``risk_parity``), as a path
        (``rp/artifacts/metrics.csv``, ``rp/metrics.csv``) or beside a file
        and a field (``risk_parity target_positions.csv``,
        ``backtest::rp``). The run directory is what makes the ref exact —
        two backtests hold two different Sharpes — so a ref naming several
        runs pools them, and a ref naming only a file pools that file across
        runs, like a ref naming the tool. A file or field the ref also names
        narrows the set when the run holds it and is ignored otherwise. The
        active run written as ``run_dir/`` or by its own name is read as the
        path below it (:func:`_run_dir_alias`).

        Args:
            key: The declaration's ref, or one half of ``scope::field``.

        Returns:
            The observed backtest records the ref names, or None when it names
            no backtest run directory and no file one wrote.
        """
        owned = [
            record
            for record in self._evidence
            if record.artifact is not None
            and record.status == "observed"
            and record.value is not None
        ]
        if not owned:
            return None
        root = self.run_dir.resolve()
        chosen: dict[int, EvidenceRecord] = {}
        by_tool: dict[int, EvidenceRecord] = {}
        files: set[str] = set()
        fields: list[str] = []
        for token in _REF_TOKEN_RE.split(key):
            token = token.strip("`'\"")
            if not token:
                continue
            hits = _records_at(token, owned, root)
            alias = None if hits else _run_dir_alias(token, root)
            if alias is not None:
                # "run_dir/artifacts/metrics.csv": the active run under a
                # stand-in name, read as the path or file below it.
                hits = _records_at(alias, owned, root)
                token = alias if hits or "/" not in alias else token
            if hits:
                chosen.update((id(record), record) for record in hits)
            elif token != key.strip() and any(record.tool == token for record in owned):
                # "backtest 两个运行": the tool name beside words. Beside a run
                # directory ("backtest::rp") it only says what the run is; a
                # ref that is the tool name alone keeps the tool scope in
                # ``_referenced``.
                by_tool.update((id(record), record) for record in owned if record.tool == token)
            elif "/" not in token and any(
                _file_stem(token) == _file_stem(record.artifact) for record in owned
            ):
                # A bare file name ("target_positions.csv"). A path that holds
                # none of this session's records names nothing: the active
                # run's copy may be another backtest's by now.
                files.add(_file_stem(token))
            else:
                fields.append(token)
        chosen = chosen or by_tool
        if not chosen and not files:
            return None
        selected = list(chosen.values()) or owned
        by_file = [record for record in selected if _file_stem(record.artifact) in files]
        selected = by_file or selected
        for token in fields:
            by_field = [
                record
                for record in selected
                if record.field == token or record.field.endswith("." + token)
            ]
            selected = by_field or selected
        return selected or None

    def _tail_risk_sources(
        self,
        records: Sequence[EvidenceRecord],
        entries: Iterable[Mapping[str, Any]] = (),
    ) -> list[tuple[str, float]]:
        """``(identity, value)`` for every tail-risk value among these sources.

        Identity is read off the field name (:func:`tail_risk_identity`), so
        ``var_95`` and ``es_95`` are two identities and ``cvar_99`` / ``es_99``
        are one.
        """
        sources: list[tuple[str, float]] = []
        for record in records:
            identity = tail_risk_identity(record.field)
            if identity and record.status == "observed" and record.value is not None:
                sources.append((identity, float(record.value)))
        for entry in entries:
            identity = tail_risk_identity(str(entry.get("field") or ""))
            if identity and entry.get("value") is not None:
                sources.append((identity, float(entry["value"])))
        return sources

    def _tail_risk_field_refs(
        self,
        records: Sequence[EvidenceRecord],
        entries: Iterable[Mapping[str, Any]] = (),
    ) -> list[str]:
        """Exact ``call_id::field`` (or ``run_dir::field``) refs for tail-risk evidence."""
        refs = {
            self._ref_source(record.call_id, record.field, record.scope)[1]
            for record in records
            if record.call_id
            and record.field
            and record.status == "observed"
            and record.value is not None
            and tail_risk_identity(record.field)
        }
        refs |= {
            self._ref_source(str(entry.get("call_id")), str(entry.get("field")), None)[1]
            for entry in entries
            if entry.get("call_id")
            and entry.get("field")
            and entry.get("value") is not None
            and tail_risk_identity(str(entry.get("field") or ""))
        }
        return sorted(refs)

    def _canonical_session_call_id(self, token: str) -> str:
        """Restore a provider suffix only when its pre-pipe token is unique.

        Some provider call ids are shaped `call_X|fc_Y`. A correction model
        can copy only `call_X`; accepting arbitrary prefixes would be unsafe,
        so restoration requires exact equality with the complete pre-pipe token
        of exactly one call id observed in this session.
        """
        known = {
            str(record.call_id)
            for record in self._evidence
            if record.call_id
        } | {
            str(entry.get("call_id"))
            for entry in self._analysis_metrics
            if entry.get("call_id")
        } | {
            str(item.get("call_id"))
            for item in getattr(self, "_deterministic_calcs", [])
            if item.get("call_id")
        }
        if token in known:
            return token
        if "|" in token:
            return token
        matches = [item for item in known if item.split("|", 1)[0] == token]
        return matches[0] if len(matches) == 1 else token

    @staticmethod
    def _strict_written_match(figure: Figure, target: float) -> tuple[bool, bool]:
        """Match deterministic results at written precision, not the 0.5% band.

        Returns `(matched, scaled_by_100)`. The scaled form covers fraction
        results rendered as percentages or percentage points.
        """
        written = figure.digits or figure.text
        places = len(written.split(".", 1)[1]) if "." in written else 0
        half = 0.5 * (10.0 ** -places) if places else max(abs(figure.value) * 1e-9, 1e-9)
        if abs(figure.value - target) <= max(half * (1 + 1e-9), 1e-9):
            return True, False
        scaled_half = half / 100.0
        if abs((figure.value / 100.0) - target) <= max(scaled_half * (1 + 1e-9), 1e-12):
            return True, True
        return False, False

    @staticmethod
    def _calc_operands(expr: str) -> list[float]:
        """Numeric data operands in a safe arithmetic expression.

        Structural scaling constants 0, 1 and 100 are excluded. Binary minus
        does not negate its right operand; explicit unary minus does.
        """
        try:
            root = ast.parse(expr, mode="eval").body
        except (SyntaxError, ValueError):
            return []

        def walk(node: ast.AST) -> list[float]:
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
                return [float(node.value)]
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                values = walk(node.operand)
                return [(-value if isinstance(node.op, ast.USub) else value) for value in values]
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
                return walk(node.left) + walk(node.right)
            return []

        values = walk(root)
        return [value for value in values if abs(value) not in {0.0, 1.0, 100.0}]

    def _ratio_display_declaration(
        self, figure: Figure, declaration: Declaration,
    ) -> Declaration | None:
        """Offer an explicit display conversion, never silently change units."""
        keys = [key.strip() for key in re.split(r"[,;]", declaration.ref) if key.strip()]
        if len(keys) != 1 or "::" not in keys[0]:
            return None
        call, path = keys[0].split("::", 1)
        call = self._canonical_session_call_id(call)
        # A display repair must not infer a different field from a suffix or
        # choose evidence from a different call when the named call exists.
        # The original declaration must also state the exact displayed value:
        # loose matching at figure precision cannot repair a wrong number.
        if not path or not self._strict_written_match(figure, declaration.value)[0]:
            return None
        candidates, _ = self._field_sources(path, None)
        known = any(record.call_id == call for record in self._evidence)
        candidates = [
            record for record in candidates
            if record.status == "observed" and record.unit == "ratio"
            and record.value is not None
            and _index_normalized(record.field) == _index_normalized(path)
            and (not known or record.call_id == call)
            and self._strict_written_match(figure, float(record.value)) == (True, True)
        ]
        if len(candidates) != 1:
            return None
        return replace(declaration, role="derived", percent=figure.percent,
                       note=f"{candidates[0].value} * 100")

    def _derived_ref_repair_metadata(
        self, figure: Figure, declaration: Declaration, symbol: str | None,
        records: Sequence[EvidenceRecord], declarations: Sequence[Declaration] = (),
    ) -> dict[str, Any]:
        """Prove a complete repair of an existing structured derivation.

        Paths restrict association before values are considered. Never invent
        missing refs, choose between calls, or replace derived intent with an
        equal observed scalar. The existing operand validator remains authority.
        """
        evaluated = _formula_in_note(declaration.note)
        keys = [key.strip() for key in re.split(r"[,;]", declaration.ref) if key.strip()]
        if evaluated is None or not keys or any("::" not in key for key in keys):
            return {}
        _, operands, tree = evaluated
        # Inline only independently proven declarations whose complete original
        # ref set is already explicit in the consumer. One level, no new evidence
        # records, no numeric search outside this structured dependency boundary.
        if declarations:
            class Inline(ast.NodeTransformer):
                changed = False

                def visit_Constant(inner, node: ast.Constant) -> ast.AST:
                    proofs = []
                    for item in declarations:
                        item_keys = {key.strip() for key in re.split(r"[,;]", item.ref) if key.strip()}
                        if (item == declaration or item.role != "derived"
                            or item.percent or item.value != node.value
                            or not item_keys or not item_keys.issubset(set(keys))):
                            continue
                        target = replace(figure, value=item.value, text=item.value_text,
                                         digits=item.value_text, percent=item.percent)
                        proof = self._derived_ref_repair_metadata(target, item, symbol, records)
                        if proof:
                            proofs.append(proof)
                    if len(proofs) > 1:
                        raise ValueError("ambiguous declared dependency")
                    if proofs:
                        inner.changed = True
                        return _evaluate_formula(proofs[0]["derive_formula"])[2].body
                    return node
            inline = Inline()
            try:
                tree = inline.visit(tree)
            except ValueError:
                return {}
            if inline.changed:
                operands = [float(node.value) for node in ast.walk(tree)
                            if isinstance(node, ast.Constant) and _is_number(node.value)]
        refs: list[str] = []
        associated: dict[str, EvidenceRecord] = {}
        for key in keys:
            call, path = (part.strip() for part in key.split("::", 1))
            call = self._canonical_session_call_id(call)
            candidates, _ = self._field_sources(path, None)
            known_call = any(record.call_id == call for record in self._evidence)
            candidates = [record for record in candidates
                          if (not known_call or record.call_id == call)
                          and record.status == "observed"
                          and record.tool != "financial_rigor"
                          and record.value is not None
                          and any(math.isclose(float(record.value), operand,
                                               rel_tol=1e-12, abs_tol=1e-12)
                                  for operand in operands)]
            exact = {self._ref_source(record.call_id, record.field, record.scope)[1]
                     for record in candidates}
            if len(exact) != 1:
                return {}
            ref = next(iter(exact))
            refs.append(ref)
            associated[ref] = next(record for record in candidates
                                   if self._ref_source(record.call_id, record.field, record.scope)[1] == ref)

        # Coverage is structural, not an effective-coefficient test. A quotient
        # requires independently evidenced sides; an unreferenced divisor must
        # not become a scalar just because later arithmetic cancels its scale.
        # Ambiguous repeated financial literals also cannot establish distinct
        # operand identities. This is a conservative repair boundary only:
        # scalar division remains available to the unchanged algebra validator.
        def coverage(node: ast.AST) -> set[str]:
            if isinstance(node, ast.Expression):
                return coverage(node.body)
            if isinstance(node, ast.Constant):
                matches = {ref for ref, record in associated.items()
                           if math.isclose(float(record.value), float(node.value),
                                           rel_tol=1e-12, abs_tol=1e-12)}
                if len(matches) > 1:
                    raise ValueError("ambiguous financial leaf")
                return matches
            if isinstance(node, ast.UnaryOp):
                return coverage(node.operand)
            if not isinstance(node, ast.BinOp):
                raise ValueError("unsupported repair structure")
            left, right = coverage(node.left), coverage(node.right)
            if isinstance(node.op, (ast.Add, ast.Sub, ast.Div)) and (left or right):
                if not left or not right:
                    raise ValueError("financial operand coverage unavailable")
            if isinstance(node.op, ast.Div) and (not right or left.intersection(right)):
                raise ValueError("untyped divisor cannot prove complete coverage")
            return left | right

        try:
            if coverage(tree) != set(associated):
                return {}
        except ValueError:
            return {}

        # Render the parser's numeric literals, not float reconstructions. This
        # also removes explanatory prose and percent display syntax from notes.
        def arithmetic(node: ast.AST) -> str:
            if isinstance(node, ast.Expression):
                return arithmetic(node.body)
            if isinstance(node, ast.Constant):
                literal = getattr(node, "_grounding_literal", None)
                if literal is not None:
                    return str(literal)
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                return ("+" if isinstance(node.op, ast.UAdd) else "-") + "(" + arithmetic(node.operand) + ")"
            operators = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}
            if isinstance(node, ast.BinOp) and type(node.op) in operators:
                return "(" + arithmetic(node.left) + " " + operators[type(node.op)] + " " + arithmetic(node.right) + ")"
            raise ValueError("unsupported repair formula")

        try:
            formula = arithmetic(tree)
        except ValueError:
            return {}
        repaired = replace(declaration, percent=figure.percent, note=formula,
                           ref="; ".join(dict.fromkeys(refs)))
        result = self._derivation(
            repaired, symbol, records, money=figure.currency, percent=figure.percent
        )
        if not isinstance(result, tuple) or not self._result_matches(figure, result[0], formula):
            return {}
        # Make a fraction-to-display conversion explicit in the repair note,
        # then prove that complete formula again with the same operand gate.
        matched, scaled = self._strict_written_match(figure, result[0])
        if matched and scaled and not _has_trailing_percent_factor(formula):
            formula = f"({formula}) * 100"
            repaired = replace(repaired, note=formula)
            result = self._derivation(
            repaired, symbol, records, money=figure.currency, percent=figure.percent
        )
            if not isinstance(result, tuple) or not self._result_matches(figure, result[0], formula):
                return {}
        return {"derive_formula": formula,
                "derive_operand_refs": list(dict.fromkeys(refs))}

    def _deterministic_derive_metadata(
        self, figure: Figure, declaration: Declaration | None = None,
    ) -> dict[str, Any]:
        """Prove a claim from one exact deterministic calc + observed operands."""
        proofs: list[dict[str, Any]] = []
        for calc in getattr(self, "_deterministic_calcs", []):
            expr = str(calc.get("expr") or "")
            if declaration is not None:
                stated = _formula_in_note(declaration.note)
                if stated is None or not all(operand in stated[1] for operand in self._calc_operands(expr)):
                    continue
            result = calc.get("result")
            if not isinstance(result, (int, float)):
                continue
            matched, scaled = self._strict_written_match(figure, float(result))
            if not matched:
                continue
            operands = self._calc_operands(expr)
            if len(operands) < 2:
                continue
            refs: list[str] = []
            valid = True
            for operand in operands:
                candidates = []
                for record in self._evidence:
                    if (
                        record.tool != "financial_rigor"
                        and record.status == "observed"
                        and record.value is not None
                        and abs(float(record.value) - operand)
                        <= max(abs(operand) * 1e-9, 1e-12)
                    ):
                        candidates.append(
                            self._ref_source(record.call_id, record.field, record.scope)[1]
                        )
                candidates = list(dict.fromkeys(candidates))
                if len(candidates) != 1:
                    valid = False
                    break
                refs.append(candidates[0])
            if not valid:
                continue
            formula = f"({expr}) * 100" if scaled else expr
            proofs.append(
                {
                    "derive_formula": formula,
                    "derive_operand_refs": list(dict.fromkeys(refs)),
                    "derive_calc_call_id": str(calc.get("call_id") or ""),
                }
            )
        return proofs[0] if len(proofs) == 1 else {}

    def _same_parent_value_twin_candidates(
        self, ref: str, figure: Figure, symbol: str | None
    ) -> list[str]:
        """Exact numeric siblings for a value leaf excluded only by kind.

        A bare field path is accepted only when the written value identifies
        exactly one source call. Sibling candidates are then restricted to
        that same call, same parent container, same identity scope and value.
        This keeps bare refs fail-closed when multiple calls could own them.
        """
        key = (ref or "").strip()
        if not key:
            return []
        if "::" in key:
            scope, field = (part.strip() for part in key.split("::", 1))
        else:
            scope, field = "", key
        if not field or "." not in field:
            return []

        tool_alias = scope.removeprefix("functions.") if scope else ""
        sources = [
            record
            for record in self._evidence
            if record.status == "observed"
            and record.value is not None
            and record.field == field
            and (
                not scope
                or scope in (record.call_id, record.tool, f"functions.{record.tool}")
                or (tool_alias and record.tool == tool_alias)
            )
            and self._matches_evidence(
                figure, [float(record.value)], [float(record.value)]
            )
            and not self._kind_fits(record, figure)
            and (not symbol or not record.symbol or record.symbol == symbol)
        ]
        if not sources:
            return []

        source_calls = {record.call_id for record in sources if record.call_id}
        if not scope and len(source_calls) != 1:
            return []

        parent = field.rsplit(".", 1)[0]
        found: list[str] = []
        for source in sources:
            for record in self._evidence:
                if (
                    record.call_id != source.call_id
                    or record.status != "observed"
                    or record.value is None
                    or not record.field.startswith(parent + ".")
                    or (symbol and record.symbol and record.symbol != symbol)
                    or record.identity_scope != source.identity_scope
                    or not self._kind_fits(record, figure)
                    or not self._matches_evidence(
                        figure, [float(record.value)], [float(record.value)]
                    )
                ):
                    continue
                found.append(
                    self._ref_source(record.call_id, record.field, record.scope)[1]
                )
        return list(dict.fromkeys(found))

    def _bare_field_ref_candidates(
        self, ref: str, symbol: str | None, figure: Figure
    ) -> list[str]:
        """Exact call refs for one bare scalar field path.

        Candidates must carry the written value with a compatible evidence
        kind and entity. Multiple compatible calls are returned as explicit
        preserve-options ambiguity; no call is selected by recency.
        """
        field = (ref or "").strip()
        if not field or "::" in field:
            return []
        records, entries = self._field_sources(field, symbol)
        money = bool(figure.currency and not figure.percent)
        found: dict[str, list[float]] = {}
        for record in records:
            if (
                record.call_id
                and record.field
                and self._kind_fits(record, figure)
                and (not symbol or not record.symbol or record.symbol == symbol)
            ):
                label = self._ref_source(record.call_id, record.field, record.scope)[1]
                found.setdefault(label, []).append(float(record.value))
        for entry in entries if not (money or figure.column) else ():
            if entry.get("call_id") and entry.get("field"):
                label = self._ref_source(
                    str(entry["call_id"]), str(entry["field"]), None
                )[1]
                found.setdefault(label, []).append(float(entry["value"]))
        compatible = [
            label
            for label, values in found.items()
            if self._matches_evidence(
                figure, values, [] if money else values
            )
        ]
        return sorted(dict.fromkeys(compatible))[:_MAX_CALL_REF_CANDIDATES]

    def _tool_field_ref_candidates(
        self, ref: str, symbol: str | None, figure: Figure | None = None
    ) -> list[str]:
        """Exact call refs for a mistaken tool_name::field declaration.

        With ``figure``, refs whose observed value matches it come first, most
        recent call first among equals; the list is capped. The hint never
        selects a call: the next draft must still declare the exact ref.
        """
        key = (ref or "").strip()
        if "::" not in key:
            return []
        scope, field = (part.strip() for part in key.split("::", 1))
        if not scope or not field:
            return []
        records, entries = self._field_sources(field, symbol)
        found: dict[str, list[float]] = {}
        for record in records:
            if record.tool == scope and record.call_id and record.field:
                found.setdefault(self._ref_source(record.call_id, record.field, record.scope)[1], []).append(
                    float(record.value)
                )
        for entry in entries:
            if entry.get("tool") == scope and entry.get("call_id") and entry.get("field"):
                found.setdefault(self._ref_source(str(entry.get("call_id")), str(entry.get("field")), None)[1], []).append(
                    float(entry["value"])
                )
        if figure is None:
            return sorted(found)
        recency = {item: index for index, item in enumerate(found)}
        matches = {
            item: self._matches_evidence(figure, found[item], found[item])
            for item in found
        }
        ranked = sorted(
            found,
            key=lambda item: (
                not matches[item],
                -recency[item],
                item,
            ),
        )
        # When the written value identifies one or more calls exactly, do not
        # dilute the correction hint with same-field calls holding other values.
        # The hint still authorizes nothing: the next draft must declare one of
        # these exact refs and survive validation normally.
        matching = [item for item in ranked if matches[item]]
        return (matching or ranked)[:_MAX_CALL_REF_CANDIDATES]

    def _indexed_field_ref_candidates(
        self, ref: str, figure: Figure, symbol: str | None = None
    ) -> list[str]:
        """Exact ``call_id::path[i]`` refs for a ``call::field`` ref that selected no value.

        Two shapes of an unresolved ref are helped, both only from the call (or
        tool) the ref names:

        * the ref names a list field without its index: paths that equal the
          declared field once every collection index is dropped are offered;
        * the ref names a container (``data.groups.positive``): its numeric
          descendants that hold the figure's value are offered, and none
          otherwise. An index written in the ref narrows the container to that
          element.

        Refs whose value matches the figure come first. These are hints for the
        next draft; a ref is never resolved through them and a container ref
        authorizes nothing.
        """
        found: dict[str, float] = {}
        containers: set[str] = set()
        for key in (part.strip() for part in re.split(r"[;,]", ref or "")):
            if "::" not in key:
                continue
            scope, field = (part.strip() for part in key.split("::", 1))
            if not scope or not field:
                continue
            wanted = _index_normalized(field)
            bare = _INDEX_RE.sub("", wanted)
            below = re.compile(r"(?:^|\.)" + re.escape(wanted) + r"(?=[.\[])")
            for call_id, tool, path, value, record in (
                *(
                    (r.call_id, r.tool, r.field, r.value, r)
                    for r in self._evidence
                    if r.status == "observed"
                ),
                *(
                    (e.get("call_id"), e.get("tool"), e.get("field"), e.get("value"), None)
                    for e in self._analysis_metrics
                ),
            ):
                if not (
                    call_id
                    and isinstance(path, str)
                    and value is not None
                    and scope in (call_id, tool)
                ):
                    continue
                if record is not None:
                    if symbol and record.symbol and record.symbol != symbol:
                        continue
                    if not self._kind_fits(record, figure):
                        continue
                item = f"{call_id}::{path}"
                if below.search(_index_normalized(path)):
                    containers.add(item)
                    found[item] = float(value)
                elif _INDEX_RE.search(path) and not _INDEX_RE.search(wanted):
                    stripped = _INDEX_RE.sub("", path)
                    if stripped == bare or stripped.endswith("." + bare):
                        found[item] = float(value)
        matches = {
            item: self._matches_evidence(figure, [value], [value])
            for item, value in found.items()
        }
        # A container is answered only by the leaf that holds the figure.
        kept = [item for item in found if item not in containers or matches[item]]
        ranked = sorted(kept, key=lambda item: (not matches[item], item))
        cap = _MAX_CONTAINER_REF_CANDIDATES if containers else _MAX_INDEXED_REF_CANDIDATES
        return ranked[:cap]

    def _other_call_field_ref_candidates(
        self, ref: str, symbol: str | None, figure: Figure
    ) -> list[str]:
        """Same-field refs from other calls of the tool whose value matches ``figure``.

        A ``call_id::field`` ref that does not hold the figure may still name the
        right field of the wrong call (scope or period). Only calls of the same
        tool as the declared call, whose value for that field matches, are
        offered, most recent first. A hint only: the declared ref stays rejected
        and the next draft must name the exact ref itself.
        """
        found: dict[str, None] = {}
        for key in (part.strip() for part in re.split(r"[;,]", ref or "")):
            if "::" not in key:
                continue
            scope, field = (part.strip() for part in key.split("::", 1))
            tools = {r.tool for r in self._evidence if r.call_id == scope}
            tools |= {e.get("tool") for e in self._analysis_metrics if e.get("call_id") == scope}
            if not scope or not field or not tools:
                continue
            records, entries = self._field_sources(field, symbol)
            for call_id, tool, path, value, record in (
                *((r.call_id, r.tool, r.field, r.value, r) for r in records),
                *((e.get("call_id"), e.get("tool"), e.get("field"), e.get("value"), None) for e in entries),
            ):
                if (
                    call_id
                    and call_id != scope
                    and tool in tools
                    and isinstance(path, str)
                    and (record is None or self._kind_fits(record, figure))
                    and self._matches_evidence(figure, [float(value)], [float(value)])
                ):
                    found[f"{call_id}::{path}"] = None
        return list(reversed(found))[:_MAX_CALL_REF_CANDIDATES]

    @staticmethod
    def _kind_fits(record: EvidenceRecord, figure: Figure) -> bool:
        """Whether a record is of the kind ``figure`` can be grounded in.

        Mirrors the narrowing ``_referenced`` applies, so a hint never points at
        a leaf the next draft would still be rejected for.
        """
        if figure.column and record.field != figure.column:
            return False
        if figure.percent:
            return not _is_price_kind(record) and not _is_metadata_count_leaf(record.field)
        if figure.currency:
            return _is_price_kind(record)
        return True

    def _field_ref_repair_candidates(
        self, field: str, symbol: str | None, figure: Figure
    ) -> list[str]:
        """Find exact refs for a field path that may carry a model-added alias prefix."""
        parts = [part for part in _index_normalized(field).split(".") if part]
        variants = [".".join(parts[index:]) for index in range(len(parts))]
        found: dict[str, list[float]] = {}
        for candidate_field in variants:
            records, entries = self._field_sources(candidate_field, symbol)
            for record in records:
                if record.call_id and record.field:
                    label = self._ref_source(record.call_id, record.field, record.scope)[1]
                    found.setdefault(label, []).append(float(record.value))
            for entry in entries:
                if entry.get("call_id") and entry.get("field"):
                    label = self._ref_source(str(entry["call_id"]), str(entry["field"]), None)[1]
                    found.setdefault(label, []).append(float(entry["value"]))
            if found:
                break
        money = bool(figure.currency and not figure.percent)
        compatible = {
            label
            for label, values in found.items()
            if self._matches_evidence(figure, values, [] if money else values)
        }
        ranked = sorted(found, key=lambda label: (label not in compatible, label))
        matching = [label for label in ranked if label in compatible]
        return (matching or ranked)[:_MAX_FIELD_REF_CANDIDATES]

    def _session_scope_field_ref_candidates(
        self, ref: str, symbol: str | None, figure: Figure
    ) -> list[str]:
        """Repair ``run_or_artifact_scope::alias.path`` to exact call refs.

        A run/artifact scope is a real session source, but it is not an exact
        call identity for ordinary tool-returned values.  When the model also
        prepends a presentation alias to the field path, keep the declaration
        invalid and offer exact ``call_id::full.path`` candidates instead.
        """
        key = (ref or "").strip()
        if "::" not in key:
            return []
        scope, field = (part.strip() for part in key.split("::", 1))
        if not scope or not field:
            return []
        call_or_tool = (
            any(scope in (record.call_id, record.tool) for record in self._evidence)
            or any(
                scope in (entry.get("call_id"), entry.get("tool"))
                for entry in self._analysis_metrics
            )
        )
        if call_or_tool or not self._artifact_scope(scope):
            return []
        return self._field_ref_repair_candidates(field, symbol, figure)

    def _unknown_call_field_ref_candidates(
        self, ref: str, symbol: str | None, figure: Figure
    ) -> list[str]:
        """Exact ``call_id::path`` refs for a ``scope::field`` whose scope names nothing.

        A model that writes an alias (``p1::field``) instead of the call id it
        was given names no call, tool or run of this session, so the ref selects
        no evidence. This only lists where the field really lives, so the next
        draft can copy an exact ref; it grants nothing, and the figure stays
        rejected until it is re-declared with one of them. Refs whose value the
        figure matches come first; at most :data:`_MAX_CALL_REF_CANDIDATES` are
        returned. Candidates keep the same symbol and kind restrictions as a
        real field ref, so the correction cannot recommend an unusable ref.
        """
        key = (ref or "").strip()
        if "::" not in key:
            return []
        scope, field = (part.strip() for part in key.split("::", 1))
        if not scope or not field or self._names_session_source(scope):
            return []
        records, entries = self._field_sources(field, symbol)
        found: dict[str, list[float]] = {}
        money = bool(figure.currency and not figure.percent)
        for record in records:
            if record.call_id and record.field and self._kind_fits(record, figure):
                label = self._ref_source(record.call_id, record.field, record.scope)[1]
                found.setdefault(label, []).append(float(record.value))
        for entry in entries if not (money or figure.column) else ():
            if entry.get("call_id") and entry.get("field"):
                label = self._ref_source(str(entry["call_id"]), str(entry["field"]), None)[1]
                found.setdefault(label, []).append(float(entry["value"]))
        compatible = {
            label
            for label, values in found.items()
            if self._matches_evidence(figure, values, [] if money else values)
        }
        ranked = sorted(found, key=lambda label: (label not in compatible, label))
        matching = [label for label in ranked if label in compatible]
        return (matching or ranked)[:_MAX_CALL_REF_CANDIDATES]

    def _exact_repair_metadata(
        self,
        candidates: Sequence[str],
        figure: Figure,
        symbol: str | None,
    ) -> dict[str, Any]:
        """Correction metadata for refs proven to hold the written figure.

        Candidate lists are hints and may contain structural fallbacks. Re-resolve
        every candidate and expose only refs whose evidence actually matches the
        written value. One proven ref is an exact repair; several proven refs are
        an explicit ambiguity the correction must preserve rather than drop.
        """
        proven: list[str] = []
        aggregate: list[str] = []
        for candidate in dict.fromkeys(str(item) for item in candidates):
            scoped = self._referenced(candidate, symbol, figure)
            matched = False
            if scoped is not None:
                records, metric_values = scoped
                values = [
                    float(record.value)
                    for record in records
                    if record.value is not None
                ] + metric_values
                money = bool(figure.currency and not figure.percent)
                deterministic_result = bool(records) and all(
                    record.tool == "financial_rigor" and record.field == "result"
                    for record in records
                )
                if deterministic_result and len(values) == 1:
                    matched = self._strict_written_match(figure, values[0])[0]
                else:
                    matched = bool(values) and self._matches_evidence(
                        figure, values, [] if money else values
                    )
            if matched:
                proven.append(candidate)
            if self._aggregate_exact_ref_match(candidate, figure):
                if candidate not in proven:
                    proven.append(candidate)
                aggregate.append(candidate)

        if not proven:
            return {}

        metadata: dict[str, Any] = {
            "proven_ref_repair_candidates": proven,
        }
        if len(proven) == 1:
            metadata["exact_ref_repair_candidate"] = proven[0]
        if aggregate and len(aggregate) == len(proven):
            metadata["aggregate_ref_candidates"] = aggregate
            if len(aggregate) == 1:
                metadata["aggregate_ref_candidate"] = aggregate[0]
        return metadata

    def _names_session_source(self, name: str) -> bool:
        """Whether ``name`` is a call id, tool name or backtest run of this session."""
        return (
            any(name in (record.call_id, record.tool) for record in self._evidence)
            or any(name in (entry.get("call_id"), entry.get("tool")) for entry in self._analysis_metrics)
            or bool(self._artifact_scope(name))
        )

    def _tail_risk_ref_required(
        self,
        figure: Figure,
        records: Sequence[EvidenceRecord],
        entries: Iterable[Mapping[str, Any]] = (),
    ) -> list[str]:
        """Tail-risk identities this figure could be quoting, when there are several.

        #1425's remaining half, decided as a policy rather than patched: a
        session that observed more than one tail-risk identity cannot tell which
        one an undeclared or call-scoped figure means — the gate reads a
        number's shape, never the words "VaR 99%" beside it — so the figure has
        to name its field. A ref that names the field narrows the scope to one
        identity before this runs, so a declared figure never reaches here.

        Empty when the scope holds at most one identity (nothing to confuse) or
        when the figure matches none of the tail-risk values, which keeps every
        other kind of figure on exactly today's path.

        Returns:
            Sorted identities, or an empty list when no ref is required.
        """
        sources = self._tail_risk_sources(records, entries)
        if len({identity for identity, _ in sources}) < 2:
            return []
        return sorted(
            {
                identity
                for identity, value in sources
                if self._matches_evidence(figure, [value], [value])
            }
        )

    def _price_pool(
        self,
        symbol: str | None,
        records: Sequence[EvidenceRecord],
        *,
        column: str | None = None,
        date: str | None = None,
        currency_code: str | None = None,
    ) -> list[float]:
        """Observed price values a figure may be compared against."""
        candidates = self._price_candidates(
            symbol, records, column=column, date=date
        )
        if currency_code is not None:
            candidates = [
                record
                for record in candidates
                if record.currency == currency_code
            ]
        return [float(record.value) for record in candidates]

    def _price_candidates(
        self,
        symbol: str | None,
        records: Sequence[EvidenceRecord],
        *,
        column: str | None = None,
        date: str | None = None,
    ) -> list[EvidenceRecord]:
        """Observed price records a figure may be compared against.

        Filtered by symbol, then by OHLC field and trade date when the figure sits
        under those table headers (spec §4).
        """
        candidates = list(records)
        if symbol:
            candidates = [record for record in candidates if record.symbol == symbol]
        elif len({record.symbol for record in records if record.symbol}) > 1:
            # Two instruments and no resolved symbol: an indicator reading is
            # symbol-bound, so it cannot ground a figure attributed to neither.
            candidates = [record for record in candidates if record.field != "indicator"]
        if column:
            candidates = [record for record in candidates if record.field == column]
        if date:
            candidates = [
                record
                for record in candidates
                if record.timestamp
                and _timestamp_matches_claim_date(record.timestamp, date)
            ]
        return [record for record in candidates if record.value is not None]

    def _row_pool(
        self,
        symbol: str | None,
        *,
        money_only: bool = False,
        currency_code: str | None = None,
    ) -> list[float]:
        """Numbers a market-data row carried (volume, amount, turnover, …).

        Only ``get_market_data`` and run-dir CSV rows count, so a generic tool's
        numeric leaves never widen the check. ``money_only`` keeps the
        money-denominated fields a currency-marked figure may quote.
        """
        return [
            float(record.value)
            for record in self._evidence
            if record.status == "observed"
            and record.value is not None
            and record.tool in {"get_market_data", "bash"}
            and (not symbol or record.symbol == symbol)
            and (not money_only or _is_price_kind(record))
            and (currency_code is None or record.currency == currency_code)
        ]

    @staticmethod
    def _nearest_prints(
        figure: Figure,
        scope: Sequence[EvidenceRecord],
        fallback: Sequence[float],
    ) -> list[float]:
        """The observed values a rejected figure is pointed at (#1433).

        A non-percent figure is pointed at its own table column, else at the
        closes (then last or adjusted prices) in scope, never at every field of
        every bar; a percent at the values it was compared with.

        Args:
            figure: The rejected figure.
            scope: The evidence records its check was scoped to.
            fallback: The values its check compared it against.

        Returns:
            Up to three observed values, closest first.
        """
        if not figure.percent:
            for name in (figure.column,) if figure.column else _CITABLE_FIELDS:
                values = [
                    float(record.value)
                    for record in scope
                    if record.value is not None
                    and (_price_field_for_path(record.field) or record.field) == name
                ]
                if values:
                    return _nearest(figure.value, values)
        return _nearest(figure.value, fallback)

    @grounding_check(name="figure-count", code="numeric_claim_conflict",
                     additional_codes=("numeric_claim_unavailable",),
                     description="Validate the count figure contract against evidence.")
    def _count_as_observed(
        self,
        figure: Figure,
        declaration: Declaration,
        symbol: str | None,
        records: Sequence[EvidenceRecord],
        *,
        why: str = "a count cannot carry a currency mark",
    ) -> list[dict[str, Any]]:
        """Check a ``count`` that looks like a price as the observation it claims to be."""
        found = self._check_observed(figure, declaration, symbol, records)
        for issue in found:
            issue["role"] = "count"
            issue["message"] = (
                f"{figure.text} is declared count, but {why}, so it was checked as "
                "observed: " + issue["message"][len(figure.text) + 1 :]
            )
        return found

    def _price_band(
        self, symbol: str | None, records: Sequence[EvidenceRecord]
    ) -> tuple[float, float] | None:
        """The observed price range of a figure's instrument, or None when unknown."""
        if symbol is None and len({record.symbol for record in records if record.symbol}) > 1:
            return None
        prices = [value for value in self._price_pool(symbol, records) if value > 0]
        return (min(prices), max(prices)) if prices else None

    @staticmethod
    def _poses_as_price(figure: Figure, band: tuple[float, float] | None) -> bool:
        """Whether an unmarked number sits where its instrument's price does.

        A decimal inside the observed range (±10%) reads as a quote. An integer
        does only for an instrument quoted in the thousands, between half and
        twice its range. A percent is never a price.

        Args:
            figure: The figure.
            band: The instrument's observed ``(low, high)``, or None.

        Returns:
            True when the number should be checked as a price.
        """
        if band is None or figure.percent:
            return False
        low, high = band
        value = abs(figure.value)
        if "." in (figure.digits or figure.text):
            return low * 0.9 <= value <= high * 1.1
        return low >= _INTEGER_PRICE_FLOOR and low * 0.5 <= value <= high * 2.0

    def _metric_pool(self, symbol: str | None) -> list[float]:
        """Metric values from completed analysis results and metric-named leaves."""
        values = [
            float(entry["value"])
            for entry in self._analysis_entries(symbol)
            if entry.get("value") is not None
        ]
        values.extend(
            float(record.value)
            for record in self._evidence
            if record.status == "observed"
            and record.value is not None
            and _metric_kind_for_path(record.field) is not None
            and _keyed_metric_map_kind(record.field) is None
            and _record_matches_entity(record, symbol)
        )
        return values

    def _matches_evidence(
        self,
        figure: Figure,
        direct: Sequence[float],
        scaled: Sequence[float],
        *,
        legacy_direct: bool = False,
    ) -> bool:
        """Whether a figure equals evidence, at its own scale or a metric's.

        ``direct`` is compared literally. ``scaled`` absorbs fraction vs percent
        (0.182 vs 18.2%) and the sign of a fall (drawdown -0.094 quoted as 9.4%).
        Both are held to the digits the figure was written with
        (:meth:`_within_written_precision`); ``legacy_direct`` keeps the flat evidence
        band for ``direct`` when it is an undeclared price checked against prints.
        """
        if legacy_direct:
            if _close_any(figure.value, direct):
                return True
            if figure.scale != 1.0 and _close_any(figure.value * figure.scale, direct):
                return True
        elif any(
            self._within_written_precision(figure, figure.value, target)
            for target in direct
        ):
            return True
        elif figure.scale != 1.0 and any(
            self._within_written_precision(
                figure, figure.value * figure.scale, target, figure.scale
            )
            for target in direct
        ):
            return True
        magnitudes = [abs(target) for target in scaled]
        return any(
            self._within_written_precision(figure, candidate, target, unit)
            for candidate, unit in (
                (abs(figure.value), 1.0),
                (abs(figure.value) / 100.0, 0.01),
            )
            for target in magnitudes
        )

    @staticmethod
    def _within_written_precision(
        figure: Figure, candidate: float, target: float, unit: float = 1.0
    ) -> bool:
        """Whether a figure is ``target`` correctly rounded to the digits it was written with.

        Raw evidence uses the relative :data:`_TOLERANCE` band. A figure written
        with decimals uses :data:`figures.ROUNDED_BAND` (the same 0.5% cap),
        narrowed to half a unit of its last written decimal. A sufficiently
        precise rendering such as
        0.82467 -> 0.825 survive; 0.82 exceeds the 0.5% relative policy.
        A figure written without decimals keeps the raw relative band alone: an
        integer's precision is not known ("6,700" may be rounded to hundreds).

        Args:
            figure: The prose figure.
            candidate: The figure's value in the units being compared.
            target: The evidence value.
            unit: How many compared units one written unit is (0.01 when a percent
                is compared as a fraction).
        """
        written = figure.digits or figure.text
        band = abs(target) * (ROUNDED_BAND if "." in written else _TOLERANCE)
        if "." in written:
            half_unit = _written_half_unit(written) * unit
            band = min(band, half_unit * (1 + 1e-9))
            gap = abs(candidate - target)
            if gap <= max(band, 1e-9):
                return True

            # Preserve the 0.5% cap for ordinary coarse rounding, but allow an
            # exact decimal half-way case when ROUND_HALF_UP produces precisely
            # the written figure. This distinguishes 0.225 -> 0.23 (valid) from
            # 0.225 -> 0.22 and keeps 0.82467 -> 0.82 rejected.
            if unit and abs(gap - half_unit) <= max(half_unit * 1e-9, 1e-12):
                places = len(written.split(".", 1)[1])
                quant = Decimal(1).scaleb(-places)
                rounded_target = (Decimal(str(target)) / Decimal(str(unit))).quantize(
                    quant, rounding=ROUND_HALF_UP
                )
                written_candidate = (Decimal(str(candidate)) / Decimal(str(unit))).quantize(
                    quant
                )
                return rounded_target == written_candidate
            return False
        return abs(candidate - target) <= max(band, 1e-9)

    def _aggregate_exact_ref_match(
        self,
        ref: str,
        figure: Figure,
    ) -> bool:
        """Whether an exact ref holds this value only as aggregate evidence.

        This is correction metadata, never an authorization path: a draft that
        attaches the aggregate to an instrument remains invalid.  The model can
        repair it by keeping the exact value/ref while rewriting the claim as
        portfolio-level.
        """
        keys = [key.strip() for key in re.split(r"[,;]", ref or "") if key.strip()]
        if len(keys) != 1 or "::" not in keys[0]:
            return False
        scoped = self._referenced(keys[0], None, figure)
        if scoped is None:
            return False
        aggregate_records, metric_values = scoped
        if metric_values or not aggregate_records:
            return False
        if any(record.identity_scope != "aggregate" for record in aggregate_records):
            return False
        values = [float(record.value) for record in aggregate_records if record.value is not None]
        return bool(values) and self._matches_evidence(figure, values, values)

    def _entity_exact_ref_match(
        self,
        ref: str,
        figure: Figure,
    ) -> str | None:
        """Return the one entity an exact ref/value belongs to, if any.

        This is correction metadata only.  Validation still uses the entity
        resolved from the answer text and therefore remains fail-closed.  The
        helper detects the narrower case where an exact call_id::field ref
        contains the written value for one entity, but nearby prose caused the
        figure to be attributed to another entity.
        """
        keys = [key.strip() for key in re.split(r"[,;]", ref or "") if key.strip()]
        if len(keys) != 1 or "::" not in keys[0]:
            return None
        scoped = self._referenced(keys[0], None, figure)
        if scoped is None:
            return None
        records, metric_values = scoped
        if metric_values or not records:
            return None
        matching = [
            record
            for record in records
            if record.identity_scope == "entity"
            and record.symbol
            and record.value is not None
            and self._matches_evidence(
                figure,
                [float(record.value)],
                [float(record.value)],
            )
        ]
        symbols = {str(record.symbol) for record in matching}
        return next(iter(symbols)) if len(symbols) == 1 else None

    def _undeclared_entity_repair_metadata(
        self,
        figure: Figure,
    ) -> dict[str, Any]:
        """Exact ref/entity hint for one undeclared entity-scoped value.

        The hint is emitted only when the written value and kind identify one
        exact observed ref belonging to one entity. It authorizes nothing: the
        current draft remains undeclared/invalid and must be rewritten with the
        exact ref in a context scoped to that entity.
        """
        matches: list[tuple[str, str]] = []
        for record in self._evidence:
            if (
                record.status != "observed"
                or record.value is None
                or record.identity_scope != "entity"
                or not record.symbol
                or not record.call_id
                or not record.field
                or not self._kind_fits(record, figure)
                or not self._matches_evidence(
                    figure,
                    [float(record.value)],
                    [float(record.value)],
                )
            ):
                continue
            matches.append(
                (
                    self._ref_source(record.call_id, record.field, record.scope)[1],
                    str(record.symbol),
                )
            )
        unique = list(dict.fromkeys(matches))
        refs = {ref for ref, _ in unique}
        symbols = {symbol for _, symbol in unique}
        if len(refs) != 1 or len(symbols) != 1:
            return {}
        ref = next(iter(refs))
        symbol = next(iter(symbols))
        return {
            "field_ref_candidates": [ref],
            "entity_ref_symbol": symbol,
        }

    def _structured_metric_repair_metadata(
        self, figure: Figure, symbol: str | None = None
    ) -> dict[str, Any]:
        """Offer exact refs for one matching structured metric leaf.

        This metadata does not approve the current draft. The corrected answer
        must cite the call and leaf, then pass the ordinary validator. Matrix
        cells require both visible axes to match the recorded path.
        """
        candidates: list[str] = []
        for record in self._evidence:
            if (
                record.status != "observed"
                or record.value is None
                or not record.call_id
                or not record.field
                or not self._kind_fits(record, figure)
                or not self._matches_evidence(figure, [float(record.value)], [float(record.value)])
            ):
                continue
            parts = record.field.split(".")
            matrix_index = next(
                (index for index, part in enumerate(parts)
                 if part.casefold() in {"correlation_matrix", "correlation", "covariance_matrix"}),
                None,
            )
            if matrix_index is not None:
                if not figure.table_column_label or not figure.symbol:
                    continue
                row = _normalize_symbol(figure.symbol)
                column = _normalize_symbol(figure.table_column_label)
                if len(parts) < matrix_index + 3:
                    continue
                if (_normalize_symbol(parts[matrix_index + 1]),
                        _normalize_symbol(parts[matrix_index + 2])) != (row, column):
                    continue
            elif record.identity_scope == "entity":
                # A table's row label is the written entity for contributions.
                written = symbol
                if figure.symbol:
                    written_symbols = _scan_symbols(figure.symbol)
                    written = next(iter(written_symbols)) if len(written_symbols) == 1 else _normalize_symbol(figure.symbol)
                if written and record.symbol != written:
                    continue
            candidates.append(self._ref_source(record.call_id, record.field, record.scope)[1])
        refs = list(dict.fromkeys(candidates))
        if not refs:
            return {}
        metadata: dict[str, Any] = {"field_ref_candidates": refs}
        if len(refs) == 1:
            metadata.update(self._exact_repair_metadata(refs, figure, symbol))
        return metadata

    def _canonical_position_weight_replacement(
        self,
        declaration: Declaration,
        figure: Figure,
        symbol: str | None,
    ) -> dict[str, Any]:
        """Canonical entity weight replacing a risk-normalized aggregate weight.

        A bare field path is accepted only when it identifies exactly one source
        call in this session. The ticker encoded in weights.<TICKER> must
        agree with the claim entity, and the canonical holding must come from
        that same call. Selection is semantic, never based on numeric proximity.
        """
        if not symbol:
            return {}

        raw_ref = (declaration.ref or "").strip()
        if "::" in raw_ref:
            scope, field = (part.strip() for part in raw_ref.split("::", 1))
        else:
            scope, field = "", raw_ref
        folded = field.casefold()
        marker = "risk_xray_args.weights."
        if marker not in folded:
            return {}

        encoded = field[folded.index(marker) + len(marker):].split(".", 1)[0].strip()
        encoded_symbol = _normalize_symbol(encoded)
        if not encoded_symbol or encoded_symbol != symbol:
            return {}

        source_calls: set[str] = set()
        for record in self._evidence:
            if (
                record.status == "observed"
                and record.field == field
                and record.call_id
            ):
                if scope:
                    tool_alias = scope.removeprefix("functions.")
                    if scope not in {
                        record.call_id,
                        record.tool,
                        f"functions.{record.tool}",
                    } and tool_alias != record.tool:
                        continue
                source_calls.add(str(record.call_id))
        if len(source_calls) != 1:
            return {}
        source_call = next(iter(source_calls))

        candidates: list[tuple[str, float]] = []
        for record in self._evidence:
            if (
                record.call_id != source_call
                or record.status != "observed"
                or record.value is None
                or record.symbol != symbol
                or record.identity_scope != "entity"
                or "holdings_native" not in record.field.casefold()
                or not record.field.casefold().endswith(".weight")
            ):
                continue
            ref = self._ref_source(record.call_id, record.field, record.scope)[1]
            candidates.append((ref, float(record.value)))

        unique = list(dict.fromkeys(candidates))
        if len(unique) != 1:
            return {}
        ref, raw_value = unique[0]
        replacement_value = raw_value * 100.0 if figure.percent else raw_value
        places = (
            len((figure.digits or "").split(".", 1)[1])
            if "." in (figure.digits or "")
            else 0
        )
        replacement_text = (
            f"{replacement_value:.{places}f}"
            if places
            else f"{replacement_value:g}"
        ) + ("%" if figure.percent else "")
        return {
            "replacement_ref_candidate": ref,
            "replacement_value": replacement_value,
            "replacement_text": replacement_text,
            "replacement_digits": (
                f"{replacement_value:.{places}f}" if places else f"{replacement_value:g}"
            ),
            "replacement_entity_symbol": symbol,
            "replacement_role": "observed",
            "replacement_source_call_id": source_call,
        }

    def _canonical_summary_weight_replacement(
        self,
        declaration: Declaration,
        figure: Figure,
    ) -> dict[str, Any]:
        """Canonical Top-N replacement for a sum of risk-normalized weights."""
        refs = [
            item.strip()
            for item in re.split(r"[,;]", declaration.ref or "")
            if item.strip()
        ]
        if len(refs) < 2:
            return {}

        source_call: str | None = None
        canonical: list[tuple[str, float]] = []
        seen_symbols: set[str] = set()
        for raw_ref in refs:
            if "::" in raw_ref:
                scope, field = (part.strip() for part in raw_ref.split("::", 1))
            else:
                scope, field = "", raw_ref
            folded = field.casefold()
            marker = "risk_xray_args.weights."
            if marker not in folded:
                return {}
            encoded = field[folded.index(marker) + len(marker):].split(".", 1)[0].strip()
            symbol = _normalize_symbol(encoded)
            if not symbol or symbol in seen_symbols:
                return {}
            seen_symbols.add(symbol)

            calls = {
                str(record.call_id)
                for record in self._evidence
                if record.status == "observed"
                and record.field == field
                and record.call_id
                and (
                    not scope
                    or scope in {record.call_id, record.tool, f"functions.{record.tool}"}
                    or scope.removeprefix("functions.") == record.tool
                )
            }
            if len(calls) != 1:
                return {}
            call_id = next(iter(calls))
            if source_call is None:
                source_call = call_id
            elif source_call != call_id:
                return {}

            matches = [
                record
                for record in self._evidence
                if record.call_id == call_id
                and record.status == "observed"
                and record.value is not None
                and record.symbol == symbol
                and record.identity_scope == "entity"
                and "holdings_native" in record.field.casefold()
                and record.field.casefold().endswith(".weight")
            ]
            unique = list(
                dict.fromkeys(
                    (
                        self._ref_source(record.call_id, record.field, record.scope)[1],
                        float(record.value),
                    )
                    for record in matches
                )
            )
            if len(unique) != 1:
                return {}
            canonical.append(unique[0])

        raw_total = sum(value for _, value in canonical)
        replacement_value = raw_total * 100.0 if figure.percent else raw_total
        places = (
            len((figure.digits or "").split(".", 1)[1])
            if "." in (figure.digits or "")
            else 0
        )
        replacement_text = (
            f"{replacement_value:.{places}f}"
            if places
            else f"{replacement_value:g}"
        ) + ("%" if figure.percent else "")
        formula = "(" + "+".join(repr(value) for _, value in canonical) + ")"
        if figure.percent:
            formula += "*100"
        return {
            "replacement_value": replacement_value,
            "replacement_text": replacement_text,
            "replacement_digits": (
                f"{replacement_value:.{places}f}" if places else f"{replacement_value:g}"
            ),
            "replacement_role": "derived",
            "replacement_refs": [ref for ref, _ in canonical],
            "replacement_formula": formula,
            "replacement_source_call_id": source_call,
        }

    @grounding_check(name="figure-observed", code="numeric_claim_conflict",
                     additional_codes=("numeric_claim_unavailable",),
                     description="Validate the observed figure contract against evidence.")
    def _check_observed(
        self,
        figure: Figure,
        declaration: Declaration | None,
        symbol: str | None,
        records: Sequence[EvidenceRecord],
    ) -> list[dict[str, Any]]:
        """An observed figure must appear in evidence of its own kind.

        A currency-marked figure is answered only by money-denominated values
        and a percent only by the rest; a table cell only by its column's field.
        """
        scoped = self._referenced(declaration.ref, symbol, figure) if declaration else None
        if scoped is not None:
            scoped_records, metric_values = scoped
            if (
                declaration is not None
                and not scoped_records
                and not metric_values
            ):
                twin_candidates = self._same_parent_value_twin_candidates(
                    declaration.ref, figure, symbol
                )
                if twin_candidates:
                    return [
                        self._figure_issue(
                            "numeric_claim_conflict",
                            figure,
                            "observed",
                            symbol,
                            "field_ref_needs_numeric_sibling",
                            f"is declared from {declaration.ref}, whose numeric value matches "
                            "but whose evidence kind cannot ground this figure; use the exact "
                            "same-call numeric sibling ref listed below",
                            source_tool_call_ids=[declaration.ref],
                            field_ref_candidates=twin_candidates,
                            **self._exact_repair_metadata(
                                twin_candidates, figure, symbol
                            ),
                        )
                    ]
                canonical_replacement = self._canonical_position_weight_replacement(
                    declaration, figure, symbol
                )
                if canonical_replacement:
                    return [
                        self._figure_issue(
                            "numeric_claim_conflict",
                            figure,
                            "observed",
                            symbol,
                            "entity_value_needs_canonical_replacement",
                            "is declared from a risk-normalized aggregate weight, but "
                            "this entity has one canonical portfolio-position weight; "
                            "replace both the written number and the ref with the "
                            "canonical entity value supplied below",
                            source_tool_call_ids=[declaration.ref],
                            field_ref_candidates=[
                                str(canonical_replacement["replacement_ref_candidate"])
                            ],
                            **canonical_replacement,
                        )
                    ]
                bare_field_candidates = self._bare_field_ref_candidates(
                    declaration.ref, symbol, figure
                )
                if bare_field_candidates:
                    return [
                        self._figure_issue(
                            "numeric_claim_conflict",
                            figure,
                            "observed",
                            symbol,
                            "field_ref_needs_call_id",
                            f"is declared observed from bare field {declaration.ref}; "
                            "use one exact session call ref proven to contain this value",
                            source_tool_call_ids=[declaration.ref],
                            field_ref_candidates=bare_field_candidates,
                            **self._exact_repair_metadata(
                                bare_field_candidates, figure, symbol
                            ),
                        )
                    ]
                call_field_candidates = self._tool_field_ref_candidates(
                    declaration.ref, symbol, figure
                )
                if (
                    symbol
                    and declaration is not None
                    and self._aggregate_exact_ref_match(declaration.ref, figure)
                ):
                    return [
                        self._figure_issue(
                            "numeric_claim_conflict",
                            figure,
                            "observed",
                            symbol,
                            "aggregate_ref_needs_unscoped_claim",
                            f"is declared from exact aggregate evidence at {declaration.ref}, "
                            f"but the answer attaches that portfolio-level metric to {symbol}",
                            source_tool_call_ids=[declaration.ref],
                        )
                    ]
                entity_ref_symbol = (
                    self._entity_exact_ref_match(declaration.ref, figure)
                    if symbol and declaration is not None
                    else None
                )
                if entity_ref_symbol and entity_ref_symbol != symbol:
                    return [
                        self._figure_issue(
                            "numeric_claim_conflict",
                            figure,
                            "observed",
                            symbol,
                            "entity_ref_needs_scoped_claim",
                            f"is declared from exact entity evidence at {declaration.ref} "
                            f"for {entity_ref_symbol}, but the answer context attributes "
                            f"the figure to {symbol}",
                            source_tool_call_ids=[declaration.ref],
                            entity_ref_symbol=entity_ref_symbol,
                        )
                    ]
                if call_field_candidates:
                    return [
                        self._figure_issue(
                            "numeric_claim_conflict",
                            figure,
                            "observed",
                            symbol,
                            "field_ref_needs_call_id",
                            f"is declared observed from {declaration.ref}, whose left side is "
                            "a tool name rather than one exact call id",
                            source_tool_call_ids=[declaration.ref],
                            ambiguous_sources=call_field_candidates,
                            field_ref_candidates=call_field_candidates,
                            **self._exact_repair_metadata(call_field_candidates, figure, symbol),
                        )
                    ]
                session_scope_candidates = self._session_scope_field_ref_candidates(
                    declaration.ref, symbol, figure
                )
                if session_scope_candidates:
                    return [
                        self._figure_issue(
                            "numeric_claim_conflict",
                            figure,
                            "observed",
                            symbol,
                            "session_scope_needs_call_id",
                            f"is declared observed from {declaration.ref}, whose left side names "
                            "a session run/artifact rather than the exact tool call that returned "
                            "this scalar",
                            source_tool_call_ids=[declaration.ref],
                            field_ref_candidates=session_scope_candidates,
                            **self._exact_repair_metadata(
                                session_scope_candidates, figure, symbol
                            ),
                        )
                    ]
                unknown_scope_candidates = self._unknown_call_field_ref_candidates(
                    declaration.ref, symbol, figure
                )
                if unknown_scope_candidates:
                    return [
                        self._figure_issue(
                            "numeric_claim_conflict",
                            figure,
                            "observed",
                            symbol,
                            "unknown_call_id",
                            f"is declared observed from {declaration.ref}, whose left side "
                            "is not a call id, tool or run of this session",
                            source_tool_call_ids=[declaration.ref],
                            field_ref_candidates=unknown_scope_candidates,
                            **self._exact_repair_metadata(
                                unknown_scope_candidates, figure, symbol
                            ),
                        )
                    ]
                if self._has_asistente_casa_evidence():
                    return [
                        self._figure_issue(
                            "numeric_claim_conflict",
                            figure,
                            "observed",
                            symbol,
                            "not_in_referenced_call",
                            f"is declared observed from {declaration.ref}, which does not identify an exact session source",
                            source_tool_call_ids=[declaration.ref],
                        )
                    ]
            if figure.currency_code is not None:
                scoped_records = [
                    record
                    for record in scoped_records
                    if record.currency == figure.currency_code
                ]
            values = [float(record.value) for record in scoped_records] + metric_values
            money = figure.currency and not figure.percent
            # A call- or tool-scoped ref pools every field that call returned,
            # so it cannot choose between the tail-risk identities in it (#1425).
            scoped_entries = [
                entry
                for entry in self._analysis_entries(symbol)
                if declaration.ref in (entry.get("call_id"), entry.get("tool"))
            ]
            tail_risk = self._tail_risk_ref_required(
                figure, scoped_records, scoped_entries
            )
            scoped_identities: list[str] = []
            if tail_risk:
                scoped_sources = self._tail_risk_sources(scoped_records, scoped_entries)
                scoped_identities = sorted({identity for identity, _ in scoped_sources})
                # Whatever else the call returned may still answer the figure.
                blocked = {
                    value for identity, value in scoped_sources if identity in tail_risk
                }
                values = [value for value in values if value not in blocked]
            if self._matches_evidence(figure, values, [] if money else values):
                return []
            if tail_risk:
                return [
                    self._figure_issue(
                        "numeric_claim_conflict",
                        figure,
                        "observed",
                        symbol,
                        "tail_risk_needs_field_ref",
                        f"is declared observed from {declaration.ref}, which returned "
                        f"{', '.join(scoped_identities)} and matches "
                        f"{', '.join(tail_risk)}; a tail-risk figure has to name the "
                        "field it quotes",
                        source_tool_call_ids=[declaration.ref],
                        ambiguous_sources=scoped_identities,
                        field_ref_candidates=self._tail_risk_field_refs(
                            scoped_records, scoped_entries
                        ),
                    )
                ]
            ambiguous = self._ambiguous_field_sources(declaration.ref, symbol)
            if ambiguous:
                return [
                    self._figure_issue(
                        "numeric_claim_conflict",
                        figure,
                        "observed",
                        symbol,
                        "ambiguous_field_ref",
                        f"is declared observed from {declaration.ref}, which names "
                        f"{', '.join(ambiguous)}, and they hold different values",
                        source_tool_call_ids=[declaration.ref],
                        ambiguous_sources=ambiguous,
                        field_ref_candidates=ambiguous,
                    )
                ]
            return [
                self._figure_issue(
                    "numeric_claim_conflict",
                    figure,
                    "observed",
                    symbol,
                    "not_in_referenced_call",
                    f"is declared observed from {declaration.ref}, whose results "
                    f"{'for ' + symbol + ' ' if symbol else ''}do not contain it",
                    source_tool_call_ids=[declaration.ref],
                    field_ref_candidates=list(
                        dict.fromkeys(
                            [
                                *self._indexed_field_ref_candidates(declaration.ref, figure),
                                *self._other_call_field_ref_candidates(declaration.ref, symbol, figure),
                            ]
                        )
                    ),
                    observed_nearest=self._nearest_prints(figure, scoped_records, values),
                )
            ]
        candidates = self._price_candidates(
            symbol, records, column=figure.column, date=figure.date
        )
        if figure.currency_code is not None:
            candidates = [
                record
                for record in candidates
                if record.currency == figure.currency_code
            ]
        prices = [float(record.value) for record in candidates]
        if figure.percent:
            # A percent is a ratio; no price or volume may answer it.
            direct: list[float] = []
            scaled = [] if figure.column else self._metric_pool(symbol)
        elif figure.column:
            direct, scaled = prices, []
        elif figure.currency:
            direct, scaled = prices + self._row_pool(
                symbol,
                money_only=True,
                currency_code=figure.currency_code,
            ), []
        else:
            direct, scaled = prices + self._row_pool(symbol), self._metric_pool(symbol)
        # An undeclared tail-risk figure is matched against every tail-risk
        # value in the session, so it needs a field ref for the same reason a
        # call-scoped one does (#1425).
        session_records = [
            record
            for record in self._evidence
            if _record_matches_entity(record, symbol)
        ]
        tail_risk = self._tail_risk_ref_required(
            figure, session_records, self._analysis_entries(symbol)
        )
        session_identities: list[str] = []
        if tail_risk:
            session_sources = self._tail_risk_sources(
                session_records, self._analysis_entries(symbol)
            )
            session_identities = sorted({identity for identity, _ in session_sources})
            blocked = {
                value for identity, value in session_sources if identity in tail_risk
            }
            direct = [value for value in direct if value not in blocked]
            scaled = [value for value in scaled if value not in blocked]
        if not direct and not scaled and not tail_risk:
            return [
                self._figure_issue(
                    "numeric_claim_unavailable",
                    figure,
                    "observed",
                    symbol,
                    "no_evidence",
                    "is declared observed but this session holds no matching tool "
                    "evidence to check it against",
                    field=figure.column,
                    date=figure.date,
                )
            ]
        if self._matches_evidence(figure, direct, scaled, legacy_direct=True):
            return []
        if tail_risk:
            return [
                self._figure_issue(
                    "numeric_claim_conflict",
                    figure,
                    "observed",
                    symbol,
                    "tail_risk_needs_field_ref",
                    f"matches {', '.join(tail_risk)}, and this session observed "
                    f"{', '.join(session_identities)}, so the figure has to name "
                    "the field it quotes",
                    ambiguous_sources=session_identities,
                    field_ref_candidates=self._tail_risk_field_refs(
                        session_records, self._analysis_entries(symbol)
                    ),
                )
            ]
        observed = sorted(direct or scaled)
        attributable = symbol is not None or len(
            {record.symbol for record in records if record.symbol}
        ) <= 1
        return [
            self._figure_issue(
                "numeric_claim_conflict",
                figure,
                "observed",
                symbol,
                "value_mismatch",
                "is declared observed but conflicts with the "
                f"{figure.column or 'observed'} evidence "
                f"{observed[0]:g}–{observed[-1]:g}",
                field=figure.column,
                date=figure.date,
                observed_min=observed[0],
                observed_max=observed[-1],
                observed_nearest=(
                    self._nearest_prints(figure, candidates, observed)
                    if attributable
                    else []
                ),
            )
        ]

    def _derivation(
        self,
        declaration: Declaration | None,
        symbol: str | None,
        records: Sequence[EvidenceRecord],
        *,
        money: bool = False,
        currency_code: str | None = None,
        percent: bool = False,
        scalar_parameters: Sequence[float] = (),
    ) -> tuple[float, list[float], bool] | str | None:
        """Evaluate a declaration's note as an observation-anchored formula.

        Returns the ``(result, operands, points)`` triple when the note is
        arithmetic over at least two operands. Data operands must be observed;
        chosen scalar parameters must be declared and visible. Every added or
        subtracted term still needs an observed anchor. Literal 1 is the
        multiplicative identity (also used in ratio-minus-one expressions),
        and square/cube exponents belong to the operator, not the operands.
        A percent claim's explicit trailing *100 is normalized as a unit
        conversion before checking the remaining data operands. ``points`` is
        True when every data operand anchored only to percent-named fields
        (``changepercent`` and friends already carry points, #1820), so the
        caller compares the result against the percent figure directly.
        """
        if declaration is None or not declaration.note.strip():
            return "no_formula"
        evaluated = _formula_in_note(declaration.note)
        if evaluated is None:
            return "formula_not_evaluable"
        result, operands, tree = evaluated
        normalized_x100 = False
        # A percent claim may spell its final unit conversion explicitly.
        # Normalize ONLY an outer, trailing literal *100 back to a fraction;
        # do not broaden result matching or exempt any inner data operand.
        # Numeric magnitude elsewhere never decides whether an operand is data.
        if (
            percent
            and isinstance(tree.body, ast.BinOp)
            and isinstance(tree.body.op, ast.Mult)
            and isinstance(tree.body.right, ast.Constant)
            and _is_number(tree.body.right.value)
            and tree.body.right.value == 100
        ):
            result /= 100
            operands = operands[:-1]
            tree = ast.Expression(body=tree.body.left)
            normalized_x100 = True
        # Exact generic financial refs are validated per operand, independently
        # of the identity of the resulting claim. Never borrow session pools.
        keys = [key.strip() for key in re.split(r"[,;]", declaration.ref) if key.strip()]
        resolved = [self._referenced_one(key, None, None, pool_ambiguous=True) for key in keys]
        # Do not silently discard empty or unresolved declared sources.
        # In particular, a valid market-price pool must never stand in for
        # "before; unknown", "before;" or an unknown exact call::field.
        ref_parts = re.split(r"[,;]", declaration.ref)
        if (len(ref_parts) > 1 or "::" in declaration.ref) and (
            any(not part.strip() for part in ref_parts)
            or any(item is None or (not item[0] and not item[1]) for item in resolved)
        ):
            return "no_evidence"
        # Keep legacy symbol-only references for observed market instruments,
        # but do not let an unknown *single* ref borrow an unrelated session
        # value. Valid call/tool/field/artifact refs resolved above.
        if (
            len(ref_parts) == 1
            and keys
            and resolved[0] is None
            # Preserve no_symbol only when attribution is genuinely absent.
            # With a surfaced symbol, an unknown ref must fail closed even
            # if other symbols also have observed bars.
            and (symbol is not None or len({r.symbol for r in records if r.symbol}) <= 1)
            and not any(
                _normalize_symbol(keys[0]) == _normalize_symbol(record.symbol)
                for record in self._evidence
                if record.symbol and record.status == "observed"
            )
        ):
            return "no_evidence"
        generic_financial = any(
            record.identity_scope in {"entity", "aggregate"}
            and record.unit in {"money", "ratio"}
            for item in resolved if item is not None for record in item[0]
        )
        if generic_financial and all("::" in key for key in keys):
            exact_records = []
            for key, item in zip(keys, resolved):
                if "::" not in key or item is None or len(item[0]) != 1 or item[1]:
                    return "no_evidence"
                call, field = (part.strip() for part in key.split("::", 1))
                record = item[0][0]
                if self._canonical_session_call_id(call) != record.call_id or field != record.field:
                    return "no_evidence"
                # Equivalent provider-id spellings name the same observation.
                if not any((r.call_id, r.field, r.scope) == (record.call_id, record.field, record.scope)
                           for r in exact_records):
                    exact_records.append(record)
            reason = validate_operands(tree, exact_records, symbol, money=money)
            # A percent-named leaf (the canonical adapter's *_pct fields, any
            # tool's ``*_percent``) encodes percentage points. Resolve only exact
            # cited leaves: never infer a scale from a similarly-valued field in
            # the session.
            percent_points = (
                percent
                and not normalized_x100
                and bool(exact_records)
                and all(
                    _is_percent_field(record.field.rsplit(".", 1)[-1])
                    and (
                        record.unit == "ratio"
                        if _is_asistente_casa_tool(record.tool)
                        else record.unit in {None, "ratio", "percent"}
                    )
                    for record in exact_records
                )
            )
            return reason if reason else (
                tree._grounding_financial_value, operands, percent_points
            )
        # Two instruments' bars and no resolved symbol: arithmetic anchored on
        # the session's pools, or on one instrument's prints the ref names,
        # would look anchored with nothing to anchor it to. Only evidence that
        # belongs to no instrument, named by the ref, can anchor it then — a
        # difference between two backtests' Sharpes is a portfolio figure.
        unattributed = not symbol and len({record.symbol for record in records if record.symbol}) > 1
        ref = declaration.ref.strip()
        scoped = self._referenced(ref, symbol, None, pool_ambiguous=True)
        strict_adapter_scope = bool(ref) and self._has_asistente_casa_evidence()
        if strict_adapter_scope and (scoped is None or (not scoped[0] and not scoped[1])):
            return "no_evidence"
        # Preserve upstream's unknown-single-ref guard, including observed-symbol refs.
        if ref and not re.search(r"[,;]", ref) and not unattributed:
            if (scoped is None or (not scoped[0] and not scoped[1])) and not any(
                _normalize_symbol(ref) == _normalize_symbol(record.symbol)
                for record in self._evidence
                if record.symbol and record.status == "observed"
            ):
                return "no_evidence"
        anchors: list[float] = []
        if not strict_adapter_scope and not unattributed:
            # A money-marked result is derived from money: an RSI or a volume
            # is not a price to take a discount of.
            anchors = self._price_pool(
                symbol, records, currency_code=currency_code
            ) + self._row_pool(
                symbol, money_only=money, currency_code=currency_code
            )
            if not money:
                anchors += self._metric_pool(symbol)
        # Resolve every explicit source in a multi-ref declaration. Missing
        # members fail closed; adapter-scoped refs never fall back to pools.
        ref_parts = re.split(r"[,;]", ref)
        if len(ref_parts) > 1:
            scoped_parts = [
                self._referenced(part.strip(), symbol, None, pool_ambiguous=True)
                if part.strip() else None
                for part in ref_parts
            ]
            if any(part is None or (not part[0] and not part[1]) for part in scoped_parts):
                return "no_evidence"
            scoped = (
                [r for part in scoped_parts for r in part[0]],
                [v for part in scoped_parts for v in part[1]],
            )
        else:
            scoped = self._referenced(declaration.ref, symbol, None, pool_ambiguous=True)
            if "::" in declaration.ref and (
                scoped is None or (not scoped[0] and not scoped[1])
            ):
                return "no_evidence"
        if scoped is not None:
            anchors.extend(
                float(record.value)
                for record in scoped[0]
                if (
                    not money or _is_price_kind(record)
                    or (record.source == "document" and record.field == "document.figure")
                )
                and (currency_code is None or record.currency == currency_code)
                and not (unattributed and record.symbol)
            )
            # A literal percent in a formula is parsed as its fraction, while
            # document.ratio records retain the percent as printed. This
            # conversion belongs only to the explicitly named document scope.
            anchors.extend(
                float(record.value) / 100.0
                for record in scoped[0]
                if record.source == "document" and record.field == "document.ratio"
                and not money and currency_code is None
            )
            if not money and not unattributed:
                anchors.extend(scoped[1])
        if not anchors:
            return "no_symbol" if unattributed else "no_evidence"

        # A formula's constants are parsed unsigned ("−0.2099 − (−0.2158)"
        # holds 0.2099 and 0.2158), so an operand is matched by magnitude, as
        # a figure is: without it no drawdown or loss could ever anchor.
        def observed(operand: float) -> bool:
            return _close_any(operand, anchors)

        if not any(observed(operand) for operand in operands):
            return "no_symbol" if unattributed else "formula_not_anchored"
        if _unanchored_term(tree, observed):
            return "additive_operand_not_observed"
        if any(
            not observed(operand)
            and operand != 1
            and not any(
                math.isclose(abs(operand), abs(parameter), rel_tol=1e-12, abs_tol=0.0)
                for parameter in scalar_parameters
            )
            for operand in operands
        ):
            return "operand_not_observed"
        # The result's unit follows the evidence the operands anchored to:
        # percent-named fields already carry points (#1820), an explicit
        # trailing *100 was normalized above, anything else is a fraction.
        points = (
            percent
            and not normalized_x100
            and self._operands_read_as_points(
                operands,
                symbol,
                records,
                scalar_parameters,
                referenced=scoped[0] if scoped is not None else (),
            )
        )
        if points:
            # Convert the inputs before evaluating: two point-valued inputs
            # divided by each other yield a fraction, not another point value.
            # Keep the original magnitudes for all anchoring checks above.
            def as_fraction(value: float) -> float:
                if any(
                    math.isclose(abs(value), abs(parameter), rel_tol=1e-12, abs_tol=0.0)
                    for parameter in scalar_parameters
                ):
                    return value
                return value / 100.0

            normalized = _evaluate_formula(ast.unparse(tree), operand_transform=as_fraction)
            if normalized is None:
                return "formula_not_evaluable"
            result = normalized[0] * 100.0
        return result, operands, points

    def _operands_read_as_points(
        self,
        operands: Sequence[float],
        symbol: str | None,
        records: Sequence[EvidenceRecord],
        scalar_parameters: Sequence[float],
        referenced: Sequence[EvidenceRecord] = (),
    ) -> bool:
        """Whether every data operand anchored only to percent-named fields.

        A field named ``changepercent`` already carries percentage points, so
        a formula over such fields evaluates in points and a percent figure
        must meet the result directly, not ``result * 100``. Conservative by
        construction: an operand whose magnitude also appears under a
        non-percent field makes the unit undecidable, and the caller keeps
        the fraction reading. ``referenced`` are the records the declaration's
        own ref resolved to: a field the claim names counts as an anchor of
        the operand's unit even when its tool is not a market-data one.
        """
        data = [
            operand
            for operand in operands
            if not any(
                math.isclose(abs(operand), abs(parameter), rel_tol=1e-12, abs_tol=0.0)
                for parameter in scalar_parameters
            )
        ]
        if not data:
            return False
        candidates = list(self._price_candidates(symbol, records))
        candidates += [
            record
            for record in self._evidence
            if record.status == "observed"
            and record.value is not None
            and record.tool in {"get_market_data", "bash"}
            and (not symbol or record.symbol == symbol)
        ]
        candidates += [
            record
            for record in self._evidence
            if record.status == "observed"
            and record.value is not None
            and _metric_kind_for_path(record.field) is not None
            and (not symbol or not record.symbol or record.symbol == symbol)
        ]
        candidates += [
            record
            for record in referenced
            if record.status == "observed" and record.value is not None
        ]
        for operand in data:
            saw_percent = saw_plain = False
            for record in candidates:
                if math.isclose(
                    abs(float(record.value)), abs(operand), rel_tol=1e-9, abs_tol=1e-12
                ):
                    if _is_percent_field(record.field):
                        saw_percent = True
                    else:
                        saw_plain = True
            if operand == 1 and not saw_percent and not saw_plain:
                continue  # Structural multiplicative identity, not a data unit.
            if not saw_percent or saw_plain:
                return False
        return True

    @staticmethod
    def _result_matches(figure: Figure, result: float, note: str = "", *, points: bool = False) -> bool:
        """Whether a formula's result is the value the prose figure states.

        The band is half a unit of the last digit the PROSE was written with
        ("约 37%" for 36.75%), so a coarser declaration cannot widen it. A "%"
        figure is compared in one unit only, since against the other reading
        a half-unit band spans fifty points: against the fraction
        (``result * 100``) by default, against the points the formula
        evaluates to when every data operand came from a percent-named field
        (#1820). A bare figure is tried both ways.
        Magnitudes are compared, because a fall is noted either as
        ``(low − high) / high`` or as the drop, unless the prose wrote a sign.
        """
        # The normalized reading, so "0,666" is three decimals and "−5,13%" is signed.
        scale = float(figure.scale or 1.0)
        half_unit = _written_half_unit(figure.digits or figure.text) * scale
        targets = (
            {result}
            if figure.percent
            and (points or _has_explicit_percent_scale(note))
            and not _has_trailing_percent_factor(note)
            else {result * 100.0}
            if figure.percent
            else {result, result * 100.0}
        )
        sign = _explicit_sign(figure.sign or figure.text)
        value = abs(figure.value) * scale
        return any(
            abs(value - abs(target)) <= max(abs(target) * _TOLERANCE, half_unit, 1e-9)
            for target in targets
            if not sign or target * sign >= 0
        )

    @grounding_check(name="figure-derived", code="numeric_claim_conflict",
                     additional_codes=("numeric_claim_unavailable",),
                     description="Validate the derived figure contract against evidence.")
    def _check_derived(
        self,
        figure: Figure,
        declaration: Declaration | None,
        symbol: str | None,
        records: Sequence[EvidenceRecord],
        scalar_parameters: Sequence[float] = (),
    ) -> list[dict[str, Any]]:
        """A derived figure must be the arithmetic its note states."""
        if declaration is not None and figure.symbol:
            label = re.sub(r"[*_`]+", "", str(figure.symbol)).strip()
            if label and _SUMMARY_ROW_LABEL_RE.fullmatch(label):
                canonical_summary = self._canonical_summary_weight_replacement(
                    declaration, figure
                )
                if canonical_summary and abs(
                    float(canonical_summary["replacement_value"]) - figure.value
                ) > max(abs(figure.value) * 1e-9, 1e-9):
                    return [
                        self._figure_issue(
                            "numeric_claim_conflict",
                            figure,
                            "derived",
                            None,
                            "derived_summary_needs_canonical_replacement",
                            "is derived from risk-normalized weights, while the same "
                            "portfolio call exposes canonical per-position weights; "
                            "replace the total with the canonical holdings sum supplied below",
                            **canonical_summary,
                        )
                    ]
        derivation = self._derivation(
            declaration, symbol, records, money=figure.currency,
            currency_code=figure.currency_code, percent=figure.percent,
            scalar_parameters=scalar_parameters,
        )
        if isinstance(derivation, str):
            derive_hint = self._derived_ref_repair_metadata(
                figure, declaration, symbol, records
            ) if declaration is not None else {}
            derive_hint = derive_hint or self._deterministic_derive_metadata(figure)
            operand_message = {
                "financial_operand_not_observed": "has a financial operand without its exact observed ref, or an unused operand ref",
                "operand_entity_conflict": "references an entity incompatible with the claimed symbol",
                "operand_snapshot_conflict": "mixes incompatible snapshots or observation timestamps",
                "operand_snapshot_unavailable": "combines calls without a common proven snapshot and timestamp",
                "operand_unit_conflict": "combines incompatible financial units or currencies",
                "operand_unit_unavailable": "references financial operands without sufficient unit or currency metadata",
                "scalar_subtree_out_of_range": "uses a constant-only factor outside the conservative effective scalar range",
                "operand_ref_ambiguous": "cannot associate equal-valued operand refs uniquely with the formula leaves",
            }.get(derivation)
            return [
                self._figure_issue(
                    "numeric_claim_conflict"
                    if derivation != "no_evidence"
                    else "numeric_claim_unavailable",
                    figure,
                    "derived",
                    symbol,
                    derivation,
                    operand_message or (
                        "uses a data operand this session did not observe. Fetch the missing "
                        "data; do not relabel a numerator or denominator as a parameter. "
                        "For a genuinely chosen coefficient, declare its value as count and "
                        "show that value and its note label in the answer"
                        if derivation == "operand_not_observed"
                        else "is declared derived, but its note adds or subtracts an operand "
                        "this session did not observe"
                        if derivation == "additive_operand_not_observed"
                        else "is declared derived, but its note is not arithmetic over at "
                        "least two operands with one of them observed in this session"
                    ),
                    **derive_hint,
                )
            ]
        result, _, points = derivation
        if self._result_matches(figure, result, declaration.note, points=points):
            # An equal result cannot introduce operands absent from the stated
            # arithmetic. Display scaling does not change financial coverage.
            derive_hint = self._deterministic_derive_metadata(figure, declaration)
            expected_refs = {
                str(item)
                for item in derive_hint.get("derive_operand_refs") or []
            }
            if expected_refs:
                declared_refs: set[str] = set()
                for item in re.split(r"[,;]", declaration.ref or ""):
                    ref = item.strip()
                    if not ref or "::" not in ref:
                        continue
                    call_id, field = ref.split("::", 1)
                    declared_refs.add(
                        self._canonical_session_call_id(call_id.strip())
                        + "::"
                        + field.strip()
                    )
                if not expected_refs.issubset(declared_refs):
                    return [
                        self._figure_issue(
                            "numeric_claim_conflict",
                            figure,
                            "derived",
                            symbol,
                            "derived_refs_incomplete",
                            "is numerically correct, but its declaration omits one or "
                            "more exact operand refs proven by the deterministic calc",
                            missing_derive_refs=sorted(expected_refs - declared_refs),
                            **derive_hint,
                        )
                    ]
            return []
        # Reported in the figure's own units, as ``_result_matches`` compares it.
        scaled = (result if points else result * 100.0) if figure.percent else result
        shown = f"{scaled:.6g}%" if figure.percent else f"{scaled:.6g}"
        # "−1.51pp" beside "12.87% − 11.36%": the size is right and the formula
        # runs the other way. Still refused (the sign is part of the claim),
        # but said, or the model rewrites the number instead of the formula.
        reversed_sign = self._result_matches(figure, -result, declaration.note, points=points)
        return [
            self._figure_issue(
                "numeric_claim_conflict",
                figure,
                "derived",
                symbol,
                "derivation_result_mismatch",
                f"is declared derived, but its own formula evaluates to {shown}",
                derived_result=shown,
                **({"sign_reversed": True} if reversed_sign else {}),
            )
        ]

    @grounding_check(name="figure-proposed", code="numeric_claim_conflict",
                     additional_codes=("numeric_claim_unavailable",),
                     description="Validate the proposed figure contract against evidence.")
    def _check_proposed(
        self,
        figure: Figure,
        declaration: Declaration | None,
        symbol: str | None,
        records: Sequence[EvidenceRecord],
        scalar_parameters: Sequence[float] = (),
    ) -> list[dict[str, Any]]:
        """A proposed level (entry, target, stop) is derived or inside the observed range.

        It cannot be required to equal a print, only to be anchored; a level far
        outside what the session saw is the invention this gate exists to stop.
        A level is a price, so a percent is never one, and a level attributed to
        no instrument of several has no range to lie in.
        """
        if figure.percent:
            return [
                self._figure_issue(
                    "numeric_claim_conflict",
                    figure,
                    "proposed",
                    symbol,
                    "proposed_not_a_price",
                    "is declared proposed, but a proposed level is a price and a percent "
                    "is not one; declare it derived with its arithmetic, or cited",
                )
            ]
        if not symbol and len({record.symbol for record in records if record.symbol}) > 1:
            return [
                self._figure_issue(
                    "numeric_claim_conflict",
                    figure,
                    "proposed",
                    symbol,
                    "no_symbol",
                    "is a proposed level, but the run holds prices for more than one "
                    "instrument and nothing attributes it to one",
                )
            ]
        derivation = self._derivation(
            declaration, symbol, records, money=figure.currency,
            currency_code=figure.currency_code, percent=figure.percent,
            scalar_parameters=scalar_parameters,
        )
        if not isinstance(derivation, str) and self._result_matches(figure, derivation[0]):
            return []
        candidates = self._price_candidates(symbol, records)
        if figure.currency_code is not None:
            candidates = [
                record
                for record in candidates
                if record.currency == figure.currency_code
            ]
        prices = [float(record.value) for record in candidates]
        if not prices:
            return [
                self._figure_issue(
                    "numeric_claim_unavailable",
                    figure,
                    "proposed",
                    symbol,
                    "no_evidence",
                    "is a proposed level but this session observed no price for "
                    "the instrument to anchor it to",
                )
            ]
        if min(prices) <= figure.value <= max(prices):
            return []
        return [
            self._figure_issue(
                "numeric_claim_conflict",
                figure,
                "proposed",
                symbol,
                "outside_observed_range",
                "is a proposed level outside the observed range "
                f"{min(prices):g}–{max(prices):g} and its note derives no value",
                observed_min=min(prices),
                observed_max=max(prices),
                observed_nearest=self._nearest_prints(figure, candidates, prices),
            )
        ]

    @grounding_check(name="figure-cited", code="numeric_claim_conflict",
                     additional_codes=("numeric_claim_unavailable",),
                     description="Validate the cited figure contract against evidence.")
    def _check_cited(
        self,
        figure: Figure,
        declaration: Declaration,
        symbol: str | None,
        declared_observed: set[float],
        line: str,
    ) -> list[dict[str, Any]]:
        """A cited figure names a source the reader can see and does not pose as a print.

        Its value is unchecked, so the citation may not launder an observation
        (the value may not also be declared observed). The block is stripped
        before release, so a source only the note names is no citation: a note
        token (``_note_tokens``) must appear on the figure's own line.
        """
        if not declaration.note.strip():
            return [
                self._figure_issue(
                    "numeric_claim_conflict",
                    figure,
                    "cited",
                    symbol,
                    "citation_without_source",
                    "is declared cited but names no source in its note",
                )
            ]
        if any(_close(figure.value, value) for value in declared_observed):
            return [
                self._figure_issue(
                    "numeric_claim_conflict",
                    figure,
                    "cited",
                    symbol,
                    "cited_as_observed",
                    "is declared cited yet presented as an observed value of this "
                    "instrument",
                )
            ]
        folded = line.casefold()
        if not any(token in folded for token in _note_tokens(declaration.note)):
            return [
                self._figure_issue(
                    "numeric_claim_conflict",
                    figure,
                    "cited",
                    symbol,
                    "citation_not_visible",
                    "is declared cited, but no source its note names appears on its "
                    "line, and the note is stripped before anyone reads the answer",
                )
            ]
        return []

    def _symbol_for_claim(
        self,
        content: str,
        records: Sequence[EvidenceRecord],
    ) -> str | None:
        """Return one canonical symbol named in a claim or this session."""
        known = {record.symbol for record in records if record.symbol} | self._session_symbols
        known.update(
            entry.get("symbol")
            for entry in self._analysis_metrics
            if entry.get("symbol")
        )
        known.update(
            symbol
            for entry in self._analysis_metrics
            for symbol in entry.get("instrument_symbols", ())
        )
        matches = {
            _normalize_symbol(match.group(0))
            for match in _CANONICAL_SYMBOL_RE.finditer(content)
            if _normalize_symbol(match.group(0)) in known
        }
        # Explicit entity_id values need not use a market-symbol spelling.
        # Match only identities already carried by evidence/session, and never
        # let a bare identifier match the prefix of a venue-qualified one.
        for identity in known:
            if identity and not _CANONICAL_SYMBOL_RE.fullmatch(identity) and re.search(
                r"(?<![A-Za-z0-9_])" + re.escape(identity) + r"(?![A-Za-z0-9_.\/-])",
                content,
                re.IGNORECASE,
            ):
                matches.add(identity)
        return next(iter(matches)) if len(matches) == 1 else None

    def _provenance_scope(self, content, records):
        """Resolve the original symbol-scoped provenance pool without changing it."""
        folded = content.casefold()
        symbols = sorted({record.symbol for record in records if record.symbol})
        # ``_scan_symbols`` canonicalizes, so an answer that writes Shanghai as
        # ``600519.SS`` still surfaces the ``600519.SH`` identity it names.
        written = _scan_symbols(content)
        mentioned = [
            symbol
            for symbol in symbols
            if symbol in written or symbol.casefold() in folded
        ]
        target_symbols = set(mentioned or (symbols if len(symbols) == 1 else []))
        target_records = [
            record
            for record in records
            if not target_symbols or record.symbol in target_symbols
        ]

        return folded, symbols, mentioned, target_records

    @grounding_check(name="canonical-symbol-surfaced", code="canonical_symbol_not_surfaced", phase="provenance", order=0,
                     description="Require canonical symbol surfaced for a checked price.")
    def _check_canonical_symbol_surfaced(self, content, records):
        issues = []
        folded, symbols, mentioned, target_records = self._provenance_scope(content, records)
        if not mentioned:
            issues.append(
                {
                    "code": "canonical_symbol_not_surfaced",
                    "symbols": symbols,
                    "value": None,
                    "role": None,
                    "span": None,
                    "symbol": None,
                    "reason": "symbol_not_surfaced",
                    "message": (
                        "A price claim must surface its locked canonical symbol and "
                        "venue suffix."
                    ),
                }
            )
        return issues

    @grounding_check(name="data-source-surfaced", code="data_source_not_surfaced", phase="provenance", order=1,
                     description="Require data source surfaced for a checked price.")
    def _check_data_source_surfaced(self, content, records):
        issues = []
        folded, symbols, mentioned, target_records = self._provenance_scope(content, records)
        sources = sorted(
            {
                record.source
                for record in target_records
                if record.source and record.source.casefold() not in {"auto", "unknown"}
            }
        )
        missing_sources = [
            source
            for source in sources
            if not any(
                alias in folded
                for alias in _SOURCE_ALIASES.get(source.casefold(), (source.casefold(),))
            )
        ]
        if missing_sources:
            issues.append(
                {
                    "code": "data_source_not_surfaced",
                    "sources": missing_sources,
                    "value": None,
                    "role": None,
                    "span": None,
                    "symbol": None,
                    "reason": "source_not_surfaced",
                    "message": (
                        "Price claims must name the actual data source: "
                        + ", ".join(missing_sources)
                        + "."
                    ),
                }
            )

        return issues

    @grounding_check(name="quote-currency-surfaced", code="currency_not_surfaced", phase="provenance", order=2,
                     description="Require quote currency surfaced for a checked price.")
    def _check_quote_currency_surfaced(self, content, records):
        issues = []
        folded, symbols, mentioned, target_records = self._provenance_scope(content, records)
        currencies = sorted(
            {record.currency for record in target_records if record.currency}
        )
        missing_currencies = [
            currency
            for currency in currencies
            if not self._currency_is_surfaced(currency, content)
        ]
        if missing_currencies:
            issues.append(
                {
                    "code": "currency_not_surfaced",
                    "currencies": missing_currencies,
                    "value": None,
                    "role": None,
                    "span": None,
                    "symbol": None,
                    "reason": "currency_not_surfaced",
                    "message": (
                        "Price claims must name their quote currency: "
                        + ", ".join(missing_currencies)
                        + "."
                    ),
                }
            )
        return issues

    def _validate_price_provenance(self, content, records):
        """Walk declared provenance checks only after a price was checked."""
        return GROUNDING_CHECKS.run_phase("provenance", self, content, records)

    @staticmethod
    def _currency_is_surfaced(currency: str, content: str) -> bool:
        """Return whether a quote currency or an unambiguous alias is visible."""
        folded = content.casefold()
        code = currency.upper()
        tokens = _CURRENCY_ALIASES.get(code, (currency.casefold(),))
        if any(token.casefold() in folded for token in tokens):
            return True
        if code != "CNY":
            return False
        return any(
            char == "元"
            and (index == 0 or content[index - 1] not in _OTHER_CURRENCY_PREFIXES)
            for index, char in enumerate(content)
        )

    @staticmethod
    def _dedupe_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove duplicate validator findings while preserving order."""
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for issue in issues:
            key = json.dumps(issue, sort_keys=True, ensure_ascii=False, default=str)
            if key in seen:
                continue
            seen.add(key)
            unique.append(issue)
        return unique


#: The role an undeclared figure is validated under (spec §4, undeclared mode).
def _declared_in_other_unit(block: FiguresBlock, figure: Figure) -> Declaration | None:
    """A declaration holding ``figure`` as a fraction where it is a percent, or the reverse.

    It does not cover the figure (``block.match`` keeps the two apart); it
    only lets the correction say what the model did declare.
    """
    half_unit = _written_half_unit(figure.digits or figure.text)
    for declaration in block.declarations:
        if declaration.percent == figure.percent:
            continue
        # Equal display magnitudes with opposite unit declarations (2.5% vs
        # 2.5 points) are still a unit mismatch. They need the explicit,
        # evidence-backed ratio display conversion below.
        if abs(abs(declaration.value) - abs(figure.value)) <= half_unit * (1 + 1e-9):
            return declaration
        in_figure_units = declaration.value * (100.0 if figure.percent else 0.01)
        # By size: a difference declared one way round and written the other
        # ("0.0151" for "−1.51pp") is still the value the model meant to name.
        if abs(abs(in_figure_units) - abs(figure.value)) <= half_unit * (1 + 1e-9):
            return declaration
    return None


def _has_ratio_display_context(content: str, figure: Figure) -> bool:
    """Whether prose explicitly frames a ratio as points or a percentage part."""
    left, right = segment_bounds(content, figure.start, figure.end)
    segment = content[left:right]
    if re.search(
        r"\b(?:percentage\s+points?|percent\s+points?|points?|pp)\b|"
        r"puntos?\s+porcentuales?|porcentuales?|单位",
        segment,
        re.IGNORECASE,
    ):
        return True
    # A written percent factor applied to the same value is explicit
    # arithmetic context, as in "25% of 2.5 units".
    return bool(
        re.search(r"\d[\d.,]*\s*%", segment)
        and re.search(r"\b(?:of|de)\b", segment, re.IGNORECASE)
    )


#: What separates the parts of a free-form ref: ``backtest::rp``,
#: ``risk_parity target_positions.csv``, ``backtest(metrics.csv)``.
_REF_TOKEN_RE = re.compile(r"::?|[\s,，;；()（）\[\]]+")


def _file_stem(path: str) -> str:
    """A file's name up to its first dot, casefolded: ``target_positions`` for any spelling."""
    return str(path).rsplit("/", 1)[-1].split(".", 1)[0].casefold()


#: How a model names the active run directory when it does not know its path.
_RUN_DIR_PLACEHOLDER = "run_dir"


def _run_dir_alias(token: str, root: Path) -> str | None:
    """The root-relative path a ref token names through a stand-in for the active run.

    A model writes the active run directory as the placeholder ``run_dir`` or
    by its own name (``20261021_195945_86_52d4e2/artifacts/metrics.csv``); both
    stand for the ledger's root, so the rest is read from there exactly as if
    written relative to it. A path leaving the run directory (absolute, or
    ``..`` once normalized) starts with neither and has no alias.

    Args:
        token: One part of a ref that named no record as written.
        root: The ledger's resolved run directory.

    Returns:
        The path below the stand-in, or None.
    """
    parts = Path(os.path.normpath(token)).parts
    if len(parts) > 1 and parts[0] in (_RUN_DIR_PLACEHOLDER, root.name):
        return "/".join(parts[1:])
    return None


def _records_at(
    token: str, owned: Sequence[EvidenceRecord], root: Path
) -> list[EvidenceRecord]:
    """The backtest records a path-like ref token names.

    A directory or file relative to the run (or absolute) names what lies at
    or under it. A file named without its ``artifacts/`` segment
    (``rp/metrics.csv``) names that file anywhere under its directory, and
    ``rp/ew`` names both run directories.

    Args:
        token: One part of a ref.
        owned: Every observed backtest record.
        root: The ledger's resolved run directory.

    Returns:
        The records at that path, or an empty list.
    """
    path = Path(token)
    if not path.is_absolute():
        path = root / path
    normalized = Path(os.path.normpath(path))
    try:
        relative = normalized.relative_to(root).as_posix()
    except ValueError:
        try:  # a backtest an earlier turn left sits beside the active run
            relative = normalized.relative_to(root.parent).as_posix()
        except ValueError:
            # A prior run in another approved run root keeps an absolute
            # artifact ref. This matches observed records only; it authorizes
            # no filesystem read and admits no new evidence.
            relative = normalized.as_posix()
    if relative in ("", "."):
        return []

    def under(artifact: str, prefix: str) -> bool:
        return artifact == prefix or artifact.startswith(prefix + "/")

    hits = [record for record in owned if under(record.artifact or "", relative)]
    if hits or "/" not in relative:
        return hits
    parent, _, name = relative.rpartition("/")
    hits = [
        record
        for record in owned
        if under(record.artifact or "", parent)
        and _file_stem(record.artifact or "") == _file_stem(name)
    ]
    if hits:
        return hits
    groups = [
        [record for record in owned if under(record.artifact or "", part)]
        for part in relative.split("/")
    ]
    return [record for group in groups for record in group] if all(groups) else []


_NO_DECLARATION = Declaration(
    index=0, value_text="", value=0.0, percent=False, role="observed", note="", ref=""
)
