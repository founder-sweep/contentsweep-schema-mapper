"""Standalone local/private hosted adapter; never registers on the shared API."""
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from starlette.concurrency import run_in_threadpool

from .billing import Billing, BillingConfig
from .engine import ENGINE_VERSION
from .serialization import loads
from .service import Service
from .store import digest

BODY_LIMIT = 4 * 1024 * 1024


def create_app(root: Path | None = None, *, billing: Billing | None = None):
    billing = billing or Billing(BillingConfig.environment())
    config = billing.config
    root = root or Path(os.environ.get("SCHEMA_MAPPER_RUNTIME", ".mcp-products/schema-mapper/service"))
    service = Service(root, mode=config.mode)

    @asynccontextmanager
    async def lifespan(app):
        async def cleanup():
            while True:
                await run_in_threadpool(service.store.cleanup)
                await run_in_threadpool(service.marketing.cleanup)
                await asyncio.sleep(60)
        task = asyncio.create_task(cleanup())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="Schema Mapper", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.service, app.state.billing = service, billing
    static = Path(__file__).with_name("web")

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers.update({
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        })
        return response

    def identity(request, *, mutate=False):
        if mutate and request.headers.get("origin") != config.origin:
            raise HTTPException(403, "same-origin action required")
        try:
            session = service.store.session(request.cookies.get("schema_mapper_session"))
        except KeyError:
            raise HTTPException(401, "start a private browser session") from None
        if mutate and not hmac.compare_digest(request.headers.get("x-csrf-token", ""), session["csrf"]):
            raise HTTPException(403, "session action token required")
        return session

    async def body(request, *, json_body=True):
        content = bytearray()
        async for chunk in request.stream():
            if len(content) + len(chunk) > BODY_LIMIT:
                raise HTTPException(413, "4 MiB request limit")
            content.extend(chunk)
        if not json_body:
            return bytes(content)
        try:
            value = loads(content.decode("utf-8"))
            if not isinstance(value, dict):
                raise TypeError
            return value
        except (ValueError, TypeError, UnicodeDecodeError):
            raise HTTPException(422, "valid JSON object required") from None

    async def call(function, *args, **kwargs):
        try:
            return await run_in_threadpool(function, *args, **kwargs)
        except KeyError:
            raise HTTPException(404, "resource absent, expired or owned by another session") from None
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from None
        except Exception:  # noqa: BLE001 -- provider diagnostics can contain credentials
            raise HTTPException(503, "temporarily unavailable; no new purchase is required") from None

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (static / "index.html").read_text()

    @app.get("/app.js")
    def javascript():
        return Response((static / "app.js").read_text(), media_type="text/javascript")

    @app.get("/style.css")
    def stylesheet():
        return Response((static / "style.css").read_text(), media_type="text/css")

    @app.get("/guides", response_class=HTMLResponse)
    def guides_index():
        from .marketing_content import guides_index
        return guides_index(config.origin)

    @app.get("/guides/{slug}", response_class=HTMLResponse)
    def guide(slug: str):
        from .marketing_content import GUIDES, guide_page
        if slug not in GUIDES:
            raise HTTPException(404, "guide absent")
        return guide_page(slug, config.origin)

    @app.get("/sample", response_class=HTMLResponse)
    def sample():
        from .marketing_content import sample_page
        return sample_page(config.origin)

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy():
        from .marketing_content import privacy_page
        return privacy_page(config.origin)

    @app.get("/assets/{name}")
    def marketing_asset(name: str):
        allowed = {"schema-mapper-demo.webm": "video/webm", "schema-mapper-demo.vtt": "text/vtt",
                   "schema-mapper-social.png": "image/png", "schema-mapper-sample.zip": "application/zip"}
        if name not in allowed or not (static / name).is_file():
            raise HTTPException(404, "asset absent")
        return FileResponse(static / name, media_type=allowed[name],
                            filename=name if name.endswith(".zip") else None)

    @app.get("/sitemap.xml")
    def sitemap():
        from html import escape

        from .marketing_content import GUIDES
        paths = ["/", "/guides", "/sample", *["/guides/" + slug for slug in GUIDES]]
        xml = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        return Response(xml + "".join("<url><loc>" + escape(config.origin + path) + "</loc></url>" for path in paths) + "</urlset>", media_type="application/xml")

    @app.get("/robots.txt")
    def robots():
        return Response("User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: " + config.origin + "/sitemap.xml\n", media_type="text/plain")

    @app.get("/health")
    def health():
        with service.store.connection() as db:
            db.execute("SELECT 1")
        return {"status": "ready", "product": "schema-mapper", "engine": ENGINE_VERSION,
                "sales_enabled": config.enabled, "mode": config.mode}

    @app.post("/api/session")
    async def session(request: Request):
        if request.headers.get("origin") != config.origin:
            raise HTTPException(403, "same-origin action required")
        await call(service.store.rate_limit, "session:" + digest(request.client.host), limit=20, seconds=60)
        context = await body(request) if request.headers.get("content-length", "0") != "0" else {}
        if set(context) - {"campaign"}:
            raise HTTPException(422, "only campaign attribution is accepted")
        token = request.cookies.get("schema_mapper_session")
        try:
            current = service.store.session(token)
            await call(service.marketing.visit, current["owner"], context.get("campaign", "direct"))
            return {"csrf": current["csrf"], "sales_enabled": config.enabled, "mode": config.mode}
        except KeyError:
            token, csrf = service.store.new_session()
        response = Response(json.dumps({"csrf": csrf, "sales_enabled": config.enabled, "mode": config.mode}),
                            media_type="application/json")
        response.set_cookie("schema_mapper_session", token, secure=config.origin.startswith("https://"),
                            httponly=True, samesite="lax", max_age=7 * 86400)
        await call(service.marketing.visit, digest(token), context.get("campaign", "direct"))
        return response

    @app.post("/api/marketing/events")
    async def marketing_event(request: Request):
        owner = identity(request, mutate=True)["owner"]
        payload = await body(request)
        if payload != {"event": "example_loaded"}:
            raise HTTPException(422, "only example_loaded may be reported by the browser")
        await call(service.marketing.event, owner, "example_loaded", str(int(time.time() // 86400)), "example")
        return {"recorded": True}

    @app.get("/runtime/download")
    def runtime_download():
        wheel = Path(os.environ.get("SCHEMA_MAPPER_WHEEL_PATH", str(root / "runtime.whl")))
        if not wheel.is_file():
            raise HTTPException(503, "runtime distribution is being prepared")
        return Response(wheel.read_bytes(), media_type="application/octet-stream",
                        headers={"Content-Disposition": 'attachment; filename="contentsweep_schema_mapper-2.0.0b2-py3-none-any.whl"'})

    @app.post("/api/proposals")
    async def propose(request: Request):
        owner = identity(request, mutate=True)["owner"]
        await call(service.store.rate_limit, "preview:" + owner, limit=20, seconds=3600)
        payload = await body(request)
        if set(payload) != {"source", "format", "target_json", "vendor", "domain"}:
            raise HTTPException(422, "source, format, target_json, vendor and domain required")
        from .marketing import DEMO_SOURCE
        attempt = "preview_" + secrets.token_hex(16)
        kind = "example" if payload["source"] == DEMO_SOURCE else "customer"
        await call(service.marketing.event, owner, "preview_started", attempt, kind)
        try:
            result = await call(service.propose, owner, **payload)
        except HTTPException:
            await call(service.marketing.event, owner, "preview_failed", attempt, kind)
            raise
        await call(service.marketing.preview, owner, result["id"], payload["source"])
        return result

    @app.post("/api/proposals/{identifier}/approve")
    async def approve(identifier: str, request: Request):
        owner = identity(request, mutate=True)["owner"]
        payload = await body(request)
        if set(payload) != {"expected_fingerprint", "decisions", "reviewer", "vendor", "domain", "approve"} or payload.pop("approve") is not True:
            raise HTTPException(422, "explicit exact review approval required")
        result = await call(service.approve, owner, identifier, **payload)
        await call(service.marketing.validated, owner, identifier, result["id"])
        return result

    @app.post("/api/orders/{identifier}/checkout")
    async def checkout(identifier: str, request: Request):
        owner = identity(request, mutate=True)["owner"]
        return await call(billing.checkout, service, owner, identifier)

    @app.post("/api/orders/{identifier}/reconcile")
    async def reconcile(identifier: str, request: Request):
        owner = identity(request, mutate=True)["owner"]
        return await call(billing.reconcile, service, owner, identifier)

    @app.get("/api/orders/{identifier}")
    async def order(identifier: str, request: Request):
        owner = identity(request)["owner"]
        return await call(lambda: service.summary(service.store.order(owner, identifier)))

    @app.post("/api/orders/{identifier}/support")
    async def support(identifier: str, request: Request):
        owner = identity(request, mutate=True)["owner"]
        payload = await body(request)
        if set(payload) != {"email", "message"}:
            raise HTTPException(422, "email and message required")
        return await call(service.store.request_support, owner, identifier, **payload)

    @app.get("/api/orders/{identifier}/download")
    async def download(identifier: str, request: Request):
        owner = identity(request)["owner"]
        data = await call(service.store.download, owner, identifier)
        await call(service.marketing.event, owner, "download", identifier)
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="schema-mapper-artifact.zip"'})

    @app.post("/api/stripe/webhook")
    async def webhook(request: Request):
        return await call(billing.webhook, service, await body(request, json_body=False),
                          request.headers.get("stripe-signature", ""))

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18083)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
