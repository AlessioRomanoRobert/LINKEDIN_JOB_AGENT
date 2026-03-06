"""
scraper.py — LinkedIn Jobs Guest API scraper
─────────────────────────────────────────────
Usa el endpoint público /jobs-guest (no requiere autenticación).
Parámetros clave de LinkedIn:
  f_WT=2          → solo trabajos remotos  (1=presencial, 2=remoto, 3=híbrido)
  f_TPR=r<secs>   → antigüedad máxima en segundos (r604800 = últimos 7 días)
  sortBy=DD       → ordenar por fecha descendente
"""

import json
import os
import random
import time

import requests
from bs4 import BeautifulSoup

# ─── Configuración por defecto (usable desde CLI o como módulo) ───────────────

JOBS_FILE  = "jobs.json"
LOCATION   = "Spain"
SEARCHES   = ["AI", "software architect", "engineer"]
MAX_PAGES  = 5     # cada página devuelve ~25 resultados en modo guest

# ─── Endpoints de LinkedIn (guest, sin login) ─────────────────────────────────

#  seeMoreJobPostings: listado paginado de tarjetas de trabajo
LIST_URL   = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

#  jobPosting: detalle completo de una oferta por su ID numérico
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

# ─── Cabeceras HTTP para parecer un navegador real ────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Referer":         "https://www.linkedin.com/jobs/search/",
}

# ─── Referencia de parámetros de filtrado de LinkedIn ─────────────────────────
#
# LinkedIn expone estos parámetros en el endpoint guest (sin login).
# Son los mismos que aparecen en la URL del buscador web.
#
# MODALIDAD DE TRABAJO  f_WT
#   1 → Presencial (On-site)
#   2 → Remoto     (Remote)       ← el que más nos interesa
#   3 → Híbrido    (Hybrid)
#
# VENTANA TEMPORAL  f_TPR=r<segundos>
#   r86400    → últimas 24 horas
#   r604800   → últimos 7 días    ← por defecto
#   r2592000  → últimos 30 días
#   (omitido) → sin límite
#
# SOLO EASY APPLY  f_AL=true
#   Muestra únicamente ofertas que permiten postularse desde LinkedIn (1 clic).
#
# POCOS CANDIDATOS  f_JIYN=true
#   Filtra ofertas con menos de 10 aplicantes. Alta tasa de respuesta.
#
# TIPO DE CONTRATO  f_JT  (puede ser valor único o lista separada por comas)
#   F → Full-time     (Jornada completa)
#   P → Part-time     (Parcial)
#   C → Contract      (Contrato por obra)
#   T → Temporary     (Temporal)
#   I → Internship    (Prácticas)
#   O → Other         (Otros)
#   Ejemplo multi-valor: f_JT=F,C
#
# NIVEL DE EXPERIENCIA  f_E  (puede ser valor único o lista separada por comas)
#   1 → Internship    (Prácticas)
#   2 → Entry level   (Junior / sin experiencia)
#   3 → Associate     (1–3 años)
#   4 → Mid-Senior    (3–7 años)     ← el más común para perfiles consolidados
#   5 → Director
#   6 → Executive
#   Ejemplo multi-valor: f_E=3,4
#
# ORDENACIÓN  sortBy
#   DD → Date Descending (más recientes primero)
#   R  → Relevance       (relevancia)
#
# PAGINACIÓN  start=0, 25, 50, ...
#   El endpoint devuelve ~25 resultados por página en modo guest.

# ─── Detección de idioma ─────────────────────────────────────────────────────
#
# Método: ratio de stop-words conocidas.
# Sin dependencias externas — funciona offline.
# Se aplica sobre la DESCRIPCIÓN de la oferta, nunca sobre el título.
#
# Stop-words españolas muy frecuentes en ofertas de trabajo
_ES_STOPWORDS = frozenset({
    "de","la","el","en","y","que","para","con","los","las","del","por",
    "una","sus","es","se","lo","al","un","le","más","pero","como","este",
    "esta","hay","tiene","son","está","también","nos","su","si","ya","todo",
    "ser","han","cuando","donde","sobre","entre","muy","bien","así","sin",
    "hasta","desde","aunque","durante","mientras","puede","puedo","podemos",
    "nuestro","nuestra","nuestros","equipo","empresa","experiencia","años",
    "trabajar","trabajo","buscamos","ofrecemos","requisitos","habilidades",
    "conocimientos","incorporarte","incorporación","funciones","tareas",
})

