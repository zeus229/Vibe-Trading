"""Rounded observed values and structured statistical derivations (GGAL.BA E2E, 2026-09-19).

Two blockers left after the native BYMA E2E:

P1. A precise observation ("38.6784…") shown rounded ("38,68") was not linked to its
    declaration (matching was exact, 1e-9), and the evidence band was a flat 0.5%, wide
    enough to accept "38,50" for 38.6784. The tolerance must come from the digits written.

P2. A correct 20/60-session volatility was rejected because its note was prose
    ("desviación estándar muestral × sqrt(252)"). The ledger only evaluated ``+ - * /`` over
    literal numbers. A derivation may now name the observed series (``close[-21:]``) and a
    closed set of functions; the gate recomputes the value from its own evidence.
"""

from __future__ import annotations

import json
import math
import random
import statistics
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.agent.grounding import GroundingLedger
from src.agent.grounding.figures import Declaration, FiguresBlock

pytestmark = pytest.mark.unit

TOOL = "get_market_data"
SYMBOL = "GGAL.BA"


def _figures(*rows: str) -> str:
    return "\n\n```figures\n" + "\n".join(rows) + "\n```"


def _reasons(result) -> list[str]:
    return [str(issue.get("reason")) for issue in result.issues]


# --- P1. rounded observed values ----------------------------------------------------------


def _risk_ledger(tmp_path: Path, payload: dict) -> GroundingLedger:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Analizá el riesgo")
    ledger.ingest_tool_result(tool_name="risk_tool", arguments={}, result=json.dumps(payload), call_id="risk_tool", success=True)
    return ledger


# A second, exactly declared percentage makes the document a decimal-comma one, so the
# figure under test is read as a decimal and not as two bare integers.
AUX = {"aux": {"annualized_vol": 0.0125}}


def _with_aux(prose: str, comma: bool) -> str:
    return prose + (" Vol diaria 1,25%." if comma else " Vol diaria 1.25%.")


@pytest.mark.parametrize(
    ("payload", "prose", "declared"),
    [
        ({"indicators": {"rsi_14": 38.6784123}}, "RSI 14: 38,68.", "38.6784123"),
        ({"indicators": {"rsi_14": 38.6784123}}, "RSI 14: 38.68.", "38.6784123"),
        ({"volatility": {"annualized_vol": 0.38543109}}, "Volatilidad: 38,54%.", "38.543109%"),
        ({"concentration": {"effective_n": 2.63949965}}, "Effective N: 2,64.", "2.63949965"),
    ],
)
def test_rounded_prose_is_linked_to_its_precise_observation(tmp_path: Path, payload: dict, prose: str, declared: str) -> None:
    leaf = next(iter(next(iter(payload.values()))))
    ledger = _risk_ledger(tmp_path, {**payload, **AUX})
    result = ledger.validate_final_answer(
        _with_aux(prose, "," in prose)
        + _figures(f"{declared} | observed | {leaf} | risk_tool", "1.25% | observed | annualized_vol | risk_tool")
    )
    assert result.valid is True, result.issues


@pytest.mark.parametrize("prose", ["Volatilidad: 38,50%.", "Volatilidad: 38,90%.", "Volatilidad: 39,00%."])
def test_a_materially_different_figure_is_rejected_even_inside_the_old_half_percent_band(tmp_path: Path, prose: str) -> None:
    ledger = _risk_ledger(tmp_path, {"volatility": {"annualized_vol": 0.386784123}, **AUX})
    result = ledger.validate_final_answer(
        _with_aux(prose, True)
        + _figures("38.6784123% | observed | annualized_vol | risk_tool", "1.25% | observed | annualized_vol | risk_tool")
    )
    assert result.valid is False, result.issues


@pytest.mark.parametrize(
    ("prose", "valid"),
    [
        ("Volatilidad: 38,68% y 1,25%.", True),
        ("Volatilidad: 38,50% y 1,25%.", False),
        ("Volatilidad: 38,7% y 1,25%.", True),
        ("Volatilidad: 38,5% y 1,25%.", False),
    ],
)
def test_an_undeclared_figure_is_grounded_only_within_its_own_rounding(tmp_path: Path, prose: str, valid: bool) -> None:
    ledger = _risk_ledger(tmp_path, {"volatility": {"annualized_vol": 0.386784123}, **AUX})
    result = ledger.validate_final_answer(prose + _figures("1.25% | observed | annualized_vol | risk_tool"))
    assert result.valid is valid, result.issues


