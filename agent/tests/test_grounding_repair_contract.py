from types import SimpleNamespace

from src.agent.grounding.repair_contract import (
    CorrectionContract,
    RepairAction,
    directive_for_issue,
)


def test_exact_ref_repair_is_preserved():
    directive = directive_for_issue(
        {
            "reason": "field_ref_needs_call_id",
            "exact_ref_repair_candidate": "call_ok::data.metric",
        }
    )
    assert directive.action is RepairAction.AUTO_REPAIR
    assert directive.preserve is True
    assert directive.exact_ref == "call_ok::data.metric"


def test_aggregate_and_entity_scope_are_preserve_rewrites():
    aggregate = directive_for_issue(
        {
            "aggregate_ref_candidate": "call_p::data.concentration.top5_pct",
        }
    )
    entity = directive_for_issue(
        {
            "entity_ref_symbol": "SPY",
            "exact_ref_repair_candidate": "call_s::data.excess_return_pct",
        }
    )

    assert aggregate.action is RepairAction.PRESERVE_REWRITE
    assert aggregate.preserve is True
    assert aggregate.target_scope == "aggregate"
    assert entity.action is RepairAction.PRESERVE_REWRITE
    assert entity.preserve is True
    assert entity.target_scope == "entity:SPY"


def test_unproven_candidate_remains_droppable():
    directive = directive_for_issue(
        {
            "reason": "field_ref_needs_call_id",
            "field_ref_candidates": ["call_fallback::data.metric"],
        }
    )
    assert directive.action is RepairAction.DROP
    assert directive.preserve is False


def test_recover_is_not_a_claim_preservation_obligation():
    directive = directive_for_issue({"reason": "no_evidence"})

    assert directive.action is RepairAction.RECOVER
    assert directive.preserve is False


def test_contract_hard_preserves_only_repairable_rejected_figures():
    validation = SimpleNamespace(
        passed_figures=("3.15%", "12.06%", "3%", "2%"),
        issues=[
            {
                "value": "50.61%",
                "figure_value": 50.61,
                "figure_percent": True,
                "figure_currency": False,
                "figure_digits": "50.61",
                "aggregate_ref_candidate": "call_p::data.concentration.top5_pct",
            },
            {
                "value": "99.99%",
                "figure_value": 99.99,
                "figure_percent": True,
                "figure_currency": False,
                "figure_digits": "99.99",
                "reason": "value_mismatch",
            },
            {
                "value": "2.93%",
                "figure_value": 2.93,
                "figure_percent": True,
                "figure_currency": False,
                "figure_digits": "2.93",
                "reason": "no_evidence",
            },
        ],
    )

    contract = CorrectionContract.from_validation(validation)

    assert [claim.text for claim in contract.required_figures] == ["50.61%"]
    assert contract.missing_figures(
        "Cartera 3.15% YTD 12.06%. Top 5 agregado: 50.61%.\n\n"
        "```figures\n"
        "50.61% | observed | top5 | call_p::data.concentration.top5_pct\n"
        "```"
    ) == ()
    assert contract.missing_figures(
        "Cartera 3.15% YTD 12.06%. Se omite el agregado.\n\n"
        "```figures\n"
        "3.15% | observed | portfolio | call_perf::data.return_pct\n"
        "```"
    ) == ("50.61%",)


def test_required_figure_matches_equivalent_numeric_formatting():
    validation = SimpleNamespace(
        passed_figures=(),
        issues=[
            {
                "value": "0.2250%",
                "figure_value": 0.225,
                "figure_percent": True,
                "figure_currency": False,
                "figure_digits": "0.2250",
                "exact_ref_repair_candidate": "call_cer::data.return_pct",
            },
            {
                "value": "2.93%",
                "figure_value": 2.93,
                "figure_percent": True,
                "figure_currency": False,
                "figure_digits": "2.93",
                "exact_ref_repair_candidate": "call_var::data.var95_pct",
            },
        ],
    )
    contract = CorrectionContract.from_validation(validation)

    assert contract.missing_figures(
        "CER: 0.225%. VaR 95%: 2.9282666%.\n\n"
        "```figures\n"
        "0.225% | observed | CER | call_cer::data.return_pct\n"
        "2.9282666% | observed | VaR | call_var::data.var95_pct\n"
        "```"
    ) == ()