# Stop-words inglesas muy frecuentes en ofertas de trabajo
_EN_STOPWORDS = frozenset({
    "the","and","of","to","in","is","for","are","with","that","this","by",
    "at","from","be","have","it","on","as","or","an","will","can","your",
    "their","do","all","about","more","has","which","its","they","was",
    "were","been","but","we","you","not","our","what","when","where","who",
    "join","team","work","experience","years","skills","requirements","role",
    "position","looking","seeking","you'll","we're","we'll","company",
    "must","should","would","could","may","please","include","including",
    "responsibilities","qualifications","preferred","required","based",
})


def detect_language(text: str) -> str:
    """
    Detecta el idioma principal de un texto mediante ratio de stop-words.
    Diseñado para descripciones de ofertas de trabajo.

    Devuelve:
        'es'      → texto mayoritariamente en español
        'en'      → texto mayoritariamente en inglés
        'unknown' → texto demasiado corto o ambiguo

    Nota: Se aplica SOLO a la descripción, nunca al título.
          Los títulos en inglés son normales incluso en ofertas españolas.
    """
    import re

    if not text or len(text) < 80:
        return "unknown"

    # Tokenizar (incluye letras acentuadas)
    words = re.findall(r"\b[a-zA-ZáéíóúñüÁÉÍÓÚÑÜ]{2,}\b", text.lower())
    if not words:
        return "unknown"

    # Usar los primeros 300 tokens para eficiencia
    sample = words[:300]
    es_hits = sum(1 for w in sample if w in _ES_STOPWORDS)
    en_hits = sum(1 for w in sample if w in _EN_STOPWORDS)

    total = es_hits + en_hits
    if total < 6:
        return "unknown"   # muy pocos indicadores → no concluimos

    ratio = es_hits / total   # 1.0 = todo español, 0.0 = todo inglés
    if ratio >= 0.55:
        return "es"
    if ratio <= 0.45:
        return "en"
    return "unknown"   # zona gris ~50/50 (ej. descripción bilingüe)


# ─── Detección de remoto (heurística de respaldo) ─────────────────────────────
# Cuando NO se usa f_WT=2, LinkedIn puede devolver roles híbridos o presenciales.
# Esta función ayuda a clasificarlos por texto si fuera necesario.

REMOTE_LOC_EXACT = {"españa", "spain", "españa y alrededores", "spain and surroundings"}
REMOTE_KEYWORDS  = ["remot", "teletrabaj", "remote", "work from home", "wfh"]


def detect_remote(job: dict) -> bool:
    """
    Heurística local para marcar un trabajo como remoto.
    Primero mira la ubicación exacta (país sin ciudad → suele ser remoto en LinkedIn),
    luego busca palabras clave en ubicación y título.
    """
    loc   = (job.get("location") or "").lower().strip()
    title = (job.get("title")    or "").lower()

    # Ubicación de país completo en LinkedIn = oferta abierta a todo el país → remoto
    if loc in REMOTE_LOC_EXACT:
        return True

    # Palabras clave explícitas
    for kw in REMOTE_KEYWORDS:
        if kw in loc or kw in title:
            return True

    return False

# ─── Persistencia ─────────────────────────────────────────────────────────────


def load_existing() -> dict:
    """
    Carga jobs.json y devuelve un dict {job_id: job}.
    Los trabajos sin job_id se ignoran (no podemos deduplicarlos).
    """
    if not os.path.exists(JOBS_FILE):
        return {}
    with open(JOBS_FILE, "r", encoding="utf-8") as f:
        jobs = json.load(f)
    return {j["job_id"]: j for j in jobs if j.get("job_id")}


