from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import services
from app.config import Settings, get_settings
from app.countries import COUNTRIES
from app.db import get_db
from app.keitaro import KeitaroClient, get_keitaro, offer_cache
from app.models import Campaign, OfferChange
from app.schemas import INTERVALS, CampaignCreate, CampaignOut, OfferAdd, OfferChangeOut, SharesUpdate

router = APIRouter(prefix="/api")


# --- dictionaries ----------------------------------------------------------
@router.get("/countries")
def countries() -> list[dict]:
    return [{"code": c, "name": n} for c, n in COUNTRIES.items()]


@router.get("/options")
def options(client: KeitaroClient = Depends(get_keitaro), settings: Settings = Depends(get_settings)) -> dict:
    """Domains, groups and traffic sources for the create form (+ preselected defaults)."""
    return services.resolve_defaults(client, settings)


@router.get("/offers")
def offers(
    q: str = Query("", description="Search by id or name"),
    limit: int = Query(20, ge=1, le=200),
    client: KeitaroClient = Depends(get_keitaro),
) -> list[dict]:
    q = q.strip().lower()
    result = []
    for o in offer_cache.get(client):
        if o.get("state") == "deleted":
            continue
        if not q or q in str(o["id"]) or q in (o.get("name") or "").lower():
            result.append({"id": o["id"], "name": o.get("name"), "state": o.get("state")})
        if len(result) >= limit:
            break
    return result


# --- campaigns -------------------------------------------------------------
@router.post("/campaigns", response_model=CampaignOut, status_code=201)
def create_campaign(
    data: CampaignCreate,
    db: Session = Depends(get_db),
    client: KeitaroClient = Depends(get_keitaro),
    settings: Settings = Depends(get_settings),
):
    return services.create_campaign(db, client, settings, data)


@router.get("/campaigns/local", response_model=list[CampaignOut])
def local_campaigns(db: Session = Depends(get_db)):
    """Campaigns created through this service."""
    return db.scalars(select(Campaign).order_by(Campaign.id.desc())).all()


@router.get("/campaigns")
def keitaro_campaigns(
    q: str = "",
    mine: bool = Query(True, description="Only campaigns with the configured name prefix"),
    client: KeitaroClient = Depends(get_keitaro),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    """All campaigns from Keitaro (editor works with any of them)."""
    q = q.strip().lower()
    out = []
    for c in client.list_campaigns():
        name = c.get("name") or ""
        if mine and settings.campaign_name_prefix and not name.startswith(settings.campaign_name_prefix):
            continue
        if q and q not in name.lower() and q != str(c["id"]):
            continue
        out.append({"id": c["id"], "name": name, "alias": c.get("alias"), "state": c.get("state")})
    return sorted(out, key=lambda c: -c["id"])


@router.get("/campaigns/{campaign_id}")
def campaign_detail(
    campaign_id: int,
    interval: str = Query("today", enum=list(INTERVALS)),
    client: KeitaroClient = Depends(get_keitaro),
    settings: Settings = Depends(get_settings),
) -> dict:
    return services.campaign_view(client, settings, campaign_id, interval)


@router.get("/campaigns/{campaign_id}/history", response_model=list[OfferChangeOut])
def campaign_history(campaign_id: int, db: Session = Depends(get_db)):
    stmt = select(OfferChange).where(OfferChange.keitaro_campaign_id == campaign_id).order_by(OfferChange.id.desc()).limit(50)
    return db.scalars(stmt).all()


# --- offer editor ----------------------------------------------------------
@router.post("/campaigns/{campaign_id}/streams/{stream_id}/offers")
def add_offer(
    campaign_id: int, stream_id: int, data: OfferAdd, db: Session = Depends(get_db), client: KeitaroClient = Depends(get_keitaro)
) -> dict:
    return services.add_offer(db, client, campaign_id, stream_id, data.offer_id)


@router.delete("/campaigns/{campaign_id}/streams/{stream_id}/offers/{offer_id}")
def remove_offer(
    campaign_id: int, stream_id: int, offer_id: int, db: Session = Depends(get_db), client: KeitaroClient = Depends(get_keitaro)
) -> dict:
    return services.remove_offer(db, client, campaign_id, stream_id, offer_id)


@router.put("/campaigns/{campaign_id}/streams/{stream_id}/shares")
def update_shares(
    campaign_id: int, stream_id: int, data: SharesUpdate, db: Session = Depends(get_db), client: KeitaroClient = Depends(get_keitaro)
) -> dict:
    return services.set_shares(db, client, campaign_id, stream_id, [i.model_dump() for i in data.offers])
