# PLAYBOOK DE IMPLEMENTACIÓN — AgencyCRM

**Versión:** 1.0  
**Fecha base:** 2026-10-05  
**Objetivo:** especificación ejecutable para que Codex implemente una aplicación Django local, funcional y escalable para prospección comercial B2B multicanal de una agencia de marketing y publicidad.

---

## 0. Mandato del proyecto

Construir una aplicación web interna para una agencia que permita:

1. Definir campañas de captación por sector, zona y servicios ofrecidos.
2. Descubrir negocios mediante proveedores externos (primero Google Places API New).
3. Deduplicar y normalizar empresas y contactos.
4. Enriquecer empresas desde sus sitios web públicos, de forma segura y limitada.
5. Detectar oportunidades de marketing y asignar un score explicable.
6. Crear leads dentro de un pipeline comercial.
7. Generar propuestas personalizadas y documentos comerciales.
8. Preparar y registrar contacto multicanal:
   - Email.
   - WhatsApp Business.
   - Teléfono como tarea manual.
   - Formulario web como tarea/manual asistida.
   - LinkedIn/otras redes como tarea/manual asistida.
9. Requerir controles de elegibilidad, supresión y aprobación antes del envío.
10. Registrar toda la actividad comercial y técnica.
11. Poder ejecutar toda la V1 con SQLite, sin Docker, en un equipo local.
12. Mantener una arquitectura que permita migrar posteriormente a PostgreSQL, Celery/Redis y despliegue en servidor sin reescribir el dominio.

La V1 debe priorizar **corrección, trazabilidad, idempotencia y seguridad operacional** por encima de volumen.

---

# 1. Decisiones arquitectónicas no negociables

## 1.1 Stack inicial

- Python 3.12.
- Django 6.1.x.
- Django REST Framework 3.18.x.
- SQLite.
- Django Templates.
- HTMX para interacciones progresivas donde aporte valor.
- Bootstrap 5 para UI inicial.
- `httpx` para HTTP saliente.
- `beautifulsoup4` para parsing HTML.
- `phonenumbers` para normalización telefónica.
- `python-dotenv` para `.env` local.
- `pytest` + `pytest-django`.
- `factory-boy`.
- `ruff`.
- `coverage`.
- `reportlab` en la fase de documentos PDF.
- `python-pptx` solo cuando se implemente generación dinámica de presentaciones.

No usar inicialmente:

- Docker.
- PostgreSQL.
- Redis.
- Celery.
- React/Vue.
- Selenium/Playwright para scraping de Google Maps.
- Automatización de WhatsApp Web.
- Automatización de LinkedIn mediante navegador.
- Scraping indiscriminado de webs.
- Hilos o procesos ad-hoc creados desde views Django.

## 1.2 Arquitectura lógica

Separar estrictamente:

```text
UI / API
   ↓
Application Services
   ↓
Domain Models + Policies
   ↓
Repositories implícitos vía Django ORM
   ↓
Provider Interfaces
   ↓
Integraciones externas
```

Reglas:

- Las views no contienen lógica de negocio.
- Los modelos no llaman APIs externas.
- Las signals no ejecutan operaciones remotas.
- Los providers no cambian estados de dominio directamente.
- Cada operación externa se invoca desde un service explícito.
- Todo envío debe pasar por una política de elegibilidad.
- Todo cambio de estado sensible debe quedar auditado.
- Todo proceso largo se representa como un `Job`.
- SQLite tendrá un único worker lógico en V1.

---

# 2. Principios de diseño

## 2.1 Empresa != Lead

`Company` representa una empresa real.

`Lead` representa una oportunidad comercial de esa empresa dentro de una campaña.

Una empresa puede aparecer en varias campañas sin duplicarse.

## 2.2 Datos externos con procedencia

Todo dato adquirido externamente debe poder responder:

- ¿de qué proveedor provino?
- ¿cuándo se observó?
- ¿qué identificador externo tenía?
- ¿en qué campaña/búsqueda se descubrió?
- ¿es dato normalizado o snapshot del proveedor?

No se debe depender del payload crudo de un proveedor para el funcionamiento interno.

## 2.3 Idempotencia

Repetir una acción no debe provocar:

- empresas duplicadas;
- leads duplicados;
- propuestas duplicadas involuntarias;
- mensajes duplicados;
- jobs duplicados.

Las acciones críticas deberán aceptar o generar una clave de idempotencia.

## 2.4 Modo simulación por defecto

La instalación inicial debe arrancar con:

```env
OUTREACH_MODE=simulation
EMAIL_PROVIDER=mock
WHATSAPP_PROVIDER=mock
```

En `simulation` ningún mensaje sale al exterior.

## 2.5 Aprobación humana por defecto

Ninguna propuesta o mensaje generado automáticamente debe enviarse sin aprobación.

La automatización plena podrá activarse en el futuro mediante configuración explícita, nunca por defecto.

---

# 3. Estructura del repositorio

```text
agencycrm/
├── manage.py
├── pyproject.toml
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── CHANGELOG.md
├── docs/
│   ├── ARCHITECTURE.md
│   ├── DOMAIN.md
│   ├── OPERATIONS.md
│   └── PROVIDERS.md
├── config/
│   ├── __init__.py
│   ├── urls.py
│   ├── asgi.py
│   ├── wsgi.py
│   └── settings/
│       ├── __init__.py
│       ├── base.py
│       ├── local.py
│       └── test.py
├── apps/
│   ├── accounts/
│   ├── dashboard/
│   ├── companies/
│   ├── campaigns/
│   ├── prospecting/
│   ├── crm/
│   ├── scoring/
│   ├── proposals/
│   ├── outreach/
│   ├── compliance/
│   ├── jobs/
│   └── audit/
├── integrations/
│   ├── common/
│   ├── google_places/
│   ├── websites/
│   ├── email/
│   ├── whatsapp/
│   └── ai/
├── common/
│   ├── dataclasses.py
│   ├── exceptions.py
│   ├── http.py
│   ├── normalization.py
│   ├── pagination.py
│   ├── security.py
│   └── types.py
├── templates/
├── static/
├── media/
├── tests/
│   ├── integration/
│   └── functional/
└── scripts/
```

Cada Django app debe usar internamente esta convención cuando aplique:

```text
app/
├── models.py
├── admin.py
├── urls.py
├── views.py
├── forms.py
├── selectors.py
├── services.py
├── policies.py
├── api/
│   ├── serializers.py
│   ├── views.py
│   └── urls.py
└── tests/
```

`selectors.py` contiene consultas de lectura.
`services.py` contiene casos de uso que mutan estado.
`policies.py` contiene decisiones de negocio sin I/O remoto.

---

# 4. Configuración

## 4.1 `.env.example`