def save_all(jobs_by_id: dict, log_fn=print):
    """Serializa el dict {id: job} a jobs.json como lista."""
    jobs = list(jobs_by_id.values())
    with open(JOBS_FILE, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)
    log_fn(f"✓ Guardados {len(jobs)} trabajos en '{JOBS_FILE}'")

# ─── Parseo de tarjeta HTML ───────────────────────────────────────────────────


def extract_job_id(href: str) -> str | None:
    """
    Extrae el job_id numérico de una URL de LinkedIn.
    Formato esperado: .../jobs/view/some-title-1234567890?...
    El ID siempre es el último segmento numérico antes del '?'.
    """
    try:
        path = href.split("?")[0].rstrip("/")
        last = path.split("-")[-1]
        return last if last.isdigit() else None
    except Exception:
        return None


def parse_card(card) -> dict | None:
    """
    Parsea un elemento <li> de la respuesta del listado y extrae los campos
    básicos de la oferta. Devuelve None si el card no contiene un trabajo válido.
    """
    # El enlace principal contiene la URL y, extraíble de ella, el job_id
    link_el = card.find("a", class_="base-card__full-link")
    if not link_el:
        return None

    href   = link_el.get("href", "")
    job_id = extract_job_id(href)

    # Fallback: intentar extraer el ID del atributo data-entity-urn
    if not job_id:
        urn    = card.get("data-entity-urn", "")
        job_id = urn.split(":")[-1] if urn else None

    title_el   = card.find("h3", class_="base-search-card__title")
    company_el = card.find("h4", class_="base-search-card__subtitle")
    loc_el     = card.find("span", class_="job-search-card__location")
    date_el    = card.find("time")

    title = title_el.get_text(strip=True) if title_el else None
    if not title:
        # Card sin título → no es una oferta de trabajo real
        return None

    job = {
        "job_id":      job_id,
        "title":       title,
        "company":     company_el.get_text(strip=True) if company_el else None,
        "location":    loc_el.get_text(strip=True)     if loc_el     else None,
        # posted_at viene en formato ISO (YYYY-MM-DD) dentro del atributo datetime
        "posted_at":   date_el.get("datetime")         if date_el    else None,
        "posted_text": date_el.get_text(strip=True)    if date_el    else None,
        # URL canónica sin tokens de tracking (refId, trackingId, etc.) que caducan
        "url":         f"https://www.linkedin.com/jobs/view/{job_id}",
        "description": None,   # se rellena en fetch_description()
        "keyword":     None,   # se rellena en main()
    }

    # Marcar si es remoto con la heurística local (complementa f_WT=2)
    job["is_remote"] = detect_remote(job)
    return job

# ─── Scraping de listados ─────────────────────────────────────────────────────