@pytest.mark.parametrize(
    ("declared", "declared_percent", "written", "digits", "written_percent", "linked"),
    [
        (38.6784123, False, 38.68, "38.68", False, True),
        (38.6784123, False, 38.5, "38.5", False, False),
        (38.6784123, False, 38.50, "38.50", False, False),
        (38.6784123, False, 39.0, "39", False, False),
        (2.63949965, False, 2.64, "2.64", False, True),
        (2.6395, False, 2.50, "2.50", False, False),
        (38.543109, True, 38.54, "38.54", True, True),
        (38.543109, True, 38.54, "38.54", False, False),  # 37% and 0.37 stay different assertions
        (-18.406041, True, -18.41, "18.41", True, True),
        (-18.406041, True, 18.41, "18.41", True, False),  # the sign is part of the claim
    ],
)
def test_declaration_matching_follows_the_digits_written(declared, declared_percent, written, digits, written_percent, linked) -> None:
    declaration = Declaration(
        index=1, value_text=str(declared), value=declared, percent=declared_percent, role="observed", note="", ref="tool"
    )
    block = FiguresBlock(True, (0, 0), "", (declaration,), ())
    assert (block.match(written, written_percent, digits) is not None) is linked


# --- P2. structured statistical derivations -----------------------------------------------


def _closes(n: int = 90, seed: int = 7) -> list[float]:
    rng = random.Random(seed)
    price, out = 6000.0, []
    for _ in range(n):
        price *= 1 + rng.uniform(-0.03, 0.03)
        out.append(round(price, 4))
    return out


def _vol(closes: list[float], window: int, *, periods: int = 252, percent: bool = True) -> float:
    tail = closes[-(window + 1):]
    returns = [tail[i] / tail[i - 1] - 1 for i in range(1, len(tail))]
    value = statistics.stdev(returns) * math.sqrt(periods)
    return value * 100 if percent else value


def _bars_ledger(tmp_path: Path, closes: list[float], *, symbol: str = SYMBOL, extra: dict | None = None) -> GroundingLedger:
    start = date(2026, 1, 1)
    rows = [
        {
            "trade_date": (start + timedelta(days=i)).isoformat() + "T00:00:00",
            "open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1000 + i,
        }
        for i, c in enumerate(closes)
    ]
    payload = {symbol: rows}
    payload.update(extra or {})
    ledger = GroundingLedger(run_dir=tmp_path, user_message=f"Analizá {symbol}")
    ledger.ingest_tool_result(tool_name=TOOL, arguments={"codes": [symbol]}, result=json.dumps(payload), call_id=TOOL, success=True)
    return ledger


def _es(value: float, places: int = 2, suffix: str = "%") -> str:
    return f"{value:.{places}f}".replace(".", ",") + suffix


ANNUAL_20 = "std_sample(returns(close[-21:])) * sqrt(252) * 100"
ANNUAL_60 = "std_sample(returns(close[-61:])) * sqrt(252) * 100"


@pytest.mark.parametrize(("window", "note"), [(20, ANNUAL_20), (60, ANNUAL_60)])
def test_annualized_sample_volatility_is_recomputed_from_observed_closes(tmp_path: Path, window: int, note: str) -> None:
    closes = _closes()
    expected = _vol(closes, window)
    result = _bars_ledger(tmp_path, closes).validate_final_answer(
        f"GGAL.BA Volatilidad de {window} ruedas: {_es(expected)}."
        + _figures(f"{expected:.2f}% | derived | {note} | {TOOL}")
    )
    assert result.valid is True, result.issues


def test_daily_sample_volatility_without_annualization(tmp_path: Path) -> None:
    closes = _closes()
    expected = _vol(closes, 20, periods=1)
    result = _bars_ledger(tmp_path, closes).validate_final_answer(
        f"GGAL.BA Desvío diario: {_es(expected)}."
        + _figures(f"{expected:.2f}% | derived | std_sample(returns(close[-21:])) * 100 | {TOOL}")
    )
    assert result.valid is True, result.issues


def test_a_wrong_sample_std_is_rejected_as_a_result_mismatch(tmp_path: Path) -> None:
    closes = _closes()
    wrong = _vol(closes, 20) + 1.5
    result = _bars_ledger(tmp_path, closes).validate_final_answer(
        f"GGAL.BA Volatilidad de 20 ruedas: {_es(wrong)}." + _figures(f"{wrong:.2f}% | derived | {ANNUAL_20} | {TOOL}")
    )
    assert result.valid is False
    assert _reasons(result) == ["derivation_result_mismatch"], result.issues


