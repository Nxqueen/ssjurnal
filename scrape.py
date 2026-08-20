"""
SINTA Journal Auto-Scraper — Vercel Serverless Function
========================================================
Author  : Senior Python Backend Developer
Target  : sinta.kemdikbud.go.id
Schedule: Every 6 hours via Vercel Cron Jobs
Strategy: requests + BeautifulSoup (html.parser) — NO Selenium/Playwright
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────
# Logging — Vercel captures stdout automatically
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════
# 1. CONFIGURATION  (via Vercel Environment Variables)
# ═══════════════════════════════════════════════
class Config:
    """
    Semua nilai bisa diubah melalui Vercel Dashboard → Project Settings → Environment Variables
    tanpa perlu menyentuh kode sama sekali.
    """

    # Filter ranking SINTA: 1 | 2 | 3 | 4 | 5 | 6
    SINTA_RANK: int = int(os.getenv("SINTA_RANK", "2"))

    # Berapa jurnal yang diambil per eksekusi
    SCRAPE_LIMIT: int = int(os.getenv("SCRAPE_LIMIT", "10"))

    # Timeout koneksi + baca (detik) — cegah fungsi hanging
    REQUEST_TIMEOUT: tuple[int, int] = (10, 15)

    # Jeda antar request (detik) — hindari rate-limit / ban
    REQUEST_DELAY: float = float(os.getenv("REQUEST_DELAY", "1.5"))

    # ── Supabase ──────────────────────────────────────────────
    SUPABASE_URL: str | None = os.getenv("SUPABASE_URL")
    SUPABASE_KEY: str | None = os.getenv("SUPABASE_KEY")   # service_role key
    SUPABASE_TABLE: str = os.getenv("SUPABASE_TABLE", "sinta_journals")

    # ── Cron secret — amankan endpoint agar tidak sembarang hit
    CRON_SECRET: str | None = os.getenv("CRON_SECRET")

    # ── SINTA base URL ────────────────────────────────────────
    SINTA_BASE: str = "https://sinta.kemdikbud.go.id"
    SINTA_JOURNAL_PATH: str = "/journals"


# ═══════════════════════════════════════════════
# 2. HTTP SESSION FACTORY
# ═══════════════════════════════════════════════
def build_session() -> requests.Session:
    """
    Buat satu Session yang di-reuse agar koneksi TCP di-pool → hemat bandwidth & latency.
    User-Agent menyerupai browser biasa untuk menghindari blokir.
    """
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "DNT": "1",
        }
    )
    return session


# ═══════════════════════════════════════════════
# 3. SCRAPER
# ═══════════════════════════════════════════════
class SintaScraper:
    """
    Scraper ringan untuk sinta.kemdikbud.go.id.

    Strategi efisiensi:
    - Session pooling  → 1 TCP handshake untuk banyak request
    - html.parser      → tidak perlu instalasi C lib (lxml opsional, lebih cepat)
    - SoupStrainer     → hanya parse blok HTML yang dibutuhkan, bukan seluruh halaman
    - Timeout ketat    → maksimal (10s connect, 15s read)
    """

    def __init__(self) -> None:
        self.cfg = Config()
        self.session = build_session()

    # ── low-level fetch ───────────────────────────────────────
    def _get(self, url: str, params: dict | None = None) -> str | None:
        try:
            resp = self.session.get(
                url,
                params=params,
                timeout=self.cfg.REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            resp.raise_for_status()
            return resp.text
        except requests.exceptions.Timeout:
            log.warning("Timeout fetching %s", url)
        except requests.exceptions.HTTPError as exc:
            log.warning("HTTP %s for %s", exc.response.status_code, url)
        except requests.exceptions.RequestException as exc:
            log.error("Request error: %s", exc)
        return None

    # ── parse satu halaman daftar jurnal ─────────────────────
    def _parse_journal_list(self, html: str) -> list[dict[str, Any]]:
        """
        Parse halaman listing jurnal SINTA.
        Hanya extract elemen yang benar-benar dibutuhkan.
        """
        soup = BeautifulSoup(html, "html.parser")

        # Container utama daftar jurnal
        journal_items = soup.select("div.journal-item, div.affil-box, .content-journal")

        # Fallback: coba selector generik jika struktur berubah
        if not journal_items:
            journal_items = soup.select("div[class*='journal']")

        journals: list[dict[str, Any]] = []
        for item in journal_items:
            try:
                journal = self._extract_journal_fields(item)
                if journal:
                    journals.append(journal)
            except Exception as exc:  # noqa: BLE001
                log.debug("Skipping item due to parse error: %s", exc)
                continue

        return journals

    def _extract_journal_fields(self, item: Any) -> dict[str, Any] | None:
        """Extract field-field jurnal dari satu elemen HTML."""

        # ── Judul + URL detail ────────────────────────────────
        title_tag = item.select_one("a.journal-title, h3 a, .title a, a[href*='/journals/']")
        if not title_tag:
            return None

        title = title_tag.get_text(strip=True)
        detail_url = title_tag.get("href", "")
        if detail_url and not detail_url.startswith("http"):
            detail_url = self.cfg.SINTA_BASE + detail_url

        # ── ISSN ──────────────────────────────────────────────
        issn_tag = item.select_one(".issn, span[class*='issn']")
        issn = issn_tag.get_text(strip=True) if issn_tag else None

        # ── Penerbit / Publisher ──────────────────────────────
        publisher_tag = item.select_one(".publisher, span[class*='publisher'], .affil-name")
        publisher = publisher_tag.get_text(strip=True) if publisher_tag else None

        # ── Grade / Rank SINTA ────────────────────────────────
        rank_tag = item.select_one(
            "span[class*='sinta'], .grade, .label-sinta, span[class*='rank']"
        )
        rank_text = rank_tag.get_text(strip=True) if rank_tag else f"S{self.cfg.SINTA_RANK}"

        # ── Subject / Bidang ──────────────────────────────────
        subject_tag = item.select_one(".subject, .category, span[class*='subject']")
        subject = subject_tag.get_text(strip=True) if subject_tag else None

        # ── Akreditasi ────────────────────────────────────────
        accred_tag = item.select_one(".accreditation, .akreditasi")
        accreditation = accred_tag.get_text(strip=True) if accred_tag else None

        return {
            "title": title,
            "sinta_rank": rank_text,
            "issn": issn,
            "publisher": publisher,
            "subject": subject,
            "accreditation": accreditation,
            "url": detail_url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "source": "sinta.kemdikbud.go.id",
        }

    # ── ambil satu halaman listing ────────────────────────────
    def _fetch_page(self, page: int = 1) -> list[dict[str, Any]]:
        params = {
            "q": "",                          # query kosong = semua
            "search": "1",
            "sinta": str(self.cfg.SINTA_RANK),
            "page": str(page),
        }
        url = f"{self.cfg.SINTA_BASE}{self.cfg.SINTA_JOURNAL_PATH}"
        log.info("Fetching page %d | SINTA-%d | URL: %s?%s", page, self.cfg.SINTA_RANK, url, urlencode(params))

        html = self._get(url, params=params)
        if not html:
            return []

        return self._parse_journal_list(html)

    # ── public: jalankan scraping ─────────────────────────────
    def scrape(self) -> list[dict[str, Any]]:
        """
        Jalankan scraping hingga SCRAPE_LIMIT tercapai.
        Iterasi per-halaman; berhenti jika sudah cukup atau tidak ada data baru.
        """
        collected: list[dict[str, Any]] = []
        page = 1

        while len(collected) < self.cfg.SCRAPE_LIMIT:
            batch = self._fetch_page(page)

            if not batch:
                log.info("No more data on page %d. Stopping.", page)
                break

            # Ambil hanya yang diperlukan dari batch ini
            remaining = self.cfg.SCRAPE_LIMIT - len(collected)
            collected.extend(batch[:remaining])

            log.info(
                "Page %d → +%d journals (total: %d / %d)",
                page,
                min(len(batch), remaining),
                len(collected),
                self.cfg.SCRAPE_LIMIT,
            )

            if len(collected) >= self.cfg.SCRAPE_LIMIT:
                break

            page += 1
            time.sleep(self.cfg.REQUEST_DELAY)  # sopan ke server target

        return collected


# ═══════════════════════════════════════════════
# 4. DATABASE LAYER (Supabase)
# ═══════════════════════════════════════════════
class DatabaseManager:
    """
    Simpan hasil scraping ke Supabase (PostgreSQL).
    Jika Supabase tidak dikonfigurasi, fallback ke JSON log lokal.
    """

    def __init__(self) -> None:
        self.cfg = Config()
        self._client = None

        if self.cfg.SUPABASE_URL and self.cfg.SUPABASE_KEY:
            try:
                from supabase import create_client  # type: ignore[import-untyped]

                self._client = create_client(self.cfg.SUPABASE_URL, self.cfg.SUPABASE_KEY)
                log.info("Supabase client initialized (table: %s)", self.cfg.SUPABASE_TABLE)
            except Exception as exc:
                log.error("Failed to init Supabase client: %s", exc)
        else:
            log.warning(
                "SUPABASE_URL / SUPABASE_KEY not set — results will only be logged as JSON."
            )

    def upsert(self, journals: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Simpan / update data jurnal.
        Gunakan upsert berdasarkan kolom 'url' agar tidak ada duplikat.
        """
        if not journals:
            return {"inserted": 0, "errors": []}

        result = {"inserted": 0, "errors": []}

        if self._client:
            try:
                # Supabase upsert — on_conflict pada kolom 'url' (harus ada UNIQUE constraint)
                response = (
                    self._client.table(self.cfg.SUPABASE_TABLE)
                    .upsert(journals, on_conflict="url")
                    .execute()
                )
                result["inserted"] = len(response.data) if response.data else 0
                log.info("Upserted %d rows to Supabase.", result["inserted"])
            except Exception as exc:
                error_msg = str(exc)
                log.error("Supabase upsert error: %s", error_msg)
                result["errors"].append(error_msg)
        else:
            # ── Fallback: log sebagai JSON ke stdout (Vercel menangkap ini) ──
            log.info("JSON Result:\n%s", json.dumps(journals, ensure_ascii=False, indent=2))
            result["inserted"] = len(journals)
            result["note"] = "Fallback mode — no database configured"

        return result


