"""Web reader tool: fetch a URL as Markdown text via the Jina Reader API."""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import time
from urllib.parse import urlsplit

import requests

from backtest.loaders.acquisition import source_deadline
from src.agent.progress import emit_progress
from src.data_gateway.reads import document_read_budget, managed_reader, public_data_read
from src.agent.tools import BaseTool
from src.tools._arguments import boolean_argument
from src.security.scanner import with_security_warnings

logger = logging.getLogger(__name__)

_JINA_PREFIX = "https://r.jina.ai/"
_TIMEOUT = 30
_MAX_LENGTH = 8000
_MAX_FIND_TERMS = 4
_MAX_FIND_TERM_LENGTH = 120
_MAX_HITS_PER_TERM = 2
_FIND_CONTEXT_CHARS = 425
_CACHED_MARKER = "Warning: This is a cached snapshot"


def _url_allowed(url: str) -> tuple[bool, str]:
    """Return whether a URL is safe to forward to the remote reader service."""
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return False, "target URL is not allowed"

    if parsed.scheme.lower() not in {"http", "https"}:
        return False, "target URL is not allowed"
    if not parsed.hostname:
        return False, "target URL is not allowed"
    if parsed.username or parsed.password:
        return False, "target URL is not allowed"

    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return False, "target URL is not allowed"

    ip_host = host.split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(ip_host)
    except ValueError:
        return True, ""

    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    ):
        return False, "target URL is not allowed"
    return True, ""



def _select_document_excerpt(text: str, find: list[str] | None, start_char: int | None) -> dict:
    """Select bounded, source-positioned excerpts without returning the whole page.

    All offsets are Unicode character indices in the original Jina response.
    Search terms are literal, case-insensitive strings (not regex expressions).
    """
    if find is not None and start_char is not None:
        raise ValueError("find and start_char cannot be combined")
    if find is not None:
        if (
            not isinstance(find, list)
            or not 1 <= len(find) <= _MAX_FIND_TERMS
            or any(
                not isinstance(term, str)
                or not 1 <= len(term.strip()) <= _MAX_FIND_TERM_LENGTH
                for term in find
            )
        ):
            raise ValueError("find must contain 1 to 4 non-empty strings of at most 120 characters")
        sections = []
        counts = {}
        for term in find:
            term = term.strip()
            matches = list(re.finditer(re.escape(term), text, flags=re.IGNORECASE))
            counts[term] = len(matches)
            for match in matches[:_MAX_HITS_PER_TERM]:
                start = max(0, match.start() - _FIND_CONTEXT_CHARS)
                end = min(len(text), match.end() + _FIND_CONTEXT_CHARS)
                sections.append({
                    "term": term,
                    "match_start": match.start(),
                    "start": start,
                    "end": end,
                    "text": text[start:end],
                })
        sections.sort(key=lambda item: item["match_start"])
        chunks = []
        for section in sections:
            label = (
                f"[{section['term']} at char {section['match_start']}; "
                f"excerpt {section['start']}:{section['end']}]"
            )
            candidate = label + "\n" + section["text"]
            remaining = _MAX_LENGTH - sum(len(part) for part in chunks) - 2 * len(chunks)
            if remaining <= len(label) + 1:
                break
            chunks.append(candidate[:remaining])
        return {
            "content": "\n\n".join(chunks),
            "find": find,
            "match_counts": counts,
            "excerpts": [
                {key: value for key, value in section.items() if key != "text"}
                for section in sections[:len(chunks)]
            ],
            "truncated": len(text) > len("\n\n".join(chunks)),
        }
    if start_char is not None:
        if type(start_char) is not int or not 0 <= start_char <= len(text):
            raise ValueError("start_char must be an integer within the document length")
        end = min(len(text), start_char + _MAX_LENGTH)
        return {
            "content": text[start_char:end],
            "range_start": start_char,
            "range_end": end,
            "truncated": start_char != 0 or end != len(text),
        }
    if len(text) > _MAX_LENGTH:
        return {
            "content": text[:_MAX_LENGTH] + f"\n\n... (truncated, total {len(text)} chars)",
        }
    return {"content": text}


