# Changelog

## 0.10.0 — 2026-10-12 · FASE 10

- WhatsAppCloudProvider con HTTP acotado, HTTPS fijo, token por entorno, versión
  Graph explícita, plantillas posicionales y upload privado del PDF principal.
- Conexión por workspace y asignación única e histórica de business phone;
  plantilla/parámetros congelados en aprobación y doble activación live.
- Webhook de máquinas con HMAC SHA-256 del cuerpo crudo, verificación de suscripción,
  límites de lote/cuerpo y aislamiento por WABA/phone/destinatario.
- Eventos idempotentes, estados sin regresiones, inbox básico escapado y transiciones
  verificadas del CRM; aceptación incierta reconciliable por callback o WAMID comprobado.
- Redacción de queries de suscripción en logs Django y Retry-After compartido con HTTP dates.
- Migración aditiva y pruebas HTTP mockeadas con DNS/sockets bloqueados; sin nuevas
  dependencias ni FASE 11. Ensayo real controlado pendiente de configuración/autorización:
  no se han realizado conexiones ni envíos a Meta.

## 0.9.0 — 2026-10-12 · FASE 9

- Backend real seleccionado: SMTP autenticado con STARTTLS/TLS y verificación de
  certificado, timeout, texto plano y adjuntos MIME; sin nuevas dependencias.
- Conexión y remitente autorizados por workspace; secretos solo en entorno,
  fingerprint HMAC, aprobación ligada al remitente/revisión y doble activación live.
- UI de conexión sin llamadas remotas ni secretos, selección explícita de email
  real, conservación de Message-ID y errores SMTP normalizados.
- Reserva de transporte persistente; aceptación incierta bloquea cualquier replay.
  Reconciliación OWNER/ADMIN con worker parado y reintentos solo tras rechazo
  confirmado. Aceptación persiste aunque cambien permisos/política durante el envío.
- Migración aditiva de conexión/identidad/reserva y estado UNKNOWN. Pruebas SMTP
  mockeadas con DNS/sockets bloqueados, sin envíos externos ni credenciales reales.
- Sin OAuth Gmail, thread IDs externos ni funcionalidades de FASE 10.

## 0.8.0 — 2026-10-12 · FASE 8

- Supresión normalizada por workspace y decisiones append-only de elegibilidad,
  límites diarios por timezone, cooldown, máximo de intentos y pausa de emergencia.
- Mensajes inmutables, aprobación humana vinculada por hash, adjuntos protegidos,
  eventos idempotentes y SEND_OUTREACH exclusivamente mock en el worker.
- Tareas manuales para teléfono, formularios y redes; sin automatización externa.
- Reevaluación antes/después de archivos, reservas y transporte; recuperación mock,
  errores sanitizados y sincronización de jobs cancelados/fallidos con mensajes.
- UI de outbox, elegibilidad, supresión, límites y tareas con CSRF y permisos.
- Tres migraciones y pruebas de políticas, aislamiento, idempotencia y E2E completo.
  Corregida la revocación de cuentas mediante consulta SQL de usuario activo.
- Sin dependencias nuevas, proveedores reales ni funcionalidades de FASE 9.

## 0.7.0 — 2026-10-12 · FASE 7

- Proposal, SalesAsset y ProposalAttachment con migración inicial, UUID,
  claves idempotentes y versiones preservadas con snapshots inmutables.
- Plantillas deterministas, contrato AIProvider y mock local etiquetado; generación
  GENERATE_PROPOSAL en el worker, PDF ReportLab y vista imprimible con autoescape.
- Revisión explícita y aprobación OWNER/ADMIN con control de revisión, integridad,
  auditoría y contrato de estados que bloquea SENT sin aprobación.
- Activos PDF privados validados con pypdf, límites de recepción/tamaño, archivado,
  adjuntos por workspace y descargas autenticadas con verificación SHA-256.
- Compensación de archivos ante rollback, pruebas sin red y revisión visual PDF.
  Sin IA real, PPTX, envíos ni funcionalidades de FASE 8.

## 0.6.0 — 2026-10-12 · FASE 6

- ScoringRule configurable y versionada por workspace, nueve reglas predeterminadas
  y motor puro scoring-v1 con score acotado a 0–100 y evidencia por aportación.
- ScoringRun y LeadScoreItem inmutables con snapshots, huella reproducible e historial;
  nuevo cálculo sustituye el score activo sin borrar cálculos anteriores.
- Detectores de reseñas, conversión observada y canal objetivo; evidencias, confianza
  y recomendaciones limitadas a servicios activos de campaña. Conservadas oportunidades manuales.
- SCORE_LEAD en el worker existente, idempotencia, auditoría y mutaciones atómicas.
- UI de desglose/histórico y edición de reglas con control de versiones, permisos y CSRF.
- Dos migraciones aditivas, pruebas sin red, rollback, recuperación y aislamiento.
  Sin nuevas dependencias ni funcionalidades de FASE 7.

## 0.5.0 — 2026-10-12 · FASE 5

- Transporte HTTP con IP pública fijada tras DNS acotado, TLS verificado,
  protección SSRF, límites globales de tiempo/bytes/peticiones y redirects explícitos.
