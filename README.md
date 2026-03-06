# Job Agent V1

Agente personal para buscar, filtrar y puntuar ofertas de trabajo en LinkedIn — sin necesidad de login, con dashboard web y evaluación automática por IA.

> **⚠️ Demo · Uso local y personal**
>
> Este proyecto es una **demo de agentes de IA** diseñada para uso propio en local.
> **No está pensada para ser desplegada en producción ni expuesta a internet.**
> No incluye autenticación robusta, cifrado de datos, auditoría de accesos ni ninguna
> otra medida de seguridad necesaria para un entorno multi-usuario o público.
>
> Si la expones en una red, configura al menos `LOCAL_TOKEN` en el `.env` (ver abajo)
> y asegúrate de que el servidor solo escucha en `127.0.0.1`.

---

## Arquitectura

```
┌─────────────────────────────────────────────────────┐
│  Browser  →  static/index.html  (vanilla JS)        │
│               │                                      │
│               ▼ HTTP / SSE                           │
│           api.py  (FastAPI + uvicorn)                │
│               │                                      │
│        ┌──────┴──────┐                              │
│        ▼             ▼                              │
│   scraper.py    OpenAI API                          │
│  (LinkedIn      (limpieza +                         │
│   guest API)     puntuación)                        │
│        │                                            │
│        ▼                                            │
│    jobs.json  (persistencia local)                  │
└─────────────────────────────────────────────────────┘
```

| Archivo              | Responsabilidad                                              |
|----------------------|--------------------------------------------------------------|
| `scraper.py`         | Scraper de LinkedIn (endpoint guest, sin login)              |
| `api.py`             | REST API (FastAPI) + progreso en tiempo real (SSE)           |
| `static/index.html`  | Dashboard web (vanilla JS, sin dependencias externas)        |
| `jobs.json`          | Almacenamiento local de todas las ofertas                    |
| `.env`               | Variables de entorno (`OPENAI_API_KEY`)                      |

---

## Instalación

```bash
# 1. Crear entorno virtual
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar variables de entorno
cp .env.example .env
# Editar .env y añadir: OPENAI_API_KEY=sk-proj-...
```

---

## Uso

### Dashboard web (recomendado)

```bash
source venv/bin/activate
uvicorn api:app --reload --port 8000
```

Abrir en el navegador: `http://localhost:8000`

Desde el dashboard se puede:
- **Ofertas** — Ver las ofertas remotas ordenadas por fecha, buscar y filtrar
- **Scraping** — Configurar y lanzar el scraper con todos los filtros; ver el progreso en tiempo real
- **Limpieza IA** — Puntuar las ofertas con OpenAI según tu perfil; ver el progreso en vivo

### CLI (sin dashboard)

```bash
source venv/bin/activate
python scraper.py
```

---

## Referencia de filtros de LinkedIn

El scraper usa el endpoint público `jobs-guest` (sin autenticación).
Los parámetros se pasan directamente a la API de LinkedIn — son los mismos que aparecen en la URL del buscador web.

### Parámetros generales

| Parámetro           | Descripción                                         | Ejemplo             |
|---------------------|-----------------------------------------------------|---------------------|
| `keywords`          | Términos de búsqueda                                | `AI Engineer`       |
| `location`          | Ubicación (texto libre)                             | `Spain`             |
| `start`             | Offset para paginación (0, 25, 50…)                 | `start=25`          |
| `sortBy=DD`         | Ordenar por fecha descendente (más recientes antes) | `sortBy=DD`         |

### Modalidad de trabajo — `f_WT`

| Valor | Modalidad      |
|-------|----------------|
| `1`   | Presencial     |
| `2`   | **Remoto** ← el que usamos por defecto |
| `3`   | Híbrido        |

Ejemplo: `f_WT=2`

### Ventana temporal — `f_TPR`

Formato: `r<segundos>` (r = "range")

| Valor       | Ventana           |
|-------------|-------------------|
| `r86400`    | Últimas 24 horas  |
| `r604800`   | Últimos 7 días ← por defecto |
| `r1209600`  | Últimas 2 semanas |
| `r2592000`  | Últimos 30 días   |
| (omitido)   | Sin límite        |

Ejemplo: `f_TPR=r604800`

### Easy Apply — `f_AL`

Solo muestra ofertas con postulación directa desde LinkedIn (sin salir a web externa).

```
f_AL=true
```

### Pocos candidatos — `f_JIYN`

Muestra ofertas con menos de 10 candidatos. Mayor visibilidad del aplicante.

```
f_JIYN=true
```

### Tipo de contrato — `f_JT`

Acepta un valor o lista separada por comas.

| Valor | Tipo                  |
|-------|-----------------------|
| `F`   | Full-time             |
| `P`   | Part-time             |
| `C`   | Contract              |
| `T`   | Temporary             |
| `I`   | Internship            |
| `O`   | Other                 |

Ejemplo single: `f_JT=F`
Ejemplo multi: `f_JT=F,C`

### Nivel de experiencia — `f_E`

Acepta un valor o lista separada por comas.

| Valor | Nivel                         |
|-------|-------------------------------|
| `1`   | Internship                    |
| `2`   | Entry level (Junior)          |
| `3`   | Associate (1–3 años)          |
| `4`   | Mid-Senior (3–7 años)         |
| `5`   | Director                      |
| `6`   | Executive                     |

Ejemplo single: `f_E=4`
Ejemplo multi: `f_E=3,4`

### URL de ejemplo completa