def scrape_listing(
    keyword:           str,
    location:          str,
    max_pages:         int,
    remote_only:       bool            = True,
    days_window:       int             = 7,
    easy_apply:        bool            = False,
    few_applicants:    bool            = False,
    job_types:         list[str] | None = None,
    experience_levels: list[int] | None = None,
    log_fn = print,
) -> list[dict]:
    """
    Rasca las páginas de resultados de LinkedIn para una keyword+ubicación,
    aplicando los filtros nativos del endpoint guest.

    Args:
        keyword:           término de búsqueda (ej. "AI Engineer")
        location:          ubicación (ej. "Spain")
        max_pages:         páginas a raspar (~25 resultados/página en guest mode)
        remote_only:       f_WT=2 → solo trabajos remotos según LinkedIn
        days_window:       f_TPR=r<s> → antigüedad máxima en días (0 = sin límite)
        easy_apply:        f_AL=true → solo ofertas con postulación directa en LinkedIn
        few_applicants:    f_JIYN=true → menos de 10 candidatos (más fácil de conseguir)
        job_types:         f_JT → lista de tipos: F, P, C, T, I, O
                           (Full-time, Part-time, Contract, Temporary, Internship, Other)
        experience_levels: f_E → lista de niveles: 1=Internship 2=Entry 3=Associate
                           4=Mid-Senior 5=Director 6=Executive
        log_fn:            callback de logging (redirigido a SSE cuando se llama desde la API)

    Returns:
        Lista de dicts con los campos básicos de cada oferta encontrada.
    """
    results   = []
    page_size = 25  # LinkedIn devuelve hasta 25 resultados por página en guest mode

    # ── Construcción de parámetros del query ──────────────────────────────────
    base_params: dict = {
        "keywords": keyword,
        "location": location,
        "sortBy":   "DD",   # Date Descending: más recientes primero
    }

    if remote_only:
        # f_WT=2: filtra en origen. Mucho más preciso que buscar "remoto" en el texto.
        base_params["f_WT"] = "2"

    if days_window > 0:
        # f_TPR=r<segundos>: ventana temporal. Evita acumular ofertas viejas en cada run.
        base_params["f_TPR"] = f"r{days_window * 86_400}"

    if easy_apply:
        # f_AL=true: solo ofertas con "Easy Apply" (postulación directa desde LinkedIn)
        base_params["f_AL"] = "true"

    if few_applicants:
        # f_JIYN=true: menos de 10 candidatos → más visibilidad para el aplicante
        base_params["f_JIYN"] = "true"

    if job_types:
        # f_JT acepta múltiples valores separados por coma (ej. "F,C")
        base_params["f_JT"] = ",".join(job_types)

    if experience_levels:
        # f_E acepta múltiples valores separados por coma (ej. "3,4")
        base_params["f_E"] = ",".join(str(e) for e in experience_levels)

    # Resumen legible de los filtros activos para el log
    active = []
    if remote_only:       active.append("remoto(f_WT=2)")
    if days_window > 0:   active.append(f"últimos {days_window}d")
    if easy_apply:        active.append("easy_apply")
    if few_applicants:    active.append("<10 candidatos")
    if job_types:         active.append(f"tipo={','.join(job_types)}")
    if experience_levels: active.append(f"exp={','.join(str(e) for e in experience_levels)}")
    log_fn(f"  Filtros activos: {' | '.join(active) or 'ninguno'}")

    for page in range(max_pages):
        # 'start' es el offset de resultados (paginación por offset)
        start  = page * page_size
        params = {**base_params, "start": start}

        log_fn(f"  Página {page + 1}/{max_pages} (offset={start})...")

        try:
            r = requests.get(LIST_URL, headers=HEADERS, params=params, timeout=15)
            r.raise_for_status()
        except requests.RequestException as e:
            log_fn(f"  ERROR HTTP: {e}")
            break

        soup  = BeautifulSoup(r.text, "html.parser")
        cards = [parse_card(li) for li in soup.find_all("li")]
        cards = [c for c in cards if c]  # eliminar Nones

        if not cards:
            # LinkedIn devuelve una lista vacía cuando no hay más resultados
            log_fn("  Sin más resultados, parando paginación.")
            break

        results.extend(cards)
        log_fn(f"  → {len(cards)} ofertas | total acumulado: {len(results)}")

        # Pausa aleatoria entre páginas para no saturar el servidor
        if page < max_pages - 1:
            wait = random.uniform(4, 8)
            log_fn(f"  Pausa {wait:.1f}s...")
            time.sleep(wait)

    return results

# ─── Scraping de detalle ──────────────────────────────────────────────────────


