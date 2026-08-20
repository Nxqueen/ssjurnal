"""
Health Check Endpoint
=====================
GET /api/health → 200 OK jika service hidup.
Berguna untuk monitoring uptime (UptimeRobot, BetterUptime, dsb).
"""

import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        pass  # suppress access log noise

    def do_GET(self):  # noqa: N802
        body = json.dumps(
            {
                "status": "healthy",
                "service": "SINTA Journal Scraper",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "config": {
                    "sinta_rank": int(os.getenv("SINTA_RANK", "2")),
                    "scrape_limit": int(os.getenv("SCRAPE_LIMIT", "10")),
                    "db_configured": bool(os.getenv("SUPABASE_URL")),
                    "cron_secret_set": bool(os.getenv("CRON_SECRET")),
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
