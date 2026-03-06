# JOB_AGENT_V1 — Guía para Claude Code

## Qué es este proyecto

Agente autónomo de búsqueda de empleo. Rasca ofertas de LinkedIn, las procesa con IA y, para las mejores, busca el contacto de RRHH en internet y envía un email personalizado.

## Stack

- **Backend**: Python 3.12 + FastAPI + Uvicorn
- **Frontend**: HTML/CSS/JS vanilla en `static/index.html` (sin framework, sin build step)
- **IA**: OpenAI SDK (`openai` package) — modelo configurable, por defecto `gpt-4o-mini`
- **Scraping**: `requests` + `BeautifulSoup4` — LinkedIn guest API (sin login)
- **Tiempo real**: SSE (Server-Sent Events) via `StreamManager` en `api.py`
- **Datos**: `jobs.json` — lista de dicts con los trabajos

## Archivos clave

```
api.py          FastAPI + orquestación de hilos + SSE streams
scraper.py      Scraping de LinkedIn (guest API, sin auth)
static/index.html  Dashboard completo (todo en un solo archivo)
jobs.json       Base de datos en disco (generada en runtime, ignorada en git)
.env            OPENAI_API_KEY y futuras claves (ignorado en git)
requirements.txt
```

## Flujo del agente (implementado y por implementar)

### 1. Scraping ✅
`POST /api/scrape/start` → hilo en background → logs via SSE `/api/scrape/stream`
- Rasca LinkedIn por keywords + location con filtros nativos (f_WT, f_TPR, f_AL, etc.)
- Deduplica por `job_id`
- Detecta idioma de la descripción (stopwords, sin dependencias externas)
- Guarda en `jobs.json`

### 2. Limpieza IA ✅
`POST /api/clean/start` → hilo en background → logs via SSE `/api/clean/stream`
- Evalúa cada oferta con OpenAI: `ai_score` (1-10), `ai_remote_verified`, `ai_notes`, `ai_reject_reason`
- Puede eliminar las de score bajo (`min_score_keep`)
- Campos que escribe en cada job: `ai_score`, `ai_remote_verified`, `ai_notes`, `ai_reject_reason`, `ai_cleaned_at`

### 3. Clasificación ⬜ (por implementar)
Agrupación/ranking de los trabajos ya puntuados. Categorías sugeridas:
- Por relevancia: alta (≥8), media (5-7), baja (≤4)
- Por tipo: remoto confirmado vs. no confirmado
- El resultado se guarda como campo `category` en cada job

### 4. Envío de email ⬜ (por implementar)
Para los trabajos de alta relevancia (score ≥ umbral configurable):
1. **Buscar contacto**: usar la API de búsqueda web para encontrar el email o LinkedIn del responsable de RRHH / hiring manager de la empresa
2. **Extraer email**: parsear resultados de búsqueda (Google/Bing/Hunter.io)
3. **Redactar email**: OpenAI genera un email personalizado en base a la oferta y el perfil del candidato
4. **Enviar** (si hay email disponible): via SMTP o API de email (SendGrid, etc.)
5. **Registrar**: guardar en el job los campos `contact_email`, `email_sent`, `email_sent_at`, `email_draft`

Endpoint previsto: `POST /api/email/start` + SSE `/api/email/stream` (mismo patrón que scrape/clean)

## Patrones a respetar

### StreamManager (api.py:54)
Todos los procesos largos usan este patrón:
```python
mgr.log("mensaje", "info|success|warning|error")
mgr.done("Completado")
mgr.error("mensaje de error")
```
El hilo de trabajo se lanza con `threading.Thread(target=run, daemon=True)`.
El event loop se captura en `startup()` y se inyecta con `mgr.set_loop(loop)`.

### Jobs en jobs.json
Campos estándar de cada job:
```
job_id, title, company, location, posted_at, url, description,
keyword, is_remote, lang,
ai_score, ai_remote_verified, ai_notes, ai_reject_reason, ai_cleaned_at,
category,                          ← clasificación (fase 3)
contact_email, email_sent,         ← contacto (fase 4)
email_sent_at, email_draft
```

### Nuevos endpoints
Seguir el patrón existente:
- `POST /api/<proceso>/start` → arranca hilo, devuelve `{"status":"started"}`
- `GET /api/<proceso>/stream` → SSE con logs
- `GET /api/<proceso>/status` → `{"running": bool}`

### Frontend
- Todo en `static/index.html` — no crear archivos JS/CSS separados salvo que sea imprescindible
- El dashboard se comunica con la API via `fetch` y `EventSource`
- Paleta de colores definida como CSS custom properties en `:root`

## Variables de entorno (.env)
```
OPENAI_API_KEY=...        # requerida para fases 2, 3, 4
SMTP_HOST=...             # para envío de emails (fase 4)
SMTP_PORT=...
SMTP_USER=...
SMTP_PASS=...
SENDER_EMAIL=...
# Alternativa: SENDGRID_API_KEY=... o similar
```

## Ejecutar en local
```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # añadir OPENAI_API_KEY
uvicorn api:app --reload --port 8000
# Dashboard en http://localhost:8000
```

## Convenciones
- Español en logs, comentarios y mensajes de usuario
- No añadir dependencias externas sin actualizar `requirements.txt`
- Mantener `jobs.json` ignorado en git (datos de runtime)
- Los errores puntuales de una oferta no deben parar el proceso completo (try/except por oferta)
