"""Business logic: campaign creation and offer editing on top of Keitaro."""
from __future__ import annotations

import logging
import secrets
import string

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.keitaro import KeitaroClient, KeitaroError
from app.models import Campaign, OfferChange
from app.schemas import CampaignCreate

log = logging.getLogger(__name__)


class ServiceError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# --------------------------------------------------------------------------
# Shares
# --------------------------------------------------------------------------
def split_shares(n: int) -> list[int]:
    """Split 100% into ``n`` integer parts that always sum to exactly 100.

    3 -> [34, 33, 33], 6 -> [17, 17, 17, 17, 16, 16]
    """
    if n <= 0:
        return []
    base, rest = divmod(100, n)
    return [base + 1 if i < rest else base for i in range(n)]


def _clean_offers(offers: list[dict]) -> list[dict]:
    """Keep only the fields Keitaro accepts when writing stream offers."""
    return [
        {"offer_id": int(o["offer_id"]), "share": int(o.get("share") or 0), "state": o.get("state") or "active"}
        for o in offers
    ]


def _redistribute(offers: list[dict]) -> list[dict]:
    shares = split_shares(len(offers))
    return [{**o, "share": s} for o, s in zip(offers, shares)]


def is_offer_stream(stream: dict) -> bool:
    return stream.get("schema") == "landings" or bool(stream.get("offers"))


# --------------------------------------------------------------------------
# Name / alias helpers
# --------------------------------------------------------------------------
def apply_prefix(name: str, prefix: str) -> str:
    return name if not prefix or name.startswith(prefix) else prefix + name


def generate_alias(length: int = 7) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _find_by_name(items: list[dict], name: str, *keys: str) -> dict | None:
    needle = name.strip().lower().rstrip("/")
    for item in items:
        for key in keys or ("name",):
            value = str(item.get(key) or "").lower().rstrip("/")
            if value == needle or value.removeprefix("https://").removeprefix("http://") == needle:
                return item
    return None


def resolve_defaults(client: KeitaroClient, settings: Settings) -> dict:
    """Return dictionaries for the create form plus ids of the default domain/group/source."""
    domains = client.list_domains()
    groups = client.list_groups("campaigns")
    sources = client.list_traffic_sources()
    def pick(items: list[dict], name: str) -> dict | None:
        # configured name first; if there is exactly one option, it is the only sane default
        return _find_by_name(items, name, "name") or (items[0] if len(items) == 1 else None)

    domains = [d for d in domains if d.get("state", "active") == "active"] or domains
    domain = pick(domains, settings.default_domain)
    group = pick(groups, settings.default_group)
    source = pick(sources, settings.default_traffic_source)
    return {
        "domains": [{"id": d["id"], "name": d["name"]} for d in domains],
        "groups": [{"id": g["id"], "name": g["name"]} for g in groups],
        "traffic_sources": [{"id": s["id"], "name": s["name"]} for s in sources],
        "default_domain_id": domain["id"] if domain else None,
        "default_group_id": group["id"] if group else None,
        "default_traffic_source_id": source["id"] if source else None,
        "name_prefix": settings.campaign_name_prefix,
    }


