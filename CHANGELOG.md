# Changelog

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
