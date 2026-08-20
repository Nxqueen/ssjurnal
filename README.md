# 📚 SINTA Journal Auto-Scraper

> Sistem scraping otomatis data jurnal dari **sinta.kemdikbud.go.id**, di-deploy sebagai **Vercel Serverless Function** yang berjalan setiap 6 jam via Vercel Cron Jobs.

---

## 🏗️ Struktur Proyek

```
sinta-scraper/
├── api/
│   ├── scrape.py          # Serverless function utama (scraper + handler)
│   └── health.py          # Health check endpoint
├── supabase/
│   └── schema.sql         # SQL schema untuk Supabase (jalankan sekali)
├── vercel.json            # Konfigurasi Vercel: routes, cron, functions
├── requirements.txt       # Dependensi Python (ringan, tanpa Selenium)
├── .env.example           # Template environment variables
├── .gitignore
└── README.md
```

---

## ⚙️ Environment Variables

### Cara Mendaftarkan di Vercel Dashboard

1. Buka **[vercel.com](https://vercel.com)** → Login → Pilih project Anda
2. Klik **Settings** (tab atas) → **Environment Variables** (menu kiri)
3. Tambahkan variabel satu per satu:

| Variable | Nilai Contoh | Keterangan |
|---|---|---|
| `SINTA_RANK` | `2` | Filter ranking SINTA (1–6) |
| `SCRAPE_LIMIT` | `10` | Jumlah jurnal per eksekusi |
| `REQUEST_DELAY` | `1.5` | Jeda antar request (detik) |
| `SUPABASE_URL` | `https://xxxx.supabase.co` | URL project Supabase |
| `SUPABASE_KEY` | `eyJhbGci...` | **Service role key** Supabase |
| `SUPABASE_TABLE` | `sinta_journals` | Nama tabel database |
| `CRON_SECRET` | `abc123...` | Token keamanan cron (wajib di production) |

4. Pada kolom **Environment**, centang **Production**, **Preview**, dan **Development** sesuai kebutuhan
5. Klik **Save** → Lakukan **Redeploy** agar variabel baru aktif

> ⚠️ **Gunakan `SUPABASE_KEY` dengan nilai `service_role` key**, bukan `anon` key, agar scraper punya akses tulis ke database.

---

## 🚀 Panduan Deploy ke Vercel

### Prasyarat
- Akun [Vercel](https://vercel.com) (gratis)
- Akun [Supabase](https://supabase.com) (gratis, opsional)
- [Vercel CLI](https://vercel.com/docs/cli): `npm i -g vercel`

### Langkah 1 — Push ke GitHub

```bash
git init
git add .
git commit -m "feat: initial SINTA scraper"
git remote add origin https://github.com/USERNAME/sinta-scraper.git
git push -u origin main
```

### Langkah 2 — Setup Database Supabase (Opsional tapi Direkomendasikan)

1. Buka **Supabase Dashboard** → **SQL Editor**
2. Paste seluruh isi `supabase/schema.sql`
3. Klik **Run** → Tabel `sinta_journals` siap digunakan
4. Ambil credentials: **Settings → API → `service_role` secret**

### Langkah 3 — Deploy ke Vercel

**Via Dashboard (termudah):**
1. Buka [vercel.com/new](https://vercel.com/new)
2. Import repository GitHub yang sudah dibuat
3. Framework Preset: **Other**
4. Daftarkan semua Environment Variables (lihat tabel di atas)
5. Klik **Deploy**

**Via CLI:**
```bash
vercel login
vercel --prod
```

### Langkah 4 — Generate CRON_SECRET

```bash
# Linux/Mac
openssl rand -hex 32

# Windows (PowerShell)
[System.Web.Security.Membership]::GeneratePassword(64, 0)
```

Masukkan hasilnya sebagai nilai `CRON_SECRET` di Vercel.

---

## 🔌 Endpoint API

| Method | Path | Fungsi |
|---|---|---|
| `GET` | `/api/scrape` | Trigger scraping manual / dipanggil Cron |
| `GET` | `/api/health` | Health check & status konfigurasi |

### Contoh Response `/api/scrape`

```json
{
  "status": "success",
  "timestamp": "2025-06-14T06:00:00.000Z",
  "config": {
    "sinta_rank": 2,
    "scrape_limit": 10
  },
  "scraped": 10,
  "db_inserted": 10,
  "db_errors": [],
  "elapsed_seconds": 8.42,
  "journals": [
    {
      "title": "Jurnal Teknologi Informasi",
      "sinta_rank": "S2",
      "issn": "1234-5678",
      "publisher": "Universitas X",
      "subject": "Computer Science",
      "accreditation": "Sinta 2",
      "url": "https://sinta.kemdikbud.go.id/journals/detail?id=...",
      "scraped_at": "2025-06-14T06:00:00.000Z",
      "source": "sinta.kemdikbud.go.id"
    }
  ]
}
```

### Contoh Response `/api/health`

```json
{
  "status": "healthy",
  "service": "SINTA Journal Scraper",
  "timestamp": "2025-06-14T06:00:00.000Z",
  "config": {
    "sinta_rank": 2,
    "scrape_limit": 10,
    "db_configured": true,
    "cron_secret_set": true
  }
}
```

---

## ⏰ Jadwal Cron

Konfigurasi di `vercel.json`:
```json
{
  "crons": [
    {
      "path": "/api/scrape",
      "schedule": "0 */6 * * *"
    }
  ]
}
```

Cron berjalan setiap **6 jam** pada menit ke-0: `00:00`, `06:00`, `12:00`, `18:00` UTC.

> Vercel Cron Jobs hanya tersedia di plan **Vercel Pro** ke atas.  
> Alternatif gratis: gunakan [GitHub Actions scheduled workflow](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#schedule) untuk hit endpoint `/api/scrape`.

### Alternatif Cron via GitHub Actions (Gratis)

Buat file `.github/workflows/cron.yml`:

```yaml
name: SINTA Scraper Cron
on:
  schedule:
    - cron: '0 */6 * * *'   # Setiap 6 jam
  workflow_dispatch:          # Trigger manual dari GitHub UI

jobs:
  scrape:
    runs-on: ubuntu-latest
    steps:
      - name: Trigger Scraper
        run: |
          curl -s -X GET \
            -H "Authorization: Bearer ${{ secrets.CRON_SECRET }}" \
            https://your-project.vercel.app/api/scrape
```

Tambahkan `CRON_SECRET` di **GitHub → Settings → Secrets and variables → Actions**.

---

## 🔒 Keamanan

- **`CRON_SECRET`**: Semua request ke `/api/scrape` divalidasi header `Authorization: Bearer <secret>`. Tanpa secret yang benar, endpoint mengembalikan `401 Unauthorized`.
- **Supabase RLS**: Row Level Security aktif; hanya `service_role` yang bisa menulis data.
- **`.env` di .gitignore**: File `.env` tidak pernah masuk ke repository.

---

## 🛠️ Development Lokal

```bash
# Clone & setup
git clone https://github.com/USERNAME/sinta-scraper.git
cd sinta-scraper

# Buat virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependensi
pip install -r requirements.txt

# Salin dan isi environment variables
cp .env.example .env
# Edit .env sesuai kebutuhan

# Test lokal (tanpa Vercel)
python -c "
import os; os.environ.setdefault('SINTA_RANK','2'); os.environ.setdefault('SCRAPE_LIMIT','5')
from api.scrape import SintaScraper
s = SintaScraper()
results = s.scrape()
import json; print(json.dumps(results, indent=2, ensure_ascii=False))
"
```

---

## 📊 Query Supabase (Contoh)

```sql
-- Lihat semua jurnal SINTA 2
SELECT title, issn, publisher, scraped_at
FROM sinta_journals
WHERE sinta_rank ILIKE '%S2%' OR sinta_rank = '2'
ORDER BY scraped_at DESC;

-- Hitung jurnal per rank
SELECT sinta_rank, COUNT(*) as total
FROM sinta_journals
GROUP BY sinta_rank
ORDER BY sinta_rank;

-- Jurnal terbaru 24 jam terakhir
SELECT * FROM sinta_journals
WHERE scraped_at > now() - interval '24 hours'
ORDER BY scraped_at DESC;
```

---

## 📝 Lisensi

MIT License — bebas digunakan dan dimodifikasi.