```env
DJANGO_SETTINGS_MODULE=config.settings.local
DEBUG=True
SECRET_KEY=change-me
ALLOWED_HOSTS=127.0.0.1,localhost

DATABASE_NAME=db.sqlite3

OUTREACH_MODE=simulation
EMAIL_PROVIDER=mock
WHATSAPP_PROVIDER=mock
AI_PROVIDER=mock
PLACES_PROVIDER=mock

GOOGLE_PLACES_API_KEY=

GMAIL_CLIENT_ID=
GMAIL_CLIENT_SECRET=
GMAIL_REDIRECT_URI=

WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_WABA_ID=
WHATSAPP_VERIFY_TOKEN=
WHATSAPP_GRAPH_API_VERSION=

DEFAULT_COUNTRY=ES
DEFAULT_TIMEZONE=Europe/Madrid

WEB_CRAWLER_USER_AGENT=AgencyCRM-Bot/1.0
WEB_CRAWLER_TIMEOUT_SECONDS=10
WEB_CRAWLER_MAX_BYTES=2000000
WEB_CRAWLER_MAX_PAGES_PER_DOMAIN=8

MAX_EMAILS_PER_DAY=25
MAX_WHATSAPP_PER_DAY=10
CONTACT_COOLDOWN_DAYS=14
```

`.env` nunca entra en Git.

## 4.2 Settings

`base.py`:

- apps instaladas;
- middleware;
- templates;
- custom user;
- timezone;
- DRF base;
- password validators;
- logging;
- file upload limits;
- security defaults.

`local.py`:

- SQLite;
- DEBUG;
- console email backend si procede;
- media local.

`test.py`:

- password hasher rápido;
- providers mock obligatorios;
- email locmem;
- filesystem temporal.

---

# 5. Multiusuario y espacio de trabajo

Aunque inicialmente exista una sola agencia, implementar `Workspace` desde el primer día para evitar una migración estructural posterior.

## 5.1 Modelos

### `accounts.User`

Custom user basado en `AbstractUser`.

Debe crearse antes de la primera migración funcional.

```python
AUTH_USER_MODEL = "accounts.User"
```

### `Workspace`

Campos:

- `id` UUID.
- `name`.
- `slug`.
- `is_active`.
- `created_at`.
- `updated_at`.

### `Membership`

Campos:

- `workspace`.
- `user`.
- `role`.
- `is_active`.
- timestamps.

Roles:

```text
OWNER
ADMIN
SALES
MEMBER
VIEWER
```

Constraint:

```text
unique(workspace, user)
```

### `AgencyProfile`

Datos de la agencia utilizados en propuestas y comunicaciones:

- workspace.
- trading_name.
- legal_name.
- website.
- email.
- phone.
- address.
- logo.
- value_proposition.
- default_sender_name.
- meeting_url.
- proposal_footer.
- brand_primary_text opcional.
- timestamps.

### `ServiceOffering`

Servicios ofrecidos:

- workspace.
- code.
- name.
- description.
- is_active.
- default_pitch.
- sort_order.

Ejemplos:

- `SEO_LOCAL`
- `SEO`
- `GOOGLE_ADS`
- `META_ADS`
- `WEB_DESIGN`
- `SOCIAL_MEDIA`
- `REPUTATION`
- `BRANDING`
- `CONTENT`
- `AUTOMATION`

---

# 6. Dominio de empresas

## 6.1 `Company`

Campos mínimos:

- `id` UUID.
- `workspace`.
- `name`.
- `normalized_name`.
- `legal_name` opcional.
- `primary_sector`.
- `description`.
- `website`.
- `normalized_domain`.
- `phone`.
- `phone_e164`.
- `address_line`.
- `city`.
- `province`.
- `postal_code`.
- `country_code`.
- `latitude`.
- `longitude`.
- `google_rating`.
- `google_review_count`.
- `is_active`.
- `do_not_contact`.
- timestamps.

Índices:

- workspace + normalized_name.
- workspace + normalized_domain.
- workspace + phone_e164.
- city.
- primary_sector.

No marcar como `unique` el nombre normalizado.

## 6.2 `CompanyExternalRef`

Permite que una empresa tenga referencias en distintos proveedores.

Campos:

- company.
- provider (`GOOGLE_PLACES`, etc.).
- external_id.
- external_url.
- observed_at.
- metadata JSON limitado.

Constraint:

```text
unique(workspace implícito por company, provider, external_id)
```

Google `place_id` se guarda aquí, no como dependencia transversal del dominio.

## 6.3 `Contact`

Campos:

- company.
- full_name.
- job_title.
- email.
- normalized_email.
- phone.
- phone_e164.
- whatsapp_e164.
- contact_type:
  - GENERAL
  - OWNER
  - MARKETING
  - SALES
  - ADMIN
  - OTHER
- is_primary.
- is_business_contact.
- source_url.
- observed_at.
- do_not_contact.
- notes.
- timestamps.

No inferir automáticamente que un teléfono normal es WhatsApp.

## 6.4 `SocialProfile`

- company.
- platform.
- url.
- username.
- observed_at.
- metadata JSON.

Platforms:

- INSTAGRAM
- FACEBOOK
- LINKEDIN
- TIKTOK
- YOUTUBE
- X
- OTHER.

Constraint razonable:

```text
unique(company, platform, url)
```

## 6.5 `WebsiteObservation`

Representa el resultado normalizado del enriquecimiento:

- company.
- root_url.
- status_code.
- title.
- meta_description.
- has_contact_page.
- has_privacy_page.
- has_booking.
- has_ecommerce.
- detected_emails JSON.
- detected_phones JSON.
- detected_social_urls JSON.
- pages_crawled.
- crawl_started_at.
- crawl_finished_at.
- error_code.
- error_message.
- findings JSON.

No almacenar indefinidamente HTML crudo salvo necesidad explícita.

---

# 7. Campañas

## 7.1 `Campaign`

Campos:

- id UUID.
- workspace.
- name.
- description.
- status.
- target_query.
- target_sector.
- target_location_text.
- target_country_code.
- desired_leads.
- minimum_score.
- require_manual_approval = True.
- created_by.
- started_at.
- completed_at.
- timestamps.

Estados:

```text
DRAFT
READY
RUNNING
PAUSED
COMPLETED
ARCHIVED
```

## 7.2 Servicios de campaña

M2M entre `Campaign` y `ServiceOffering`.

No almacenar una lista CSV.

## 7.3 Canales habilitados

Crear `CampaignChannel`:

- campaign.
- channel.
- enabled.
- priority.
- max_attempts.
- cooldown_days.

Canales:

```text
EMAIL
WHATSAPP
PHONE
CONTACT_FORM
LINKEDIN
INSTAGRAM
OTHER
```

