# Contratos y providers

Los adapters devuelven dataclasses/resultados normalizados y excepciones curadas.
No mutan Company/Lead/Message, no aprueban, no deciden elegibilidad y no escriben
auditoría. Los services invocan el adapter después de validar contexto/política y
persisten el resultado. Los tests bloquean sockets y DNS; usan fixtures y mocks.

## Implementaciones disponibles

| Función | Mock offline | Adapter real | Estado de validación |
|---|---|---|---|
| Búsqueda de empresas | MockPlacesProvider | GooglePlacesProvider | Text/Nearby/Details probados con HTTP mocks; ensayo externo pendiente |
| Sitio oficial | MockWebsiteProvider/FixtureClient | WebsiteCrawler + cliente HTTP seguro | Fixtures, SSRF, robots y límites probados offline; acceso real exige activación |
| Email | MockEmailProvider | SMTPEmailProvider | SMTP autenticado/TLS probado con mocks; sin envío externo en aceptación |
| WhatsApp | MockWhatsAppProvider | WhatsAppCloudProvider | Plantillas, upload/webhook/eventos con HTTP mocks; ensayo externo pendiente |
| Propuesta | Template determinista / MockAIProvider | Ninguno | La generación no depende de IA; no existe un adapter IA real |

CSV es una fuente MANUAL_IMPORT y no necesita un provider remoto. Teléfono,
formulario, LinkedIn, Instagram y otros canales generan tareas manuales: no se
automatiza navegador, sesión social ni envío de formularios.

## Selección y activación

La instalación inicial usa simulation/mock. La selección está en
`integrations/common/factory.py` y `integrations/websites/factory.py`.

- Google exige `PLACES_PROVIDER=google`, enabled y API key. Es adquisición,
  independiente del modo de comunicaciones; un plan guarda su provider original.
- Crawler real exige `WEBSITE_PROVIDER=web` y `WEB_CRAWLER_ENABLED`; los Jobs
  conservan empresa/URL/provider. Un Job mock sigue offline tras cambiar entorno.
- Email real exige `OUTREACH_MODE=live`, `EMAIL_PROVIDER=smtp`, live habilitado en
  workspace, conexión/remitente autorizados y aprobación ligada a su revisión.
- WhatsApp real exige `OUTREACH_MODE=live`, `WHATSAPP_PROVIDER=cloud`, live de
  workspace, conexión vigente y plantilla aprobada/parámetros congelados.

Pausa, supresión, aprobaciones, cooldown, límites diarios e intentos se verifican
también en el worker. Habilitar un provider no autoriza por sí solo ningún envío.
La V1 seleccionó SMTP para email: Gmail OAuth, refresh tokens y lectura de replies
de buzón no están implementados. No se guardan passwords de Gmail.

Los secretos permanecen en entorno. Las conexiones guardan autorización y
fingerprint/revisión, no passwords/access tokens. Las credenciales de cada adapter
son de instalación; no hay un secret store independiente por workspace. El
aislamiento de remitentes/phone IDs y callbacks sigue siendo por workspace.

## Google Places

API New mediante HTTP, sin scraping de Maps. FieldMasks explícitos, budgets de
peticiones por campaña y snapshots/provenance del resultado. Se reservan requests
y se respetan límites antes de adquirir. No se piden fotos ni reviews completas.
Referencias externas/dominio/teléfono se deduplican con reglas conservadoras.
Fallback ante falta de credenciales: mock o CSV elegidos explícitamente.

## Crawler

No ejecuta JavaScript, no autentica ni supera CAPTCHAs. Respeta robots y límites
de páginas, bytes, redirects y tiempo. Revalida destinos, bloquea IPs privadas/
reservadas y metadata cloud, y mantiene el destino de conexión bajo el control
del cliente seguro. Solo analiza contenido permitido del sitio oficial.
Guarda observaciones normalizadas; no conserva HTML crudo masivo.

## Mensajes y errores

SMTP usa STARTTLS/TLS con verificación de certificado y timeout. Receipt/Message-ID
se conservan. Un timeout tras reservar transporte puede ser UNKNOWN; no se
reintenta ciegamente. No se leen buzones ni se infiere entrega/lectura por SMTP.

WhatsApp usa HTTPS/Cloud API con versión Graph explícita, plantillas posicionales
y PDF privado según diseño. El webhook autentica HMAC del cuerpo crudo y valida
contexto WABA/phone/destinatario; es idempotente y filtra los datos guardados.
Es una ruta de máquinas autenticada por firma, separada de la API de sesión.
Diseño, variables y ensayo pendiente: [WHATSAPP.md](WHATSAPP.md).

Errores de configuración/auth, rate limit, timeout, fallo temporal/permanente y
aceptación incierta se convierten en errores de dominio/proveedor. Los retries
solo se aplican a errores declarados temporales y respetan Retry-After/budget.
Las reservas inciertas requieren reconciliación antes de permitir otra tentativa.

## Aceptación externa

El escenario de V1 está validado offline; no demuestra conectividad, credenciales,
costes ni aceptación por Google/SMTP/Meta en una cuenta real. Estos ensayos siguen
pendientes de configuración, destinatarios/presupuesto del operador y autorización
explícita de red/envío. No se ejecutan desde seeds ni tests. No se hizo despliegue.

Instrucciones locales y recovery: [OPERATIONS.md](OPERATIONS.md). Evidencia del
flujo simulado: [ACCEPTANCE.md](ACCEPTANCE.md).
