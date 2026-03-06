# JOB_AGENT_V1 — Guía para Claude Code

## Propósito del proyecto

Agente autónomo de búsqueda de empleo para uso **local y personal**.
Rasca ofertas de LinkedIn sin login, las filtra y puntúa con IA según el perfil del candidato y, en fases futuras, buscará el contacto de RRHH y enviará emails personalizados automáticamente.

> **Demo de agentes de IA — no apta para producción.**
> No tiene medidas de seguridad para entornos multi-usuario ni exposición pública.

---

## Stack técnico

| Capa | Tecnología |
|------|-----------|
| Backend | Python 3.12 · FastAPI · Uvicorn |
| Frontend | Vanilla HTML/CSS/JS en `static/index.html` (sin framework, sin build step) |
| IA | OpenAI SDK — Structured Outputs via `client.beta.chat.completions.parse()` · modelo por defecto `gpt-5-mini` |
| Scraping | `requests` + `BeautifulSoup4` — LinkedIn guest API (sin autenticación) |
| Tiempo real | SSE (Server-Sent Events) — clase `StreamManager` en `api.py` |
| Persistencia | `jobs.json` + `jobs_discarded.json` — listas de dicts en disco |
| Seguridad | CORS restringido a localhost · token opcional `LOCAL_TOKEN` vía middleware |

---

## Estructura de archivos

```
api.py                FastAPI · orquestación de hilos · SSE · endpoints REST
scraper.py            Scraping LinkedIn (guest API) · detección de idioma · detección de remoto
static/index.html     Dashboard completo (todo en un archivo · vanilla JS)
jobs.json             Ofertas activas (runtime · ignorado en git)
jobs_discarded.json   Ofertas descartadas con motivo (runtime · ignorado en git)
.env                  Claves privadas (ignorado en git)
.env.example          Plantilla documentada del .env
requirements.txt      Dependencias Python
CHECKLIST.md          Estado del proyecto · tareas pendientes
```

---

## Fases del agente

### Fase 1 — Scraping ✅
`POST /api/scrape/start` → hilo daemon → logs SSE en `/api/scrape/stream`
`POST /api/scrape/stop` → señal de parada cooperativa (guarda lo obtenido)

- Parámetros LinkedIn nativos: `f_WT`, `f_TPR`, `f_AL`, `f_JIYN`, `f_JT`, `f_E`, `sortBy=DD`
- Deduplicación por `job_id` (entre sesiones y entre keywords de la misma sesión)
- URLs canónicas sin tokens de tracking (`/jobs/view/{job_id}`)
- Detección de idioma en la **descripción** (ratio de stop-words, sin deps externas)
- Guardado incremental en disco tras cada keyword y tras cada descripción
- Ofertas descartadas por filtro de idioma → `jobs_discarded.json` con `discard_reason`

### Fase 2 — Evaluación IA ✅
`POST /api/clean/start` → hilo daemon → logs SSE en `/api/clean/stream`
`POST /api/clean/stop` → señal de parada cooperativa (guarda lo procesado)

- **Structured Outputs** con schema Pydantic `JobEvaluation` — tipos garantizados
- Perfil configurable desde el dashboard: rol, seniority (peso bajo), stack_yes, stack_no, notas libres
- System prompt con criterios de scoring explícitos (el stack tiene el mayor peso)
- Una llamada OpenAI por oferta — contexto pequeño y scoring independiente
- `max_completion_tokens=2000` — necesario para modelos de thinking (reasoning interno)
- Campos escritos: `ai_score`, `ai_remote_verified`, `ai_notes`, `ai_reject_reason`, `ai_cleaned_at`

### Fase 3 — Clasificación ❌ Descartada
> Redundante con `ai_score` + filtros del dashboard. Se pasa directamente a fase 4.

### Fase 4 — Contacto y envío de email ⬜
`POST /api/email/start` → hilo daemon → logs SSE en `/api/email/stream`

