"""Thin synchronous client for the Keitaro Admin API v1.

Docs: <keitaro>/admin_api/v1/docs  (auth header: ``Api-Key``).
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import Settings, get_settings


class KeitaroError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(f"Keitaro API error {status_code}: {message}")
        self.status_code = status_code
        self.message = message


class KeitaroClient:
    def __init__(self, settings: Settings | None = None, transport: httpx.BaseTransport | None = None):
        self.settings = settings or get_settings()
        self._http = httpx.Client(
            base_url=self.settings.keitaro_api_base,
            headers={"Api-Key": self.settings.keitaro_api_key, "Accept": "application/json"},
            timeout=self.settings.keitaro_timeout,
            transport=transport,
        )

    # --- low level -------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise KeitaroError(502, f"cannot reach Keitaro: {exc}") from exc
        if resp.status_code >= 400:
            try:
                body = resp.json()
                message = body.get("error") or body.get("message") or str(body)
            except ValueError:
                message = resp.text[:500]
            raise KeitaroError(resp.status_code, message)
        if not resp.content:
            return None
        return resp.json()

    # --- dictionaries ----------------------------------------------------
    def list_domains(self) -> list[dict]:
        return self._request("GET", "/domains")

    def list_groups(self, group_type: str = "campaigns") -> list[dict]:
        return self._request("GET", "/groups", params={"type": group_type})

    def list_traffic_sources(self) -> list[dict]:
        return self._request("GET", "/traffic_sources")

    def list_offers(self) -> list[dict]:
        return self._request("GET", "/offers")

    def get_offer(self, offer_id: int) -> dict:
        return self._request("GET", f"/offers/{offer_id}")

    # --- campaigns -------------------------------------------------------
    def list_campaigns(self) -> list[dict]:
        return self._request("GET", "/campaigns")

    def get_campaign(self, campaign_id: int) -> dict:
        return self._request("GET", f"/campaigns/{campaign_id}")

    def create_campaign(self, payload: dict) -> dict:
        return self._request("POST", "/campaigns", json=payload)

    def delete_campaign(self, campaign_id: int) -> None:
        self._request("DELETE", f"/campaigns/{campaign_id}")

    # --- streams ---------------------------------------------------------
    def list_streams(self, campaign_id: int) -> list[dict]:
        return self._request("GET", f"/campaigns/{campaign_id}/streams")

    def get_stream(self, stream_id: int) -> dict:
        return self._request("GET", f"/streams/{stream_id}")

    def create_stream(self, payload: dict) -> dict:
        return self._request("POST", "/streams", json=payload)

    def update_stream(self, stream_id: int, payload: dict) -> dict:
        return self._request("PUT", f"/streams/{stream_id}", json=payload)

    # --- reports ---------------------------------------------------------
    def build_report(self, payload: dict) -> dict:
        return self._request("POST", "/report/build", json=payload)


class OfferCache:
    """Offers list rarely changes; cache it so autocomplete stays snappy."""

    def __init__(self, ttl: float = 60.0):
        self.ttl = ttl
        self._data: list[dict] | None = None
        self._at = 0.0

    def get(self, client: KeitaroClient) -> list[dict]:
        if self._data is None or time.monotonic() - self._at > self.ttl:
            self._data = client.list_offers()
            self._at = time.monotonic()
        return self._data

    def clear(self) -> None:
        self._data = None


offer_cache = OfferCache()


_fake = None


def get_keitaro() -> KeitaroClient:
    settings = get_settings()
    if settings.keitaro_mock:
        global _fake
        if _fake is None:
            from app.fake_keitaro import FakeKeitaro, seed_demo

            _fake = FakeKeitaro()
            seed_demo(_fake)
        return _fake  # type: ignore[return-value]
    return KeitaroClient(settings)