def test_a_recomputed_statistic_must_be_correctly_rounded_not_merely_close(tmp_path: Path) -> None:
    closes = _closes()
    exact = _vol(closes, 20)
    rounded = round(exact, 2)
    slightly_off = rounded + 0.02  # inside the flat 0.5% evidence band, outside the rounding of the digits written
    ledger = _bars_ledger(tmp_path, closes)
    ok = ledger.validate_final_answer(f"GGAL.BA Vol: {_es(rounded)}." + _figures(f"{rounded:.2f}% | derived | {ANNUAL_20} | {TOOL}"))
    off = ledger.validate_final_answer(f"GGAL.BA Vol: {_es(slightly_off)}." + _figures(f"{slightly_off:.2f}% | derived | {ANNUAL_20} | {TOOL}"))
    assert ok.valid is True, ok.issues
    assert _reasons(off) == ["derivation_result_mismatch"], off.issues


def test_the_population_std_is_not_the_sample_std(tmp_path: Path) -> None:
    closes = _closes()
    tail = closes[-21:]
    returns = [tail[i] / tail[i - 1] - 1 for i in range(1, len(tail))]
    population = statistics.pstdev(returns) * math.sqrt(252) * 100
    result = _bars_ledger(tmp_path, closes).validate_final_answer(
        f"GGAL.BA Volatilidad de 20 ruedas: {_es(population)}." + _figures(f"{population:.2f}% | derived | {ANNUAL_20} | {TOOL}")
    )
    assert _reasons(result) == ["derivation_result_mismatch"], result.issues


def test_the_wrong_window_is_rejected(tmp_path: Path) -> None:
    closes = _closes()
    vol60 = _vol(closes, 60)
    result = _bars_ledger(tmp_path, closes).validate_final_answer(
        f"GGAL.BA Volatilidad de 20 ruedas: {_es(vol60)}." + _figures(f"{vol60:.2f}% | derived | {ANNUAL_20} | {TOOL}")
    )
    assert _reasons(result) == ["derivation_result_mismatch"], result.issues


def test_a_wrong_annualization_factor_is_rejected(tmp_path: Path) -> None:
    closes = _closes()
    calendar = _vol(closes, 20, periods=365)
    result = _bars_ledger(tmp_path, closes).validate_final_answer(
        f"GGAL.BA Volatilidad de 20 ruedas: {_es(calendar)}."
        + _figures(f"{calendar:.2f}% | derived | std_sample(returns(close[-21:])) * sqrt(252) * 100 | {TOOL}")
    )
    assert _reasons(result) == ["derivation_result_mismatch"], result.issues


def test_a_series_the_run_never_observed_cannot_ground_a_derivation(tmp_path: Path) -> None:
    closes = _closes()
    expected = _vol(closes, 20)
    ledger = GroundingLedger(run_dir=tmp_path, user_message="vol")
    ledger.ingest_tool_result(tool_name="other_tool", arguments={}, result=json.dumps({"x": 1.5, "y": 2.5}), call_id="other_tool", success=True)
    result = ledger.validate_final_answer(
        f"GGAL.BA Volatilidad: {_es(expected)}." + _figures(f"{expected:.2f}% | derived | {ANNUAL_20} | other_tool")
    )
    assert result.valid is False
    assert _reasons(result) == ["series_not_observed"], result.issues


def test_a_window_longer_than_the_observed_history_is_rejected(tmp_path: Path) -> None:
    closes = _closes(30)
    result = _bars_ledger(tmp_path, closes).validate_final_answer(
        "GGAL.BA Volatilidad: 20,00%." + _figures(f"20.00% | derived | std_sample(returns(close[-61:])) * sqrt(252) * 100 | {TOOL}")
    )
    assert _reasons(result) == ["series_too_short"], result.issues


def test_a_series_of_another_symbol_is_not_used(tmp_path: Path) -> None:
    closes = _closes()
    other = _closes(seed=99)
    expected_other = _vol(other, 20)
    ledger = _bars_ledger(tmp_path, closes, extra={"PAMP.BA": [
        {"trade_date": (date(2026, 1, 1) + timedelta(days=i)).isoformat() + "T00:00:00", "open": c, "high": c, "low": c, "close": c, "volume": 1}
        for i, c in enumerate(other)
    ]})
    result = ledger.validate_final_answer(
        f"GGAL.BA vol de 20 ruedas: {_es(expected_other)}." + _figures(f"{expected_other:.2f}% | derived | {ANNUAL_20} | {TOOL}")
    )
    assert result.valid is False, "GGAL.BA must be checked against GGAL.BA's series, not PAMP.BA's"


