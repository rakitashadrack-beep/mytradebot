"""Run the local-only MyTradeBot web UI. Open http://127.0.0.1:8000 in your browser."""
import uvicorn
from web.app import app

if __name__ == "__main__":
    print("\n  MyTradeBot Web UI")
    print("  Open http://127.0.0.1:8000 in your browser\n")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