def parse_hiring_contact(soup) -> dict | None:
    """
    Extrae el contacto del equipo de contratación ("Anunciante del empleo") del HTML
    del detalle de una oferta de LinkedIn.

    LinkedIn muestra una sección "Conoce al equipo de contratación" con el nombre,
    cargo y URL de LinkedIn del responsable que publicó la oferta.
    Las clases CSS están ofuscadas y cambian, así que buscamos por texto, no por clase.

    Returns:
        dict {name, title, linkedin_url} o None si no se encuentra.
    """
    # Localizar el marcador textual "Anunciante del empleo" o su versión en inglés
    marker_el = None
    for marker in ["Anunciante del empleo", "Job poster"]:
        marker_el = soup.find(string=lambda t, m=marker: t and m.lower() in t.lower())
        if marker_el:
            break

    if not marker_el:
        return None

    # Subir por el árbol hasta encontrar el <a> que envuelve toda la tarjeta del contacto
    node = marker_el.parent
    a_tag = None
    for _ in range(10):
        if node is None:
            break
        if node.name == "a" and "linkedin.com/in/" in (node.get("href") or ""):
            a_tag = node
            break
        node = node.parent

    if not a_tag:
        return None

    linkedin_url = a_tag["href"].split("?")[0].rstrip("/")

    # Nombre: el enlace interno con la URL de LinkedIn
    name_a = a_tag.find("a", href=lambda h: h and "linkedin.com/in/" in h)
    name   = name_a.get_text(strip=True) if name_a else None

    if not name:
        return None

    # Cargo: el <p> dentro del <a> que no es el nombre, no es el marcador y no es el grado de conexión
    title       = None
    skip_texts  = {name, "Anunciante del empleo", "Job poster"}
    for p in a_tag.find_all("p"):
        text = p.get_text(strip=True)
        if text and text not in skip_texts and "•" not in text and len(text) > 3:
            title = text
            break

    return {"name": name, "title": title, "linkedin_url": linkedin_url}


def fetch_detail(job_id: str, log_fn=None) -> tuple[str | None, dict | None, bool | None]:
    """
    Obtiene la descripción completa, el contacto de contratación y si es remoto
    de una oferta, en una sola petición HTTP al endpoint /jobPosting/<id>.

    Returns:
        (description: str|None, hiring_contact: dict|None, is_remote: bool|None)
        is_remote es True si LinkedIn muestra el botón/etiqueta "En remoto" / "Remote".
        is_remote es None si no se puede determinar (no se encontró el indicador).
    """
    url = DETAIL_URL.format(job_id=job_id)
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except requests.RequestException as e:
        if log_fn:
            log_fn(f"  (error detalle {job_id}: {e})")
        return None, None, None

    soup = BeautifulSoup(r.text, "html.parser")

    # ── Descripción ───────────────────────────────────────────────────────────
    description = None
    for el in [
        soup.find("div", class_="description__text"),
        soup.find("div", {"class": lambda c: c and "show-more-less-html__markup" in c}),
        soup.find("span", attrs={"data-testid": "expandable-text-box"}),
    ]:
        if el:
            text = el.get_text(separator="\n", strip=True)
            if len(text) > 50:
                description = text
                break

    # ── Detección de remoto desde el detalle ──────────────────────────────────
    # LinkedIn muestra un botón/enlace con texto "En remoto" o "Remote" en las
    # ofertas que tienen modalidad remota explícita.
    _REMOTE_LABELS = {"en remoto", "remote", "remoto", "teletrabajo"}
    is_remote_detail = None
    remote_node = soup.find(string=lambda t: t and t.strip().lower() in _REMOTE_LABELS)
    if remote_node:
        is_remote_detail = True

    # ── Contacto de contratación ──────────────────────────────────────────────
    hiring_contact = parse_hiring_contact(soup)

    return description, hiring_contact, is_remote_detail


def fetch_description(job_id: str, log_fn=None) -> str | None:
    """Wrapper de compatibilidad — devuelve solo la descripción."""
    desc, _, _ = fetch_detail(job_id, log_fn=log_fn)
    return desc

# ─── Flujo principal ──────────────────────────────────────────────────────────


