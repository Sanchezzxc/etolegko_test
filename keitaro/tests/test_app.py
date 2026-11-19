import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings, get_settings
from app.db import Base, get_db
from app.fake_keitaro import FakeKeitaro, seed_demo
from app.keitaro import KeitaroClient, get_keitaro, offer_cache
from app.main import app
from app.services import split_shares

SETTINGS = Settings(
    keitaro_url="https://k.test", keitaro_api_key="secret", campaign_name_prefix="test_ivanov_", _env_file=None
)


@pytest.fixture
def fake():
    f = FakeKeitaro()
    seed_demo(f)
    return f


@pytest.fixture
def client(fake):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    offer_cache.clear()
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_keitaro] = lambda: fake
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------- shares
@pytest.mark.parametrize("n,expected", [(1, [100]), (2, [50, 50]), (3, [34, 33, 33]), (6, [17, 17, 17, 17, 16, 16])])
def test_split_shares(n, expected):
    assert split_shares(n) == expected


@pytest.mark.parametrize("n", range(1, 40))
def test_split_shares_always_100(n):
    parts = split_shares(n)
    assert sum(parts) == 100 and len(parts) == n and max(parts) - min(parts) <= 1


# ---------------------------------------------------------------- creator
def test_create_campaign(client, fake):
    r = client.post("/api/campaigns", json={"name": "campaign1", "countries": "au, mx", "offer_id": 3800})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "test_ivanov_campaign1"
    assert body["countries"] == ["AU", "MX"]
    assert body["url"].startswith("https://kylovoa.com/")

    camp = fake.campaigns[body["keitaro_id"]]
    # defaults resolved by name
    assert (camp["domain_id"], camp["group_id"], camp["traffic_source_id"]) == (1, 10, 20)
    assert camp["type"] == "position"

    streams = sorted(fake.list_streams(body["keitaro_id"]), key=lambda s: s["position"])
    assert len(streams) == 2
    google, offers = streams
    assert google["action_payload"] == "https://google.com"
    assert google["filters"] == [{"name": "country", "mode": "accept", "payload": ["AU", "MX"]}]
    assert offers["schema"] == "landings"
    assert [(o["offer_id"], o["share"]) for o in offers["offers"]] == [(3800, 100)]

    # stored locally
    local = client.get("/api/campaigns/local").json()
    assert [c["keitaro_id"] for c in local] == [body["keitaro_id"]]


def test_create_does_not_double_prefix(client):
    r = client.post("/api/campaigns", json={"name": "test_ivanov_x", "countries": ["RO"], "offer_id": 3749})
    assert r.json()["name"] == "test_ivanov_x"


def test_create_explicit_domain_group_source(client, fake):
    r = client.post("/api/campaigns", json={
        "name": "c", "countries": ["RO"], "offer_id": 3749, "domain_id": 2, "group_id": 11, "traffic_source_id": 21,
    })
    camp = fake.campaigns[r.json()["keitaro_id"]]
    assert (camp["domain_id"], camp["group_id"], camp["traffic_source_id"]) == (2, 11, 21)


def test_create_validation(client):
    assert client.post("/api/campaigns", json={"name": "c", "countries": ["XX"], "offer_id": 3749}).status_code == 422
    assert client.post("/api/campaigns", json={"name": "c", "countries": [], "offer_id": 3749}).status_code == 422
    r = client.post("/api/campaigns", json={"name": "c", "countries": ["AU"], "offer_id": 999999})
    assert r.status_code == 404 and "999999" in r.json()["detail"]


def test_create_rolls_back_on_stream_failure(client, fake, monkeypatch):
    from app.keitaro import KeitaroError

    calls = {"n": 0}
    orig = fake.create_stream

    def flaky(payload):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeitaroError(500, "boom")
        return orig(payload)

    monkeypatch.setattr(fake, "create_stream", flaky)
    before = set(fake.campaigns)
    r = client.post("/api/campaigns", json={"name": "c", "countries": ["AU"], "offer_id": 3749})
    assert r.status_code == 502
    assert set(fake.campaigns) == before  # half-built campaign deleted
    assert client.get("/api/campaigns/local").json() == []


def test_offer_search(client):
    assert [o["id"] for o in client.get("/api/offers?q=prize").json()] == [3717]
    assert [o["id"] for o in client.get("/api/offers?q=3749").json()] == [3749]


def test_options_defaults(client):
    o = client.get("/api/options").json()
    assert (o["default_domain_id"], o["default_group_id"], o["default_traffic_source_id"]) == (1, 10, 20)


def test_campaign_list_filters_by_prefix(client):
    client.post("/api/campaigns", json={"name": "mine", "countries": ["AU"], "offer_id": 3749})
    names = [c["name"] for c in client.get("/api/campaigns").json()]
    assert names == ["test_ivanov_mine"]
    all_names = [c["name"] for c in client.get("/api/campaigns?mine=false").json()]
    assert "test_demo_campaign" in all_names