En V1:
- EMAIL y WHATSAPP pueden ser mock.
- PHONE, CONTACT_FORM, LINKEDIN e INSTAGRAM crean tareas manuales.
- No automatizar LinkedIn/Instagram mediante navegador.

---

# 8. Prospección

## 8.1 Problema a resolver

Una campaña no equivale a una llamada API.

El sistema debe crear un plan de búsqueda formado por una o más consultas y/o áreas.

## 8.2 `SearchPlan`

Campos:

- campaign.
- provider.
- status.
- strategy.
- target_count.
- generated_at.
- completed_at.

Strategies:

```text
TEXT_QUERIES
GEO_GRID
HYBRID
MANUAL_IMPORT
```

## 8.3 `SearchPlanItem`

Campos:

- plan.
- sequence.
- query_text.
- location_text.
- latitude.
- longitude.
- radius_m.
- place_type.
- max_results.
- status.

Esto permite, por ejemplo:

```text
dentistas en A Coruña
dentistas en Oleiros
dentistas en Culleredo
dentistas en Arteixo
```

o una rejilla geográfica para Nearby Search.

## 8.4 `SearchRun`

Cada ejecución real:

- plan_item.
- provider.
- request_fingerprint.
- status.
- started_at.
- finished_at.
- results_seen.
- companies_created.
- companies_updated.
- leads_created.
- duplicates.
- error_code.
- error_message.
- provider_cursor/token cifrado o de corta duración si procede.
- provider_metadata JSON.

## 8.5 Provider interface

```python
class PlacesProvider(Protocol):
    def text_search(self, request: PlaceTextSearchRequest) -> PlaceSearchPage: ...
    def nearby_search(self, request: PlaceNearbySearchRequest) -> PlaceSearchPage: ...
    def get_details(self, external_id: str, fields: set[str]) -> PlaceDetails: ...
```

Usar dataclasses/Pydantic-like structures propias; no propagar JSON del proveedor.

Implementaciones:

```text
MockPlacesProvider
GooglePlacesProvider
```

## 8.6 Google Places

Usar Places API (New), no scraping del sitio web de Google Maps.

Diseñar FieldMask mínimo y explícito.

Campos iniciales recomendados:

- id.
- displayName.
- formattedAddress.
- addressComponents si necesario.
- location.
- primaryType.
- types.
- nationalPhoneNumber / internationalPhoneNumber cuando proceda.
- websiteUri.
- rating.
- userRatingCount.
- googleMapsUri.

No pedir fotografías ni reviews completas en V1.

Cost control:
- no solicitar campos que no se usan;
- registrar número de requests;
- permitir límites por campaña;
- evitar Details si Text/Nearby ya devolvió lo necesario;
- cachear/evitar repetir una consulta idéntica dentro de una ventana configurada.

Deduplicación principal:
1. `provider + external_id`.
2. dominio normalizado.
3. teléfono E.164.
4. coincidencia heurística nombre+dirección solo como sugerencia, nunca fusión automática de baja confianza.

## 8.7 Importación CSV

Implementar también `MANUAL_IMPORT`.

CSV mínimo:

```text
name,website,email,phone,address,city,province,country
```

Debe:
- validar fila;
- normalizar;
- presentar errores por línea;
- deduplicar;
- crear provenance `MANUAL_IMPORT`.

Esto permite trabajar aun sin API de Google.

---

# 9. Jobs y worker local

## 9.1 Restricción de V1

SQLite no debe tratarse como una cola altamente concurrente.

La V1 soporta exactamente un proceso worker.

## 9.2 `Job`

Campos:

- id UUID.
- workspace.
- kind.
- status.
- priority.
- payload JSON.
- result JSON.
- progress_current.
- progress_total.
- attempts.
- max_attempts.
- idempotency_key.
- available_at.
- started_at.
- finished_at.
- last_error.
- created_by.
- timestamps.

Estados:

```text
PENDING
RUNNING
SUCCEEDED
FAILED
CANCELLED
```

Constraint:

```text
unique(workspace, idempotency_key) cuando idempotency_key no sea null
```

Kinds:

```text
BUILD_SEARCH_PLAN
RUN_SEARCH_ITEM
ENRICH_COMPANY
SCORE_LEAD
GENERATE_PROPOSAL
PREPARE_OUTREACH
SEND_OUTREACH
REFRESH_METRICS
```

## 9.3 `JobAttempt`

- job.
- attempt_number.
- started_at.
- finished_at.
- succeeded.
- error_type.
- error_message.
- traceback_excerpt.

## 9.4 Runner

Management command:

```bash
python manage.py run_worker
```

Flags:

```bash
python manage.py run_worker --once
python manage.py run_worker --sleep 2
python manage.py run_worker --kinds ENRICH_COMPANY,SCORE_LEAD
```

Reglas:
- no ejecutar desde `runserver`;
- no crear threads desde una view;
- una view encola un Job y devuelve;
- UI consulta progreso;
- worker marca RUNNING/SUCCEEDED/FAILED;
- recovery: job RUNNING demasiado antiguo puede volver a PENDING manualmente mediante command/admin.

Preparar la API del runner para reemplazarla por Celery más adelante.

---

# 10. Enriquecimiento web

## 10.1 Objetivo

Analizar el sitio oficial de una empresa para extraer únicamente información comercial pública y señales relevantes.

## 10.2 Alcance V1

Rastrear:

1. homepage;
2. enlaces internos que parezcan:
   - contacto;
   - about/nosotros;
   - servicios;
   - reservas/citas;
3. máximo configurable de páginas.

No ejecutar JavaScript.

## 10.3 Seguridad SSRF obligatoria

Antes de cada petición:

- aceptar solo `http` y `https`;
- rechazar credenciales embebidas en URL;
- resolver host;
- bloquear loopback;
- bloquear RFC1918;
- bloquear link-local;
- bloquear multicast;
- bloquear rangos reservados;
- bloquear metadata cloud;
- revalidar destino tras cada redirect;
- máximo 5 redirects;
- timeout total;
- límite de bytes;
- validar `Content-Type`;
- solo HTML/texto para crawler;
- user agent explícito.

Nunca permitir que un usuario introduzca una URL que dé acceso a servicios internos de la máquina.

## 10.4 Cortesía y límites

- máximo 1 request concurrente por dominio;
- delay configurable;
- respetar robots.txt como política del producto;
- no autenticarse;
- no sortear CAPTCHAs;
- no enviar formularios;
- no acceder a zonas privadas.

## 10.5 Extracción

Detectar:

- enlaces `mailto:`;
- emails empresariales visibles;
- enlaces `tel:`;
- teléfonos visibles;
- enlaces sociales;
- contacto;
- política/privacidad;
- llamada a acción;
- reserva;
- ecommerce;
- title/meta description;
- presencia básica de analytics/píxeles solo si es evidente en HTML, sin convertirlo en auditoría invasiva.

