"""
api.py — Job Agent REST API
────────────────────────────
FastAPI backend que orquesta:
  • Scraping de LinkedIn (con progreso en tiempo real via SSE)
  • Limpieza/puntuación de ofertas con OpenAI (también via SSE)
  • CRUD básico sobre jobs.json

Ejecutar:
    uvicorn api:app --reload --port 8000
"""

import asyncio
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Carga OPENAI_API_KEY (y cualquier otra variable) desde .env
load_dotenv()

# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Job Agent API",
    description="Backend para scraping de LinkedIn y limpieza de ofertas con IA",
    version="1.0.0",
)

# CORS abierto en desarrollo; restringir en producción según necesidades
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rutas de archivos
JOBS_FILE  = Path("jobs.json")
STATIC_DIR = Path("static")

# ─── StreamManager ────────────────────────────────────────────────────────────

class StreamManager:
    """
    Gestiona un proceso largo (scraping o limpieza) y distribuye sus logs
    a todos los clientes SSE conectados en ese momento.

    Uso típico:
        mgr.log("mensaje")        → envía evento de tipo 'log'
        mgr.done("Completado")    → envía 'done' y marca running=False
        mgr.error("fallo")        → envía 'error' y marca running=False

    Los clientes SSE se suscriben con subscribe() y se eliminan con unsubscribe().
    Thread-safe: el lock protege la lista de suscriptores.
    """

    def __init__(self, name: str):
        self.name    = name
        self.running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._subscribers: list[asyncio.Queue] = []
        self._lock = threading.Lock()

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        """Guarda el event loop de asyncio para poder enviar desde hilos."""
        self._loop = loop

    # ── Métodos de emisión (llamados desde hilos de trabajo) ──────────────────

    def log(self, msg: str, level: str = "info"):
        """Emite un mensaje de log con nivel: info | success | warning | error."""
        self._emit({
            "type":  "log",
            "msg":   msg.strip(),
            "level": level,
            "ts":    datetime.now().strftime("%H:%M:%S"),
        })

    def done(self, msg: str = "Completado"):
        """Emite evento de finalización y desactiva el flag 'running'."""
        self.running = False
        self._emit({"type": "done", "msg": msg, "ts": datetime.now().strftime("%H:%M:%S")})

    def error(self, msg: str):
        """Emite evento de error y desactiva el flag 'running'."""
        self.running = False
        self._emit({"type": "error", "msg": msg, "ts": datetime.now().strftime("%H:%M:%S")})

    # ── Internos ──────────────────────────────────────────────────────────────

    def _emit(self, event: dict):
        """Envía el evento al event loop de asyncio desde cualquier hilo."""
        if not self._loop:
            return
        asyncio.run_coroutine_threadsafe(self._broadcast(event), self._loop)

    async def _broadcast(self, event: dict):
        """Pone el evento en la cola de cada suscriptor conectado."""
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Si la cola del cliente está llena, descartamos (no bloqueamos)
                pass

    def subscribe(self) -> asyncio.Queue:
        """Registra un nuevo cliente SSE y devuelve su cola de eventos."""
        q = asyncio.Queue(maxsize=500)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        """Elimina un cliente SSE de la lista de suscriptores."""
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)


# Una instancia por proceso largo
scrape_mgr = StreamManager("scrape")
clean_mgr  = StreamManager("clean")


@app.on_event("startup")
async def startup():
    """Captura el event loop al arrancar para poder usar los StreamManagers."""
    loop = asyncio.get_running_loop()
    scrape_mgr.set_loop(loop)
    clean_mgr.set_loop(loop)
    STATIC_DIR.mkdir(exist_ok=True)

# ─── Helpers ──────────────────────────────────────────────────────────────────