@managed_reader
def read_url(
    url: str,
    no_cache: bool = False,
    find: list[str] | None = None,
    start_char: int | None = None,
) -> str:
    """Fetch web page content via the Jina Reader API.

    The full URL (including query string) is sent to the third-party Jina
    Reader service (r.jina.ai); never pass credentials/tokens or private
    addresses. Results may be a cached snapshot.

    Args:
        url: Target URL.
        no_cache: When true, ask the reader for a fresh (uncached) fetch.
        find: Optional literal phrases to find in the full response, returning
            brief source-positioned excerpts instead of the first 8,000 chars.
        start_char: Optional source character offset for reading later sections.

    Returns:
        JSON result with title, content, url; ``cached: true`` is added
        when the reader served a stale snapshot.
    """
    target_url = url.strip()
    allowed, error = _url_allowed(target_url)
    if not allowed:
        return json.dumps({"status": "error", "error": error}, ensure_ascii=False)

    # Validate selection arguments before making a remote HTTP request.
    if start_char is not None and (type(start_char) is not int or start_char < 0):
        return json.dumps({
            "status": "error",
            "error": "start_char must be a non-negative integer",
        }, ensure_ascii=False)
    try:
        _select_document_excerpt("", find, start_char if start_char is None else 0)
    except ValueError as exc:
        return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

    timeout = _TIMEOUT
    try:
        # Use the remaining gateway source budget for the HTTP transport as
        # well. A timed-out reader cannot free its source slot until GET ends.
        until = source_deadline.get()
        if until is not None:
            remaining = until - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Document read deadline expired before HTTP dispatch")
            if document_read_budget.get():
                timeout = min(_TIMEOUT, remaining)
        headers = {"Accept": "text/markdown"}
        if no_cache:
            headers["x-no-cache"] = "true"
        emit_progress(
            "fetching",
            message=f"GET {target_url[:60]}{'…' if len(target_url) > 60 else ''}",
        )
        resp = requests.get(
            f"{_JINA_PREFIX}{target_url}",
            headers=headers,
            timeout=timeout,
        )
        emit_progress("parsing", message="extracting markdown")
        if resp.status_code != 200:
            logger.warning("read_url upstream HTTP %s: %s", resp.status_code, resp.text[:500])
            return json.dumps({
                "status": "error",
                "error": f"remote reader returned HTTP {resp.status_code}: {resp.text[:500]}",
            }, ensure_ascii=False)

        text = resp.text
        title = ""
        for line in text.split("\n"):
            if line.startswith("Title:"):
                title = line[6:].strip()
                break

        try:
            selection = _select_document_excerpt(text, find, start_char)
        except ValueError as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)

        result = {
            "status": "ok",
            "title": title,
            "url": target_url,
            "length": len(resp.text),
            **selection,
        }
        if _CACHED_MARKER in resp.text:
            result["cached"] = True
        result = with_security_warnings(result, fields=("content",))
        return json.dumps(result, ensure_ascii=False)

    except requests.Timeout:
        return json.dumps({"status": "error", "error": f"Request timed out ({timeout:g}s)"}, ensure_ascii=False)
    except Exception as exc:
        logger.warning("read_url request failed: %s", exc)
        return json.dumps(
            {"status": "error", "error": f"remote reader request failed: {exc}"},
            ensure_ascii=False,
        )


class WebReaderTool(BaseTool):
    """Web reader tool."""

    name = "read_url"
    description = (
        "Read a public web page as Markdown. For long financial filings and "
        "reports, use optional find phrases to extract matching sections with "
        "character offsets, or start_char to inspect a later window. Standard "
        "reads return only the first 8,000 characters."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL of the web page to read"},
            "no_cache": {
                "type": "boolean",
                "description": (
                    "Request a fresh (uncached) fetch. Use only when the prior "
                    "read explicitly reported cached=true or the task genuinely "
                    "requires newer data. Never set no_cache merely because a "
                    "previous result was compacted or replayed."
                ),
                "default": False,
            },
            "find": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional 1-4 literal phrases to locate anywhere in a long "
                    "page (such as 'revenue' or 'net income'). Returns brief "
                    "excerpts with original character offsets, not only the start "
                    "of the page. Use this before another broad read."
                ),
                "maxItems": 4,
            },
            "start_char": {
                "type": "integer",
                "description": (
                    "Optional original character offset for the next 8,000-character "
                    "window. Use an excerpt's start/end offsets to inspect tables "
                    "around an identified phrase. Cannot combine with find."
                ),
                "minimum": 0,
            },
        },
        "required": ["url"],
    }
    repeatable = True
    # Normal repeated reads remain allowed while the prior payload is visible.
    # If compaction removes an exact successful read, AgentLoop may restore the
    # run-scoped result instead of hitting Jina/the origin again. ``no_cache``
    # remains the explicit freshness escape hatch and bypasses that replay.
    replay_after_compaction = True

    @public_data_read(cacheable=False, timeout_profile="web_reader")
    def execute(self, **kwargs) -> str:
        """Fetch web page."""
        try:
            no_cache = boolean_argument(kwargs.get("no_cache", False), field="no_cache")
        except ValueError as exc:
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)
        return read_url(
            kwargs["url"],
            no_cache=no_cache,
            find=kwargs.get("find"),
            start_char=kwargs.get("start_char"),
        )