Normalizar teléfonos con `phonenumbers`.

No recolectar datos personales irrelevantes.

---

# 11. CRM

## 11.1 `Lead`

Campos:

- id UUID.
- workspace.
- campaign.
- company.
- status.
- score.
- score_version.
- recommended_service.
- assigned_to.
- next_action_at.
- last_contact_at.
- lost_reason.
- notes.
- timestamps.

Constraint:

```text
unique(campaign, company)
```

Estados:

```text
NEW
ENRICHING
QUALIFIED
READY
CONTACTED
REPLIED
MEETING
PROPOSAL
WON
LOST
DISQUALIFIED
```

No permitir transiciones arbitrarias desde cualquier estado.

Crear un `LeadStateService`.

## 11.2 `LeadStatusHistory`

Append-only:

- lead.
- from_status.
- to_status.
- reason.
- actor.
- created_at.

## 11.3 `Opportunity`

Campos:

- lead.
- type.
- severity.
- title.
- description.
- evidence.
- recommended_service.
- confidence.
- detected_by.
- is_active.
- timestamps.

Types:

```text
SEO_LOCAL
SEO
WEB
CONVERSION
GOOGLE_ADS
META_ADS
SOCIAL_MEDIA
REVIEWS
BRANDING
CONTENT
AUTOMATION
OTHER
```

Severity:

```text
LOW
MEDIUM
HIGH
```

Evidence debe ser breve y verificable.

---

# 12. Scoring

## 12.1 Requisitos

El score debe ser:

- explicable;
- reproducible;
- versionado;
- configurable progresivamente.

Nunca usar una puntuación opaca sin breakdown.

## 12.2 `ScoringRule`

- workspace.
- code.
- name.
- points.
- enabled.
- configuration JSON.
- version.
- sort_order.

## 12.3 `LeadScoreItem`

- lead.
- scoring_run_id.
- rule_code.
- points.
- explanation.
- evidence.
- created_at.

## 12.4 Primera versión de reglas

Ejemplo, ajustable:

```text
+10 sitio web disponible
+12 email empresarial disponible
+8 teléfono comercial normalizado
+5 perfiles sociales encontrados
+15 oportunidad HIGH detectada
+8 oportunidad MEDIUM detectada
+10 pocas reseñas relativo a criterio de campaña
+10 CTA débil / falta de conversión detectada
+7 ausencia de canal relevante para el servicio
```

No asumir automáticamente que "no tiene web" siempre es buen lead.

Normalizar score final a 0-100.

---

# 13. Propuestas y activos comerciales

## 13.1 `Proposal`

Campos:

- id UUID.
- lead.
- version.
- status.
- title.
- executive_summary.
- problem_statement.
- recommended_solution.
- services_json.
- next_steps.
- internal_notes.
- generated_by (`TEMPLATE`, `AI`, `HUMAN`).
- generated_model opcional.
- approved_by.
- approved_at.
- created_at.
- updated_at.

Constraint:

```text
unique(lead, version)
```

Estados:

```text
DRAFT
GENERATED
REVIEWED
APPROVED
SENT
SUPERSEDED
```

## 13.2 Generación inicial

La primera implementación debe ser determinista mediante templates Django/Jinja-like.

No hacer depender la aplicación de IA para ser funcional.

`MockAIProvider` y contrato para futura IA:

```python
class AIProvider(Protocol):
    def generate_proposal(self, context: ProposalContext) -> GeneratedProposal: ...
```

## 13.3 Documentos

V1:
- vista HTML imprimible;
- PDF generado con ReportLab;
- fichero almacenado bajo `MEDIA_ROOT/proposals/...`.

Activos de agencia:

### `SalesAsset`

- workspace.
- name.
- asset_type.
- file.
- is_default.
- is_active.
- created_at.

Types:

```text
AGENCY_DECK
CASE_STUDY
RATE_CARD
BROCHURE
OTHER
```

La presentación corporativa inicial puede ser un PDF/PPTX subido manualmente.

Fase posterior:
- generación dinámica `.pptx` con `python-pptx`.

---

# 14. Outreach multicanal

## 14.1 `OutreachMessage`

Campos:

- id UUID.
- workspace.
- lead.
- contact nullable.
- channel.
- direction (`OUTBOUND`, `INBOUND`).
- subject.
- body_text.
- body_html opcional.
- status.
- provider.
- provider_message_id.
- idempotency_key.
- approved_by.
- approved_at.
- scheduled_at.
- sent_at.
- delivered_at.
- failed_at.
- error_code.
- error_message.
- reply_to_message nullable.
- timestamps.

Estados:

```text
DRAFT
PENDING_APPROVAL
APPROVED
QUEUED
SENDING
SENT
DELIVERED
READ
FAILED
REPLIED
CANCELLED
SUPPRESSED
```

Constraint:

```text
unique(workspace, idempotency_key)
```

## 14.2 `MessageAttachment`

- message.
- file.
- filename.
- mime_type.
- size_bytes.
- asset/proposal reference opcional.

## 14.3 `MessageEvent`

Append-only:

- message.
- event_type.
- provider_event_id.
- occurred_at.
- payload JSON filtrado.

Eventos:

```text
QUEUED
PROVIDER_ACCEPTED
SENT
DELIVERED
READ
BOUNCED
FAILED
REPLIED
UNSUBSCRIBED
```

## 14.4 Provider contract

```python
class OutboundProvider(Protocol):
    channel: str

    def send(self, request: SendMessageRequest) -> SendMessageResult: ...
```

Implementaciones:

```text
MockEmailProvider
MockWhatsAppProvider
GmailProvider        # fase posterior
WhatsAppCloudProvider # fase posterior
```

Providers:
- retornan resultados;
- no hacen `message.save()`;
- no cambian Lead;
- no escriben AuditEvent;
- no deciden elegibilidad.

Eso corresponde a `OutreachService`.

---

# 15. Email

## 15.1 V1

- mock provider;
- opcional Django console/locmem para pruebas;
- vista previa;
- aprobación;
- attachments locales;
- simulación de éxito/fallo.

## 15.2 Gmail posterior

Implementar OAuth2.

No almacenar passwords de Gmail.

Separar:
- credencial/conexión;
- provider;
- refresh token cifrado/secret store futuro;
- sender identity.

Registrar:
- provider_message_id;
- thread id si existe;
- errores normalizados.

La UI no debe revelar tokens.

---

# 16. WhatsApp Business

## 16.1 V1

`MockWhatsAppProvider`.

Debe ser posible probar:
- preparación;
- aprobación;
- envío simulado;
- eventos simulados;
- fallo simulado;
- supresión.

## 16.2 Fase real

Usar WhatsApp Business Platform / Cloud API.

Guardar configuración:

- phone number ID.
- WABA ID.
- versión API.
- nombre de template.
- idioma.
- categoría/template metadata.
- webhook verify token.