def load_jobs() -> list[dict]:
    """Lee jobs.json y devuelve la lista completa. Devuelve [] si no existe."""
    if not JOBS_FILE.exists():
        return []
    with open(JOBS_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_jobs(jobs: list[dict]):
    """Serializa la lista de trabajos a jobs.json."""
    with open(JOBS_FILE, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)


# Palabras clave para detección de remoto en la API
# (misma lógica que en scraper.py; duplicada para que la API sea autocontenida)
REMOTE_LOC_EXACT = {"españa", "spain", "españa y alrededores", "spain and surroundings"}
REMOTE_KEYWORDS  = ["remot", "teletrabaj", "remote", "work from home", "wfh"]


def is_remote(job: dict) -> bool:
    """
    Determina si una oferta es remota.
    Prioriza el campo 'is_remote' ya calculado en el scraper.
    Si no existe, aplica la heurística por texto.
    """
    if "is_remote" in job:
        return bool(job["is_remote"])
    loc   = (job.get("location") or "").lower().strip()
    title = (job.get("title")    or "").lower()
    if loc in REMOTE_LOC_EXACT:
        return True
    for kw in REMOTE_KEYWORDS:
        if kw in loc or kw in title:
            return True
    return False


def enrich_jobs(jobs: list[dict]) -> list[dict]:
    """Añade/actualiza el campo 'is_remote' en cada trabajo."""
    for job in jobs:
        job["is_remote"] = is_remote(job)
    return jobs

# ─── SSE helper ───────────────────────────────────────────────────────────────


async def sse_generator(request: Request, mgr: StreamManager):
    """
    Generador async para Server-Sent Events.
    Se suscribe al StreamManager, espera eventos y los formatea como SSE.
    Envía pings cada 15s para mantener la conexión viva.
    Se desuscribe automáticamente cuando el cliente desconecta o el proceso termina.
    """
    q = mgr.subscribe()
    try:
        while True:
            # Detectar desconexión del cliente (p.ej. cierra la pestaña)
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(q.get(), timeout=15.0)
                yield f"data: {json.dumps(event)}\n\n"
                # 'done' o 'error' señalan el fin del proceso
                if event.get("type") in ("done", "error"):
                    break
            except asyncio.TimeoutError:
                # Ping para mantener la conexión viva (evita timeouts de proxies)
                yield "data: {\"type\":\"ping\"}\n\n"
    finally:
        mgr.unsubscribe(q)


def sse_response(request: Request, mgr: StreamManager) -> StreamingResponse:
    """Envuelve el generador SSE en una StreamingResponse con las cabeceras correctas."""
    return StreamingResponse(
        sse_generator(request, mgr),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "Connection":       "keep-alive",
            "X-Accel-Buffering": "no",   # desactiva buffering en Nginx si se usa de proxy
        },
    )

# ─── Modelos Pydantic ─────────────────────────────────────────────────────────


class ScrapeParams(BaseModel):
    """
    Parámetros configurables para una sesión de scraping.
    Todos los campos f_* se traducen directamente a parámetros del endpoint guest de LinkedIn.
    """

    keywords: list[str] = Field(
        default=["AI", "software architect", "engineer"],
        description="Lista de términos de búsqueda",
    )
    location: str = Field(
        default="Spain",
        description="Ubicación de búsqueda (texto libre, igual que en el buscador de LinkedIn)",
    )
    max_pages: int = Field(
        default=5,
        ge=1, le=40,
        description="Páginas por keyword. Cada página devuelve ~25 resultados en guest mode.",
    )
    # ── Filtros nativos LinkedIn ───────────────────────────────────────────────
    remote_only: bool = Field(
        default=True,
        description="f_WT=2 → solo trabajos marcados como Remote en LinkedIn",
    )
    days_window: int = Field(
        default=7,
        ge=0, le=30,
        description="f_TPR=r<segundos> → antigüedad máxima en días (0 = sin límite)",
    )
    easy_apply: bool = Field(
        default=False,
        description="f_AL=true → solo ofertas con postulación directa (Easy Apply)",
    )
    few_applicants: bool = Field(
        default=False,
        description="f_JIYN=true → menos de 10 candidatos; mayor visibilidad",
    )
    job_types: list[str] = Field(
        default=[],
        description=(
            "f_JT → tipos de contrato. Valores: F=Full-time P=Part-time "
            "C=Contract T=Temporary I=Internship O=Other. "
            "Lista vacía = todos."
        ),
    )
    experience_levels: list[int] = Field(
        default=[],
        description=(
            "f_E → niveles de experiencia. Valores: 1=Internship 2=Entry 3=Associate "
            "4=Mid-Senior 5=Director 6=Executive. "
            "Lista vacía = todos."
        ),
    )
    # ── Filtro de idioma ──────────────────────────────────────────────────────
    lang_filter: Optional[str] = Field(
        default=None,
        description=(
            "Filtrar ofertas por idioma de la DESCRIPCIÓN (no del título). "
            "Valores: 'es' = solo español | 'en' = solo inglés | None = sin filtro. "
            "Cuando está activo, fetch_descriptions se fuerza a True automáticamente."
        ),
    )
    # ── Opciones de scraping ───────────────────────────────────────────────────
    fetch_descriptions: bool = Field(
        default=True,
        description="Obtener el HTML de detalle de cada oferta para extraer la descripción completa",
    )


