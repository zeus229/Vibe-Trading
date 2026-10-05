"""Regression tests for compact horizon labels in Markdown tables."""

from src.agent.grounding.figures import parse_figures_block, scan_figures


def _figures(content: str):
    return scan_figures(content, parse_figures_block(content))


def test_one_year_table_label_is_not_measured():
    content = (
        "| Periodo | Cartera |\n"
        "|---|---:|\n"
        "| 1Y | 53.32% |\n"
    )

    figures = _figures(content)
    horizon = next(item for item in figures if item.text == "1")
    value = next(item for item in figures if item.text == "53.32%")

    assert horizon.shape == "bare"
    assert value.shape == "measured"


def test_multi_digit_year_horizon_table_label_is_not_measured():
    content = (
        "| Horizon | Return |\n"
        "|---|---:|\n"
        "| 10y | 12.50% |\n"
    )

    horizon = next(item for item in _figures(content) if item.text == "10")
    assert horizon.shape == "bare"


def test_magnitude_suffix_is_not_mistaken_for_year_horizon():
    content = (
        "| Metric | Value |\n"
        "|---|---:|\n"
        "| Revenue | 5M |\n"
        "| EBITDA | 24.6M |\n"
    )

    figures = _figures(content)
    five = next(item for item in figures if item.text == "5")
    twenty_four = next(item for item in figures if item.text == "24.6")

    assert five.shape == "measured"
    assert twenty_four.shape == "measured"


def test_year_like_value_under_price_column_remains_measured():
    content = (
        "| Symbol | Close |\n"
        "|---|---:|\n"
        "| AAA.US | 1Y |\n"
    )

    horizon = next(item for item in _figures(content) if item.text == "1")
    assert horizon.shape == "measured"