def test_contract_retry_prompt_is_focused():
    validation = SimpleNamespace(
        passed_figures=(),
        issues=[
            {
                "value": "50.61%",
                "figure_value": 50.61,
                "figure_percent": True,
                "figure_currency": False,
                "figure_digits": "50.61",
                "aggregate_ref_candidate": "call_p::data.concentration.top5_pct",
            }
        ],
    )
    contract = CorrectionContract.from_validation(validation)
    prompt = contract.violation_prompt(("50.61%",))

    assert "silently removed" in prompt
    assert "50.61%" in prompt
    assert "Restore those numeric claims" in prompt
    assert "Equivalent numeric formatting is allowed" in prompt
    assert "Do not add unrelated numeric claims" in prompt


def test_contract_preserves_decimal_comma_without_percent_from_issue_metadata():
    validation = SimpleNamespace(
        passed_figures=(),
        issues=[
            {
                "value": "+1,79",
                "figure_value": 1.79,
                "figure_percent": False,
                "figure_currency": False,
                "figure_digits": "1.79",
                "entity_ref_symbol": "SPY",
                "exact_ref_repair_candidate": "call_spy::data.excess_return_pct",
            },
            {
                "value": "4,39",
                "figure_value": 4.39,
                "figure_percent": False,
                "figure_currency": False,
                "figure_digits": "4.39",
                "exact_ref_repair_candidate": "call_risk::data.effective_n",
            },
        ],
    )

    contract = CorrectionContract.from_validation(validation)

    assert [claim.text for claim in contract.required_figures] == ["+1,79", "4,39"]
    assert contract.missing_figures(
        "SPY +1.79 pp. Effective N 4.39.\n\n"
        "```figures\n"
        "1.79 | observed | SPY gap | call_spy::data.excess_return_pct\n"
        "4.39 | observed | Effective N | call_risk::data.effective_n\n"
        "```"
    ) == ()


def test_contract_ignores_currency_label_position_when_ref_and_value_survive():
    validation = SimpleNamespace(
        passed_figures=(),
        issues=[
            {
                "value": "57,81%",
                "figure_value": 57.81,
                "figure_percent": True,
                "figure_currency": True,
                "figure_digits": "57.81",
                "aggregate_ref_candidate": "call_fx::data.currency_exposure.ARS",
            },
            {
                "value": "42,19%",
                "figure_value": 42.19,
                "figure_percent": True,
                "figure_currency": True,
                "figure_digits": "42.19",
                "aggregate_ref_candidate": "call_fx::data.currency_exposure.USD",
            },
        ],
    )
    contract = CorrectionContract.from_validation(validation)

    draft = (
        "La distribución monetaria es 57,81% en ARS y 42,19% en USD.\n\n"
        "```figures\n"
        "57.81% | observed | ARS exposure | call_fx::data.currency_exposure.ARS\n"
        "42.19% | observed | USD exposure | call_fx::data.currency_exposure.USD\n"
        "```"
    )
    assert contract.missing_figures(draft) == ()


def test_same_numeric_value_with_wrong_ref_does_not_satisfy_contract():
    validation = SimpleNamespace(
        passed_figures=(),
        issues=[
            {
                "value": "50,61%",
                "figure_value": 50.61,
                "figure_percent": True,
                "figure_currency": False,
                "figure_digits": "50.61",
                "aggregate_ref_candidate": "call_p::data.concentration.top5_pct",
            }
        ],
    )
    contract = CorrectionContract.from_validation(validation)

    draft = (
        "Otra métrica también vale 50,61%.\n\n"
        "```figures\n"
        "50.61% | observed | other metric | call_other::data.metric\n"
        "```"
    )
    assert contract.missing_figures(draft) == ("50,61%",)


def test_multiple_proven_refs_are_preserved_as_options():
    issue = {
        "value": "221963026.08",
        "figure_value": 221963026.08,
        "figure_percent": False,
        "figure_currency": True,
        "figure_digits": "221963026.08",
        "reason": "field_ref_needs_call_id",
        "field_ref_candidates": [
            "call_snapshot_a::data.total_value",
            "call_snapshot_b::data.total_value",
        ],
        "proven_ref_repair_candidates": [
            "call_snapshot_a::data.total_value",
            "call_snapshot_b::data.total_value",
        ],
    }

    directive = directive_for_issue(issue)

    assert directive.action is RepairAction.PRESERVE_OPTIONS
    assert directive.preserve is True
    assert directive.exact_ref is None
    assert directive.allowed_refs == (
        "call_snapshot_a::data.total_value",
        "call_snapshot_b::data.total_value",
    )


