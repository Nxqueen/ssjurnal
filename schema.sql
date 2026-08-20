-- ═══════════════════════════════════════════════════════
-- SINTA Journal Scraper — Supabase Schema
-- Jalankan sekali di Supabase SQL Editor (Dashboard → SQL)
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS sinta_journals (
    id              BIGSERIAL       PRIMARY KEY,

    -- Data jurnal
    title           TEXT            NOT NULL,
    sinta_rank      VARCHAR(10),
    issn            VARCHAR(30),
    publisher       TEXT,
    subject         TEXT,
    accreditation   TEXT,
    url             TEXT            NOT NULL UNIQUE,   -- UNIQUE → kunci upsert

    -- Metadata scraping
    scraped_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    source          TEXT            DEFAULT 'sinta.kemdikbud.go.id',

    -- Audit
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

-- Index untuk query filter rank
CREATE INDEX IF NOT EXISTS idx_sinta_journals_rank
    ON sinta_journals (sinta_rank);

-- Index untuk query terbaru
CREATE INDEX IF NOT EXISTS idx_sinta_journals_scraped_at
    ON sinta_journals (scraped_at DESC);

-- Auto-update kolom updated_at setiap kali row diupdate
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_updated_at ON sinta_journals;
CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON sinta_journals
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ── Row Level Security (RLS) — aktifkan agar lebih aman ──
ALTER TABLE sinta_journals ENABLE ROW LEVEL SECURITY;

-- Hanya service_role (backend) yang bisa INSERT/UPDATE/DELETE
-- Anon key hanya boleh SELECT (untuk public read jika dibutuhkan)
CREATE POLICY "service_role_full_access" ON sinta_journals
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE POLICY "anon_read_only" ON sinta_journals
    FOR SELECT
    TO anon
    USING (true);
