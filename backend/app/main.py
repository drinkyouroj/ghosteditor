from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response as StarletteResponse

from app.analysis.router import router as bible_router
from app.auth.router import router as auth_router
from app.config import settings
from app.manuscripts.router import router as manuscripts_router
from app.stripe.router import router as stripe_router

MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB


class ContentSizeLimitMiddleware:
    """Reject requests with Content-Length > MAX_UPLOAD_SIZE before reading the body.
    Per DECISION_003 JUDGE amendment: defense in depth against memory bombs.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            content_length = headers.get(b"content-length")
            if content_length is not None:
                try:
                    if int(content_length) > MAX_UPLOAD_SIZE:
                        response = StarletteResponse(
                            content='{"detail":"Request body too large. Maximum 10MB."}',
                            status_code=413,
                            media_type="application/json",
                        )
                        await response(scope, receive, send)
                        return
                except ValueError:
                    pass
        await self.app(scope, receive, send)


app = FastAPI(
    title="GhostEditor",
    description="AI developmental editor for self-published authors",
    version="0.1.0",
)

app.add_middleware(ContentSizeLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(manuscripts_router)
app.include_router(bible_router)
app.include_router(stripe_router)


@app.on_event("startup")
async def startup():
    """Create S3 bucket on startup (for MinIO local dev)."""
    if settings.s3_endpoint_url:
        from app.manuscripts.s3 import ensure_bucket_exists
        try:
            ensure_bucket_exists()
        except Exception:
            pass  # MinIO may not be running yet


@app.get("/health")
async def health():
    return {"status": "ok"}
