"""In-memory stand-in for KeitaroClient.

Used by the tests and by ``KEITARO_MOCK=1`` so the UI can be tried without an API key.
Mimics the subset of Admin API v1 behaviour this service relies on.
"""
from __future__ import annotations

import copy
import itertools

from app.keitaro import KeitaroError


class FakeKeitaro:
    def __init__(self):
        self._ids = itertools.count(1000)
        self.domains = [{"id": 1, "name": "kylovoa.com"}, {"id": 2, "name": "other.example"}]
        self.groups = [{"id": 10, "name": "FORTESTS"}, {"id": 11, "name": "Other"}]
        self.sources = [{"id": 20, "name": "FORTESTS"}, {"id": 21, "name": "Facebook"}]
        self.offers = {
            3749: {"id": 3749, "name": "Miaflow [BEAUTY-RO-BE_0009] spin ro", "state": "active"},
            3717: {"id": 3717, "name": "Miaflow [BEAUTY-RO-BE_0008] prize ro", "state": "active"},
            3800: {"id": 3800, "name": "Demo offer AU", "state": "active"},
            3801: {"id": 3801, "name": "Demo offer MX", "state": "active"},
        }
        self.campaigns: dict[int, dict] = {}
        self.streams: dict[int, dict] = {}
        self.report_rows: dict[tuple, list[dict]] = {}  # (dims...) -> rows

    # dictionaries
    def list_domains(self):
        return copy.deepcopy(self.domains)

    def list_groups(self, group_type="campaigns"):
        return copy.deepcopy(self.groups)

    def list_traffic_sources(self):
        return copy.deepcopy(self.sources)

    def list_offers(self):
        return copy.deepcopy(list(self.offers.values()))

    def get_offer(self, offer_id):
        if offer_id not in self.offers:
            raise KeitaroError(404, "Not found")
        return copy.deepcopy(self.offers[offer_id])

    # campaigns
    def list_campaigns(self):
        return copy.deepcopy(list(self.campaigns.values()))

    def get_campaign(self, campaign_id):
        if campaign_id not in self.campaigns:
            raise KeitaroError(404, "Not found")
        return copy.deepcopy(self.campaigns[campaign_id])

    def create_campaign(self, payload):
        cid = next(self._ids)
        domain = next((d["name"] for d in self.domains if d["id"] == payload.get("domain_id")), "")
        self.campaigns[cid] = {"id": cid, **payload, "domain": f"https://{domain}/" if domain else ""}
        return copy.deepcopy(self.campaigns[cid])

    def delete_campaign(self, campaign_id):
        self.campaigns.pop(campaign_id, None)
        for sid in [s for s, v in self.streams.items() if v["campaign_id"] == campaign_id]:
            del self.streams[sid]

    # streams
    def list_streams(self, campaign_id):
        return copy.deepcopy([s for s in self.streams.values() if s["campaign_id"] == campaign_id])

    def get_stream(self, stream_id):
        if stream_id not in self.streams:
            raise KeitaroError(404, "Not found")
        return copy.deepcopy(self.streams[stream_id])

    def _offers(self, stream_id, offers):
        return [{"id": next(self._ids), "stream_id": stream_id, **o} for o in offers]

    def create_stream(self, payload):
        if payload["campaign_id"] not in self.campaigns:
            raise KeitaroError(422, "campaign_id is invalid")
        sid = next(self._ids)
        stream = {"id": sid, **copy.deepcopy(payload)}
        stream["offers"] = self._offers(sid, payload.get("offers") or [])
        self.streams[sid] = stream
        return copy.deepcopy(stream)

    def update_stream(self, stream_id, payload):
        if stream_id not in self.streams:
            raise KeitaroError(404, "Not found")
        payload = copy.deepcopy(payload)
        if "offers" in payload:
            for o in payload["offers"]:
                if o["offer_id"] not in self.offers:
                    raise KeitaroError(422, f"offer {o['offer_id']} not found")
            payload["offers"] = self._offers(stream_id, payload["offers"])
        self.streams[stream_id].update(payload)
        return copy.deepcopy(self.streams[stream_id])

    # reports
    def build_report(self, payload):
        cid = payload["filters"][0]["expression"]
        rows = [r for r in self.report_rows.get(tuple(payload["dimensions"]), []) if r.get("campaign_id", cid) == cid]
        return {"rows": copy.deepcopy(rows), "total": len(rows)}


def seed_demo(fake: FakeKeitaro) -> None:
    """A campaign like the one on the task screenshot, with some fake traffic."""
    c = fake.create_campaign({"name": "test_demo_campaign", "alias": "wVqN1R", "state": "active", "domain_id": 1})
    g = fake.create_stream({
        "campaign_id": c["id"], "name": "Flow 1", "type": "regular", "position": 1, "schema": "redirect",
        "action_type": "http", "action_payload": "https://google.com",
        "filters": [{"name": "country", "mode": "accept", "payload": ["AU"]}],
    })
    o = fake.create_stream({
        "campaign_id": c["id"], "name": "Flow 2", "type": "regular", "position": 2, "schema": "landings",
        "offers": [{"offer_id": 3749, "share": 50, "state": "active"}, {"offer_id": 3717, "share": 50, "state": "active"}],
    })
    # stream_id=0: clicks that matched no stream (real Keitaro reports them like this)
    fake.report_rows[("stream_id",)] = [
        {"campaign_id": c["id"], "stream_id": 0, "clicks": 10, "campaign_unique_clicks": 1, "stream_unique_clicks": 0, "bots": 8},
        {"campaign_id": c["id"], "stream_id": g["id"], "clicks": 42, "campaign_unique_clicks": 37, "stream_unique_clicks": 38, "bots": 3},
        {"campaign_id": c["id"], "stream_id": o["id"], "clicks": 120, "campaign_unique_clicks": 101, "stream_unique_clicks": 104, "bots": 6, "conversions": 4, "revenue": 88.5},
    ]
    fake.report_rows[("stream_id", "offer_id")] = [
        {"campaign_id": c["id"], "stream_id": o["id"], "offer_id": 3749, "clicks": 61, "stream_unique_clicks": 53, "conversions": 3, "revenue": 66.0},
        {"campaign_id": c["id"], "stream_id": o["id"], "offer_id": 3717, "clicks": 59, "stream_unique_clicks": 51, "conversions": 1, "revenue": 22.5},
    ]
