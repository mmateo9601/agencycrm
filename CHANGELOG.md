# Changelog

## 2026-10-13 · API interna V1

- Once endpoints de la sección 23: lecturas paginadas, creación de campaña,
  aprobación de propuesta/mensaje y encolado mediante servicios existentes.
- SessionAuthentication con CSRF, workspace obligatorio y permisos activos;
  JSON y campos explícitos, filtros validados y ausencia de payloads/secretos.
- Campañas idempotentes mediante UUID estable y digest auditado de la solicitud;
  INSERT explícito para impedir sobrescrituras por colisión de identidad.
- API dividida por app, helpers de transporte comunes y contrato documentado.
  Sin llamadas de red, ejecución de providers, nuevas dependencias ni migraciones.
- Validación: 559 tests verdes (35 de API), cobertura global 96% y API 100%.
  Django check, Ruff y detección de migraciones verdes; servidor local y 403
  JSON sin sesión comprobados. Revocar al aprobador bloquea el encolado.

## 2026-10-13 · Datos demo V1

- Comando `seed_demo` idempotente: Demo Agency, servicios, campaña Dentistas
  Galicia, diez empresas/contactos, oportunidades, scores y estados con historial.
- Propuesta PDF sin aprobación, mensajes mock pendientes, supresión y jobs
  diagnósticos cancelados; ningún provider ni envío se ejecuta desde el seed.
- Actor técnico sin contraseña utilizable y cuenta OWNER opcional mediante flag,
  sin adoptar cuentas existentes ni restablecer credenciales/permisos del operador.
- UUIDs reservados, bloqueo del worker, transacción y compensación del PDF;
  conservación de datos ajenos y cambios manuales al repetir. Sin migraciones/deps.
- Validación: 524 tests verdes, cobertura global 96% y seed 100%; Ruff,
  Django check y detección de migraciones verdes. CLI repetida sobre SQLite
  temporal con red bloqueada y servidor local comprobado.

## 2026-10-13 · Complemento del dashboard V1

- Dashboard comercial por workspace con empresas, leads activos por estado,
  jobs fallidos y mensajes pendientes de aprobación; enlaces a los listados.
- Agregados de lectura en selectors, permisos activos para todos los roles,
  aislamiento incluso con varias membresías y consultas independientes del volumen.
- Portada y navegación apuntan al dashboard; prueba E2E comprueba el embudo
  después del envío simulado. Sin dependencias ni migraciones nuevas.
- Documentados los entregables globales restantes sin inventar una nueva fase.
- Validación: 504 tests, cobertura global 96% y dashboard 100%; Django check,
  Ruff y detección de migraciones pendientes verdes. Arranque local comprobado.

## 0.12.0 — 2026-10-12 · FASE 12

- Permisos de auditoría, exportación y retención por workspace y rol activo.
- CSP nativa, nonces del admin y respuestas privadas sin caché.
- Rechazo de symlinks/junctions para PDFs y límite de un archivo por subida.
- Auditoría filtrable/paginada, logins explícitos y redacción de claves sensibles.
- CSV con columnas explícitas, filtros, auditoría y neutralización de fórmulas.
- Retención confirmada mediante Job: cachés caducadas y texto entrante antiguo.
- Backup SQLite + PDFs, hashes, validación de ZIP y restore en destino nuevo.
- Paginación de relaciones/eventos/plantillas y optimización de consultas e índices.
- Bloqueo del worker compatible con las URLs SQLite en memoria usadas por tests.
- Documentación operativa y checklist de seguridad; deployment HTTPS como plantilla.

## 0.11.0 — 2026-10-12 · FASE 11

- Reglas habilitables y recordatorios internos idempotentes por contacto confirmado.
- Próxima acción derivada de tareas, conservando fechas manuales y cooldown.
- Cancelación por respuesta/reunión/cierre/supresión y sustitución por nuevo contacto.
- Acciones internas auditadas; revisiones nunca envían mensajes automáticamente.

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
