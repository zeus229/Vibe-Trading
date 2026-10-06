"""Regressions for exact decimal ties and duplicate declaration binding."""

from pathlib import Path

from src.agent.grounding.figures import Declaration, Figure, FiguresBlock
from src.agent.grounding.ledger import GroundingLedger


def _figure(value: float, start: int) -> Figure:
    digits = f"{value:.2f}"
    return Figure(
        text=digits + "%",
        value=value,
        percent=True,
        start=start,
        end=start + len(digits) + 1,
        line=0,
        shape="measured",
        digits=digits,
    )


def _decl(index: int, value: float, ref: str) -> Declaration:
    return Declaration(
        index=index,
        value_text=f"{value}%",
        value=value,
        percent=True,
        role="observed",
        note="",
        ref=ref,
    )


def _block(*declarations: Declaration) -> FiguresBlock:
    return FiguresBlock(
        present=True,
        span=None,
        raw="",
        declarations=tuple(declarations),
        malformed=(),
        spans=(),
    )


def test_exact_decimal_half_up_rounding_is_allowed(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="report")
    figure = _figure(0.23, 0)

    assert ledger._within_written_precision(figure, 0.23, 0.225)
    assert _block(_decl(1, 0.225, "call_cer::data.cer_mtd")).match(
        0.23, True, "0.23"
    ) is not None


def test_wrong_side_of_decimal_tie_is_rejected(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="report")
    figure = _figure(0.22, 0)

    assert not ledger._within_written_precision(figure, 0.22, 0.225)
    assert _block(_decl(1, 0.225, "call_cer::data.cer_mtd")).match(
        0.22, True, "0.22"
    ) is None


def test_existing_coarse_rounding_cap_remains_closed(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="report")
    figure = _figure(0.82, 0)

    assert not ledger._within_written_precision(figure, 0.82, 0.82467)
    assert _block(_decl(1, 0.82467, "call_x::data.metric")).match(
        0.82, True, "0.82"
    ) is None


def test_duplicate_numeric_declarations_bind_in_document_order(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="report")
    cer = _decl(1, 0.23, "call_cer::data.cer_mtd")
    cash = _decl(2, 0.23, "call_cash::data.cash_pct")
    first = _figure(0.23, 10)
    second = _figure(0.23, 30)

    resolved = ledger._declarations_by_span(_block(cer, cash), [first, second])

    assert resolved[(first.start, first.end)] is cer
    assert resolved[(second.start, second.end)] is cash


def test_extra_duplicate_occurrence_fails_closed(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="report")
    cer = _decl(1, 0.23, "call_cer::data.cer_mtd")
    cash = _decl(2, 0.23, "call_cash::data.cash_pct")
    figures = [_figure(0.23, start) for start in (10, 30, 50)]

    resolved = ledger._declarations_by_span(_block(cer, cash), figures)

    assert resolved[(figures[0].start, figures[0].end)] is cer
    assert resolved[(figures[1].start, figures[1].end)] is cash
    assert resolved[(figures[2].start, figures[2].end)] is None


def test_single_declaration_can_still_ground_repeated_prose(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="report")
    declaration = _decl(1, 0.23, "call_cer::data.cer_mtd")
    first = _figure(0.23, 10)
    second = _figure(0.23, 30)

    resolved = ledger._declarations_by_span(_block(declaration), [first, second])

    assert resolved[(first.start, first.end)] is declaration
    assert resolved[(second.start, second.end)] is declaration