- Crawler secuencial con robots.txt, delay, extractores HTML y proveedor mock
  predeterminado; web real requiere doble activación. Sin consultas reales.
- WebsiteObservation por intento, enriquecimiento idempotente de contactos/perfiles,
  auditoría transaccional y ejecución ENRICH_COMPANY fuera de transacciones HTTP.
- UI desde la empresa, historial paginado, CSRF y comprobaciones de workspace,
  permisos y URL antes y después de la adquisición.
- Dos migraciones aditivas y dependencias justificadas BeautifulSoup/httpcore.
- Pruebas con fixtures, sockets/DNS bloqueados, SSRF, robots, límites, rollback,
  revocación, recuperación y reintentos. Sin funcionalidades de FASE 6.

## 0.4.0 — 2026-10-06 · FASE 4

- GooglePlacesProvider con Text Search, Nearby Search y Place Details, FieldMasks
  explícitos, origen fijo, límites de respuesta/tiempo y errores normalizados.
- Activación por entorno; mock sigue siendo predeterminado y cada plan conserva
  su proveedor. Email/WhatsApp permanecen simulados.
- Presupuesto acumulado por campaña reservado antes de HTTP, caché por workspace,
  contadores visibles y procedencia GOOGLE_PLACES; sin paginación o Details implícitos.
- Dos migraciones aditivas para presupuestos, país del plan, contadores y caché.
- httpx justificado y versiones fijadas; pruebas HTTP simuladas con red bloqueada.
- Sin consulta real con credenciales: queda pendiente la verificación operativa
  del operador. Sin funcionalidades de FASE 5.

## 0.3.0 — 2026-10-05 · FASE 3

- SearchPlan, SearchPlanItem y SearchRun, planner acotado TEXT_QUERIES y
  MANUAL_IMPORT; ejecución mediante RUN_SEARCH_ITEM en el worker existente.
- CSV UTF-8 validado, límites de tamaño/filas y errores parciales por línea.
- Ingestión transaccional de Company, fuentes, Contact y Lead con historial;
  SearchResult conserva procedencia y evita repetir filas tras interrupciones.
- Deduplicación por identidad externa y coincidencias de alta confianza;
  DuplicateCandidate informa de ambigüedades sin fusionar empresas automáticamente.
- UI de planes, ejecuciones y sugerencias; progreso mediante jobs, CSRF y
  aislamiento por workspace. Estados sincronizados en fallo, cancelación y recovery.
- Una migración inicial de prospección y pruebas de aceptación/regresión.
  Sin dependencias nuevas, red, Google real ni funcionalidades de FASE 4.

## 0.2.0 — 2026-10-05 · FASE 2

- Job y JobAttempt con migración inicial, idempotencia por workspace, progreso,
  presupuesto de reintentos y auditoría transaccional.
- Worker local con bloqueo entre procesos, ejecución fuera de transacciones,
  reintentos temporales con espera y recuperación manual de trabajos interrumpidos.
- Contratos y mocks puros de Places, Email y WhatsApp; diagnóstico ficticio
  ejecutable sin red ni escrituras de dominio. Tipos de fases posteriores reservados.
- UI por workspace para encolar, consultar progreso, cancelar pendientes y recuperar
  trabajos antiguos; permisos, CSRF y admin de solo lectura.
- Pruebas de concurrencia, aislamiento, rollback, errores sanitizados y simulación.
  Sin nuevas dependencias, Docker, proveedores reales ni funcionalidades de FASE 3.

## 0.1.0 — 2026-10-05 · FASE 1

- Empresas, referencias externas, contactos y perfiles sociales; normalización
  de nombres, dominios, emails y teléfonos (phonenumbers justificado).
- Campañas con servicios M2M validados por workspace y configuración de canales.
- Leads únicos por campaña/empresa, oportunidades manuales y LeadStateService
  con estados permitidos, revisión concurrente e historial append-only.
- UI CRUD por workspace, filtros, paginación, roles básicos y archivado de
  empresas/campañas/leads sin destruir historial. Admin del dominio de solo lectura.
- Auditoría transaccional mínima de mutaciones, sin datos sensibles en metadata.
- Cuatro migraciones nuevas; preservada la migración inicial de cuentas.
- Pruebas de flujo UI, CSRF, aislamiento, permisos, datos manipulados, constraints,
  archivado y rollback. Sin jobs, providers ni funcionalidades de FASE 2.

## 0.0.1 — 2026-10-05 · FASE 0

- Proyecto Django 6.1.x / DRF 3.18.x con Python 3.14, SQLite local y settings
  base/local/test.
- User personalizado UUID y migración inicial de Workspace, Membership,
  AgencyProfile y ServiceOffering; constraints de roles y unicidad por workspace.
- Sesiones, login/logout con CSRF, admin de plataforma exclusivo para
  superusuarios y selectores de datos autorizados por workspace.
- Plantilla Bootstrap local, portada autenticada y health con comprobación de DB.
- pytest, factories, bloqueo de red en tests, coverage, Ruff, `.env.example`,
  exclusiones Git y documentación de instalación y límites de FASE 0.
- Sin Docker, providers reales ni funcionalidades de FASE 1.
