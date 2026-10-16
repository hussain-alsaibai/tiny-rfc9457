# tiny-rfc9457

**RFC 9457 Problem Details renderer and parser for Python.**

Stdlib only. MIT licensed. ~340 LOC. 21 tests.

```bash
pip install tiny-rfc9457
```

---

## What is RFC 9457?

RFC 9457 (July 2023, "Problem Details for HTTP APIs") is the successor
to RFC 7807. It defines a single JSON shape for *all* HTTP error
responses, so clients can parse error bodies uniformly instead of
reverse-engineering each API's error contract.

A Problem Details document:

```json
{
  "type": "urn:problem:validation-failed",
  "title": "Validation failed",
  "status": 422,
  "detail": "3 fields invalid",
  "instance": "/users",
  "errors": [
    { "pointer": "/email", "detail": "invalid format" }
  ]
}
```

Content-Type: `application/problem+json`.

---

## Quick start

```python
from tiny_rfc9457 import Problem, ProblemType, problem_response

p = Problem(
    type=ProblemType.RATE_LIMIT_EXCEEDED,
    title="Too Many Requests",
    status=429,
    detail="you've hit 100 RPM; retry in 23s",
    instance="/requests/abc123",
)

response = problem_response(p)  # drops into Flask, FastAPI, Django, Bottle...
# {"status": 429, "headers": {"Content-Type": "application/problem+json"}, "body": "..."}
```

---

## Public API

### `Problem(type, title, status, detail=None, instance=None, errors=[], extensions={})`

The canonical document. Frozen dataclass with `as_dict()`, `as_json()`,
`as_content_type()`. ``__post_init__`` enforces the spec:
- `status` must be 100–599.
- If `type == about:blank` and `title` is empty, the standard HTTP
  reason phrase is filled in.

### `SubProblem(type, detail, pointer, name)`

A nested sub-problem for the RFC 9457 §3.2 `errors` extension. Use
`pointer` for JSON-pointer-style paths (`/email`), `name` for form-field
names (`password`).

### `ProblemType`

A namespace of common problem-type URIs:
- `ABOUT_BLANK`, `RATE_LIMIT_EXCEEDED`, `VALIDATION_FAILED`,
  `NOT_FOUND`, `UNAUTHORIZED`, `FORBIDDEN`, `CONFLICT`, `INTERNAL`,
  `TIMEOUT`, `DEPENDENCY_FAILURE`

Add your own URIs as plain strings — preferably URLs you control.

### `problem_response(problem, *, request=None, body_bytes=False) -> dict`

Render a `Problem` into an HTTP-framework-ready dict:

```python
{
    "status": <int>,
    "headers": {"Content-Type": "application/problem+json"},
    "body": "<json or bytes>",
}
```

If `request` is passed and `problem.instance` is `None`, the request
path is used as the instance URI.

### `parse(payload) -> Problem`

Parse a Problem from a JSON string or bytes. Symmetric to `as_json()`.
Used by clients consuming problem responses.

### `from_exception(exc, *, instance=None) -> Problem`

Convert any Python exception into a Problem. Uses the registry below;
defaults to a 500 Internal for unmapped exceptions.

### `registry.register(ExceptionType)`

Decorator that maps an exception type to a `Problem` factory:

```python
from tiny_rfc9457 import registry, Problem, ProblemType

class MyError(Exception):
    pass

@registry.register(MyError)
def _to_problem(exc: MyError) -> Problem:
    return Problem(
        type=ProblemType.VALIDATION_FAILED,
        title="Validation failed",
        status=422,
        detail=str(exc),
    )
```

The registry walks the exception's MRO, so a base-class mapper catches
all subclasses.

---

## Why this, not a plain dict?

The spec is small but *particular*. Half-implementations get the
Content-Type wrong, forget that `type` defaults to `about:blank`, drop
the `status` mirror, or render `errors` as a dict instead of a list.
`tiny-rfc9457` enforces the spec and round-trips cleanly via
`as_json` ↔ `parse`.

---

## Framework integration

### Flask

```python
from flask import jsonify
from tiny_rfc9457 import Problem, problem_response

@app.errorhandler(404)
def _not_found(e):
    p = Problem(title="Not Found", status=404, detail=str(e))
    r = problem_response(p, request=request)
    return (r["body"], r["status"], r["headers"])
```

### FastAPI

```python
from fastapi import FastAPI
from tiny_rfc9457 import Problem, problem_response

app = FastAPI()

@app.exception_handler(ValueError)
async def _value_error(request, exc):
    p = Problem(title="Validation failed", status=422, detail=str(exc))
    r = problem_response(p)
    return JSONResponse(content=json.loads(r["body"]), status_code=r["status"], headers=r["headers"])
```

### Django

```python
from django.http import HttpResponse
from tiny_rfc9457 import Problem, problem_response

def problem_response_django(request, problem):
    r = problem_response(problem, request=request)
    return HttpResponse(r["body"], status=r["status"], content_type=r["headers"]["Content-Type"])
```

---

## Composition with `tiny-router`

```python
from tiny_router import App
from tiny_rfc9457 import Problem, problem_response

app = App()

@app.exception_handler(Exception)
def _render(exc, req):
    from tiny_rfc9457 import from_exception
    p = from_exception(exc, instance=req.path)
    r = problem_response(p)
    return r
```

---

## License

MIT — copy, fork, ship.