Diseñar:

```text
OutboundMessage -> Cloud API -> provider_message_id
                         ↓
                     Webhooks
                         ↓
                 MessageEvent
```

Los webhooks:
- verifican firma/token según mecanismo vigente;
- son idempotentes;
- almacenan solo payload filtrado necesario;
- responden rápido;
- encolan procesamiento si es costoso.

No automatizar WhatsApp Web.

---

# 17. Otros canales

## 17.1 Teléfono

No realizar llamadas automáticas en V1.

Crear `SalesTask`:

- lead.
- type=CALL.
- due_at.
- assigned_to.
- status.
- notes.
- outcome.

## 17.2 Formulario web

Guardar `contact_form_url`.

Crear tarea manual:

```text
CONTACT_FORM
```

No enviar formularios automáticamente en V1.

## 17.3 LinkedIn / Instagram

Guardar perfiles y crear acción manual.

No automatizar sesiones de navegador ni eludir controles de plataforma.

---

# 18. Compliance y seguridad comercial

La aplicación no debe asumir que disponer públicamente de un email o teléfono implica automáticamente permiso para cualquier campaña.

Implementar controles operativos configurables; la revisión jurídica/política aplicable corresponde al operador de la agencia.

## 18.1 `SuppressionEntry`

Campos:

- workspace.
- scope:
  - EMAIL
  - PHONE
  - DOMAIN
  - COMPANY
- normalized_value.
- company nullable.
- reason.
- source.
- created_by.
- created_at.
- expires_at nullable.

Reasons:

```text
DO_NOT_CONTACT
UNSUBSCRIBED
COMPLAINT
BOUNCED
MANUAL_BLOCK
POLICY
OTHER
```

## 18.2 `ContactEligibilityDecision`

- lead.
- contact.
- channel.
- decision:
  - ELIGIBLE
  - INELIGIBLE
  - REVIEW_REQUIRED
- reasons JSON.
- policy_version.
- evaluated_at.
- evaluated_by nullable.

## 18.3 Eligibility Service

Antes de enviar:

```python
decision = eligibility_service.evaluate(
    lead=lead,
    contact=contact,
    channel=channel,
)
```

Debe comprobar como mínimo:

1. workspace activo;
2. campaign activa;
3. company `do_not_contact == False`;
4. contact `do_not_contact == False`;
5. no hay suppression match;
6. datos requeridos presentes;
7. proposal/message aprobado;
8. canal habilitado en campaña;
9. cooldown respetado;
10. máximo de intentos respetado;
11. límite diario no excedido;
12. modo de outreach correcto;
13. policy-specific manual review si aplica.

Si no es `ELIGIBLE`, el provider no se llama.

---

# 19. Rate limiting y protección frente a errores de campaña

Crear `OutreachSettings` por workspace:

- daily_email_limit.
- daily_whatsapp_limit.
- default_cooldown_days.
- max_attempts_per_company.
- manual_approval_required.
- live_mode_enabled.

Para activar `live` deben cumplirse dos condiciones:

```text
OUTREACH_MODE=live
AND
workspace.live_mode_enabled=True
```

Esto crea una doble barrera.

Añadir botón de emergencia:

```text
PAUSE ALL OUTREACH
```

que deshabilita nuevos envíos sin borrar jobs ni datos.

---

# 20. Audit log

## `AuditEvent`

Append-only:

- id UUID.
- workspace.
- actor nullable.
- action.
- object_type.
- object_id.
- correlation_id.
- metadata JSON filtrado.
- ip_address opcional.
- created_at.

Eventos obligatorios:

- LOGIN relevante.
- CAMPAIGN_CREATED.
- CAMPAIGN_STARTED.
- SEARCH_EXECUTED.
- COMPANY_CREATED/MERGED.
- LEAD_STATUS_CHANGED.
- SCORE_CALCULATED.
- PROPOSAL_GENERATED.
- PROPOSAL_APPROVED.
- MESSAGE_APPROVED.
- MESSAGE_SEND_ATTEMPT.
- MESSAGE_SENT.
- MESSAGE_FAILED.
- SUPPRESSION_ADDED.
- LIVE_MODE_CHANGED.

No guardar secretos en metadata.

---

# 21. Observabilidad

## 21.1 Logging

Formato estructurado o semiestructurado.

Cada operación larga debe tener:

- `correlation_id`;
- job id;
- campaign id si aplica;
- lead/company id cuando aplique.

Niveles:
- DEBUG solo local.
- INFO operaciones normales.
- WARNING recuperables.
- ERROR fallos.

Nunca loguear:
- access tokens;
- refresh tokens;
- API keys;
- cuerpo completo de mensajes si no es imprescindible;
- datos personales no necesarios.

## 21.2 Health

Añadir:

```text
/health/
```

retorna:
- app ok;
- DB accesible.

No exponer secretos/config completa.

---

# 22. UI requerida

## 22.1 Navegación

```text
Dashboard

Captación
  Campañas
  Planes de búsqueda
  Trabajos

CRM
  Empresas
  Leads
  Contactos
  Pipeline

Ventas
  Oportunidades
  Propuestas
  Outbox
  Tareas

Compliance
  Supresión
  Elegibilidad

Configuración
  Agencia
  Servicios
  Integraciones
  Límites
  Usuarios

Auditoría
```

## 22.2 Dashboard

Mostrar:

- empresas totales;
- leads nuevos;
- cualificados;
- contactados;
- respuestas;
- reuniones;
- propuestas;
- ganados;
- jobs fallidos;
- mensajes pendientes de aprobación.

## 22.3 Filtros esenciales

Leads:

- campaña;
- status;
- score mínimo/máximo;
- sector;
- ciudad;
- assigned_to;
- tiene email;
- tiene teléfono;
- elegibilidad;
- oportunidad.

## 22.4 Página Lead

Debe mostrar en una sola pantalla:

- empresa;
- contactos;
- fuente;
- score + breakdown;
- oportunidades;
- propuesta actual;
- historial;
- mensajes;
- tareas;
- elegibilidad;
- acciones permitidas.

Las acciones no válidas deben quedar deshabilitadas con explicación.

---

# 23. API interna

Prefijo:

```text
/api/v1/
```

Endpoints iniciales:

```text
GET  /api/v1/companies/
GET  /api/v1/companies/{id}/
GET  /api/v1/leads/
GET  /api/v1/leads/{id}/
POST /api/v1/campaigns/
GET  /api/v1/campaigns/
GET  /api/v1/jobs/
GET  /api/v1/jobs/{id}/
POST /api/v1/proposals/{id}/approve/
POST /api/v1/messages/{id}/approve/
POST /api/v1/messages/{id}/enqueue/
```

Autenticación:
- SessionAuthentication inicialmente.
- CSRF activo.

