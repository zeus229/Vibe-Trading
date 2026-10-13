"""Verified portfolio listing identity must fail closed on ambiguity."""
import pytest

from src.agent.grounding import GroundingLedger

pytestmark = pytest.mark.unit


def row(isin, symbol, *, verified=True):
    return {
        "source_instrument_type": "ACCIONES", "market": "BYMA",
        "isin": isin,
        "provider_identity": {
            "provider": "yahoo", "resolution": "isin", "verified": verified,
            "resolved_by_isin": isin, "symbol": symbol,
        },
    }


def ingest(ledger, *rows):
    ledger._ingest_trusted_portfolio_identities(
        {"context": {"holdings_native": {"ACCIONES": list(rows)}}}, "call_portfolio"
    )


def state(ledger, isin):
    return ledger._identities.get("portfolio:" + isin)


def test_one_verified_listing_stays_locked(tmp_path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Portfolio")
    ingest(ledger, row("AR0000000001", "GGAL.BA"))
    assert state(ledger, "AR0000000001").status == "locked"
    assert state(ledger, "AR0000000001").symbol == "GGAL.BA"


@pytest.mark.parametrize("reverse", [False, True])
def test_two_qualified_listings_for_same_isin_do_not_choose_last(tmp_path, reverse):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Portfolio")
    rows = [row("AR0000000001", "GGAL.BA"), row("AR0000000001", "GGALD.BA")]
    ingest(ledger, *(list(reversed(rows)) if reverse else rows))
    record = state(ledger, "AR0000000001")
    assert record.status == "portfolio_conflict"
    assert record.symbol is None
    assert {item["symbol"] for item in record.candidates} == {"GGAL.BA", "GGALD.BA"}


def test_conflict_across_separate_calls_stays_closed(tmp_path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Portfolio")
    ingest(ledger, row("AR0000000001", "GGAL.BA"))
    ingest(ledger, row("AR0000000001", "GGALD.BA"))
    assert state(ledger, "AR0000000001").status == "portfolio_conflict"
    ingest(ledger, row("AR0000000001", "GGAL.BA"))
    assert state(ledger, "AR0000000001").status == "portfolio_conflict"


@pytest.mark.parametrize("separate", [False, True])
def test_distinct_isins_for_same_symbol_are_both_conflicting(tmp_path, separate):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Portfolio")
    first = row("AR0000000001", "GGAL.BA")
    second = row("AR0000000002", "GGAL.BA")
    if separate:
        ingest(ledger, first)
        ingest(ledger, second)
    else:
        ingest(ledger, first, second)
    assert state(ledger, "AR0000000001").status == "portfolio_conflict"
    assert state(ledger, "AR0000000002").status == "portfolio_conflict"


def test_unverified_row_does_not_create_conflict(tmp_path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Portfolio")
    ingest(ledger, row("AR0000000001", "GGAL.BA"),
           row("AR0000000001", "GGALD.BA", verified=False))
    assert state(ledger, "AR0000000001").status == "locked"
    assert state(ledger, "AR0000000001").symbol == "GGAL.BA"


def test_unrelated_locked_symbol_survives_portfolio_ambiguity(tmp_path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Analyze verified holdings")
    ingest(
        ledger,
        row("AR0000000001", "GGAL.BA"),
        row("AR0000000001", "GGALD.BA"),
        row("AR0000000002", "YPFD.BA"),
    )
    assert state(ledger, "AR0000000001").status == "portfolio_conflict"
    assert ledger.identity_status == "locked"
    assert "YPFD.BA" in ledger.authorized_symbols
    assert "GGAL.BA" not in ledger.authorized_symbols
    good = ledger.authorize_tool_call(
        "technical_indicators", {"symbol": "YPFD.BA"},
        batch_authorized_symbols=ledger.authorized_symbols, call_id="call_good",
        batch_identity_status=ledger.identity_status,
    )
    bad = ledger.authorize_tool_call(
        "technical_indicators", {"symbol": "GGAL.BA"},
        batch_authorized_symbols=ledger.authorized_symbols, call_id="call_bad",
        batch_identity_status=ledger.identity_status,
    )
    assert good.allowed
    assert not bad.allowed


def test_unrelated_symbol_is_still_available_after_later_conflict(tmp_path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Analyze verified holdings")
    ingest(ledger, row("AR0000000002", "YPFD.BA"))
    ingest(ledger, row("AR0000000001", "GGAL.BA"))
    ingest(ledger, row("AR0000000001", "GGALD.BA"))
    assert ledger.identity_status == "locked"
    assert ledger.authorize_tool_call(
        "get_market_data", {"codes": ["YPFD.BA"]},
        batch_authorized_symbols=ledger.authorized_symbols, call_id="call_clean",
        batch_identity_status=ledger.identity_status,
    ).allowed
    assert state(ledger, "AR0000000001").status == "portfolio_conflict"


def test_conflict_prose_is_rejected_without_poisoning_clean_prose(tmp_path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Analyze verified holdings")
    ingest(ledger, row("AR0000000001", "GGAL.BA"),
           row("AR0000000001", "GGALD.BA"),
           row("AR0000000002", "YPFD.BA"))
    bad = ledger.validate_final_answer("GGAL.BA shows an uptrend; hold.")
    assert not bad.valid
    assert any(issue.get("reason") == "portfolio_identity_conflict" for issue in bad.issues)
    clean = ledger.revalidate("YPFD.BA is a verified portfolio listing.")
    assert not any(issue.get("reason") == "portfolio_identity_conflict" for issue in clean.issues)


def test_symbol_search_cannot_supersede_portfolio_conflict(tmp_path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Analyze verified holdings")
    ingest(ledger, row("AR0000000001", "GGAL.BA"),
           row("AR0000000002", "GGAL.BA"))
    assert all(state(ledger, isin).status == "portfolio_conflict"
               for isin in ("AR0000000001", "AR0000000002"))
    ledger._supersede_shortlists("GGAL.BA")
    assert all(state(ledger, isin).status == "portfolio_conflict"
               for isin in ("AR0000000001", "AR0000000002"))
    assert "GGAL.BA" in ledger._portfolio_conflict_symbols()
