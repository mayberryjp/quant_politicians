"""U.S. Senate eFD session + mandatory-agreement handshake (Senate Slice 1).

The eFD search app requires an authorized session before any search:

1. ``GET  /search/home/`` -> HTML carrying a ``csrfmiddlewaretoken`` (+ csrftoken cookie)
2. ``POST /search/home/`` with ``prohibition_agreement=1`` + the CSRF token -> session cookie

This module encapsulates that handshake, holds cookies in an ``httpx.Client``, optionally
persists the session to Redis (``qp:senate:session``) so it survives worker restarts, and
transparently re-authorizes when the eFD bounces an unauthorized request back to the
agreement page. See the compliance note in the Senate spec (issue #2, section 3.5).
"""

from __future__ import annotations

import logging
import re
import time

import httpx

from app.config import settings

logger = logging.getLogger("quant_politicians.senate.efd")
WORKER = "senate"

_CSRF_RE = re.compile(r'name=["\']csrfmiddlewaretoken["\'][^>]*value=["\']([^"\']+)["\']')
_CSRF_RE_ALT = re.compile(r'value=["\']([^"\']+)["\'][^>]*name=["\']csrfmiddlewaretoken["\']')


class EfdAuthError(Exception):
    """Raised when the eFD agreement handshake cannot be completed."""


def parse_csrf_token(html: str) -> str | None:
    match = _CSRF_RE.search(html) or _CSRF_RE_ALT.search(html)
    return match.group(1) if match else None


class EfdSession:
    """Authorized eFD HTTP session (agreement + CSRF + cookies)."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        user_agent: str | None = None,
        request_delay: float | None = None,
        state_repo=None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (base_url or settings.senate_efd_base_url).rstrip("/")
        self.user_agent = user_agent or settings.http_user_agent
        self.request_delay = settings.senate_request_delay if request_delay is None else request_delay
        self.state_repo = state_repo
        self._client = client or httpx.Client(timeout=httpx.Timeout(60.0), follow_redirects=False)
        self._csrf: str | None = None
        self._authorized = False
        self._restore_session()

    # ------------------------------------------------------------------
    def _headers(self, extra: dict | None = None) -> dict:
        headers = {"User-Agent": self.user_agent}
        if extra:
            headers.update(extra)
        return headers

    @property
    def csrf_token(self) -> str | None:
        return self._csrf

    @property
    def authorized(self) -> bool:
        return self._authorized

    # --- Redis persistence (optional) ---
    def _restore_session(self) -> None:
        if not self.state_repo:
            return
        data = self.state_repo.get_session(WORKER)
        if not data:
            return
        self._csrf = data.get("csrf")
        for name, value in (data.get("cookies") or {}).items():
            self._client.cookies.set(name, value)
        self._authorized = bool(data.get("authorized"))
        logger.info("restored eFD session from redis")

    def _persist_session(self) -> None:
        if not self.state_repo:
            return
        cookies = {c.name: c.value for c in self._client.cookies.jar}
        self.state_repo.set_session(
            WORKER,
            {"csrf": self._csrf, "cookies": cookies, "authorized": self._authorized, "schema_version": 1},
        )

    # --- Handshake ---
    def authorize(self) -> None:
        home = f"{self.base_url}/search/home/"
        resp = self._client.get(home, headers=self._headers())
        resp.raise_for_status()
        self._csrf = parse_csrf_token(resp.text)
        if not self._csrf:
            raise EfdAuthError("no csrfmiddlewaretoken on eFD home page")
        if self.request_delay:
            time.sleep(self.request_delay)
        accepted = self._client.post(
            home,
            data={"csrfmiddlewaretoken": self._csrf, "prohibition_agreement": "1"},
            headers=self._headers({"Referer": home}),
        )
        accepted.raise_for_status()
        self._authorized = True
        self._persist_session()
        logger.info("eFD agreement accepted; session authorized")

    def ensure_authorized(self) -> None:
        if not self._authorized:
            self.authorize()

    # --- Request with transparent re-auth ---
    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        self.ensure_authorized()
        url = f"{self.base_url}{path}"
        extra_headers = kwargs.pop("headers", None)
        resp = self._client.request(method, url, headers=self._headers(extra_headers), **kwargs)
        if self._is_agreement_bounce(resp):
            logger.info("eFD session expired; re-authorizing and retrying")
            self._authorized = False
            self.authorize()
            resp = self._client.request(method, url, headers=self._headers(extra_headers), **kwargs)
        return resp

    def _is_agreement_bounce(self, resp: httpx.Response) -> bool:
        if resp.status_code in (301, 302, 303, 307, 308):
            return "/search/home" in resp.headers.get("location", "")
        if resp.status_code == 200 and "prohibition_agreement" in resp.text:
            return True
        return False

    def close(self) -> None:
        self._client.close()