No habilitar API pública anónima.

---

# 24. Seguridad Django

Obligatorio:

- custom user.
- `LoginRequiredMixin` / decorators.
- permisos por workspace.
- CSRF.
- password validators.
- escape automático de templates.
- no usar `mark_safe` con contenido externo.
- sanitizar HTML generado si se permite body HTML.
- límites de subida.
- validación de MIME/extensión.
- nombres de archivo aleatorios.
- no servir archivos sensibles arbitrariamente.
- CSP disponible y documentada.
- cookies seguras en entornos no locales.
- `SECURE_SSL_REDIRECT` futuro en producción.
- secrets solo por entorno.
- `DEBUG=False` fuera de local.

---

# 25. Normalización y deduplicación

Crear helpers comunes:

```python
normalize_domain(url)
normalize_email(email)
normalize_phone(phone, default_region)
normalize_company_name(name)
canonicalize_url(url)
```

## 25.1 Regla de merge

Nunca fusionar automáticamente empresas solo por nombre.

Confianza:

- HIGH: mismo external provider id.
- HIGH: mismo dominio normalizado + nombre compatible.
- HIGH/MEDIUM: mismo teléfono empresarial.
- MEDIUM: mismo nombre + dirección muy similar.
- LOW: nombre solamente.

Solo HIGH puede hacer auto-link.
MEDIUM crea `DuplicateCandidate` para revisión.

---

# 26. Errores y retries

Crear jerarquía:

```text
ApplicationError
├── ValidationError
├── PolicyError
│   ├── ContactSuppressedError
│   └── ApprovalRequiredError
├── ProviderError
│   ├── ProviderAuthError
│   ├── ProviderRateLimitError
│   ├── ProviderTimeoutError
│   ├── ProviderTemporaryError
│   └── ProviderPermanentError
└── CrawlError
```

Retries:
- timeout: sí, limitado;
- 429: sí, respetando retry-after;
- 5xx: sí, backoff;
- auth 401/403: no automático infinito;
- validation: no;
- suppression/policy: no.

No usar retry ciego para mensajes si existe posibilidad de que el proveedor los haya aceptado antes del timeout.
Consultar estado/provider id si el proveedor lo permite.

---

# 27. Tests obligatorios

## 27.1 Unitarios

- normalización de dominio.
- normalización de teléfono.
- scoring.
- state transitions.
- eligibility.
- suppression.
- idempotency key.
- search planning.
- provider adapters con fixtures.
- SSRF URL validator.

## 27.2 Integración

- crear campaña -> plan -> job.
- mock Places -> company -> lead.
- enrichment mock -> contactos.
- scoring -> breakdown.
- proposal -> approval.
- message -> approval -> simulation send.
- suppression bloquea envío.
- límite diario bloquea envío.
- job retry.
- duplicate provider result no crea empresa nueva.

## 27.3 Tests críticos que deben existir literalmente

```python
def test_simulation_mode_never_calls_live_provider(): ...


def test_suppressed_contact_cannot_be_sent_message(): ...


def test_duplicate_place_id_does_not_create_duplicate_company(): ...


def test_message_send_is_idempotent(): ...


def test_unapproved_message_cannot_be_sent(): ...


def test_unapproved_proposal_cannot_be_marked_sent(): ...


def test_crawler_rejects_private_ip_targets(): ...


def test_workspace_data_is_not_visible_to_other_workspace(): ...
```

## 27.4 Cobertura

Objetivo inicial:
- 80% global mínimo.
- >90% en compliance/outreach/scoring/services críticos.

No perseguir 100% artificial.

---

# 28. Datos demo

Crear:

```bash
python manage.py seed_demo
```

Debe generar:

- workspace Demo Agency.
- usuario demo opcional mediante flags.
- servicios.
- campaña "Dentistas Galicia".
- 10 empresas ficticias.
- contactos ficticios.
- oportunidades.
- leads en distintos estados.
- propuesta.
- mensajes mock.
- una supresión.
- jobs históricos.

Nunca usar datos reales en fixtures del repositorio.

---

# 29. Comandos operativos

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py seed_demo

python manage.py runserver
python manage.py run_worker

