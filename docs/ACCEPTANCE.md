# Aceptación offline de V1

`tests/test_acceptance.py::test_playbook_v1_acceptance_from_empty_database`
ejecuta las secciones 33–34 del playbook en una base sin registros comerciales.
No utiliza `seed_demo` ni datos comerciales de otras fixtures. Las migraciones
estándar se aplican por pytest-django; luego se crea la configuración de agencia.

Fixture: campaña **Dentistas Galicia**, servicios **SEO Local + Google Ads**,
target **A Coruña**, Mock Places con **Clínica Norte**, **Clínica Atlántico** y
**Dental Demo**. La fixture añade cinco reseñas ficticias por negocio para
verificar una oportunidad reproducible del detector de reviews.

## Evidencia del escenario

| Paso | Comprobación |
|---|---|
| Usuario / workspace / agencia | User y membership OWNER activos; agencia y dos servicios |
| Sesión | Login real con Client y CSRF enforced; token renovado tras login |
| Campaña | POST API con workspace/clave, servicios y target del escenario |
| Plan / ejecución | Plan del service y Job RUN_SEARCH_ITEM en worker |
| Dedupe | Repetir plan y ejecutar otra búsqueda mantiene exactamente tres empresas/leads/refs |
| Enriquecimiento | Job ENRICH_COMPANY, observación mock y contacto empresarial |
| Score / oportunidad | Job SCORE_LEAD, nueve items explicables y oportunidad generada REVIEWS |
| Propuesta | Job GENERATE_PROPOSAL, revisión humana, aprobación API y descarga PDF privada |
| Eligibility / mensaje | Antes de aprobar no elegible; después de revisión/aprobación, ELIGIBLE |
| Contacto | API encola, worker usa MockEmailProvider y confirma SENT/receipt/evento |
| Idempotencia | Preparación con misma clave y encolado repetido mantienen mensaje/Job; un solo send |
| Supresión | Nueva versión/mensaje aprobado; bloqueo de empresa evita Job y deja SUPPRESSED |
| Dashboard | Tres empresas/leads, uno CONTACTED, cero jobs fallidos/aprobaciones pendientes |
| Auditoría | Acciones de login a envío/supresión en orden, y consulta protegida operativa |

Los sends reales SMTP/WhatsApp tienen guards que fallan la prueba si se invocan.
Además `tests/conftest.py` bloquea sockets y DNS para toda la suite. Los mocks
usan dominios `.invalid`; no se necesita Google, SMTP, Meta ni una cuenta externa.

## Ejecutar

Validación actual (2026-10-13): 594 tests verdes, cobertura global 96%,
Django check y Ruff sin errores, sin migraciones pendientes y arranque local
health/login/admin comprobado. El servidor se detuvo al terminar la prueba.

La [verificación funcional](FUNCTIONAL_VERIFICATION.md) añade el flujo completo
mediante formularios HTML con CSRF, comprobaciones de páginas OWNER/VIEWER y
una instalación SQLite limpia reproducible, sin modificar el contrato de este E2E.

```powershell
pytest tests/test_acceptance.py
python manage.py check
python manage.py makemigrations --check --dry-run
pytest
ruff check .
```

Las demás pruebas cubren errores, aislamiento, roles, revocaciones, límites,
SSRF, callbacks, recuperación, backup/restore, retención, seed y API. Este E2E
complementa esas pruebas; no sustituye comprobaciones de un despliegue real.

No se añadieron modelos, migraciones, dependencias ni funcionalidades de red en
este cierre. Los ensayos externos de Google/email/WhatsApp y TLS/despliegue siguen
pendientes del operador. La documentación distingue estado implementado de esas
validaciones externas: [PROVIDERS.md](PROVIDERS.md), [SECURITY.md](SECURITY.md).