Para cada job con score ≥ umbral configurable:
1. **Buscar contacto** — web search (Hunter.io / Google / Bing) para encontrar email o LinkedIn del hiring manager
2. **Verificar** — confirmar que el contacto corresponde a la empresa
3. **Redactar** — OpenAI genera email personalizado basado en la oferta y el perfil del candidato
4. **Enviar** — SMTP o SendGrid si hay email disponible
5. **Registrar** — `contact_email`, `email_sent`, `email_sent_at`, `email_draft` en el job

---

## Patrones obligatorios

### StreamManager (`api.py`)
```python
# Emitir logs desde cualquier hilo de trabajo
mgr.log("mensaje", "info|success|warning|error")
mgr.done("Completado")
mgr.error("mensaje de error")

# Parada cooperativa
mgr.reset()           # llamar antes de iniciar el hilo
mgr.stop_requested    # property — comprobar en los bucles
mgr.request_stop()    # llamar desde el endpoint /stop
```

El hilo se lanza con `threading.Thread(target=run, daemon=True)`.
El event loop se captura en `startup()` y se inyecta con `mgr.set_loop(loop)`.

### Nuevos procesos largos
Seguir exactamente este patrón de endpoints:
```
POST /api/<proceso>/start   → arranca hilo · devuelve {"status":"started"}
POST /api/<proceso>/stop    → señal de parada cooperativa
GET  /api/<proceso>/stream  → SSE con logs
GET  /api/<proceso>/status  → {"running": bool}
```

### Schema de jobs (`jobs.json`)
```
job_id            str     ID numérico de LinkedIn (clave de deduplicación)
title             str
company           str
location          str
posted_at         str     ISO date YYYY-MM-DD
posted_text       str     texto relativo ("Hace 3 horas")
url               str     URL canónica sin tracking tokens
description       str|null  texto plano de la descripción completa
keyword           str     keyword de búsqueda que encontró esta oferta
is_remote         bool
lang              str     'es' | 'en' | 'unknown'
ai_score          int|null   1–10
ai_remote_verified bool|null
ai_notes          str|null   máx 80 chars
ai_reject_reason  str|null   solo si score ≤ 3
ai_cleaned_at     str|null   ISO datetime
category          str|null   'high'|'mid'|'low'  ← fase 3
contact_email     str|null   ← fase 4
email_sent        bool|null  ← fase 4
email_sent_at     str|null   ← fase 4
email_draft       str|null   ← fase 4
```

### Frontend (`static/index.html`)
- Todo en un único archivo — no crear JS/CSS separados
- Comunicación con la API via `fetch` (helper `API`) y `EventSource`
- Paleta definida como CSS custom properties en `:root`
- Patrón SSE: `sseConnect(path, consoleId, onFinish)`

---

## Variables de entorno

```bash
OPENAI_API_KEY=sk-proj-...     # requerida para fases 2, 3, 4

LOCAL_TOKEN=mi-token-secreto   # opcional — activa autenticación en /api/*
                               # si no se define, la API es libre (solo local)

# Fase 4 — envío de email (pendiente)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=...
SMTP_PASS=...
SENDER_EMAIL=...
# Alternativa: SENDGRID_API_KEY=...
```

---

## Ejecutar en local

```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # añadir OPENAI_API_KEY
uvicorn api:app --reload --port 8000
# Dashboard → http://localhost:8000
```

---

## Convenciones

- **Idioma**: español en logs, comentarios y mensajes de usuario
- **Dependencias**: no añadir sin actualizar `requirements.txt`
- **Errores por oferta**: `try/except` individual — un fallo no para el proceso completo
- **Guardado incremental**: los procesos largos escriben a disco durante la ejecución, no solo al final
- **Runtime data**: `jobs.json` y `jobs_discarded.json` ignorados en git
- **Seguridad**: no relajar CORS ni el middleware de token sin motivo explícito
