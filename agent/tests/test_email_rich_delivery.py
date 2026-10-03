from __future__ import annotations

import asyncio
from io import BytesIO

import pytest
from pypdf import PdfReader

from src.channels.bus.events import OutboundMessage
from src.channels.bus.queue import MessageBus
from src.channels.email import EmailChannel
from src.channels.rich_text import render_email_html


def _channel(outbound_format: str, *, pdf_password: str = "") -> EmailChannel:
    return EmailChannel(
        {
            "consent_granted": True,
            "smtp_host": "smtp.example.test",
            "smtp_username": "bot@example.test",
            "smtp_password": "secret",
            "from_address": "bot@example.test",
            "outbound_format": outbound_format,
            "pdf_password": pdf_password,
        },
        MessageBus(),
    )


def test_rich_email_renderer_preserves_markdown_and_sanitizes_html():
    rendered = render_email_html(
        "# Portfolio\n\n**Return:** 1.2%\n\n| Asset | Weight |\n|---|---:|\n| ABC | 10% |\n"
        "<script>alert(1)</script>\n[jump](javascript:alert(1))"
    )

    assert "<h1>Portfolio</h1>" in rendered
    assert "<strong>Return:</strong>" in rendered
    assert "<table>" in rendered
    assert "<script>" not in rendered
    assert "javascript:" not in rendered


def test_html_delivery_keeps_plain_fallback_and_adds_html(monkeypatch):
    channel = _channel("html")
    sent = []
    monkeypatch.setattr(channel, "_smtp_send", lambda message: sent.append(message))

    asyncio.run(
        channel.send(
        OutboundMessage(
            channel="email",
            chat_id="reader@example.test",
            content="# Daily report\n\n**Return:** 1.2%",
        )
        )
    )

    assert len(sent) == 1
    message = sent[0]
    assert message.get_body(preferencelist=("plain",)).get_content().startswith("# Daily report")
    html_part = message.get_body(preferencelist=("html",))
    assert html_part is not None
    assert "<h1>Daily report</h1>" in html_part.get_content()


def test_plain_delivery_preserves_legacy_single_part(monkeypatch):
    channel = _channel("plain")
    sent = []
    monkeypatch.setattr(channel, "_smtp_send", lambda message: sent.append(message))

    asyncio.run(
        channel.send(
        OutboundMessage(
            channel="email",
            chat_id="reader@example.test",
            content="Legacy plain body",
        )
        )
    )

    assert len(sent) == 1
    assert sent[0].get_content_type() == "text/plain"
    assert sent[0].get_content().strip() == "Legacy plain body"


def test_html_pdf_delivery_attaches_generated_pdf(monkeypatch):
    channel = _channel("html+pdf")
    sent = []
    monkeypatch.setattr(channel, "_smtp_send", lambda message: sent.append(message))

    asyncio.run(
        channel.send(
        OutboundMessage(
            channel="email",
            chat_id="reader@example.test",
            content="# PDF report\n\nA generated report.",
        )
        )
    )

    assert len(sent) == 1
    message = sent[0]
    pdf_parts = [part for part in message.iter_attachments() if part.get_content_type() == "application/pdf"]
    assert len(pdf_parts) == 1
    assert pdf_parts[0].get_filename() == "vibe-trading-report.pdf"
    assert pdf_parts[0].get_payload(decode=True).startswith(b"%PDF")


def test_per_message_html_overrides_plain_channel_default(monkeypatch):
    channel = _channel("plain")
    sent = []
    monkeypatch.setattr(channel, "_smtp_send", lambda message: sent.append(message))

    asyncio.run(
        channel.send(
            OutboundMessage(
                channel="email",
                chat_id="reader@example.test",
                content="# Scheduled report\n\nFull report body.",
                metadata={"delivery_format": "html"},
            )
        )
    )

    message = sent[0]
    assert message.get_body(preferencelist=("plain",)).get_content().startswith("# Scheduled report")
    assert "<h1>Scheduled report</h1>" in message.get_body(preferencelist=("html",)).get_content()
    assert list(message.iter_attachments()) == []


def test_per_message_pdf_keeps_report_out_of_body(monkeypatch):
    channel = _channel("plain")
    sent = []
    monkeypatch.setattr(channel, "_smtp_send", lambda message: sent.append(message))

    report = "# Sensitive report\n\nPortfolio value: 123456"
    asyncio.run(
        channel.send(
            OutboundMessage(
                channel="email",
                chat_id="reader@example.test",
                content=report,
                metadata={"delivery_format": "pdf"},
            )
        )
    )

    message = sent[0]
    plain_body = message.get_body(preferencelist=("plain",)).get_content()
    html_body = message.get_body(preferencelist=("html",)).get_content()
    assert "Report attached as PDF." in plain_body
    assert "123456" not in plain_body
    assert "123456" not in html_body
    pdf_parts = [
        part for part in message.iter_attachments()
        if part.get_content_type() == "application/pdf"
    ]
    assert len(pdf_parts) == 1
    assert pdf_parts[0].get_payload(decode=True).startswith(b"%PDF")


def test_per_message_pdf_can_be_password_protected(monkeypatch):
    channel = _channel("plain", pdf_password="correct horse battery staple")
    sent = []
    monkeypatch.setattr(channel, "_smtp_send", lambda message: sent.append(message))

    asyncio.run(
        channel.send(
            OutboundMessage(
                channel="email",
                chat_id="reader@example.test",
                content="# Protected report\n\nPortfolio value: 123456",
                metadata={"delivery_format": "pdf", "protect_pdf": True},
            )
        )
    )

    pdf_part = next(
        part for part in sent[0].iter_attachments()
        if part.get_content_type() == "application/pdf"
    )
    encrypted = pdf_part.get_payload(decode=True)
    reader = PdfReader(BytesIO(encrypted))
    assert reader.is_encrypted
    assert reader.decrypt("wrong password") == 0
    assert reader.decrypt("correct horse battery staple") != 0
    assert "Portfolio value: 123456" in "".join(page.extract_text() or "" for page in reader.pages)


def test_protected_pdf_fails_closed_without_configured_password(monkeypatch):
    channel = _channel("plain")
    sent = []
    monkeypatch.setattr(channel, "_smtp_send", lambda message: sent.append(message))

    with pytest.raises(RuntimeError, match="no PDF password is configured"):
        asyncio.run(
            channel.send(
                OutboundMessage(
                    channel="email",
                    chat_id="reader@example.test",
                    content="# Sensitive report\n\nPortfolio value: 123456",
                    metadata={"delivery_format": "pdf", "protect_pdf": True},
                )
            )
        )

    assert sent == []


def test_pdf_protection_rejects_non_pdf_delivery(monkeypatch):
    channel = _channel("plain", pdf_password="secret")
    monkeypatch.setattr(channel, "_smtp_send", lambda message: None)

    with pytest.raises(ValueError, match="requires PDF delivery"):
        asyncio.run(
            channel.send(
                OutboundMessage(
                    channel="email",
                    chat_id="reader@example.test",
                    content="report",
                    metadata={"delivery_format": "html", "protect_pdf": True},
                )
            )
        )