class CleanParams(BaseModel):
    """Parámetros para la limpieza y puntuación de ofertas con OpenAI."""

    model: str = Field(
        default="gpt-4o-mini",
        description="Modelo de OpenAI a usar (gpt-4o-mini, gpt-4o, gpt-4-turbo...)",
    )
    profile: str = Field(
        default="Senior AI/ML engineer buscando roles remotos senior",
        description="Descripción del perfil y preferencias del candidato",
    )
    check_remote: bool = Field(
        default=True,
        description="Pedir al modelo que confirme si el trabajo es realmente remoto",
    )
    score_relevance: bool = Field(
        default=True,
        description="Pedir una puntuación 1-10 de relevancia para el perfil",
    )
    force_reclean: bool = Field(
        default=False,
        description="Si True, reprocesa también trabajos ya puntuados",
    )
    min_score_keep: Optional[int] = Field(
        default=None,
        ge=1, le=10,
        description="Eliminar de jobs.json los trabajos con score por debajo de este umbral",
    )

# ─── Endpoints: Jobs ──────────────────────────────────────────────────────────


@app.get("/api/jobs", summary="Listar ofertas")
async def get_jobs(
    remote_only: bool          = True,
    min_score:   Optional[int] = None,
    search:      Optional[str] = None,
):
    """
    Devuelve la lista de ofertas aplicando filtros opcionales.
    Siempre ordenadas por fecha de publicación descendente (más recientes primero).

    Params:
        remote_only: si True, devuelve solo las marcadas como is_remote=True
        min_score:   filtro mínimo de ai_score (solo si la oferta ya fue puntuada)
        search:      búsqueda de texto libre en título y empresa
    """
    jobs = enrich_jobs(load_jobs())

    # Filtrar solo remotas
    if remote_only:
        jobs = [j for j in jobs if j.get("is_remote")]

    # Filtrar por score mínimo (ignora trabajos sin score)
    if min_score is not None:
        jobs = [j for j in jobs if (j.get("ai_score") or 0) >= min_score]

    # Búsqueda de texto libre
    if search:
        s = search.lower()
        jobs = [
            j for j in jobs
            if s in (j.get("title")   or "").lower()
            or s in (j.get("company") or "").lower()
        ]

    # Ordenar por fecha descendente; None al final
    jobs.sort(key=lambda j: j.get("posted_at") or "", reverse=True)

    return {"jobs": jobs, "total": len(jobs)}


@app.delete("/api/jobs/{job_id}", summary="Eliminar una oferta")
async def delete_job(job_id: str):
    """Elimina un trabajo de jobs.json por su job_id numérico."""
    jobs   = load_jobs()
    before = len(jobs)
    jobs   = [j for j in jobs if str(j.get("job_id")) != job_id]
    if len(jobs) == before:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
    save_jobs(jobs)
    return {"deleted": True, "remaining": len(jobs)}


