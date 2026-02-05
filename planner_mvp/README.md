# NESC Planering (v4.4.9)

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

## Microsoft-inloggning (Azure AD / Microsoft Entra ID)

Appen stödjer Microsoft-inloggning via OAuth2/OIDC (Authorization Code + PKCE).

### 1) Skapa App registration
Microsoft Entra admin center → **App registrations** → **New registration**.

- Supported account types: vanligtvis **Accounts in this organizational directory only** (Single tenant)
- Redirect URI (Web):
  - `https://DIN-RENDER-URL/auth/microsoft/callback`

Skapa även en **Client secret** (Certificates & secrets).

### 2) Sätt env vars på Render
På din Render Web Service:

- `AUTH_PROVIDER=azuread`
- `PUBLIC_BASE_URL=https://DIN-RENDER-URL`
- `AZURE_TENANT_ID=<din tenant id>` (eller `organizations`)
- `AZURE_CLIENT_ID=<Application (client) ID>`
- `AZURE_CLIENT_SECRET=<client secret value>`

Valfritt (för att låsa ner vem som får logga in):
- `AZURE_ALLOWED_DOMAIN=nesc.se`

### 3) Logga in
Gå till `/login` och klicka **Logga in med Microsoft**.

## Uppgradering lokalt (SQLite)

Om du får schema-fel mellan zip-versioner:
1) Stoppa servern
2) Radera `planner.db`
3) Kör seed igen:

```powershell
python -m app.seed
```
