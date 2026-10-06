# Changelog

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