@app.get("/api/stats", summary="Estadísticas generales")
async def get_stats():
    """
    Resumen rápido del estado actual de jobs.json y de los procesos en curso.
    Consultado periódicamente por el dashboard para actualizar la barra de stats.
    """
    jobs          = enrich_jobs(load_jobs())
    remote_count  = sum(1 for j in jobs if j.get("is_remote"))
    scored_count  = sum(1 for j in jobs if j.get("ai_score") is not None)
    dates         = [j.get("posted_at") for j in jobs if j.get("posted_at")]

    return {
        "total":        len(jobs),
        "remote":       remote_count,
        "scored":       scored_count,
        "last_scraped": max(dates) if dates else None,
        "scraping":     scrape_mgr.running,
        "cleaning":     clean_mgr.running,
    }

# ─── Endpoints: Scraping ──────────────────────────────────────────────────────


@app.post("/api/scrape/start", summary="Iniciar scraping")
async def start_scrape(params: ScrapeParams):
    """
    Arranca el scraper en un hilo de fondo.
    Devuelve 409 si ya hay un scraping en curso.
    Los logs se reciben en tiempo real conectándose a /api/scrape/stream (SSE).
    """
    if scrape_mgr.running:
        raise HTTPException(status_code=409, detail="Scraping ya en curso")

    scrape_mgr.running = True

    def run():
        """Hilo de trabajo: llama al scraper y redirige los logs al StreamManager."""
        try:
            # Importamos aquí para que los errores de import lleguen al log
            from scraper import (
                detect_remote,
                fetch_description,
                load_existing,
                save_all,
                scrape_listing,
            )

            import random

            scrape_mgr.log(
                f"Iniciando scraping | keywords={params.keywords} | "
                f"location={params.location} | max_pages={params.max_pages} | "
                f"remote_only={params.remote_only} | days_window={params.days_window}d"
            )

            existing = load_existing()
            scrape_mgr.log(f"Trabajos existentes en disco: {len(existing)}")

            new_jobs: dict = {}

            # ── Fase 1: listados ──────────────────────────────────────────────
            for i, keyword in enumerate(params.keywords):
                scrape_mgr.log(f"{'─'*40}")
                scrape_mgr.log(f"[{i+1}/{len(params.keywords)}] '{keyword}' en {params.location}")

                found = scrape_listing(
                    keyword,
                    params.location,
                    params.max_pages,
                    remote_only       = params.remote_only,
                    days_window       = params.days_window,
                    easy_apply        = params.easy_apply,
                    few_applicants    = params.few_applicants,
                    job_types         = params.job_types or None,
                    experience_levels = params.experience_levels or None,
                    log_fn            = lambda m: scrape_mgr.log(m),
                )

                added = 0
                for job in found:
                    jid = job.get("job_id")
                    if not jid:
                        continue  # sin ID no podemos deduplicar
                    if jid in existing or jid in new_jobs:
                        continue  # duplicado, skip
                    job["keyword"]   = keyword
                    job["is_remote"] = detect_remote(job)
                    new_jobs[jid]    = job
                    added += 1

                scrape_mgr.log(
                    f"→ {added} nuevos (de {len(found)} encontrados)",
                    "success",
                )

                # Pausa entre keywords para evitar bloqueos por rate-limit
                if i < len(params.keywords) - 1:
                    wait = 12
                    scrape_mgr.log(f"Pausa {wait}s entre keywords...")
                    time.sleep(wait)

            scrape_mgr.log(f"\nTotal trabajos nuevos esta sesión: {len(new_jobs)}")

            # ── Fase 2: descripciones + detección de idioma ───────────────────
            # El filtro de idioma requiere la descripción: forzamos fetch si está activo
            must_fetch = params.fetch_descriptions or bool(params.lang_filter)

            if new_jobs and must_fetch:
                from scraper import detect_language

                if params.lang_filter:
                    scrape_mgr.log(
                        f"Obteniendo descripciones + filtro de idioma: solo '{params.lang_filter}'"
                    )
                else:
                    scrape_mgr.log("Obteniendo descripciones completas...")

                skipped_lang = 0
                jobs_list = list(new_jobs.values())

                for i, job in enumerate(jobs_list):
                    jid = job.get("job_id")
                    if not jid:
                        continue

                    scrape_mgr.log(
                        f"[{i+1}/{len(jobs_list)}] {job['title']} @ {job.get('company', '?')}"
                    )
                    desc = fetch_description(jid)
                    job["description"] = desc

                    # Detectar idioma de la descripción (NO del título)
                    lang = detect_language(desc) if desc else "unknown"
                    job["lang"] = lang

                    # Aplicar filtro de idioma si está configurado
                    if params.lang_filter and lang not in ("unknown", params.lang_filter):
                        scrape_mgr.log(
                            f"  Excluida: descripción en '{lang}' (filtro: solo '{params.lang_filter}')",
                            "warning",
                        )
                        del new_jobs[jid]
                        skipped_lang += 1
                    else:
                        lang_tag = f" [{lang}]" if lang != "unknown" else ""
                        if desc:
                            scrape_mgr.log(f"  OK ({len(desc)} chars{lang_tag})", "success")
                        else:
                            scrape_mgr.log("  sin descripción", "warning")
                        new_jobs[jid] = job

                    time.sleep(random.uniform(3, 6))

                if params.lang_filter and skipped_lang:
                    scrape_mgr.log(
                        f"Filtro idioma: {skipped_lang} ofertas excluidas (descripción no en '{params.lang_filter}')",
                        "info",
                    )

            # ── Fase 3: persistir ─────────────────────────────────────────────
            combined = {**existing, **new_jobs}
            save_all(combined, log_fn=lambda m: scrape_mgr.log(m, "success"))

            scrape_mgr.done(f"Completado. {len(new_jobs)} trabajos nuevos guardados.")

        except Exception as exc:
            scrape_mgr.error(f"Error inesperado: {exc}")

    threading.Thread(target=run, daemon=True, name="scraper").start()
    return {"status": "started", "message": "Scraping iniciado; conéctate a /api/scrape/stream para ver el progreso"}