def main(
    keywords:           list[str] | None = None,
    location:           str             = LOCATION,
    max_pages:          int             = MAX_PAGES,
    remote_only:        bool            = True,
    days_window:        int             = 7,
    easy_apply:         bool            = False,
    few_applicants:     bool            = False,
    job_types:          list[str] | None = None,
    experience_levels:  list[int] | None = None,
    fetch_descs:        bool            = True,
    log_fn = print,
):
    """
    Orquesta el scraping completo:
      1. Carga trabajos existentes para deduplicar.
      2. Para cada keyword, rasca el listado con los filtros configurados.
      3. Opcionalmente obtiene la descripción completa de cada trabajo nuevo.
      4. Guarda todo en jobs.json.

    Args:
        keywords:    lista de términos de búsqueda (default: SEARCHES global)
        location:    ubicación de búsqueda
        max_pages:   máximo de páginas por keyword
        remote_only: pasar f_WT=2 a LinkedIn (solo remotos en origen)
        days_window: antigüedad máxima en días (0 = sin límite)
        fetch_descs: si True, obtiene la descripción completa de cada oferta nueva
        log_fn:      función de logging (print por defecto; la API pasa su propio callback)
    """
    if keywords is None:
        keywords = SEARCHES

    existing = load_existing()
    log_fn(f"Trabajos existentes en disco: {len(existing)}")
    log_fn(f"Configuración: remote_only={remote_only}, ventana={days_window}d, desc={fetch_descs}")

    new_jobs: dict = {}

    # ── Fase 1: listados ──────────────────────────────────────────────────────
    for i, keyword in enumerate(keywords):
        log_fn(f"{'─'*50}")
        log_fn(f"[{i+1}/{len(keywords)}] Búsqueda: '{keyword}' en {location}")

        found = scrape_listing(
            keyword, location, max_pages,
            remote_only       = remote_only,
            days_window       = days_window,
            easy_apply        = easy_apply,
            few_applicants    = few_applicants,
            job_types         = job_types,
            experience_levels = experience_levels,
            log_fn            = log_fn,
        )

        added = 0
        for job in found:
            jid = job.get("job_id")
            if not jid:
                continue                           # sin ID no podemos deduplicar
            if jid in existing or jid in new_jobs:
                continue                           # ya lo tenemos
            job["keyword"]   = keyword
            job["is_remote"] = detect_remote(job)  # reconfirmar con heurística
            new_jobs[jid]    = job
            added += 1

        log_fn(f"→ {added} nuevos (de {len(found)} encontrados)")

        # Pausa más larga entre keywords para evitar bloqueos
        if i < len(keywords) - 1:
            wait = random.uniform(10, 18)
            log_fn(f"Pausa {wait:.1f}s antes de la siguiente keyword...")
            time.sleep(wait)

    log_fn(f"\nTotal trabajos nuevos: {len(new_jobs)}")

    if not new_jobs:
        log_fn("Sin novedades. Saliendo.")
        return

    # ── Fase 2: descripciones ─────────────────────────────────────────────────
    if fetch_descs:
        log_fn("Obteniendo descripciones completas...")
        jobs_list = list(new_jobs.values())
        for i, job in enumerate(jobs_list):
            jid = job.get("job_id")
            if not jid:
                continue
            log_fn(f"  [{i+1}/{len(jobs_list)}] {job['title']} @ {job['company']}")
            desc = fetch_description(jid, log_fn=log_fn)
            job["description"] = desc
            new_jobs[jid] = job
            log_fn(f"  {'OK (' + str(len(desc)) + ' chars)' if desc else 'sin descripción'}")
            time.sleep(random.uniform(3, 6))

    # ── Fase 3: guardar ───────────────────────────────────────────────────────
    combined = {**existing, **new_jobs}
    save_all(combined, log_fn=log_fn)

    # Vista previa en terminal
    log_fn("\n── Muestra (primeras 3 ofertas nuevas) ──")
    for job in list(new_jobs.values())[:3]:
        log_fn(f"  [{job.get('keyword')}] {job['title']}")
        log_fn(f"  {job['company']} — {job['location']} | remoto={job['is_remote']}")
        preview = (job.get("description") or "")[:120].replace("\n", " ")
        if preview:
            log_fn(f"  {preview}...")


if __name__ == "__main__":
    main()
