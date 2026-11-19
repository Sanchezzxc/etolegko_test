from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import router
from app.keitaro import KeitaroError
from app.services import ServiceError

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Keitaro wrapper", version="1.0.0")
app.include_router(router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def no_cache_frontend(request: Request, call_next):
    # The frontend is unbundled (no hashed filenames), so make browsers revalidate it.
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(KeitaroError)
def keitaro_error(_: Request, exc: KeitaroError):
    # 401/403 from Keitaro means our API key is wrong, which is a gateway problem for our client.
    status = exc.status_code if exc.status_code in (400, 404, 409, 422) else 502
    return JSONResponse(status_code=status, content={"detail": f"Keitaro: {exc.message}"})


@app.exception_handler(ServiceError)
def service_error(_: Request, exc: ServiceError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/campaign/{campaign_id}", include_in_schema=False)
def editor(campaign_id: int):
    return FileResponse(STATIC / "editor.html")
