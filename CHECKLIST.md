# CHECKLIST — Job Agent V1

Estado del proyecto · actualizado 2026-03-06

---

## Fase 1 — Scraping

- [x] Scraping de LinkedIn via guest API (`seeMoreJobPostings/search`) sin autenticación
- [x] Filtros nativos de LinkedIn: `f_WT` (remoto), `f_TPR` (ventana temporal), `f_AL` (Easy Apply), `f_JIYN` (<10 candidatos), `f_JT` (tipo contrato), `f_E` (nivel experiencia), `sortBy=DD`
- [x] Paginación automática con pausa aleatoria entre páginas
- [x] Deduplicación por `job_id` entre sesiones y entre keywords de la misma sesión
- [x] URLs canónicas sin tokens de tracking que caducan
- [x] Detección de remoto: filtro nativo `f_WT=2` + heurística local de respaldo
- [x] Fetching de descripción completa por oferta (`/jobPosting/{id}`)
- [x] Detección de idioma en la descripción (ratio stop-words, sin dependencias externas)
- [x] Filtro por idioma configurable — descarta ofertas que no coincidan
- [x] Ofertas descartadas → `jobs_discarded.json` con `discard_reason` y `discarded_at`
- [x] Guardado incremental en disco tras cada keyword y tras cada descripción
- [x] Parada cooperativa: `POST /api/scrape/stop` — guarda lo obtenido hasta ese momento
- [x] SSE en tiempo real: `GET /api/scrape/stream`
- [x] Pausa configurable entre keywords para evitar rate-limit de LinkedIn

---

## Fase 2 — Evaluación IA

- [x] Evaluación por oferta con OpenAI (una llamada por oferta — contexto pequeño e independiente)
- [x] Structured Outputs con schema Pydantic `JobEvaluation` — tipos garantizados
- [x] Perfil configurable desde el dashboard: rol (múltiples separados por coma), seniority, stack_yes, stack_no, preferencias libres
- [x] System prompt con criterios explícitos — el stack tecnológico tiene máximo peso
- [x] Seniority con peso reducido — las empresas ponen niveles arbitrarios
- [x] `max_completion_tokens=2500` — compatible con modelos de thinking (reasoning interno)
- [x] Campos guardados: `ai_score`, `ai_remote_verified`, `ai_notes`, `ai_reject_reason`, `ai_cleaned_at`
- [x] Opción de re-evaluar ofertas ya puntuadas (`force_reclean`)
- [x] Opción de eliminar ofertas bajo un umbral de score tras la limpieza (`min_score_keep`)
- [x] Guardado a disco tras cada oferta procesada (no solo al final)
- [x] Parada cooperativa: `POST /api/clean/stop` — guarda lo procesado hasta ese momento
- [x] SSE en tiempo real: `GET /api/clean/stream`
- [x] Defaults de stack realistas preconfigurados en el formulario

---

## ~~Fase 3 — Clasificación~~ ❌ Descartada

> Redundante. El campo `ai_score` (1–10) + el filtro por score del dashboard + el color del anillo
> cubren exactamente lo mismo. No aporta valor sobre lo ya implementado.

---

## Fase 4 — Contacto y envío de email

- [ ] Buscar contacto de RRHH / hiring manager de la empresa (Hunter.io, búsqueda web)
- [ ] Verificar que el contacto encontrado corresponde a la empresa correcta
- [ ] Redactar email personalizado con OpenAI (basado en oferta + perfil del candidato)
- [ ] Enviar email via SMTP o SendGrid si se dispone de contacto
- [ ] Registrar resultado en el job: `contact_email`, `email_sent`, `email_sent_at`, `email_draft`
- [ ] Endpoint `POST /api/email/start` + SSE `/api/email/stream` + `POST /api/email/stop`
- [ ] Variables de entorno: `SMTP_*` o `SENDGRID_API_KEY`
- [ ] Vista de emails enviados en el dashboard

---

## Dashboard

- [x] Tema oscuro premium con CSS custom properties
- [x] Tab Ofertas — tabla con score, notas, idioma, remoto, empresa
- [x] Tab Ofertas — filtros: solo remotas, idioma, score mínimo, búsqueda libre
- [x] Tab Ofertas — ordenación por columna (fecha, score, título, empresa)
- [x] Tab Ofertas — score visual con anillo de color (verde/amber/rojo)
- [x] Tab Ofertas — avatares de empresa con color determinístico
- [x] Tab Ofertas — eliminar oferta individual con animación
- [x] Tab Ofertas — sección de descartadas (colapsable) con motivo de descarte
- [x] Tab Scraping — todos los filtros de LinkedIn configurables desde el UI
- [x] Tab Scraping — botón Iniciar + botón Parar
- [x] Tab Scraping — consola en tiempo real con niveles de log coloreados
- [x] Tab Limpieza IA — perfil estructurado: rol, seniority, stack sí/no, notas
- [x] Tab Limpieza IA — selección de modelo OpenAI
- [x] Tab Limpieza IA — botón Iniciar + botón Parar
- [x] Tab Limpieza IA — consola en tiempo real
- [x] Header con stats en tiempo real (total, remotas, puntuadas, última fecha)
- [x] Badges de estado animados (Scraping · IA en curso)
- [x] Tab Limpieza — eliminar ofertas con score bajo
- [ ] Tab Clasificación (fase 3)
- [ ] Tab Email / Contacto (fase 4)
- [ ] Vista de detalle de oferta (modal o panel lateral)
- [ ] Exportar ofertas filtradas a CSV

---

## Infraestructura y calidad

- [x] SSE con `StreamManager` — fan-out a múltiples clientes, thread-safe
- [x] Parada cooperativa con `threading.Event` en todos los procesos largos
- [x] Guardado incremental — no se pierde trabajo si el proceso se para
- [x] CORS restringido a localhost
- [x] Middleware de token local opcional (`LOCAL_TOKEN`)
- [x] `.gitignore` completo (venv, .env, datos de runtime)
- [x] `.env.example` documentado
- [x] `README.md` con referencia completa de parámetros LinkedIn y API
- [x] Disclaimer de demo/uso local en README
- [ ] Tests (al menos unitarios de `detect_language` y `detect_remote`)
- [ ] Logging a archivo (ahora solo consola + SSE)
- [ ] Límite de rate configurable para las llamadas a OpenAI
- [ ] Manejo de errores de red con reintentos configurables