@app.get("/api/scrape/status", summary="Estado del scraping")
async def scrape_status():
    """Indica si hay un scraping en curso. Útil al recargar el dashboard."""
    return {"running": scrape_mgr.running}


@app.get("/api/scrape/stream", summary="Stream SSE del scraping")
async def scrape_stream(request: Request):
    """
    Endpoint SSE. El cliente se conecta con EventSource y recibe logs en tiempo real.
    Cada evento tiene la forma: { type, msg, level, ts }
    Tipos: log | done | error | ping
    """
    return sse_response(request, scrape_mgr)

# ─── Endpoints: Limpieza IA ───────────────────────────────────────────────────


@app.post("/api/clean/start", summary="Iniciar limpieza con OpenAI")
async def start_clean(params: CleanParams):
    """
    Procesa las ofertas de jobs.json con OpenAI para:
      • Puntuar relevancia (1-10) según el perfil del candidato
      • Verificar si el trabajo es realmente remoto (leyendo la descripción)
      • Generar notas breves

    Requiere OPENAI_API_KEY en el .env.
    Los resultados se guardan en jobs.json en los campos: ai_score, ai_remote_verified,
    ai_notes, ai_reject_reason, ai_cleaned_at.
    """
    if clean_mgr.running:
        raise HTTPException(status_code=409, detail="Limpieza ya en curso")

    # Verificar API key antes de lanzar el hilo
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="OPENAI_API_KEY no está configurada en el .env",
        )

    clean_mgr.running = True

    def run():
        """Hilo de trabajo: itera las ofertas y llama a la API de OpenAI."""
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key)

            jobs = load_jobs()

            # Seleccionar qué trabajos procesar
            to_process = (
                jobs
                if params.force_reclean
                else [j for j in jobs if j.get("ai_score") is None]
            )

            clean_mgr.log(
                f"Modelo: {params.model} | "
                f"Procesando: {len(to_process)}/{len(jobs)} trabajos"
            )
            clean_mgr.log(f"Perfil: {params.profile}")

            # System prompt que define el comportamiento del modelo
            system_prompt = (
                f"Eres un asistente que evalúa ofertas de trabajo para un candidato con este perfil:\n"
                f"{params.profile}\n\n"
                "Responde SIEMPRE en JSON con exactamente estos campos:\n"
                "{\n"
                '  "score": <número 1-10 de relevancia para el perfil>,\n'
                '  "is_remote": <true o false — ¿el trabajo es realmente remoto según la descripción?>,\n'
                '  "notes": "<observaciones breves en español, máx 80 chars>",\n'
                '  "reject_reason": "<motivo si score <= 3, sino null>"\n'
                "}"
            )

            processed = 0
            for job in to_process:
                title    = job.get("title",    "N/A")
                company  = job.get("company",  "N/A")
                location = job.get("location", "N/A")
                # Truncamos la descripción para no gastar tokens en exceso
                desc = (job.get("description") or "")[:1500]

                clean_mgr.log(f"[{processed+1}/{len(to_process)}] {title} @ {company}")

                user_content = (
                    f"Título: {title}\n"
                    f"Empresa: {company}\n"
                    f"Ubicación: {location}\n"
                    f"Descripción:\n{desc if desc else 'No disponible'}"
                )

                try:
                    resp = client.chat.completions.create(
                        model=params.model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user",   "content": user_content},
                        ],
                        # json_object garantiza respuesta JSON parseable
                        response_format={"type": "json_object"},
                        temperature=0.1,   # baja temperatura → respuestas consistentes
                        max_tokens=200,
                    )

                    result = json.loads(resp.choices[0].message.content)

                    # Escribir resultados en el objeto del trabajo
                    job["ai_score"]           = result.get("score")
                    job["ai_remote_verified"] = result.get("is_remote")
                    job["ai_notes"]           = result.get("notes")
                    job["ai_reject_reason"]   = result.get("reject_reason")
                    job["ai_cleaned_at"]      = datetime.now().isoformat()

                    score = job["ai_score"] or 0
                    level = "success" if score >= 7 else "warning" if score >= 5 else "error"
                    clean_mgr.log(
                        f"  Score: {score}/10 — {job.get('ai_notes', '')}",
                        level,
                    )

                except Exception as exc:
                    # Error puntual en una oferta; continuamos con las demás
                    clean_mgr.log(f"  Error OpenAI: {exc}", "error")

                processed += 1
                # Pausa mínima para respetar el rate-limit de OpenAI en tier bajo
                time.sleep(0.3)

            # Guardar resultados
            save_jobs(jobs)

            # Opcional: eliminar trabajos bajo el umbral de score
            if params.min_score_keep is not None:
                before  = len(jobs)
                jobs    = [
                    j for j in jobs
                    if (j.get("ai_score") or 0) >= params.min_score_keep
                    or j.get("ai_score") is None   # conservamos los no puntuados
                ]
                removed = before - len(jobs)
                save_jobs(jobs)
                clean_mgr.log(
                    f"Eliminados {removed} trabajos con score < {params.min_score_keep}",
                    "info",
                )

            clean_mgr.done(f"Limpieza completada. {processed} trabajos procesados.")

        except Exception as exc:
            clean_mgr.error(f"Error inesperado: {exc}")

    threading.Thread(target=run, daemon=True, name="cleaner").start()
    return {"status": "started", "message": "Limpieza iniciada; conéctate a /api/clean/stream para ver el progreso"}


@app.get("/api/clean/status", summary="Estado de la limpieza")
async def clean_status():
    """Indica si hay una limpieza en curso. Útil al recargar el dashboard."""
    return {"running": clean_mgr.running}


@app.get("/api/clean/stream", summary="Stream SSE de la limpieza")
async def clean_stream(request: Request):
    """
    Endpoint SSE. El cliente se conecta con EventSource y recibe el progreso
    de la limpieza IA en tiempo real.
    """
    return sse_response(request, clean_mgr)

# ─── Archivos estáticos y dashboard ───────────────────────────────────────────

# Servir el dashboard (static/index.html y sus assets si los hubiera)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", include_in_schema=False)
async def root():
    """Sirve el dashboard principal."""
    return FileResponse("static/index.html")


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
