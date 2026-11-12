"""Run the local-only unified MyTradeBot app at http://127.0.0.1:8000."""
import os
import secrets
import threading
import webbrowser
from pathlib import Path

import uvicorn


def load_local_environment() -> None:
    env_path = Path(__file__).with_name(".env")
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            if key:
                os.environ.setdefault(key, value)
    if os.getenv("MYTRADEBOT_ENV", "development") != "production":
        jwt_secret = os.getenv("JWT_SECRET", "")
        if len(jwt_secret) < 32 or jwt_secret == "development-only-change-me":
            os.environ["JWT_SECRET"] = secrets.token_urlsafe(48)
        license_secret = os.getenv("LICENSE_SIGNING_SECRET", "")
        if len(license_secret) < 32 or license_secret == os.environ["JWT_SECRET"]:
            os.environ["LICENSE_SIGNING_SECRET"] = secrets.token_urlsafe(48)


def main() -> None:
    load_local_environment()
    from web.app import app

    print("\n  MyTradeBot Web UI")
    print("  Opening http://127.0.0.1:8000 in your browser")
    print("  This server is local-only; phone relay is not configured.\n")
    threading.Timer(1.0, webbrowser.open, args=("http://127.0.0.1:8000",)).start()
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