python manage.py run_worker --once
python manage.py requeue_stale_jobs
python manage.py rebuild_scores --campaign <uuid>
python manage.py refresh_dashboard_metrics
python manage.py check
pytest
ruff check .
```

README debe explicar que en desarrollo normal se usan dos terminales:

Terminal A:

```bash
python manage.py runserver
```

Terminal B:

```bash
python manage.py run_worker
```

---

# 30. Fases de implementación para Codex

Codex NO debe implementar todo en un único cambio masivo.

Cada fase debe:
1. leer este playbook;
2. inspeccionar código existente;
3. implementar solo la fase;
4. crear migraciones;
5. crear tests;
6. ejecutar tests;
7. ejecutar `ruff check .`;
8. ejecutar `python manage.py check`;
9. actualizar README/CHANGELOG;
10. no continuar si la fase actual no está verde.

---

## FASE 0 — Bootstrap

Entregables:

- proyecto Django;
- settings separados;
- `.env.example`;
- custom user;
- Workspace;
- Membership;
- AgencyProfile;
- ServiceOffering;
- login/logout;
- admin;
- base template;
- health endpoint;
- pytest/ruff;
- README.

Acceptance:

```text
python manage.py check -> OK
pytest -> verde
runserver -> login operativo
admin -> operativo
/health/ -> 200
```

---

## FASE 1 — Companies + Campaigns + CRM básico

Entregables:

- Company.
- CompanyExternalRef.
- Contact.
- SocialProfile.
- Campaign.
- CampaignChannel.
- Lead.
- LeadStatusHistory.
- Opportunity.
- selectors/services.
- admin.
- UI CRUD.
- filtros.
- tests de aislamiento por workspace.

Acceptance:

- crear campaña desde UI;
- crear empresa;
- crear lead;
- cambiar status por service;
- ver historial;
- no duplicar lead campaign/company.

---

## FASE 2 — Jobs + providers mock

Entregables:

- Job.
- JobAttempt.
- JobService.
- runner management command.
- mock Places.
- mock Email.
- mock WhatsApp.
- UI de jobs.
- requeue stale.

Acceptance:

- una view puede encolar;
- worker procesa;
- progreso visible;
- fallo controlado;
- retry controlado;
- un solo worker documentado.

---

## FASE 3 — Prospección

Entregables:

- SearchPlan.
- SearchPlanItem.
- SearchRun.
- planner.
- mock Places funcional.
- importador CSV.
- dedupe.
- creación Company + Lead.

Acceptance:

- campaña genera plan;
- mock devuelve negocios;
- worker crea empresas;
- rerun no duplica;
- CSV con errores parciales informa por fila.

Al final de esta fase la app debe ser útil sin Google real.

---

## FASE 4 — Google Places real

Entregables:

- GooglePlacesProvider.
- configuración por env.
- Text Search.
- Nearby Search si necesario.
- Place Details.
- FieldMask mínimo.
- errores normalizados.
- budgets/límites.
- provenance.
- tests HTTP mockeados.

Acceptance:

- integration disabled si no hay key;
- jamás hace llamada real en tests;
- campaña pequeña funciona con key real;
- resultados se deduplican;
- errores 429/5xx quedan en JobAttempt;
- coste/request count visible de forma básica.

---

## FASE 5 — Enrichment web

Entregables:

- safe HTTP client.
- SSRF protection.
- crawler.
- robots policy.
- extractores.
- WebsiteObservation.
- enrichment jobs.
- Contact/SocialProfile enrichment.
- tests contra HTML fixtures.

Acceptance:

- crawler no entra en IP privada;
- respeta límites;
- extrae emails/teléfonos/social;
- fallo de una web no falla campaña;
- no hay raw HTML masivo persistente.

---

## FASE 6 — Scoring + opportunities

Entregables:

- ScoringRule.
- LeadScoreItem.
- scoring engine.
- reglas default.
- Opportunity detectors iniciales.
- UI breakdown.

Acceptance:

- score reproducible;
- breakdown visible;
- reglas deshabilitables;
- nueva ejecución reemplaza score activo sin borrar histórico esencial.

---

## FASE 7 — Propuestas y documentos

Entregables:

- Proposal.
- SalesAsset.
- ProposalService.
- template determinista.
- vista previa.
- aprobación.
- PDF.
- attachments.
- AIProvider mock.

Acceptance:

- propuesta se genera sin IA;
- versión nueva no destruye anterior;
- PDF abre correctamente;
- solo APPROVED es elegible para envío.

---

## FASE 8 — Compliance + outreach mock

Entregables:

- SuppressionEntry.
- ContactEligibilityDecision.
- OutreachSettings.
- OutreachMessage.
- MessageAttachment.
- MessageEvent.
- EligibilityService.
- OutboundService.
- MockEmailProvider.
- MockWhatsAppProvider.
- manual channels/tasks.
- emergency pause.

Acceptance:

- simulation jamás llama providers live;
- suppression bloquea;
- cooldown bloquea;
- daily limit bloquea;
- mensajes requieren aprobación;
- auditoría completa;
- envío mock actualiza estados.

---

## FASE 9 — Email real

Entregables:

- Gmail OAuth/provider o backend real seleccionado.
- connection status UI.
- token handling.
- sender identity.
- send.
- error mapping.
- reply/thread identifiers cuando sea viable.
- tests con HTTP mocks.

Acceptance:

- sin credenciales -> degradación limpia;
- live requiere doble activación;
- mensaje aprobado se envía una vez;
- provider id persistido;
- fallo visible y recuperable.

---

## FASE 10 — WhatsApp Business real

Entregables:

- WhatsAppCloudProvider.
- template support.
- document/link support según diseño final.
- webhook verification.
- webhook idempotency.
- MessageEvents.
- inbound message skeleton.
- status updates.

Acceptance:

- test message real controlado;
- template configurado;
- provider id guardado;
- webhook duplicado no duplica evento;
- delivery/read/failure actualiza estado;
- secretos nunca aparecen en logs.

---

## FASE 11 — Automatización de seguimiento controlada

Entregables:

- SalesTask.
- follow-up rules.
- scheduling.
- next_action_at.
- recordatorios en UI.
- no auto-contactar si suppression/reply/meeting/won.

Acceptance:

- respuesta cancela seguimientos pendientes configurados;
- WON/LOST detiene automatización;
- cooldown siempre respetado.

---

## FASE 12 — Hardening

Entregables:

- permisos finos.
- CSP.
- upload hardening.
- audit review.
- backup command.
- export CSV.
- data retention jobs.
- performance indexes.
- query optimization.
- pagination completa.
- Playwright smoke opcional.
- docs operativas.

Acceptance:

- checklist de seguridad verde;
- backup/restore probado;
- export funciona;
- >80% coverage;
- cero errores ruff;
- `check --deploy` documentado para futuro.

---

# 31. Migración futura a PostgreSQL + Celery

La V1 debe preparar esta sustitución:

```text
SQLite                         PostgreSQL
DB Job runner                  Celery
single run_worker              multiple workers
local filesystem               object storage
local sessions                 shared sessions/cache
```

No debe ser necesario cambiar:

- modelos de dominio salvo optimizaciones;
- services;
- policies;
- provider contracts;
- UI;
- API.

Cambiará principalmente:
- settings DB;
- job dispatch adapter;
- locking/transactions;
- deployment.

No usar SQL específico de SQLite.

---

# 32. Reglas para Codex

Codex debe obedecer estas reglas durante toda la implementación.

## 32.1 Nunca

- crear un mega `models.py` sin separación lógica cuando crezca demasiado;
- meter lógica de API externa en views;
- usar `requests` directamente desde templates/views;
- usar signals para enviar mensajes;
- guardar API keys en DB en texto sin un diseño explícito;
- realizar network calls en tests;
- desactivar CSRF para “hacerlo funcionar”;
- fusionar empresas ambiguas automáticamente;
- usar scraping de Google Maps como fallback;
- automatizar WhatsApp Web;
- automatizar LinkedIn/Instagram con navegador;
- enviar mensajes reales desde seeds/tests;
- ocultar excepciones con `except Exception: pass`;
- cambiar estados sin services;
- editar migraciones ya aplicadas salvo fase de bootstrap aún no compartida;
- añadir una dependencia sin documentar el motivo.

## 32.2 Siempre

- type hints en services/providers;
- docstrings en contratos públicos;
- transacciones en casos de uso multi-write;
- constraints DB además de validación Python;
- índices donde los filtros frecuentes lo requieran;
- tests para bugs corregidos;
- normalizar entradas en límites del sistema;
- registrar audit event en acciones críticas;
- respetar workspace scope;
- paginar listados;
- escapar contenido externo;
- usar timezone-aware datetimes;
- conservar provider IDs;
- gestionar timeout en HTTP saliente.

---

# 33. Definition of Done global

La aplicación se considera V1 funcional cuando, partiendo de una instalación vacía:

1. se crea un usuario y workspace;
2. se configura la agencia y servicios;
3. se crea una campaña;
4. se genera un plan;
5. se ejecuta prospección mock o CSV;
6. aparecen empresas sin duplicados;
7. se crean leads;
8. se enriquecen con fixtures/mock o sitios permitidos;
9. se calcula score explicable;
10. aparecen oportunidades;
11. se genera una propuesta;
12. un humano la aprueba;
13. se prepara un email o WhatsApp;
14. eligibility autoriza o bloquea con motivo;
15. un humano aprueba el mensaje;
16. simulation envía mediante provider mock;
17. el estado y eventos quedan registrados;
18. una suppression posterior evita nuevos contactos;
19. dashboard refleja el funnel;
20. audit permite reconstruir la secuencia.

Y además:

```bash
python manage.py check
pytest
ruff check .
```

deben finalizar correctamente.

---

# 34. Escenario E2E obligatorio

Fixture:

```text
Campaña: Dentistas Galicia
Servicio: SEO Local + Google Ads
Target: A Coruña
```

Mock Places devuelve:

```text
Clínica Norte
Clínica Atlántico
Dental Demo
```

Flujo:

```text
Campaign
  ↓
