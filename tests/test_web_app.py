import asyncio
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response

from web.app import app, apply_browser_security_headers, index


def build_request(scheme="http", host="127.0.0.1:8000", method="GET", origin=None):
    headers = [(b"host", host.encode())]
    if origin:
        headers.append((b"origin", origin.encode()))
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/",
            "headers": headers,
            "query_string": b"",
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 12345),
            "scheme": scheme,
        }
    )


async def no_content(_request):
    return Response(status_code=200)


def test_browser_responses_have_security_headers_and_https_transport_policy():
    response = asyncio.run(
        apply_browser_security_headers(build_request("https"), no_content)
    )

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["Strict-Transport-Security"].startswith("max-age=")


def test_remote_hosts_and_cross_origin_controls_are_rejected():
    remote_host = asyncio.run(
        apply_browser_security_headers(
            build_request(host="attacker.example"),
            no_content,
        )
    )
    cross_origin = asyncio.run(
        apply_browser_security_headers(
            build_request(method="POST", origin="https://attacker.example"),
            no_content,
        )
    )

    assert remote_host.status_code == 421
    assert cross_origin.status_code == 403


def test_same_origin_control_requests_are_allowed():
    response = asyncio.run(
        apply_browser_security_headers(
            build_request(method="POST", origin="http://127.0.0.1:8000"),
            no_content,
        )
    )

    assert response.status_code == 200


def test_unified_web_app_exposes_installable_pwa_assets_without_caching_api_data():
    assets = Path(__file__).parents[1] / "web" / "static"
    manifest = json.loads((assets / "manifest.webmanifest").read_text())
    service_worker = (assets / "service-worker.js").read_text()

    assert manifest["start_url"] == "/"
    assert manifest["display"] == "standalone"
    assert {icon["sizes"] for icon in manifest["icons"] if icon["type"] == "image/png"} >= {
        "192x192",
        "512x512",
    }
    assert all(
        (assets / icon["src"].removeprefix("/")).is_file()
        for icon in manifest["icons"]
        if icon["type"] == "image/png"
    )
    assert (assets / "icons" / "apple-touch-icon.png").is_file()
    assert {route.path for route in app.routes} >= {
        "/manifest.webmanifest",
        "/icon.svg",
        "/icons/{icon_name}",
        "/service-worker.js",
    }
    assert 'url.pathname.startsWith("/api/")' in service_worker
    assert 'url.pathname.startsWith("/account-api/")' in service_worker


def test_local_launcher_generates_distinct_random_development_secrets(monkeypatch):
    from web_main import load_local_environment

    monkeypatch.delenv("MYTRADEBOT_ENV", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("LICENSE_SIGNING_SECRET", raising=False)
    monkeypatch.setenv("DEVELOPER_EMAIL", "masonrakitafx@gmail.com")

    try:
        load_local_environment()

        jwt_secret = os.environ["JWT_SECRET"]
        license_secret = os.environ["LICENSE_SIGNING_SECRET"]
        assert len(jwt_secret) >= 32
        assert len(license_secret) >= 32
        assert jwt_secret != license_secret
    finally:
        os.environ.pop("JWT_SECRET", None)
        os.environ.pop("LICENSE_SIGNING_SECRET", None)


def test_pwa_assets_and_security_headers_are_served_over_loopback():
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        home = client.get("/")
        manifest = client.get("/manifest.webmanifest")
        icon = client.get("/icons/icon-192.png")
        favicon = client.get("/favicon.ico")
        service_worker = client.get("/service-worker.js")
        remote_host = client.get("/", headers={"Host": "attacker.example"})

    assert home.status_code == 200
    assert home.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in home.headers["content-security-policy"]
    assert manifest.status_code == 200
    assert manifest.json()["display"] == "standalone"
    assert icon.status_code == 200 and icon.headers["content-type"] == "image/png"
    assert favicon.status_code == 200
    assert favicon.headers["content-type"] == "image/svg+xml"
    assert service_worker.status_code == 200
    assert service_worker.headers["service-worker-allowed"] == "/"
    assert remote_host.status_code == 421


def test_unified_app_serves_account_gate_before_bot_workspace():
    response = asyncio.run(index(build_request()))

    assert response.status_code == 200
    assert b"Create an account" in response.body
    assert b"Continue with phone verification" in response.body
    assert b"Open app workspace" in response.body
    assert b'id="bot-app" class="app-body hidden"' in response.body
    assert all(label in response.body for label in (b"History", b"Performance", b"News", b"Settings"))
    assert b"START BOT" in response.body
    assert b"STOP BOT" in response.body
    assert b"US NFP & CPI calendar" in response.body
    assert b'rel="manifest" href="/manifest.webmanifest"' in response.body
    assert any(route.path == "/account-api" for route in app.routes)