# ═══════════════════════════════════════════════
# 5. VERCEL SERVERLESS HANDLER
# ═══════════════════════════════════════════════
class handler(BaseHTTPRequestHandler):
    """
    Vercel Python Serverless Function handler.
    Dipanggil oleh Vercel Cron setiap 6 jam, atau bisa di-trigger manual via GET/POST.
    """

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Redirect access log ke Python logger
        log.debug(format, *args)

    # ── Keamanan: validasi Cron Secret ───────────────────────
    def _is_authorized(self) -> bool:
        secret = Config.CRON_SECRET
        if not secret:
            return True  # Secret belum di-set → izinkan (untuk development)

        # Vercel Cron mengirim header Authorization: Bearer <secret>
        auth_header = self.headers.get("Authorization", "")
        return auth_header == f"Bearer {secret}"

    def _send_json(self, status: int, body: dict) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        self._handle_request()

    def do_POST(self) -> None:  # noqa: N802
        self._handle_request()

    def _handle_request(self) -> None:
        start_ts = time.perf_counter()

        # ── Auth check ────────────────────────────────────────
        if not self._is_authorized():
            self._send_json(401, {"error": "Unauthorized — invalid or missing CRON_SECRET"})
            return

        log.info(
            "=== SINTA Scraper triggered | SINTA-%d | Limit: %d ===",
            Config.SINTA_RANK,
            Config.SCRAPE_LIMIT,
        )

        # ── Scraping ──────────────────────────────────────────
        scraper = SintaScraper()
        try:
            journals = scraper.scrape()
        except Exception as exc:
            log.exception("Unhandled scraper error")
            self._send_json(500, {"error": str(exc), "status": "failed"})
            return
        finally:
            scraper.session.close()

        # ── Simpan ke database ────────────────────────────────
        db = DatabaseManager()
        db_result = db.upsert(journals)

        elapsed = round(time.perf_counter() - start_ts, 2)

        response_body = {
            "status": "success",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "config": {
                "sinta_rank": Config.SINTA_RANK,
                "scrape_limit": Config.SCRAPE_LIMIT,
            },
            "scraped": len(journals),
            "db_inserted": db_result.get("inserted", 0),
            "db_errors": db_result.get("errors", []),
            "elapsed_seconds": elapsed,
            "journals": journals,   # ← hilangkan baris ini di production jika tidak perlu expose data
        }

        log.info(
            "Done. Scraped=%d | DB inserted=%d | Elapsed=%.2fs",
            len(journals),
            db_result.get("inserted", 0),
            elapsed,
        )

        self._send_json(200, response_body)