SearchPlan
  ↓
SearchRun
  ↓
Companies
  ↓
Leads
  ↓
Enrichment
  ↓
Scoring
  ↓
Opportunity
  ↓
Proposal
  ↓
Approval
  ↓
OutreachMessage EMAIL
  ↓
Eligibility
  ↓
Approval
  ↓
MockEmailProvider
  ↓
SENT
  ↓
MessageEvent
  ↓
AuditEvent
```

Segundo intento con el mismo `idempotency_key`:
- no debe mandar de nuevo.

Añadir empresa a Suppression:
- intento siguiente termina `SUPPRESSED`.

---

# 35. Primer prompt para Codex

Usar literalmente como punto de partida:

```text
Lee por completo IMPLEMENTATION_PLAYBOOK.md antes de modificar código.

Implementa únicamente la FASE 0 — Bootstrap.

Objetivo: crear la base Django de AgencyCRM conforme al playbook, preparada para SQLite local y sin Docker.

Requisitos:
- Python 3.12.
- Django 6.1.x.
- DRF 3.18.x.
- settings base/local/test.
- custom User desde la primera migración.
- Workspace, Membership, AgencyProfile y ServiceOffering.
- autenticación por sesión.
- admin.
- template base Bootstrap.
- health endpoint.
- pytest, pytest-django, factory-boy, coverage y ruff.
- .env.example, .gitignore y README.
- UUIDs para entidades principales.
- timezone Europe/Madrid configurable.
- workspace isolation helpers básicos.
- ningún provider real.
- ningún Docker.
- ninguna dependencia no justificada.

Antes de terminar:
1. crea migraciones;
2. ejecuta python manage.py check;
3. ejecuta pytest;
4. ejecuta ruff check .;
5. corrige cualquier fallo;
6. resume archivos modificados, decisiones y comandos ejecutados.

No avances a FASE 1.
```

---

# 36. Prompt estándar para las fases siguientes

```text
Lee IMPLEMENTATION_PLAYBOOK.md y revisa el código actual.

Implementa únicamente la FASE <N> — <NOMBRE>.

No rehagas funcionalidades verdes de fases anteriores salvo que exista un bug o requisito incompatible.

Antes de programar:
1. identifica modelos/services/providers afectados;
2. identifica migraciones necesarias;
3. identifica riesgos de idempotencia, workspace isolation y seguridad;
4. define los tests que demostrarán la aceptación.

Implementa siguiendo:
- views delgadas;
- mutations en services;
- queries en selectors;
- providers detrás de interfaces;
- network bloqueada/mockeada en tests;
- audit y policies donde corresponda.

Antes de terminar:
- python manage.py check
- pytest
- ruff check .

No continúes a otra fase.
Entrega un resumen de:
- cambios;
- migraciones;
- tests;
- deuda técnica explícita;
- comandos ejecutados.
```

---

# 37. Criterios para revisión humana de cada PR/entrega

Revisar:

## Arquitectura
- ¿la lógica está en el lugar correcto?
- ¿hay acoplamiento a proveedor?
- ¿hay acceso cross-workspace posible?
- ¿hay SQL/SQLite específico?
- ¿hay duplicación evitable?

## Seguridad
- ¿entrada externa validada?
- ¿SSRFi posible?
- ¿secreto logueado?
- ¿CSRF/permissions respetados?
- ¿HTML externo se renderiza inseguro?

## Datos
- ¿constraints suficientes?
- ¿indexes razonables?
- ¿timestamps?
- ¿provenance?
- ¿idempotencia?

## Operaciones
- ¿fallo externo deja estados coherentes?
- ¿hay retry razonable?
- ¿se puede reintentar manualmente?
- ¿audit permite entender qué ocurrió?

## Tests
- ¿hay happy path?
- ¿hay failure path?
- ¿hay duplicate/idempotency test?
- ¿hay policy/security test?

---

# 38. Decisiones aplazadas de forma consciente

No resolver en V1:

- multi-región cloud;
- Kubernetes;
- microservicios;
- vector DB;
- agentes autónomos sin supervisión;
- renderizado JS general;
- scraping anti-bot;
- CRM externo bidireccional;
- llamadas telefónicas automatizadas;
- automatización de LinkedIn/Instagram;
- billing de clientes;
- app móvil;
- multi-idioma completo;
- data warehouse.

Deben permanecer fuera hasta que el flujo comercial básico esté validado.

---

# 39. Riesgos principales y mitigaciones

## Riesgo: duplicar contactos o mensajes
Mitigación:
- unique constraints;
- idempotency keys;
- provider message IDs;
- status machine.

## Riesgo: SQLite bloqueado
Mitigación:
- un solo worker;
- transacciones cortas;
- no mantener DB transaction durante HTTP externo;
- migrar a PostgreSQL antes de concurrencia real.

## Riesgo: costes Google
Mitigación:
- FieldMask mínimo;
- request budgets;
- SearchPlan;
- dedupe;
- evitar details repetidos.

## Riesgo: crawler inseguro
Mitigación:
- SSRF guard;
- size/time limits;
- redirects controlados;
- no JS;
- no formularios.

## Riesgo: spam / incumplimiento
Mitigación:
- manual approval;
- suppression global;
- eligibility engine;
- rate caps;
- provenance;
- contact cooldown;
- live mode doblemente protegido.

## Riesgo: dependencia de IA
Mitigación:
- generación determinista funcional;
- AI provider opcional.

## Riesgo: integración WhatsApp cambia
Mitigación:
- provider adapter;
- version API configurable;
- webhook normalization.

---

# 40. Resultado esperado

Al completar la V1 se debe poder instalar en local:

```bash
python -m venv .venv
```

activar el entorno y:

```bash
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py createsuperuser
python manage.py seed_demo
```

Terminal 1:

```bash
python manage.py runserver
```

Terminal 2:

```bash
python manage.py run_worker
```

Y operar todo el flujo desde navegador sin depender de Docker, PostgreSQL ni Redis.

La arquitectura debe permitir sustituir posteriormente la infraestructura sin reescribir el comportamiento comercial central.