# ---------------------------------------------------------------- editor
def _demo(fake):
    cid = next(iter(fake.campaigns))
    streams = sorted(fake.list_streams(cid), key=lambda s: s["position"])
    return cid, streams[0]["id"], streams[1]["id"]


def _shares(fake, sid):
    return [(o["offer_id"], o["share"]) for o in fake.streams[sid]["offers"]]


def test_view_numbers(client, fake):
    cid, gid, oid = _demo(fake)
    v = client.get(f"/api/campaigns/{cid}").json()
    # totals include the stream_id=0 row (unattributed clicks), uniques are campaign-level
    assert v["totals"] == {"clicks": 172, "unique": 139, "bots": 17, "conversions": 4, "revenue": 88.5}
    assert v["unattributed"]["clicks"] == 10
    offer_stream = next(s for s in v["streams"] if s["id"] == oid)
    assert offer_stream["share_total"] == 100
    assert offer_stream["stats"]["unique"] == 104  # stream-level uniques
    assert [(o["offer_id"], o["share"], o["stats"]["clicks"], o["stats"]["unique"]) for o in offer_stream["offers"]] == [
        (3749, 50, 61, 53), (3717, 50, 59, 51)
    ]
    google = next(s for s in v["streams"] if s["id"] == gid)
    assert not google["is_offer_stream"] and google["stats"]["clicks"] == 42


def test_add_offer_redistributes(client, fake):
    cid, _, oid = _demo(fake)
    r = client.post(f"/api/campaigns/{cid}/streams/{oid}/offers", json={"offer_id": 3800})
    assert r.status_code == 200, r.text
    assert _shares(fake, oid) == [(3749, 34), (3717, 33), (3800, 33)]

    client.post(f"/api/campaigns/{cid}/streams/{oid}/offers", json={"offer_id": 3801})
    assert _shares(fake, oid) == [(3749, 25), (3717, 25), (3800, 25), (3801, 25)]

    hist = client.get(f"/api/campaigns/{cid}/history").json()
    assert [h["action"] for h in hist] == ["add", "add"]


def test_add_offer_errors(client, fake):
    cid, gid, oid = _demo(fake)
    assert client.post(f"/api/campaigns/{cid}/streams/{oid}/offers", json={"offer_id": 3749}).status_code == 409
    assert client.post(f"/api/campaigns/{cid}/streams/{oid}/offers", json={"offer_id": 424242}).status_code == 404
    assert client.post(f"/api/campaigns/{cid}/streams/{gid}/offers", json={"offer_id": 3800}).status_code == 400
    assert client.post(f"/api/campaigns/{cid + 1}/streams/{oid}/offers", json={"offer_id": 3800}).status_code == 404


def test_remove_offer(client, fake):
    cid, _, oid = _demo(fake)
    client.post(f"/api/campaigns/{cid}/streams/{oid}/offers", json={"offer_id": 3800})
    r = client.delete(f"/api/campaigns/{cid}/streams/{oid}/offers/3717")
    assert r.status_code == 200
    assert _shares(fake, oid) == [(3749, 50), (3800, 50)]
    client.delete(f"/api/campaigns/{cid}/streams/{oid}/offers/3749")
    assert _shares(fake, oid) == [(3800, 100)]
    # last offer cannot be removed; unknown offer -> 404
    assert client.delete(f"/api/campaigns/{cid}/streams/{oid}/offers/3800").status_code == 400
    assert client.delete(f"/api/campaigns/{cid}/streams/{oid}/offers/1").status_code == 404
    assert _shares(fake, oid) == [(3800, 100)]


def test_set_shares(client, fake):
    cid, _, oid = _demo(fake)
    url = f"/api/campaigns/{cid}/streams/{oid}/shares"
    ok = client.put(url, json={"offers": [{"offer_id": 3749, "share": 70}, {"offer_id": 3717, "share": 30}]})
    assert ok.status_code == 200 and _shares(fake, oid) == [(3749, 70), (3717, 30)]
    bad_sum = client.put(url, json={"offers": [{"offer_id": 3749, "share": 70}, {"offer_id": 3717, "share": 20}]})
    assert bad_sum.status_code == 400 and "100" in bad_sum.json()["detail"]
    bad_set = client.put(url, json={"offers": [{"offer_id": 3749, "share": 100}]})
    assert bad_set.status_code == 400
    assert _shares(fake, oid) == [(3749, 70), (3717, 30)]


# ---------------------------------------------------------------- real HTTP client
@respx.mock
def test_real_client_sends_api_key_and_parses_errors():
    route = respx.get("https://k.test/admin_api/v1/offers").mock(return_value=httpx.Response(200, json=[{"id": 1}]))
    kc = KeitaroClient(SETTINGS)
    assert kc.list_offers() == [{"id": 1}]
    assert route.calls.last.request.headers["Api-Key"] == "secret"

    respx.put("https://k.test/admin_api/v1/streams/5").mock(return_value=httpx.Response(422, json={"error": "bad offers"}))
    from app.keitaro import KeitaroError

    with pytest.raises(KeitaroError) as e:
        kc.update_stream(5, {"offers": []})
    assert e.value.status_code == 422 and e.value.message == "bad offers"