# --------------------------------------------------------------------------
# Campaign creation
# --------------------------------------------------------------------------
def create_campaign(db: Session, client: KeitaroClient, settings: Settings, data: CampaignCreate) -> Campaign:
    # Validate the offer up-front so we don't leave a half-built campaign behind.
    try:
        client.get_offer(data.offer_id)
    except KeitaroError as exc:
        if exc.status_code == 404:
            raise ServiceError(f"Offer #{data.offer_id} not found in Keitaro", 404) from exc
        raise

    domain_id, group_id, source_id = data.domain_id, data.group_id, data.traffic_source_id
    if not (domain_id and group_id and source_id):
        defaults = resolve_defaults(client, settings)
        domain_id = domain_id or defaults["default_domain_id"]
        group_id = group_id or defaults["default_group_id"]
        source_id = source_id or defaults["default_traffic_source_id"]
    missing = [n for n, v in (("domain", domain_id), ("group", group_id), ("traffic source", source_id)) if not v]
    if missing:
        raise ServiceError(f"Could not resolve: {', '.join(missing)}. Pick them explicitly.")

    name = apply_prefix(data.name, settings.campaign_name_prefix)
    campaign = client.create_campaign(
        {
            "name": name,
            "alias": generate_alias(),
            "type": "position",  # rotate streams by position: geo stream first, offer stream second
            "state": "active",
            "domain_id": domain_id,
            "group_id": group_id,
            "traffic_source_id": source_id,
            "cost_type": "CPC",
            "cost_value": 0,
            "cost_currency": "USD",
            "cost_auto": True,
        }
    )
    kid = campaign["id"]

    try:
        google_stream = client.create_stream(
            {
                "campaign_id": kid,
                "type": "regular",
                "name": f"Google ({', '.join(data.countries)})",
                "position": 1,
                "weight": 100,
                "state": "active",
                "schema": "redirect",
                "action_type": "http",
                "action_payload": settings.google_url,
                "collect_clicks": True,
                "filter_or": False,
                "filters": [{"name": "country", "mode": "accept", "payload": data.countries}],
            }
        )
        offer_stream = client.create_stream(
            {
                "campaign_id": kid,
                "type": "regular",
                "name": "Offers",
                "position": 2,
                "weight": 100,
                "state": "active",
                "schema": "landings",
                "action_type": "http",
                "collect_clicks": True,
                "filters": [],
                "landings": [],
                "offers": [{"offer_id": data.offer_id, "share": 100, "state": "active"}],
            }
        )
    except KeitaroError:
        log.exception("Stream creation failed, rolling back campaign %s", kid)
        try:
            client.delete_campaign(kid)
        except KeitaroError:
            log.exception("Rollback of campaign %s failed", kid)
        raise

    row = Campaign(
        keitaro_id=kid,
        name=campaign.get("name", name),
        alias=campaign.get("alias", ""),
        countries=data.countries,
        offer_id=data.offer_id,
        domain_id=domain_id,
        group_id=group_id,
        traffic_source_id=source_id,
        google_stream_id=google_stream["id"],
        offer_stream_id=offer_stream["id"],
        url=campaign_url(client, campaign),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def campaign_url(client: KeitaroClient, campaign: dict) -> str | None:
    domain = campaign.get("domain") or ""
    alias = campaign.get("alias")
    if not alias:
        return None
    if not domain and campaign.get("domain_id"):
        try:
            for d in client.list_domains():
                if d["id"] == campaign["domain_id"]:
                    domain = d["name"]
                    break
        except KeitaroError:
            pass
    if not domain:
        return None
    if not domain.startswith("http"):
        domain = "https://" + domain
    return domain.rstrip("/") + "/" + alias


# --------------------------------------------------------------------------
# Offer editor
# --------------------------------------------------------------------------
def _load_offer_stream(client: KeitaroClient, campaign_id: int, stream_id: int) -> dict:
    stream = client.get_stream(stream_id)
    if int(stream.get("campaign_id", campaign_id)) != campaign_id:
        raise ServiceError("Stream does not belong to this campaign", 404)
    if not is_offer_stream(stream):
        raise ServiceError("This stream does not distribute traffic to offers")
    return stream


def _save_offers(
    db: Session, client: KeitaroClient, campaign_id: int, stream_id: int, offers: list[dict], action: str, offer_id: int | None
) -> dict:
    updated = client.update_stream(stream_id, {"offers": offers})
    local = db.scalar(select(Campaign).where(Campaign.keitaro_id == campaign_id))
    db.add(
        OfferChange(
            keitaro_campaign_id=campaign_id,
            campaign_id=local.id if local else None,
            stream_id=stream_id,
            action=action,
            offer_id=offer_id,
            offers_after=offers,
        )
    )
    db.commit()
    return updated


def add_offer(db: Session, client: KeitaroClient, campaign_id: int, stream_id: int, offer_id: int) -> dict:
    stream = _load_offer_stream(client, campaign_id, stream_id)
    offers = _clean_offers(stream.get("offers") or [])
    if any(o["offer_id"] == offer_id for o in offers):
        raise ServiceError(f"Offer #{offer_id} is already in this stream", 409)
    try:
        client.get_offer(offer_id)
    except KeitaroError as exc:
        if exc.status_code == 404:
            raise ServiceError(f"Offer #{offer_id} not found in Keitaro", 404) from exc
        raise
    offers = _redistribute(offers + [{"offer_id": offer_id, "share": 0, "state": "active"}])
    return _save_offers(db, client, campaign_id, stream_id, offers, "add", offer_id)


def remove_offer(db: Session, client: KeitaroClient, campaign_id: int, stream_id: int, offer_id: int) -> dict:
    stream = _load_offer_stream(client, campaign_id, stream_id)
    offers = _clean_offers(stream.get("offers") or [])
    remaining = [o for o in offers if o["offer_id"] != offer_id]
    if len(remaining) == len(offers):
        raise ServiceError(f"Offer #{offer_id} is not in this stream", 404)
    if not remaining:
        raise ServiceError("Cannot remove the last offer: the stream would have nowhere to send traffic")
    offers = _redistribute(remaining)
    return _save_offers(db, client, campaign_id, stream_id, offers, "remove", offer_id)


def set_shares(db: Session, client: KeitaroClient, campaign_id: int, stream_id: int, items: list[dict]) -> dict:
    stream = _load_offer_stream(client, campaign_id, stream_id)
    current = {o["offer_id"]: o for o in _clean_offers(stream.get("offers") or [])}
    requested = {int(i["offer_id"]): int(i["share"]) for i in items}
    if set(requested) != set(current):
        raise ServiceError("Share list must contain exactly the offers of the stream")
    total = sum(requested.values())
    if total != 100:
        raise ServiceError(f"Shares must sum to 100, got {total}")
    offers = [{**o, "share": requested[oid]} for oid, o in current.items()]
    return _save_offers(db, client, campaign_id, stream_id, offers, "set_shares", None)


# --------------------------------------------------------------------------
# Campaign view (streams + offers + stats)
# --------------------------------------------------------------------------
def _report(client: KeitaroClient, settings: Settings, campaign_id: int, interval: str, dims: list[str]) -> list[dict]:
    payload = {
        "range": {"interval": interval, "timezone": settings.report_timezone},
        "dimensions": dims,
        "measures": ["clicks", "campaign_unique_clicks", "stream_unique_clicks", "bots", "conversions", "revenue"],
        "filters": [{"name": "campaign_id", "operator": "EQUALS", "expression": campaign_id}],
        "sort": [{"name": "clicks", "order": "DESC"}],
    }
    try:
        result = client.build_report(payload)
    except KeitaroError:
        # Some Keitaro versions don't know the "bots" measure; retry without it.
        payload["measures"] = [m for m in payload["measures"] if m != "bots"]
        result = client.build_report(payload)
    return (result or {}).get("rows", [])


def _stats(row: dict | None, unique_key: str = "stream_unique_clicks") -> dict:
    row = row or {}
    return {
        "clicks": int(row.get("clicks") or 0),
        "unique": int(row.get(unique_key) or 0),
        "bots": int(row.get("bots") or 0),
        "conversions": int(row.get("conversions") or 0),
        "revenue": float(row.get("revenue") or 0),
    }


def campaign_view(client: KeitaroClient, settings: Settings, campaign_id: int, interval: str) -> dict:
    campaign = client.get_campaign(campaign_id)
    streams = sorted(client.list_streams(campaign_id), key=lambda s: (s.get("position") or 0, s["id"]))

    try:
        offer_names = {o["id"]: o["name"] for o in client.list_offers()}
    except KeitaroError:
        offer_names = {}

    stats_error = None
    all_stream_rows: list[dict] = []
    try:
        all_stream_rows = _report(client, settings, campaign_id, interval, ["stream_id"])
        stream_rows = {int(r["stream_id"]): r for r in all_stream_rows if r.get("stream_id")}
        offer_rows = {
            (int(r["stream_id"]), int(r["offer_id"])): r
            for r in _report(client, settings, campaign_id, interval, ["stream_id", "offer_id"])
            if r.get("stream_id") and r.get("offer_id")
        }
    except KeitaroError as exc:
        stream_rows, offer_rows, stats_error = {}, {}, exc.message

    out_streams = []
    for s in streams:
        offers = [
            {
                "offer_id": o["offer_id"],
                "name": offer_names.get(o["offer_id"], f"Offer #{o['offer_id']}"),
                "share": o.get("share", 0),
                "state": o.get("state", "active"),
                "stats": _stats(offer_rows.get((s["id"], o["offer_id"]))),
            }
            for o in s.get("offers") or []
        ]
        out_streams.append(
            {
                "id": s["id"],
                "name": s.get("name") or f"Stream {s['id']}",
                "type": s.get("type"),
                "schema": s.get("schema"),
                "position": s.get("position"),
                "state": s.get("state"),
                "action_type": s.get("action_type"),
                "action_payload": s.get("action_payload"),
                "filters": s.get("filters") or [],
                "is_offer_stream": is_offer_stream(s),
                "offers": offers,
                "share_total": sum(int(o["share"] or 0) for o in offers),
                "stats": _stats(stream_rows.get(s["id"])),
            }
        )

    # Campaign totals come from *all* report rows, including stream_id=0
    # (clicks that matched no stream, e.g. filtered bots) — same as Keitaro's campaign totals.
    row_stats = [_stats(r, "campaign_unique_clicks") for r in all_stream_rows]
    totals = {k: sum(r[k] for r in row_stats) for k in ("clicks", "unique", "bots", "conversions", "revenue")}
    totals["revenue"] = round(totals["revenue"], 2)
    unattributed = next((_stats(r) for r in all_stream_rows if not r.get("stream_id")), None)
    return {
        "campaign": {
            "id": campaign["id"],
            "name": campaign.get("name"),
            "alias": campaign.get("alias"),
            "state": campaign.get("state"),
            "url": campaign_url(client, campaign),
        },
        "interval": interval,
        "streams": out_streams,
        "totals": totals,
        "unattributed": unattributed,
        "stats_error": stats_error,
    }
