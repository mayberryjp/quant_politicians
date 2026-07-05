"""Senate Slice 1 tests: eFD CSRF parse, agreement handshake, re-auth, persistence."""

from __future__ import annotations

import httpx
import pytest

from app.redis.repository import StateRepository
from app.services.senate_efd import EfdAuthError, EfdSession, parse_csrf_token

HOME_HTML = (
    '<html><body><form method="post">'
    '<input type="hidden" name="csrfmiddlewaretoken" value="TESTCSRF123">'
    "</form></body></html>"
)


def make_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


class TestCsrf:
    def test_parse(self):
        assert parse_csrf_token(HOME_HTML) == "TESTCSRF123"

    def test_parse_missing(self):
        assert parse_csrf_token("<html>no token here</html>") is None


class TestAuthorize:
    def test_handshake_get_then_post_agreement(self):
        calls = []

        def handler(request):
            calls.append((request.method, request.url.path))
            if request.method == "GET" and request.url.path == "/search/home/":
                return httpx.Response(200, text=HOME_HTML, headers={"set-cookie": "csrftoken=abc; Path=/"})
            if request.method == "POST" and request.url.path == "/search/home/":
                body = request.content.decode()
                assert "prohibition_agreement=1" in body
                assert "csrfmiddlewaretoken=TESTCSRF123" in body
                return httpx.Response(200, text="ok")
            return httpx.Response(404)

        sess = EfdSession(base_url="https://efd.test", request_delay=0, client=make_client(handler))
        sess.authorize()
        assert sess.authorized is True
        assert sess.csrf_token == "TESTCSRF123"
        assert ("GET", "/search/home/") in calls
        assert ("POST", "/search/home/") in calls

    def test_missing_csrf_raises(self):
        def handler(request):
            return httpx.Response(200, text="<html>no token</html>")

        sess = EfdSession(base_url="https://efd.test", request_delay=0, client=make_client(handler))
        with pytest.raises(EfdAuthError):
            sess.authorize()

    def test_ensure_authorized_is_idempotent(self):
        calls = []

        def handler(request):
            calls.append((request.method, request.url.path))
            if request.method == "GET":
                return httpx.Response(200, text=HOME_HTML)
            return httpx.Response(200, text="ok")

        sess = EfdSession(base_url="https://efd.test", request_delay=0, client=make_client(handler))
        sess.ensure_authorized()
        sess.ensure_authorized()
        assert calls.count(("GET", "/search/home/")) == 1


class TestReauth:
    def test_reauth_on_agreement_bounce(self):
        state = {"expired": True}
        calls = []

        def handler(request):
            calls.append((request.method, request.url.path))
            path = request.url.path
            if path == "/search/home/":
                return httpx.Response(200, text=HOME_HTML) if request.method == "GET" else httpx.Response(200, text="ok")
            if path == "/search/report/data/":
                if state["expired"]:
                    state["expired"] = False
                    return httpx.Response(302, headers={"location": "/search/home/"})
                return httpx.Response(200, text='{"data": []}')
            return httpx.Response(404)

        sess = EfdSession(base_url="https://efd.test", request_delay=0, client=make_client(handler))
        resp = sess.request("GET", "/search/report/data/")
        assert resp.status_code == 200
        assert calls.count(("GET", "/search/report/data/")) == 2  # bounce then retry


class TestPersistence:
    def test_persist_and_restore(self, fake_redis):
        repo = StateRepository(fake_redis)

        def handler(request):
            if request.method == "GET":
                return httpx.Response(200, text=HOME_HTML, headers={"set-cookie": "csrftoken=abc; Path=/"})
            return httpx.Response(200, text="ok")

        sess = EfdSession(base_url="https://efd.test", request_delay=0, state_repo=repo, client=make_client(handler))
        sess.authorize()
        data = repo.get_session("senate")
        assert data and data["authorized"] is True
        assert data["csrf"] == "TESTCSRF123"

        # A fresh session restores authorization from Redis without re-handshaking.
        restored = EfdSession(base_url="https://efd.test", request_delay=0, state_repo=repo, client=make_client(handler))
        assert restored.authorized is True
        assert restored.csrf_token == "TESTCSRF123"
