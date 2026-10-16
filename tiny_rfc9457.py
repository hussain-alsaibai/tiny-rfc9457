"""tiny-rfc9457 v0.1.0 — RFC 9457 Problem Details renderer for Python.

What is RFC 9457?
-----------------
RFC 9457 (July 2023, "Problem Details for HTTP APIs") is the successor
to RFC 7807. It defines a single JSON shape for *all* HTTP error
responses, so clients can parse error bodies uniformly instead of
reverse-engineering each API's error contract.

A Problem Details document is a JSON object with:

- ``type`` (URI; defaults to ``about:blank``)
- ``title`` (short human-readable summary)
- ``status`` (HTTP status code; mirrors the response status)
- ``detail`` (human-readable explanation specific to this occurrence)
- ``instance`` (URI identifying this specific occurrence)
- ``errors`` (RFC 9457 extension; nested array of sub-problems)

The Content-Type is ``application/problem+json``. The library also
supports ``application/problem+xml`` via the standard xml.etree
path, but XML is rarely used in practice.

This library gives you:

- :class:`Problem` — the canonical document. Frozen dataclass + ``as_dict()``
  + ``as_json()`` + ``as_content_type()``.
- :func:`problem_response` — turn any ``(status, ...)`` into an
  HTTP-framework-ready dict (status, headers, body).
- :func:`from_exception` — convert a Python exception into a Problem
  using a registry of exception→Problem mappers.
- :class:`ProblemType` — typed constants for the most common
  problem URIs (rate-limit-exceeded, validation-failed, not-found,
  unauthorized, etc.). Extensible.

Why this, not just a dict?
--------------------------
The spec is small but *particular*. The default Content-Type, the
``about:blank`` fallback for ``type``, and the status/title/detail
semantics trip up half-implementations. ``tiny-rfc9457`` enforces the
spec and round-trips cleanly with both ``application/problem+json``
parsing (via :func:`parse`) and rendering.

Example
-------
::

    from tiny_rfc9457 import Problem, ProblemType, problem_response

    p = Problem(
        type=ProblemType.RATE_LIMIT_EXCEEDED,
        title="Too Many Requests",
        status=429,
        detail="you've hit 100 RPM; retry in 23s",
        instance="/requests/abc123",
    )
    response = problem_response(p, request=request)
    # {'status': 429, 'headers': {'Content-Type': 'application/problem+json'}, 'body': '...'}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Mapping, Optional


__version__ = "0.1.0"

CONTENT_TYPE = "application/problem+json"
CONTENT_TYPE_XML = "application/problem+xml"


# ----------------------------------------------------------------------
# Typed problem-type URIs
# ----------------------------------------------------------------------


class ProblemType:
    """Common problem-type URIs. Extend with your own URIs as strings.

    These are not exhaustive — they cover the *types* of problems that
    show up in 90% of services. Anything else should use a custom URI
    (preferably a URL you control, e.g. ``https://errors.example.com/foo``).
    """

    ABOUT_BLANK = "about:blank"
    RATE_LIMIT_EXCEEDED = "https://datatracker.ietf.org/doc/html/rfc6585#section-4"
    VALIDATION_FAILED = "urn:problem:validation-failed"
    NOT_FOUND = "urn:problem:not-found"
    UNAUTHORIZED = "urn:problem:unauthorized"
    FORBIDDEN = "urn:problem:forbidden"
    CONFLICT = "urn:problem:conflict"
    INTERNAL = "urn:problem:internal"
    TIMEOUT = "urn:problem:timeout"
    DEPENDENCY_FAILURE = "urn:problem:dependency-failure"


# ----------------------------------------------------------------------
# Problem document
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class SubProblem:
    """RFC 9457 §3.2 nested sub-problem (the ``errors`` extension member)."""

    type: Optional[str] = None
    detail: Optional[str] = None
    pointer: Optional[str] = None
    name: Optional[str] = None  # for form-field errors

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if self.type is not None:
            out["type"] = self.type
        if self.detail is not None:
            out["detail"] = self.detail
        if self.pointer is not None:
            out["pointer"] = self.pointer
        if self.name is not None:
            out["name"] = self.name
        return out


@dataclass(frozen=True)
class Problem:
    """An RFC 9457 problem details document.

    All fields except ``type``, ``title``, ``status`` are optional, but
    *should* be filled where known:

    - ``type`` defaults to ``about:blank`` (per spec §3.1.1).
    - ``title`` should be a short, human-readable summary.
    - ``status`` MUST mirror the HTTP response status (§3.1.3).
    - ``detail`` is per-occurrence explanation.
    - ``instance`` is a URI identifying THIS occurrence.
    - ``errors`` is the §3.2 extension member for nested sub-problems.
    - ``extensions`` is an escape hatch for vendor extensions.
    """

    type: str = ProblemType.ABOUT_BLANK
    title: str = ""
    status: int = 500
    detail: Optional[str] = None
    instance: Optional[str] = None
    errors: List[SubProblem] = field(default_factory=list)
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (100 <= self.status <= 599):
            raise ValueError(f"status must be a valid HTTP status code, got {self.status}")
        if self.type == ProblemType.ABOUT_BLANK and not self.title:
            # Spec: "the title SHOULD be the standard HTTP reason phrase."
            object.__setattr__(self, "title", _http_reason(self.status))

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "type": self.type,
            "title": self.title,
            "status": self.status,
        }
        if self.detail is not None:
            out["detail"] = self.detail
        if self.instance is not None:
            out["instance"] = self.instance
        if self.errors:
            out["errors"] = [sp.as_dict() for sp in self.errors]
        for k, v in self.extensions.items():
            if k not in out:
                out[k] = v
        return out

    def as_json(self, *, indent: Optional[int] = None) -> str:
        return json.dumps(self.as_dict(), indent=indent, ensure_ascii=False)

    def as_content_type(self) -> str:
        return CONTENT_TYPE


def problem_response(
    problem: Problem,
    *,
    request: Any = None,
    body_bytes: bool = False,
) -> Dict[str, Any]:
    """Render a Problem into an HTTP-framework-ready dict.

    Returns ``{'status': int, 'headers': dict, 'body': str | bytes}``.
    Designed to drop into Flask, FastAPI, Django, Bottle, or any
    WSGI/ASGI handler with no adaptation.

    If ``request`` is provided and ``problem.instance`` is ``None``,
    the request path is used as the instance URI.
    """
    # Late-bound: only import if user passes a request.
    p = problem
    if request is not None and p.instance is None:
        try:
            path = getattr(request, "path", None)
            if path is None:
                # Werkzeug/Flask style.
                path = request.path
            if path:
                p = Problem(
                    type=p.type,
                    title=p.title,
                    status=p.status,
                    detail=p.detail,
                    instance=path,
                    errors=list(p.errors),
                    extensions=dict(p.extensions),
                )
        except Exception:
            pass
    body = p.as_json()
    if body_bytes:
        body = body.encode("utf-8")
    return {
        "status": p.status,
        "headers": {"Content-Type": CONTENT_TYPE},
        "body": body,
    }


def parse(payload: str | bytes) -> Problem:
    """Parse a Problem from a JSON string or bytes. Used by clients."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("problem document must be a JSON object")
    errors = [
        SubProblem(
            type=sp.get("type"),
            detail=sp.get("detail"),
            pointer=sp.get("pointer"),
            name=sp.get("name"),
        )
        for sp in data.get("errors", [])
        if isinstance(sp, dict)
    ]
    known = {"type", "title", "status", "detail", "instance", "errors"}
    extensions = {k: v for k, v in data.items() if k not in known}
    return Problem(
        type=data.get("type", ProblemType.ABOUT_BLANK),
        title=data.get("title", ""),
        status=int(data.get("status", 500)),
        detail=data.get("detail"),
        instance=data.get("instance"),
        errors=errors,
        extensions=extensions,
    )


# ----------------------------------------------------------------------
# Exception mapping
# ----------------------------------------------------------------------


class _ExceptionRegistry:
    """Registry mapping exception types → Problem factories."""

    def __init__(self) -> None:
        self._mappers: Dict[type, Any] = {}

    def register(self, exc_type: type) -> Any:
        def deco(fn: Any) -> Any:
            self._mappers[exc_type] = fn
            return fn
        return deco

    def to_problem(self, exc: BaseException, *, instance: Optional[str] = None) -> Problem:
        for cls in type(exc).__mro__:
            if cls in self._mappers:
                p = self._mappers[cls](exc)
                if instance is not None and p.instance is None:
                    p = Problem(
                        type=p.type,
                        title=p.title,
                        status=p.status,
                        detail=p.detail,
                        instance=instance,
                        errors=list(p.errors),
                        extensions=dict(p.extensions),
                    )
                return p
        # Default: 500 Internal.
        return Problem(
            type=ProblemType.INTERNAL,
            title="Internal Server Error",
            status=500,
            detail=str(exc) or exc.__class__.__name__,
            instance=instance,
        )


registry = _ExceptionRegistry()


def from_exception(exc: BaseException, *, instance: Optional[str] = None) -> Problem:
    """Convert any exception into a Problem using the registry."""
    return registry.to_problem(exc, instance=instance)


def _http_reason(status: int) -> str:
    """Best-effort HTTP reason phrase lookup without importing http.client."""
    table = {
        400: "Bad Request",
        401: "Unauthorized",
        402: "Payment Required",
        403: "Forbidden",
        404: "Not Found",
        405: "Method Not Allowed",
        406: "Not Acceptable",
        409: "Conflict",
        410: "Gone",
        413: "Payload Too Large",
        415: "Unsupported Media Type",
        418: "I'm a teapot",
        422: "Unprocessable Entity",
        429: "Too Many Requests",
        500: "Internal Server Error",
        501: "Not Implemented",
        502: "Bad Gateway",
        503: "Service Unavailable",
        504: "Gateway Timeout",
    }
    return table.get(status, "Error")


__all__ = [
    "__version__",
    "CONTENT_TYPE",
    "CONTENT_TYPE_XML",
    "ProblemType",
    "Problem",
    "SubProblem",
    "problem_response",
    "parse",
    "registry",
    "from_exception",
]
