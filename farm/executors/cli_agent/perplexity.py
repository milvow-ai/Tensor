"""Perplexity executor: the Sonar API (OpenAI-compatible chat completions), answer mode only.

Perplexity has no agent CLI. Account is an API key; the connection's ``auth_ref`` is ``env:<NAME>`` (the key
is saved there by the ``save_secret`` command) and is resolved only here, for the one request. The key never
reaches a result, a log line or an error text: everything that can carry text goes through ``redact``.
"""

import re
import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from farm.executors.base import ErrorKind, ExecRequest, ExecResult
from farm.executors.cli_agent.base import BaseCliAgentExecutor, parse_json_or_none
from farm.secrets import AuthRefError, redact, resolve_auth

API_URL = "https://api.perplexity.ai/chat/completions"
DEFAULT_MODEL = "sonar"
PERPLEXITY_MODELS = ("sonar", "sonar-pro", "sonar-reasoning-pro", "sonar-deep-research")
MAX_ERROR_CHARS = 500
MAX_SOURCES = 20
_NO_KEY = (
    "No Perplexity API key is saved for this account: paste one in the dashboard "
    "(https://www.perplexity.ai/account/api/keys)."
)


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _retry_after(header: str | None, now: datetime) -> float | None:
    """Seconds to wait from a ``Retry-After`` header (delta-seconds or an HTTP date)."""
    if not header:
        return None
    text = header.strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - now).total_seconds())


def _error_text(response: httpx.Response) -> str:
    detail = ""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            detail = str(err.get("message") or "")
        elif isinstance(err, str):
            detail = err
        elif body.get("detail"):
            detail = str(body["detail"])
    if not detail:
        detail = response.text[:MAX_ERROR_CHARS]
    return redact(f"Perplexity API HTTP {response.status_code}: {detail}".strip())[:MAX_ERROR_CHARS]


def _sources(body: dict[str, Any]) -> list[tuple[str, str]]:
    """(title, url) pairs from ``search_results``, else from ``citations``; no duplicates."""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    results = body.get("search_results")
    if isinstance(results, list):
        for item in results:
            if isinstance(item, dict) and isinstance(item.get("url"), str) and item["url"] not in seen:
                seen.add(item["url"])
                found.append((str(item.get("title") or "").strip(), item["url"]))
    citations = body.get("citations")
    if isinstance(citations, list):
        for url in citations:
            if isinstance(url, str) and url not in seen:
                seen.add(url)
                found.append(("", url))
    return found[:MAX_SOURCES]


def _with_sources(text: str, body: dict[str, Any]) -> str:
    sources = _sources(body)
    if not sources:
        return text
    lines = [
        f"[{n}] {title} - {url}" if title else f"[{n}] {url}" for n, (title, url) in enumerate(sources, 1)
    ]
    return f"{text}\n\nSources:\n" + "\n".join(lines)


class PerplexityApiExecutor(BaseCliAgentExecutor):
    """Asks the Perplexity Sonar API; the account's API key comes from its ``auth_ref``."""

    async def execute(self, req: ExecRequest) -> ExecResult:
        meta = req.connection.meta or {}
        task = req.params.get("task") or req.params.get("prompt", "")
        model = req.params.get("model") or meta.get("model") or meta.get("default_model") or DEFAULT_MODEL
        mode = req.params.get("mode", "answer")
        timeout_s = float(req.params.get("timeout_s", req.timeout_s))
        json_schema = req.params.get("json_schema")
        model_str = str(model)

        def fail(
            kind: ErrorKind,
            message: str,
            *,
            duration_s: float = 0.0,
            retry_after_s: float | None = None,
            reset_at: datetime | None = None,
        ) -> ExecResult:
            return self.build_exec_result(
                ok=False,
                ai="perplexity",
                model=model_str,
                connection=req.connection,
                duration_s=duration_s,
                error_kind=kind,
                error=redact(message)[:MAX_ERROR_CHARS],
                retry_after_s=retry_after_s,
                reset_at=reset_at,
            )

        ok, problem = self.validate_cli_identifiers(model=model_str)
        if not ok:
            return fail(ErrorKind.BAD_REQUEST, problem or "Invalid model")
        if mode != "answer":
            return fail(ErrorKind.BAD_REQUEST, "Perplexity answers questions only; it cannot edit files.")
        if not str(task).strip():
            return fail(ErrorKind.BAD_REQUEST, "The task is empty.")

        auth_ref = req.connection.auth_ref or ""
        if not auth_ref.startswith("env:"):
            return fail(ErrorKind.NEEDS_LOGIN, _NO_KEY)
        try:
            api_key = resolve_auth(auth_ref)
        except AuthRefError as exc:
            return fail(ErrorKind.NEEDS_LOGIN, f"{exc}. {_NO_KEY}")

        payload: dict[str, Any] = {"model": model_str, "messages": [{"role": "user", "content": str(task)}]}
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                response = await client.post(API_URL, json=payload, headers=headers)
        except httpx.TimeoutException:
            return fail(
                ErrorKind.TIMEOUT,
                f"Perplexity API timed out after {timeout_s}s",
                duration_s=time.monotonic() - started,
            )
        except httpx.HTTPError as exc:
            return fail(
                ErrorKind.SERVER,
                f"Perplexity API request failed: {type(exc).__name__}: {exc}",
                duration_s=time.monotonic() - started,
            )
        duration_s = time.monotonic() - started

        status = response.status_code
        if status >= 400:
            message = _error_text(response)
            if status in (401, 403):
                return fail(ErrorKind.AUTH, message, duration_s=duration_s)
            if status == 402:
                return fail(ErrorKind.LIMIT_REACHED, message, duration_s=duration_s)
            if status == 429:
                now = datetime.now(UTC)
                wait = _retry_after(response.headers.get("retry-after"), now)
                return fail(
                    ErrorKind.RATE_LIMITED,
                    message,
                    duration_s=duration_s,
                    retry_after_s=wait,
                    reset_at=None if wait is None else now + timedelta(seconds=wait),
                )
            if status >= 500:
                return fail(ErrorKind.SERVER, message, duration_s=duration_s)
            return fail(ErrorKind.BAD_REQUEST, message, duration_s=duration_s)

        try:
            body = response.json()
            text = str(body["choices"][0]["message"]["content"] or "").strip()
        except (ValueError, KeyError, IndexError, TypeError):
            return fail(
                ErrorKind.SERVER,
                "Perplexity API sent an answer in an unexpected shape",
                duration_s=duration_s,
            )
        if not text:
            return fail(ErrorKind.EMPTY, "Perplexity API sent an empty answer", duration_s=duration_s)

        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        raw: dict[str, Any] = {
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "reasoning_tokens": usage.get("reasoning_tokens"),
        }
        raw = {k: v for k, v in raw.items() if _as_float(v) is not None}
        cost = usage.get("cost")
        total_cost = _as_float(cost.get("total_cost")) if isinstance(cost, dict) else _as_float(cost)
        if total_cost is not None:
            raw["cost_usd"] = total_cost

        parsed_json: Any = None
        if json_schema or text.startswith(("{", "[")):
            parsed_json = parse_json_or_none(text)

        result = self.build_exec_result(
            ok=True,
            text=redact(_with_sources(text, body)),
            ai="perplexity",
            model=str(body.get("model") or model_str),
            connection=req.connection,
            raw_data=raw,
            parsed_json=parsed_json,
            duration_s=duration_s,
        )
        if result.data is not None and total_cost is None:
            result.data["cost_estimated"] = True  # the API sent no cost: recorded as 0
        return result
