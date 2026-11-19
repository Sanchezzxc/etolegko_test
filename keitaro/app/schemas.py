from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.countries import COUNTRIES

INTERVALS = ("today", "yesterday", "7_days_ago", "first_day_of_this_month", "1_month_ago", "all_time")


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    countries: list[str] = Field(min_length=1, description="ISO alpha-2 codes, e.g. ['AU']")
    offer_id: int = Field(gt=0)
    domain_id: int | None = None
    group_id: int | None = None
    traffic_source_id: int | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name is empty")
        return v

    @field_validator("countries", mode="before")
    @classmethod
    def parse_countries(cls, v):
        if isinstance(v, str):
            v = v.split(",")
        codes: list[str] = []
        for raw in v:
            code = str(raw).strip().upper()
            if not code:
                continue
            if code not in COUNTRIES:
                raise ValueError(f"unknown country code: {code}")
            if code not in codes:
                codes.append(code)
        if not codes:
            raise ValueError("at least one country is required")
        return codes


class CampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    keitaro_id: int
    name: str
    alias: str
    countries: list[str]
    offer_id: int
    domain_id: int | None
    group_id: int | None
    traffic_source_id: int | None
    google_stream_id: int | None
    offer_stream_id: int | None
    url: str | None
    created_at: datetime


class OfferAdd(BaseModel):
    offer_id: int = Field(gt=0)


class ShareItem(BaseModel):
    offer_id: int = Field(gt=0)
    share: int = Field(ge=0, le=100)


class SharesUpdate(BaseModel):
    offers: list[ShareItem] = Field(min_length=1)


class OfferChangeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    keitaro_campaign_id: int
    stream_id: int
    action: str
    offer_id: int | None
    offers_after: list[dict]
    created_at: datetime
