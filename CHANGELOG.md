# Changelog

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
