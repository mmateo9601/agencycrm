# Verificación funcional de V1

Revisión local del **2026-10-13** contra el playbook y el código implementado:
**594 tests verdes**, cobertura global **96 %**, checks Django/Ruff sin errores,
dependencias compatibles y sin migraciones pendientes.

Esta evidencia acredita los escenarios probados de la V1 local. No constituye
una garantía absoluta de ausencia de errores ni de funcionamiento de cuentas
externas o infraestructura que no se ha ensayado.

## Matriz de integración

| Área | Integración comprobada | Evidencia principal |
|---|---|---|
| Bootstrap | User UUID, sesión/CSRF, admin, workspace, agencia, servicios y health | `tests/test_workspace.py` |
| CRM | Campañas, canales, empresas/contactos, leads, transiciones y archivo | `tests/test_phase1.py`, `tests/test_workspace_integration.py` |
| Aislamiento | Roles, revocación, UUID ajenos y superusuario sin bypass | `tests/test_workspace.py`, `tests/test_api.py`, `tests/test_hardening.py` |
| Worker | Cola, intentos, retries, recuperación y bloqueo por SQLite | `tests/test_jobs.py`, `tests/test_operational_commands.py` |
| Captación | Planes, CSV, provenance y dedupe | `tests/test_prospecting.py`, `tests/test_acceptance.py` |
| Google Places | Text/Nearby/Details, FieldMasks, budgets y errores con HTTP mocks | `tests/test_google_places.py` |
| Enriquecimiento | Extracción, límites, robots y protección SSRF | `tests/test_enrichment.py`, `tests/test_websites.py`, `tests/test_http.py` |
| Scoring | Reglas, breakdown, snapshots, oportunidades y lotes | `tests/test_scoring.py`, `tests/test_operational_commands.py` |
| Propuestas | Generación, versiones, revisión/aprobación y PDF privado | `tests/test_proposals.py`, `tests/test_workspace_integration.py` |
| Contacto | Elegibilidad, aprobación, supresión, pausa, límites e idempotencia | `tests/test_outreach.py`, `tests/test_acceptance.py` |
| Email / WhatsApp | Guards live, SMTP/TLS, templates, callbacks y aceptación incierta con mocks | `tests/test_email.py`, `tests/test_whatsapp.py` |
| Seguimientos | Reglas, scheduling, tareas y cancelación por respuesta/cierre | `tests/test_followups.py`, `tests/test_followup_integration.py` |
| Dashboard / API | Agregados, sesión, CSRF, filtros y respuestas acotadas | `tests/test_dashboard.py`, `tests/test_api.py` |
| Operación segura | Auditoría, CSV, retención, backup y restore | `tests/test_hardening.py`, `tests/test_backups.py` |
| Instalación / demo | Migraciones desde cero, demo idempotente, login y worker | `tests/test_seed_demo.py`, `scripts/verify_local_install.py` |

El [escenario de aceptación](ACCEPTANCE.md) verifica las secciones 33–34 desde
una base sin datos comerciales con API, services y worker. El nuevo escenario
`test_commercial_flow_through_html_forms_with_csrf_and_worker` recorre además
los formularios reales: campaña, canal, plan, enriquecimiento, score, transición,
propuesta, PDF, aprobación y un único envío mock tras repetir la acción.

La revisión de páginas ejecuta listas, fichas, scoring, propuestas, archivos,
outbox, tareas, configuración y auditoría con OWNER y VIEWER. Comprueba respuesta,
ausencia de INSERT/UPDATE/DELETE en GET, no-store, bloqueo de otro workspace y
denegación a un superusuario sin membership. Es una prueba HTTP de Django;
no simula el renderizado visual de un navegador.

## Carencias corregidas

- Filtro `eligibility` compartido entre HTML, API y CSV. Usa la última evaluación
  registrada del lead, de cualquier mensaje/canal; distingue leads sin evaluación.
- La ficha muestra veredicto, fecha, canal, motivos y enlace al mensaje evaluado.
  Advierte que es evidencia histórica: la política vigente se comprueba antes del envío.
- La navegación incluye **Límites y pausa**, para acceder directamente a los controles.
- Instalación limpia reproducible con datos temporales, secretos efímeros,
  CSRF activo y red bloqueada. No utiliza la base comercial ni sus archivos.

Sin cambios de modelos, migraciones ni dependencias.

## Reproducir

Con Python 3.14 y el entorno virtual activo, desde la raíz:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python -m pip check
python -m coverage run -m pytest
python -m coverage report
python -m ruff check .
python scripts/verify_local_install.py
```

El script usa settings locales sobre una SQLite nueva bajo `tmp/`, aplica todas
las migraciones, repite el seed, verifica login/CSRF, health, dashboard y API,
y ejecuta el worker una vez. Cierra conexiones y elimina su instalación temporal.
Las dependencias deben estar instaladas antes; el script no descarga paquetes.

El arranque de `runserver --noreload` también se verificó por socket local:
health y login respondieron 200; admin redirigió a su login, que respondió 200.
El servidor se detuvo al terminar.

## Validaciones externas pendientes

| Punto | Estado y requisito para cerrarlo |
|---|---|
| Google real | Adapter implementado; ensayo con API key, cuenta y presupuesto autorizado pendiente |
| SMTP real | Adapter implementado; verificar cuenta, destinatario de prueba y recepción con autorización explícita |
| WhatsApp real | Adapter/webhook implementados; ensayo con cuenta Meta, template aprobado y callback HTTPS pendiente |
| Producción | Settings de producción, HTTPS/proxy, dominio, secretos y operación del alojamiento pendientes |
| PostgreSQL / Celery / object storage | Evolución prevista; no son infraestructura requerida para la V1 SQLite |

El campo de logo sigue reservado sin uploader. BUILD_SEARCH_PLAN y
PREPARE_OUTREACH son enums reservados sin handlers: las operaciones acotadas se
ejecutan mediante services y UI; no se anuncian como trabajos disponibles. Gmail
OAuth, IA real y automatización de redes no forman parte del flujo V1 seleccionado.
Estos límites permanecen explícitos en [Arquitectura](ARCHITECTURE.md) y
[Providers](PROVIDERS.md).