def test_preserve_options_contract_accepts_any_proven_ref_but_not_other_refs():
    validation = SimpleNamespace(
        passed_figures=(),
        issues=[
            {
                "value": "221963026.08",
                "figure_value": 221963026.08,
                "figure_percent": False,
                "figure_currency": True,
                "figure_digits": "221963026.08",
                "reason": "field_ref_needs_call_id",
                "proven_ref_repair_candidates": [
                    "call_snapshot_a::data.total_value",
                    "call_snapshot_b::data.total_value",
                ],
            }
        ],
    )
    contract = CorrectionContract.from_validation(validation)

    with_first = (
        "Valor actual ARS 221963026.08.\n\n"
        "```figures\n"
        "221963026.08 | observed | current value | call_snapshot_a::data.total_value\n"
        "```"
    )
    with_second = (
        "Valor actual ARS 221963026.08.\n\n"
        "```figures\n"
        "221963026.08 | observed | current value | call_snapshot_b::data.total_value\n"
        "```"
    )
    with_wrong = (
        "Valor actual ARS 221963026.08.\n\n"
        "```figures\n"
        "221963026.08 | observed | current value | call_other::data.total_value\n"
        "```"
    )

    assert contract.missing_figures(with_first) == ()
    assert contract.missing_figures(with_second) == ()
    assert contract.missing_figures(with_wrong) == ("221963026.08",)


def test_multiple_candidate_hints_without_proof_remain_droppable():
    directive = directive_for_issue(
        {
            "reason": "field_ref_needs_call_id",
            "field_ref_candidates": [
                "call_a::data.metric",
                "call_b::data.metric",
            ],
        }
    )

    assert directive.action is RepairAction.DROP
    assert directive.preserve is False


def test_contract_accepts_unique_pre_pipe_alias_for_allowed_ref():
    validation = SimpleNamespace(
        passed_figures=(),
        issues=[
            {
                "value": "35.81%",
                "figure_value": 35.81,
                "figure_percent": True,
                "figure_currency": False,
                "figure_digits": "35.81",
                "reason": "field_ref_needs_call_id",
                "proven_ref_repair_candidates": [
                    "call_alpha|fc_beta::data.volatility.annualized_vol"
                ],
            }
        ],
    )
    contract = CorrectionContract.from_validation(validation)

    draft = (
        "Volatility is 35.81%.\n\n"
        "```figures\n"
        "35.81% | observed | annualized volatility | "
        "call_alpha::data.volatility.annualized_vol\n"
        "```"
    )

    assert contract.missing_figures(draft) == ()


def test_contract_rejects_ambiguous_pre_pipe_alias_across_allowed_refs():
    validation = SimpleNamespace(
        passed_figures=(),
        issues=[
            {
                "value": "35.81%",
                "figure_value": 35.81,
                "figure_percent": True,
                "figure_currency": False,
                "figure_digits": "35.81",
                "reason": "field_ref_needs_call_id",
                "proven_ref_repair_candidates": [
                    "call_alpha|fc_one::data.volatility.annualized_vol",
                    "call_alpha|fc_two::data.volatility.annualized_vol",
                ],
            }
        ],
    )
    contract = CorrectionContract.from_validation(validation)

    draft = (
        "Volatility is 35.81%.\n\n"
        "```figures\n"
        "35.81% | observed | annualized volatility | "
        "call_alpha::data.volatility.annualized_vol\n"
        "```"
    )

    assert contract.missing_figures(draft) == ("35.81%",)



def test_replace_contract_requires_replacement_value_and_ref():
    validation = SimpleNamespace(
        passed_figures=(),
        issues=[
            {
                "value": "20.01%",
                "figure_value": 20.01,
                "figure_percent": True,
                "figure_currency": False,
                "figure_digits": "20.01",
                "reason": "entity_value_needs_canonical_replacement",
                "replacement_text": "19.97%",
                "replacement_value": 19.97,
                "replacement_digits": "19.97",
                "replacement_ref_candidate": "call_p|fc_w::context.holdings_native.ARS[0].weight",
                "replacement_entity_symbol": "YPFD",
            }
        ],
    )

    contract = CorrectionContract.from_validation(validation)
    assert len(contract.required_figures) == 1
    required = contract.required_figures[0]
    assert required.text == "19.97%"
    assert required.value == 19.97
    assert required.allowed_refs == (
        "call_p|fc_w::context.holdings_native.ARS[0].weight",
    )

    old_value = (
        "YPFD 20.01%.\n\n"
        "```figures\n"
        "20.01% | observed | weight | "
        "call_p|fc_w::context.holdings_native.ARS[0].weight\n"
        "```"
    )
    assert contract.missing_figures(old_value) == ("19.97%",)

    replaced = (
        "YPFD 19.97%.\n\n"
        "```figures\n"
        "19.97% | observed | weight | "
        "call_p|fc_w::context.holdings_native.ARS[0].weight\n"
        "```"
    )
    assert contract.missing_figures(replaced) == ()


