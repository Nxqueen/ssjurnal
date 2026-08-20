"""
SINTA Journal Auto-Scraper — Vercel Serverless Function
========================================================
Author  : Senior Python Backend Developer
Target  : sinta.kemdiktisaintek.go.id  (domain baru per 2025)
Schedule: Every 6 hours via Vercel Cron Jobs / GitHub Actions
Strategy: requests + BeautifulSoup (html.parser) — NO Selenium/Playwright

PERUBAHAN DARI VERSI LAMA:
- Domain: kemdikbud.go.id → kemdiktisaintek.go.id
- Path: /journals → /journals/index/
- Pagination: ?page=N  (filter rank via URL tidak didukung server, pakai JS)
- Selector: diperbarui sesuai struktur HTML aktual SINTA
- Rank filter: dilakukan sisi scraper (parse teks "S2 Accredited" dari HTML)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from typing import Any

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
# 1. CONFIGURATION
# ═══════════════════════════════════════════════
class Config:
    # Filter ranking SINTA: 1 | 2 | 3 | 4 | 5 | 6
    SINTA_RANK: int = int(os.getenv("SINTA_RANK", "2"))

    # Berapa jurnal yang diambil per eksekusi
    SCRAPE_LIMIT: int = int(os.getenv("SCRAPE_LIMIT", "10"))

    # Timeout koneksi + baca (detik)
    REQUEST_TIMEOUT: tuple[int, int] = (10, 20)

    # Jeda antar request (detik)
    REQUEST_DELAY: float = float(os.getenv("REQUEST_DELAY", "1.5"))

    # Supabase
    SUPABASE_URL: str | None = os.getenv("SUPABASE_URL")
    SUPABASE_KEY: str | None = os.getenv("SUPABASE_KEY")
    SUPABASE_TABLE: str = os.getenv("SUPABASE_TABLE", "sinta_journals")

    # Cron secret
    CRON_SECRET: str | None = os.getenv("CRON_SECRET")

    # ── SINTA base URL — DOMAIN BARU ──────────────────────────
    SINTA_BASE: str = "https://sinta.kemdiktisaintek.go.id"
    # Path listing jurnal — BUKAN /journals, tapi /journals/index/
    SINTA_JOURNAL_PATH: str = "/journals/index/"
    # Jurnal per halaman (default SINTA = 10)
    JOURNALS_PER_PAGE: int = 10


# ═══════════════════════════════════════════════
# 2. HTTP SESSION FACTORY
# ═══════════════════════════════════════════════
def build_session() -> requests.Session:
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
    Scraper untuk sinta.kemdiktisaintek.go.id.

    CATATAN PENTING — Filter rank:
    Website SINTA menggunakan JavaScript (AJAX) untuk filter rank,
    sehingga filter via URL parameter (?sinta=2) TIDAK berfungsi di sisi server.
    Solusi: scraper mengambil halaman satu per satu, mem-parse badge
    akreditasi dari HTML ("S2 Accredited"), lalu memfilter di sisi Python.
    """

    def __init__(self) -> None:
        self.cfg = Config()
        self.session = build_session()
        self._rank_pattern = re.compile(
            rf"S{self.cfg.SINTA_RANK}\s+Accredited", re.IGNORECASE
        )

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

    # ── parse satu halaman listing jurnal ────────────────────
    def _parse_journal_list(self, html: str) -> list[dict[str, Any]]:
        """
        Parse halaman listing jurnal SINTA.

        Struktur HTML SINTA (hasil observasi langsung):
        - Setiap jurnal memiliki link ke /journals/profile/{id}
        - Akreditasi tertulis sebagai teks "S1 Accredited", "S2 Accredited", dst.
          dalam tag <a href="#!">
        - ISSN, Subject Area tertulis sebagai teks biasa
        - Afiliasi/penerbit ada di link /affiliations/profile/{id}
        """
        soup = BeautifulSoup(html, "html.parser")

        # Temukan semua link ke halaman profil jurnal
        # Ini adalah anchor utama tiap entri jurnal di halaman listing
        profile_links = soup.select("a[href*='/journals/profile/']")

        if not profile_links:
            log.warning(
                "Tidak ditemukan link /journals/profile/ — "
                "kemungkinan struktur HTML berubah atau request diblokir."
            )
            # DEBUG: log 500 karakter pertama HTML untuk diagnosa
            log.debug("HTML preview: %s", html[:500])
            return []

        log.info("Ditemukan %d profile link di halaman ini", len(profile_links))

        journals: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        for link in profile_links:
            href = link.get("href", "")
            if not href or href in seen_urls:
                continue
            seen_urls.add(href)

            try:
                journal = self._extract_journal_fields(link)
                if journal:
                    journals.append(journal)
            except Exception as exc:
                log.debug("Skipping item due to parse error: %s", exc)
                continue

        return journals

    def _extract_journal_fields(self, title_link) -> dict[str, Any] | None:
        """
        Extract field jurnal dari satu elemen (anchor judul jurnal).
        Naik ke parent container untuk ambil semua info terkait.
        """
        title = title_link.get_text(strip=True)
        if not title:
            return None

        href = title_link.get("href", "")
        detail_url = href if href.startswith("http") else self.cfg.SINTA_BASE + href

        # ── Cari container parent jurnal ──────────────────────
        # Naik beberapa level hingga menemukan div yang hanya
        # berisi satu link /journals/profile/ (= satu entri jurnal)
        container = title_link.parent
        for _ in range(6):
            if container is None:
                break
            links_in_container = container.select("a[href*='/journals/profile/']")
            if len(links_in_container) == 1:
                break
            container = container.parent

        if container is None:
            return None

        full_text = container.get_text(" ", strip=True)

        # ── Akreditasi: cari teks "S1 Accredited" dst. ───────
        # Tag: <a href="#!">S2 Accredited</a>
        accred_tag = container.find(
            "a",
            href=lambda h: h in ("#!", "#"),
            string=re.compile(r"S\d\s+Accredited", re.I),
        )
        if accred_tag is None:
            # Fallback: cari teks apapun yang cocok
            accred_text_node = container.find(
                string=re.compile(r"S\d\s+Accredited", re.I)
            )
            accreditation = accred_text_node.strip() if accred_text_node else None
        else:
            accreditation = accred_tag.get_text(strip=True)

        # ── Filter rank — skip jurnal yang tidak sesuai ───────
        if accreditation is None:
            # Jika tidak ada badge akreditasi, skip
            return None

        if not self._rank_pattern.search(accreditation):
            # Jurnal ini bukan rank yang diinginkan, skip
            return None

        # ── ISSN ──────────────────────────────────────────────
        p_issn_m = re.search(r"P-ISSN\s*:\s*([\dX\-]+)", full_text, re.I)
        e_issn_m = re.search(r"E-ISSN\s*:\s*([\dX\-]+)", full_text, re.I)
        p_issn = p_issn_m.group(1).strip() if p_issn_m else None
        e_issn = e_issn_m.group(1).strip() if e_issn_m else None
        issn = None
        if p_issn and p_issn != "0":
            issn = p_issn
        elif e_issn and e_issn != "0":
            issn = e_issn

        # ── Subject Area ──────────────────────────────────────
        subject_m = re.search(r"Subject Area\s*:\s*([^\|]+)", full_text, re.I)
        subject = subject_m.group(1).strip() if subject_m else None

        # ── Penerbit / Publisher ──────────────────────────────
        affil_link = container.find(
            "a", href=lambda h: h and "/affiliations/profile/" in h
        )
        publisher = affil_link.get_text(strip=True) if affil_link else None

        return {
            "title": title,
            "sinta_rank": f"S{self.cfg.SINTA_RANK}",
            "issn": issn,
            "publisher": publisher,
            "subject": subject,
            "accreditation": accreditation,
            "url": detail_url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "source": "sinta.kemdiktisaintek.go.id",
        }

    # ── fetch satu halaman ────────────────────────────────────
    def _fetch_page(self, page: int = 1) -> list[dict[str, Any]]:
        """
        Fetch dan parse satu halaman listing.
        Catatan: Filter rank TIDAK bisa lewat URL parameter (dihandle JS),
        sehingga kita scrape semua jurnal lalu filter di Python.
        """
        url = f"{self.cfg.SINTA_BASE}{self.cfg.SINTA_JOURNAL_PATH}"
        params = {"page": str(page)}

        log.info(
            "Fetching halaman %d | target SINTA-%d | URL: %s?page=%d",
            page, self.cfg.SINTA_RANK, url, page,
        )

        html = self._get(url, params=params)
        if not html:
            return []

        return self._parse_journal_list(html)

    # ── public: jalankan scraping ─────────────────────────────
    def scrape(self) -> list[dict[str, Any]]:
        """
        Scraping multi-halaman hingga SCRAPE_LIMIT jurnal sesuai rank terkumpul.

        Karena filter rank tidak bisa via URL, scraper akan membaca halaman
        satu per satu dan memfilter sendiri. Jurnal S1 biasanya ada di halaman
        awal (sorted by impact), S2-S6 tersebar di halaman selanjutnya.
        Batas maksimum halaman = 200 (2000 jurnal) untuk mencegah loop tak terbatas.
        """
        collected: list[dict[str, Any]] = []
        page = 1
        MAX_PAGES = 200  # safety limit

        while len(collected) < self.cfg.SCRAPE_LIMIT and page <= MAX_PAGES:
            batch = self._fetch_page(page)

            if not batch and page > 1:
                # Jika halaman pertama kosong mungkin ada masalah koneksi,
                # tapi halaman berikutnya kosong berarti data habis
                log.info("Tidak ada data di halaman %d. Berhenti.", page)
                break

            # batch sudah difilter by rank di dalam _extract_journal_fields
            remaining = self.cfg.SCRAPE_LIMIT - len(collected)
            new_items = batch[:remaining]
            collected.extend(new_items)

            log.info(
                "Hal. %d → +%d jurnal S%d ditemukan (total: %d / %d)",
                page,
                len(new_items),
                self.cfg.SINTA_RANK,
                len(collected),
                self.cfg.SCRAPE_LIMIT,
            )

            if len(collected) >= self.cfg.SCRAPE_LIMIT:
                break

            page += 1
            time.sleep(self.cfg.REQUEST_DELAY)

        if page > MAX_PAGES:
            log.warning("Mencapai batas halaman maksimum (%d). Hentikan.", MAX_PAGES)

        return collected


