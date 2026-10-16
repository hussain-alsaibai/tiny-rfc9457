"""Tests for tiny_rfc9457 v0.1.0."""

from __future__ import annotations

import json
import unittest

from tiny_rfc9457 import (
    CONTENT_TYPE,
    Problem,
    ProblemType,
    SubProblem,
    __version__,
    from_exception,
    parse,
    problem_response,
    registry,
)


class TestVersion(unittest.TestCase):
    def test_version(self) -> None:
        self.assertEqual(__version__, "0.1.0")


class TestConstruction(unittest.TestCase):
    def test_minimal(self) -> None:
        p = Problem(title="Bad Request", status=400)
        self.assertEqual(p.type, ProblemType.ABOUT_BLANK)
        self.assertEqual(p.title, "Bad Request")
        self.assertEqual(p.status, 400)

    def test_defaults_title_for_about_blank(self) -> None:
        p = Problem(status=404)
        self.assertEqual(p.type, ProblemType.ABOUT_BLANK)
        self.assertEqual(p.title, "Not Found")

    def test_keeps_custom_title_with_explicit_type(self) -> None:
        p = Problem(type=ProblemType.NOT_FOUND, title="User missing", status=404)
        self.assertEqual(p.title, "User missing")

    def test_rejects_bad_status(self) -> None:
        with self.assertRaises(ValueError):
            Problem(status=99)
        with self.assertRaises(ValueError):
            Problem(status=600)


class TestAsDict(unittest.TestCase):
    def test_minimal_dict(self) -> None:
        p = Problem(title="Bad Request", status=400)
        d = p.as_dict()
        self.assertEqual(d["type"], "about:blank")
        self.assertEqual(d["title"], "Bad Request")
        self.assertEqual(d["status"], 400)
        self.assertNotIn("detail", d)
        self.assertNotIn("instance", d)
        self.assertNotIn("errors", d)

    def test_full_dict(self) -> None:
        p = Problem(
            type=ProblemType.VALIDATION_FAILED,
            title="Validation failed",
            status=422,
            detail="3 fields invalid",
            instance="/users",
            errors=[
                SubProblem(pointer="/email", detail="invalid format"),
                SubProblem(name="password", detail="too short"),
            ],
            extensions={"trace_id": "abc"},
        )
        d = p.as_dict()
        self.assertEqual(d["type"], ProblemType.VALIDATION_FAILED)
        self.assertEqual(d["status"], 422)
        self.assertEqual(d["trace_id"], "abc")
        self.assertEqual(len(d["errors"]), 2)
        self.assertEqual(d["errors"][0]["pointer"], "/email")

    def test_as_json_round_trip(self) -> None:
        p = Problem(
            type=ProblemType.RATE_LIMIT_EXCEEDED,
            title="Too Many",
            status=429,
            detail="try in 23s",
            instance="/api/x",
        )
        body = p.as_json()
        loaded = json.loads(body)
        self.assertEqual(loaded["status"], 429)
        self.assertEqual(loaded["type"], ProblemType.RATE_LIMIT_EXCEEDED)


class TestProblemResponse(unittest.TestCase):
    def test_returns_status_headers_body(self) -> None:
        p = Problem(title="Bad Request", status=400, detail="oops")
        r = problem_response(p)
        self.assertEqual(r["status"], 400)
        self.assertEqual(r["headers"]["Content-Type"], CONTENT_TYPE)
        self.assertIn("Bad Request", r["body"])

    def test_uses_request_path_for_instance(self) -> None:
        class Req:
            path = "/api/users/42"

        p = Problem(title="Not Found", status=404, detail="missing")
        r = problem_response(p, request=Req())
        body = json.loads(r["body"])
        self.assertEqual(body["instance"], "/api/users/42")

    def test_preserves_explicit_instance(self) -> None:
        class Req:
            path = "/api/users/42"

        p = Problem(title="Not Found", status=404, instance="/specific/abc")
        r = problem_response(p, request=Req())
        body = json.loads(r["body"])
        self.assertEqual(body["instance"], "/specific/abc")

    def test_body_bytes_mode(self) -> None:
        p = Problem(title="Bad Request", status=400)
        r = problem_response(p, body_bytes=True)
        self.assertIsInstance(r["body"], bytes)


class TestParse(unittest.TestCase):
    def test_parse_round_trip(self) -> None:
        original = Problem(
            type=ProblemType.VALIDATION_FAILED,
            title="Validation failed",
            status=422,
            detail="oops",
            instance="/u",
            errors=[SubProblem(pointer="/email", detail="bad")],
            extensions={"trace_id": "x"},
        )
        body = original.as_json()
        parsed = parse(body)
        self.assertEqual(parsed.status, 422)
        self.assertEqual(parsed.type, ProblemType.VALIDATION_FAILED)
        self.assertEqual(parsed.detail, "oops")
        self.assertEqual(len(parsed.errors), 1)
        self.assertEqual(parsed.errors[0].pointer, "/email")
        self.assertEqual(parsed.extensions["trace_id"], "x")

    def test_parse_accepts_bytes(self) -> None:
        body = b'{"type":"about:blank","title":"Bad Request","status":400}'
        p = parse(body)
        self.assertEqual(p.status, 400)

    def test_parse_rejects_non_object(self) -> None:
        with self.assertRaises(ValueError):
            parse("[1, 2, 3]")

    def test_parse_fills_defaults(self) -> None:
        p = parse('{"status": 404}')
        self.assertEqual(p.type, ProblemType.ABOUT_BLANK)
        self.assertEqual(p.title, "Not Found")


class TestExceptionRegistry(unittest.TestCase):
    def test_default_500_for_unmapped(self) -> None:
        class MyError(Exception):
            pass

        p = from_exception(MyError("kaboom"))
        self.assertEqual(p.status, 500)
        self.assertEqual(p.title, "Internal Server Error")
        self.assertEqual(p.detail, "kaboom")

    def test_registered_exception(self) -> None:
        class MyError(Exception):
            pass

        @registry.register(MyError)
        def _to_problem(exc: Exception) -> Problem:
            return Problem(
                type=ProblemType.VALIDATION_FAILED,
                title="Custom",
                status=422,
                detail=str(exc),
            )

        p = from_exception(MyError("bad"))
        self.assertEqual(p.status, 422)
        self.assertEqual(p.title, "Custom")
        # Cleanup for test isolation.
        del registry._mappers[MyError]

    def test_mro_walks_up(self) -> None:
        class Base(Exception):
            pass

        class Sub(Base):
            pass

        @registry.register(Base)
        def _to_problem(exc: Exception) -> Problem:
            return Problem(type=ProblemType.NOT_FOUND, title="base mapped", status=404)

        p = from_exception(Sub())
        self.assertEqual(p.title, "base mapped")
        del registry._mappers[Base]

    def test_instance_passed_through(self) -> None:
        p = from_exception(ValueError("bad"), instance="/api/x")
        self.assertEqual(p.instance, "/api/x")


class TestContentType(unittest.TestCase):
    def test_content_type_constant(self) -> None:
        self.assertEqual(CONTENT_TYPE, "application/problem+json")


if __name__ == "__main__":
    unittest.main()