def test_derive_contract_requires_all_operand_refs():
    validation = SimpleNamespace(
        passed_figures=(),
        issues=[
            {
                "value": "12.73%",
                "figure_value": 12.73,
                "figure_percent": True,
                "figure_currency": False,
                "figure_digits": "12.73",
                "reason": "derived_refs_incomplete",
                # Contract-only fixture: authorization is tested through the ledger.
                "derived_repair_verified": True,
                "derive_formula": "(0.3581-0.2308)*100",
                "derive_operand_refs": [
                    "call_a|fc_x::data.volatility.annualized_vol",
                    "call_b|fc_y::data.volatility.annualized_vol",
                ],
            }
        ],
    )
    contract = CorrectionContract.from_validation(validation)

    one_ref = (
        "Difference 12.73%.\n\n"
        "```figures\n"
        "12.73% | derived | (0.3581-0.2308)*100 | "
        "call_a|fc_x::data.volatility.annualized_vol\n"
        "```"
    )
    assert contract.missing_figures(one_ref) == ("12.73%",)

    both_refs = (
        "Difference 12.73%.\n\n"
        "```figures\n"
        "12.73% | derived | (0.3581-0.2308)*100 | "
        "call_a|fc_x::data.volatility.annualized_vol;"
        "call_b|fc_y::data.volatility.annualized_vol\n"
        "```"
    )
    assert contract.missing_figures(both_refs) == ()



def test_replace_derived_contract_requires_canonical_total_and_all_refs():
    refs = [
        "call_p|fc_w::context.holdings_native.ARS[0].weight",
        "call_p|fc_w::context.holdings_native.ARS[1].weight",
        "call_p|fc_w::context.holdings_native.ARS[2].weight",
        "call_p|fc_w::context.holdings_native.ARS[3].weight",
        "call_p|fc_w::context.holdings_native.ARS[4].weight",
    ]
    validation = SimpleNamespace(
        passed_figures=(),
        issues=[
            {
                "value": "50.73%",
                "figure_value": 50.73,
                "figure_percent": True,
                "figure_currency": False,
                "figure_digits": "50.73",
                "reason": "derived_summary_needs_canonical_replacement",
                "replacement_text": "50.61%",
                "replacement_value": 50.61,
                "replacement_digits": "50.61",
                # Contract-only fixture: authorization is tested through the ledger.
                "derived_repair_verified": True,
                "replacement_role": "derived",
                "replacement_refs": refs,
                "replacement_formula": "(0.1997+0.1311+0.0598+0.0591+0.0564)*100",
            }
        ],
    )

    contract = CorrectionContract.from_validation(validation)
    assert len(contract.required_figures) == 1
    required = contract.required_figures[0]
    assert required.text == "50.61%"
    assert required.value == 50.61
    assert required.require_all_refs is True
    assert set(required.allowed_refs) == set(refs)

    old_total = (
        "Total 50.73%.\n\n"
        "```figures\n"
        "50.73% | derived | (0.2001+0.1314+0.0599+0.0592+0.0567)*100 | "
        + ";".join(refs)
        + "\n```"
    )
    assert contract.missing_figures(old_total) == ("50.61%",)

    missing_one = (
        "Total 50.61%.\n\n"
        "```figures\n"
        "50.61% | derived | (0.1997+0.1311+0.0598+0.0591+0.0564)*100 | "
        + ";".join(refs[:-1])
        + "\n```"
    )
    assert contract.missing_figures(missing_one) == ("50.61%",)

    canonical = (
        "Total 50.61%.\n\n"
        "```figures\n"
        "50.61% | derived | (0.1997+0.1311+0.0598+0.0591+0.0564)*100 | "
        + ";".join(refs)
        + "\n```"
    )
    assert contract.missing_figures(canonical) == ()
