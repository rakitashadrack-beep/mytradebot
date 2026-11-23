"""Run the local-only MyTradeBot account portal at http://127.0.0.1:8001."""
import uvicorn

from backend.app import app


if __name__ == "__main__":
    print("\n  MyTradeBot Account Portal")
    print("  Open http://127.0.0.1:8001 in your browser\n")
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="warning")