def _later_observation(ledger: GroundingLedger, day_offset: int, close: float, call_id: str) -> None:
    stamp = (date(2026, 1, 1) + timedelta(days=day_offset)).isoformat() + "T00:00:00"
    row = {"trade_date": stamp, "open": 1, "high": 1, "low": 1, "close": close, "volume": 1}
    ledger.ingest_tool_result(tool_name=TOOL, arguments={"codes": [SYMBOL]}, result=json.dumps({SYMBOL: [row]}), call_id=call_id, success=True)


def test_a_session_two_results_disagree_on_is_refused_when_the_window_reads_it(tmp_path: Path) -> None:
    closes = _closes()
    ledger = _bars_ledger(tmp_path, closes)
    _later_observation(ledger, 80, 9999.0, "second")  # inside the last 21 sessions
    expected = _vol(closes, 20)
    result = ledger.validate_final_answer(f"GGAL.BA Vol: {_es(expected)}." + _figures(f"{expected:.2f}% | derived | {ANNUAL_20} | {TOOL}"))
    assert _reasons(result) == ["series_conflict"], result.issues


def test_a_disagreement_outside_the_window_does_not_matter(tmp_path: Path) -> None:
    closes = _closes()
    ledger = _bars_ledger(tmp_path, closes)
    _later_observation(ledger, 4, 9999.0, "second")  # a session far outside the last 21
    expected = _vol(closes, 20)
    result = ledger.validate_final_answer(f"GGAL.BA Vol: {_es(expected)}." + _figures(f"{expected:.2f}% | derived | {ANNUAL_20} | {TOOL}"))
    assert result.valid is True, result.issues


def test_float32_noise_between_overlapping_calls_is_not_a_conflict(tmp_path: Path) -> None:
    closes = _closes()
    ledger = _bars_ledger(tmp_path, closes)
    _later_observation(ledger, 80, closes[80] * (1 + 6e-8), "second")  # seventh-digit noise, as yfinance returns
    expected = _vol(closes, 20)
    result = ledger.validate_final_answer(f"GGAL.BA Vol: {_es(expected)}." + _figures(f"{expected:.2f}% | derived | {ANNUAL_20} | {TOOL}"))
    assert result.valid is True, result.issues


@pytest.mark.parametrize(
    "note",
    [
        "desviación estándar muestral de los retornos × sqrt(252)",
        "std_sample(retornos) * sqrt(252) * 100",
        "__import__('os').system('x')",
        "std_population(returns(close[-21:])) * 100",
        "std_sample(returns(close[-21:]), 1) * 100",
        "std_sample(returns(close[-21:])) ** 2 * 100",
    ],
)
def test_free_text_and_unknown_operations_are_still_not_derivations(tmp_path: Path, note: str) -> None:
    closes = _closes()
    expected = _vol(closes, 20)
    result = _bars_ledger(tmp_path, closes).validate_final_answer(
        f"GGAL.BA Vol: {_es(expected)}." + _figures(f"{expected:.2f}% | derived | {note} | {TOOL}")
    )
    assert result.valid is False
    assert _reasons(result) == ["formula_not_evaluable"], result.issues


def test_the_grammar_is_general_not_a_volatility_special_case(tmp_path: Path) -> None:
    closes = _closes()
    mean5 = statistics.fmean(closes[-5:])
    total3 = sum(closes[-3:])
    ledger = _bars_ledger(tmp_path, closes)
    ok = ledger.validate_final_answer(
        f"GGAL.BA Media de 5 cierres: {mean5:.2f}. Suma de 3 cierres: {total3:.2f}."
        + _figures(f"{mean5:.2f} | derived | mean(close[-5:]) | {TOOL}", f"{total3:.2f} | derived | sum(close[-3:]) | {TOOL}")
    )
    assert ok.valid is True, ok.issues
    bad = ledger.validate_final_answer(
        f"GGAL.BA Media de 5 cierres: {mean5 + 40:.2f}." + _figures(f"{mean5 + 40:.2f} | derived | mean(close[-5:]) | {TOOL}")
    )
    assert _reasons(bad) == ["derivation_result_mismatch"], bad.issues