# ═══════════════════════════════════════════════
# 4. DATABASE LAYER (Supabase)
# ═══════════════════════════════════════════════
class DatabaseManager:
    def __init__(self) -> None:
        self.cfg = Config()
        self._client = None

        if self.cfg.SUPABASE_URL and self.cfg.SUPABASE_KEY:
            try:
                from supabase import create_client
                self._client = create_client(self.cfg.SUPABASE_URL, self.cfg.SUPABASE_KEY)
                log.info("Supabase client initialized (table: %s)", self.cfg.SUPABASE_TABLE)
            except Exception as exc:
                log.error("Failed to init Supabase client: %s", exc)
        else:
            log.warning("SUPABASE_URL / SUPABASE_KEY not set — fallback ke JSON log.")

    def upsert(self, journals: list[dict[str, Any]]) -> dict[str, Any]:
        if not journals:
            return {"inserted": 0, "errors": []}

        result: dict[str, Any] = {"inserted": 0, "errors": []}

        if self._client:
            try:
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
            log.info("JSON Result:\n%s", json.dumps(journals, ensure_ascii=False, indent=2))
            result["inserted"] = len(journals)
            result["note"] = "Fallback mode — no database configured"

        return result


# ═══════════════════════════════════════════════
# 5. VERCEL SERVERLESS HANDLER
# ═══════════════════════════════════════════════
class handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        log.debug(format, *args)

    def _is_authorized(self) -> bool:
        secret = Config.CRON_SECRET
        if not secret:
            return True
        auth_header = self.headers.get("Authorization", "")
        return auth_header == f"Bearer {secret}"

    def _send_json(self, status: int, body: dict) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        self._handle_request()

    def do_POST(self) -> None:
        self._handle_request()

    def _handle_request(self) -> None:
        import time as _time
        start_ts = _time.perf_counter()

        if not self._is_authorized():
            self._send_json(401, {"error": "Unauthorized — invalid or missing CRON_SECRET"})
            return

        log.info(
            "=== SINTA Scraper triggered | SINTA-%d | Limit: %d ===",
            Config.SINTA_RANK,
            Config.SCRAPE_LIMIT,
        )

        scraper = SintaScraper()
        try:
            journals = scraper.scrape()
        except Exception as exc:
            log.exception("Unhandled scraper error")
            self._send_json(500, {"error": str(exc), "status": "failed"})
            return
        finally:
            scraper.session.close()

        db = DatabaseManager()
        db_result = db.upsert(journals)

        elapsed = round(_time.perf_counter() - start_ts, 2)

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
            "journals": journals,
        }

        log.info(
            "Done. Scraped=%d | DB inserted=%d | Elapsed=%.2fs",
            len(journals),
            db_result.get("inserted", 0),
            elapsed,
        )

        self._send_json(200, response_body)