```
https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search
  ?keywords=AI+Engineer
  &location=Spain
  &sortBy=DD
  &f_WT=2
  &f_TPR=r604800
  &f_AL=true
  &f_JT=F
  &f_E=3,4
  &start=0
```

---

## API Reference

Base URL: `http://localhost:8000`

### `GET /api/jobs`

Devuelve la lista de ofertas de `jobs.json` con filtros opcionales.
**Siempre ordenadas por `posted_at` descendente.**

| Query param   | Tipo    | Default | Descripción                              |
|---------------|---------|---------|------------------------------------------|
| `remote_only` | bool    | `true`  | Mostrar solo ofertas remotas             |
| `min_score`   | int     | `null`  | Score mínimo de IA (1–10)                |
| `search`      | string  | `null`  | Búsqueda de texto en título y empresa    |

**Respuesta:**
```json
{
  "jobs": [ ... ],
  "total": 42
}
```

---

### `GET /api/stats`

Estadísticas del estado actual.

```json
{
  "total":        150,
  "remote":       87,
  "scored":       45,
  "last_scraped": "2026-03-06",
  "scraping":     false,
  "cleaning":     false
}
```

---

### `DELETE /api/jobs/{job_id}`

Elimina una oferta de `jobs.json`.

---

### `POST /api/scrape/start`

Inicia el scraping en un hilo de fondo. Devuelve `409` si ya hay uno en curso.

**Body:**
```json
{
  "keywords":           ["AI", "ML Engineer"],
  "location":           "Spain",
  "max_pages":          5,
  "remote_only":        true,
  "days_window":        7,
  "easy_apply":         false,
  "few_applicants":     false,
  "job_types":          ["F"],
  "experience_levels":  [3, 4],
  "fetch_descriptions": true
}
```

---

### `GET /api/scrape/stream`

**Server-Sent Events.** Conectar con `EventSource('/api/scrape/stream')`.

Cada evento tiene la forma:
```json
{ "type": "log", "msg": "texto", "level": "info|success|warning|error", "ts": "HH:MM:SS" }
```

Tipos de evento: `log` | `done` | `error` | `ping`

---

### `GET /api/scrape/status`

```json
{ "running": false }
```

---

### `POST /api/clean/start`

Procesa las ofertas con OpenAI. Requiere `OPENAI_API_KEY` en `.env`.

**Body:**
```json
{
  "model":           "gpt-4o-mini",
  "profile":         "Senior AI/ML engineer buscando roles remotos senior",
  "check_remote":    true,
  "score_relevance": true,
  "force_reclean":   false,
  "min_score_keep":  5
}
```

**Campos añadidos a cada oferta tras la limpieza:**

| Campo                | Descripción                                          |
|----------------------|------------------------------------------------------|
| `ai_score`           | Relevancia 1–10 para el perfil del candidato         |
| `ai_remote_verified` | Si el modelo confirma que es realmente remoto        |
| `ai_notes`           | Observación breve (máx. 80 chars)                    |
| `ai_reject_reason`   | Motivo de rechazo si score ≤ 3                       |
| `ai_cleaned_at`      | Timestamp ISO de la última evaluación                |

---

### `GET /api/clean/stream`

Igual que `/api/scrape/stream` pero para el proceso de limpieza.

---

### `GET /api/clean/status`

```json
{ "running": false }
```

---

## Estructura de `jobs.json`

Cada objeto en el array representa una oferta:

```json
{
  "job_id":             "4382101303",
  "title":              "ML Engineer",
  "company":            "Empresa S.A.",
  "location":           "España",
  "posted_at":          "2026-03-06",
  "posted_text":        "Hace 4 horas",
  "url":                "https://es.linkedin.com/jobs/view/...",
  "description":        "Texto completo de la descripción...",
  "keyword":            "AI",
  "is_remote":          true,
  "ai_score":           8,
  "ai_remote_verified": true,
  "ai_notes":           "Encaja bien. Stack PyTorch + FastAPI.",
  "ai_reject_reason":   null,
  "ai_cleaned_at":      "2026-03-06T19:30:00.000000"
}
```

---

## Variables de entorno (`.env`)

```env
OPENAI_API_KEY=sk-proj-...
```

Solo se necesita para la limpieza con IA. El scraping funciona sin ella.

---

## Limitaciones conocidas

- LinkedIn devuelve ~25 resultados por página en guest mode (sin login).
- LinkedIn corta los resultados alrededor de la oferta 1000 por búsqueda.
- Si se hacen muchas peticiones seguidas, LinkedIn puede devolver HTML vacío o bloquear temporalmente. El scraper incluye pausas aleatorias entre páginas y entre keywords.
- Las URLs de las ofertas incluyen tokens de tracking que pueden caducar.
- `sortBy=DD` funciona en la práctica pero no está documentado oficialmente por LinkedIn.
- `f_JIYN` puede no estar disponible en todos los mercados/idiomas.

---

## Estructura del proyecto

```
JOB_AGENT_V1/
├── scraper.py          # Scraper de LinkedIn (módulo importable + CLI)
├── api.py              # FastAPI backend con SSE
├── static/
│   └── index.html      # Dashboard (vanilla JS, sin dependencias)
├── jobs.json           # Datos persistidos (se crea al primer scraping)
├── .env                # Variables de entorno (NO subir a git)
├── .env.example        # Plantilla del .env
├── requirements.txt    # Dependencias Python
└── venv/               # Entorno virtual (NO subir a git)
```
