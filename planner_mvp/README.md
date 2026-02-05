# NESC Planering (v4.4.8)

NESC Planering är en FastAPI/SQLModel-app för resursplanering.

- **Tidschema** (dag/vecka/månad) med projekt‑% och enheter i timmar
- **Fri text (rosa)** som % (småjobb utan projekt)
- **Rapport** med 30 veckor och färgkodning
- **Företag**: hantera medlemmar och enhetskatalog

Databas:
- Lokalt: **SQLite** (fil)
- Render: **PostgreSQL** via `DATABASE_URL`

## Lokalt (Windows PowerShell)

```powershell
cd planner_mvp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app.seed
python -m uvicorn app.main:app --reload
```

Öppna:
- http://127.0.0.1:8000/

Logga in:
- http://127.0.0.1:8000/login

## Lokalt (macOS/Linux)

```bash
cd planner_mvp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.seed
python -m uvicorn app.main:app --reload
```

## Publicera på Render (Blueprint)

Den här zippen innehåller en `render.yaml` i repo‑roten, så du kan deploya via Render Blueprint.

1) Skapa ett GitHub-repo och pusha allt (inklusive `render.yaml`).
2) Render → **New** → **Blueprint** → välj repot.
3) Vänta tills både web service och databas är "Live".
4) Öppna Render-URL → logga in på `/login`.

### Initiera första gången
Välj en av två vägar:

**A) Skapa via /setup (rekommenderat)**
- Gå till `/setup` på din Render-URL och skapa företag + medlemmar + enhetskatalog.

**B) Skapa demo-data (seed) via Render Shell**
- Render → din web service → **Shell**
- Kör:
  ```bash
  python -m app.seed
  ```

## Uppgradering lokalt (SQLite)

Om du får schema-fel mellan zip-versioner:
1) Stoppa servern
2) Radera `planner.db`
3) Kör seed igen:

```powershell
python -m app.seed
```
