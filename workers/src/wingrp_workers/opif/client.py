"""Polite client for the FCC Online Public Inspection File (OPIF) JSON API.

Rules (see CLAUDE.md "Data-source rules"):
- Identify ourselves with a descriptive User-Agent that includes contact info.
- Throttle requests and back off on server errors.
- Never work around bot protection. A 403 or 429 stops the run; it is not retried.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from datetime import date, timedelta

import httpx

BASE = "https://publicfiles.fcc.gov"
DEFAULT_MIN_INTERVAL_S = 1.0
HISTORY_PAGE_SIZE = 500


class OpifBlocked(RuntimeError):
    """FCC refused the request (403/429). Stop and raise it with a human."""


class OpifClient:
    def __init__(
        self,
        user_agent: str | None = None,
        min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        user_agent = user_agent or os.environ.get("WINGRP_USER_AGENT")
        if not user_agent:
            raise ValueError(
                "Set WINGRP_USER_AGENT, e.g. 'win-grp/0.1 (contact: you@example.com)'"
            )
        self._http = httpx.Client(
            base_url=BASE,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=60.0,
            transport=transport,
        )
        self._min_interval_s = min_interval_s
        self._last_request = 0.0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> OpifClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _get_json(self, path: str, params: dict | None = None) -> dict:
        for attempt in range(4):
            wait = self._min_interval_s - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                resp = self._http.get(path, params=params)
            except httpx.TransportError:
                if attempt == 3:
                    raise
                time.sleep(2**attempt * 5)
                continue
            if resp.status_code in (403, 429):
                raise OpifBlocked(f"{resp.status_code} from {resp.request.url}")
            if resp.status_code >= 500:
                if attempt == 3:
                    resp.raise_for_status()
                time.sleep(2**attempt * 5)
                continue
            resp.raise_for_status()
            return resp.json()
        raise AssertionError("unreachable")

    def download(self, folder_id: str, file_manager_id: str, ext: str = "pdf") -> bytes:
        """Download one file. Uses file_manager_id (file_id returns 400).

        The endpoint 302s to files.fcc.gov. Same throttle and same rule as the
        JSON calls: a 403/429 raises OpifBlocked and is not retried.
        """
        wait = self._min_interval_s - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        resp = self._http.get(
            f"/api/manager/download/{folder_id}/{file_manager_id}.{ext}",
            headers={"Accept": "*/*"},
            follow_redirects=True,
        )
        if resp.status_code in (403, 429):
            raise OpifBlocked(f"{resp.status_code} from {resp.request.url}")
        resp.raise_for_status()
        return resp.content

    def search_facility(self, keyword: str) -> dict:
        """Facility search. Note: no `.json` suffix (with it, FCC returns no results)."""
        data = self._get_json(f"/api/service/facility/search/{keyword}")
        return data["results"]["globalSearchResults"]

    def file_history(
        self, start: date, end: date, entity_id: str | None = None
    ) -> Iterator[dict]:
        """Yield change-feed rows (new/delete/purge) between start and end, inclusive.

        Walks the range in 31-day windows and pages within each window.
        """
        window_start = start
        while window_start <= end:
            window_end = min(window_start + timedelta(days=30), end)
            offset = 0
            while True:
                params = {
                    "startDate": window_start.isoformat(),
                    "endDate": window_end.isoformat(),
                    "count": HISTORY_PAGE_SIZE,
                    "offset": offset,
                }
                if entity_id:
                    params["entityId"] = entity_id
                data = self._get_json("/api/manager/file/history.json", params)
                rows = data.get("history") or []
                yield from rows
                if len(rows) < HISTORY_PAGE_SIZE:
                    break
                offset += HISTORY_PAGE_SIZE
            window_start = window_end + timedelta(days=1)
