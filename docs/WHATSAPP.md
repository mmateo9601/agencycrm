# WhatsApp Cloud — operación de FASE 10

La instalación permanece en simulation/mock. El backend se valida con HTTP mocks y
DNS/sockets bloqueados. No se ha activado `.env`, suscrito remotamente una app ni
realizado ningún envío a Meta. No hay funcionalidad de FASE 11.

## Configuración y autorización

```env
WHATSAPP_PROVIDER=mock
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_WABA_ID=
WHATSAPP_GRAPH_API_VERSION=
WHATSAPP_APP_SECRET=
WHATSAPP_VERIFY_TOKEN=
WHATSAPP_WEBHOOK_ENABLED=false
WHATSAPP_HTTP_TIMEOUT=15
```

Elegir una versión compatible expresamente desde la aplicación Meta. No se asume
una versión vigente ni se consulta Meta al abrir vistas. App secret y verify token
deben tener al menos 32 caracteres; usar un verify token aleatorio fuerte.
Token de acceso, app secret y verify token permanecen solo en entorno. La conexión
guarda IDs, versión, autorización/revisión y fingerprint HMAC ligado a SECRET_KEY.
Rotación exige reautorizar y preparar/aprobar nuevos mensajes. No copiar secretos
a referencias de comprobación, cuerpos ni plantillas.

V1 soporta un perfil de credenciales global. Su phone_number_id se asigna de forma
única e histórica a un workspace, incluso desactivado; no se comparte ni reasigna.
Cambiar a otra cuenta/número requiere diseñar otro perfil, no sobrescribir su historia.

1. Configurar app, WABA, número y versión compatible en Meta y entorno. Reiniciar
   Django y worker. **WhatsApp Business** muestra completitud local y autorización;
   no verifica credenciales, conectividad ni aprobación remotamente.
2. OWNER/ADMIN autoriza la conexión y registra plantilla **ya aprobada en Meta**:
   nombre, idioma, categoría MARKETING/UTILITY, texto exacto y header DOCUMENT opcional.
   La confirmación del operador no crea ni aprueba una plantilla remotamente.
3. Activar WHATSAPP_WEBHOOK_ENABLED=true y configurar/suscribir en Meta una URL pública
   **HTTPS** terminada en `/integrations/whatsapp/<workspace_uuid>/webhook/`, campo
   `messages` del WABA correspondiente. Proxy, HTTPS y suscripción son configuración
   del operador; `runserver` no se publica ni es un servidor de producción.
4. Permitir canales reales en el workspace. Establecer OUTREACH_MODE=live y
   WHATSAPP_PROVIDER=cloud en entorno, y reiniciar. El permiso de workspace es común
   a email y WhatsApp, pero cada canal exige su propia conexión autorizada.
5. Preparar WHATSAPP, marcar real, elegir plantilla y parámetros como lista JSON.
   Dejar cuerpo vacío para renderizar desde la definición; texto proporcionado debe
   coincidir exactamente con el resultado. Asunto es referencia local, no transmitida.
6. OWNER/ADMIN aprueba preview, documentos y política. Encolar y procesar:

```powershell
python manage.py run_worker --kinds SEND_OUTREACH
```

Permanecen supresión, pausa, cuota diaria, cooldown, intentos, roles/aprobación
vigentes y fecha programada. Se comprueban tras archivos e inmediatamente antes del
transporte. Nunca se convierten mocks previos en reales.

## Plantillas, documentos y enlaces

Cuerpo de hasta 4000 caracteres, parámetros consecutivos `{{1}}` a `{{10}}`, cada
valor de texto entre 1 y 1000 caracteres; se aplica también el límite del mensaje
renderizado y de metadatos. Definiciones se desactivan, no se editan; registrar otra
identidad para cambiar contenido. No hay parámetros con nombre, botones dinámicos,
templates de autenticación ni envío libre fuera de plantilla.

Header DOCUMENT sube solo el PDF principal privado de propuesta a `/<phone_id>/media`,
con integridad verificada y hasta 2 MB, y referencia su media ID en el template.
Activos de agencia son referencias locales y no se transmiten. Sin ese header no
se suben archivos. No se publican URLs privadas ni se descargan URLs arbitrarias.
Los enlaces pueden ser texto de parámetros; no hay botones URL dinámicos ni fetch
de enlaces. Los documentos entrantes tampoco se descargan.

Transporte fijo `https://graph.facebook.com/<version>/<phone_id>/`, TLS verificado,
timeout, sin redirects/proxies de entorno y respuesta limitada a 64 KiB. Guarda WAMID
y transmite `biz_opaque_callback_data` con UUID del mensaje. Ese identificador ayuda
a reconciliar el recibo mediante callback; no garantiza deduplicación externa.
SENT significa aceptación, no entrega/lectura hasta recibir sus eventos.

## Autenticación y eventos

Endpoint de máquinas DRF con MetaWebhookAuthentication; no acepta sesión/cookies
ni privilegios humanos. GET compara verify token y challenge. POST comprueba
X-Hub-Signature-256 por HMAC SHA-256 del cuerpo **crudo** antes de parsear JSON.
La UI mantiene sesiones y CSRF. No se añade csrf_exempt a views de sesión ni se
admiten callbacks sin firma. Django redacta queries de suscripción en sus logs de
desarrollo; configurar la misma redacción en proxy/servidor externo, fuera de Django.

Cuerpo máximo 128 KiB, diez entries/cambios y total 50 eventos por lote. Procesamiento
local corto/atómico sin I/O remoto. Si aumenta volumen, encolar en infraestructura
futura. Se verifican WABA, business phone, workspace y destinatario. WAMID desconocido
requiere mensaje con reserva real, aprobación y callback UUID coincidentes; se ignoran
otras identidades y campos sin conservar payload crudo.

Duplicados no repiten eventos ni auditoría. READ/REPLIED no retroceden por SENT o
DELIVERED tardíos; FAILED posterior no borra prueba de entrega/lectura. Fallos guardan
solo códigos numéricos y mensaje local, sin detalles crudos. Aceptación confirmada
persiste incluso si cambian permisos/pausa; reconcilia job FAILED sin modificar uno
RUNNING. Propuesta con transiciones pendientes por revocación permite **Actualizar CRM**.

Inbox básico conserva ID, remitente, tipo, UTC y texto hasta 4000 caracteres, escapado
y paginado. Contexto de respuesta se resuelve dentro del workspace/número. Contacto
solo se infiere si el teléfono es único en ese espacio. Una respuesta contextual
marca mensaje REPLIED y CONTACTED → REPLIED con actor de sistema verificado; no reabre
leads cerrados. Sin contexto conserva mensaje sin inferir lead entre campañas.
No envía respuestas, descarga media ni cancela/programa seguimientos de FASE 11.

## Fallos y reconciliación

401/403/rechazos permanentes quedan FAILED sanitizados. 429 respeta Retry-After en
segundos o HTTP date. Fallos previos a petición o de upload de media pueden reintentarse
dentro de presupuesto. Upload con recibo perdido podría dejar media huérfana remota
sin enviar un mensaje; limpieza remota queda pendiente de diseño de retención.

Timeout/desconexión, 5xx/redirect o respuesta inválida durante POST de mensaje quedan
UNKNOWN sin replay. No se traslada la idempotencia del mock a Cloud. Pausar no puede
retirar un mensaje ya aceptado. Callback firmado con UUID/destinatario correctos puede
reconciliar UNKNOWN y preservar DELIVERED/READ antes de la respuesta HTTP del worker.

Sin callback, parar el worker y reconciliar como OWNER/ADMIN: aceptación requiere
**WAMID comprobado en Meta**; ausencia confirmada permite reencolar solo tras verificación
y políticas actuales. Ausencia de información no basta. Fallo de entrega con WAMID
aceptado nunca se reenvía al recuperar/reintentar el mismo job. Auditoría conserva
resultado/hash de referencia, no su texto libre ni secretos.

## Ensayo externo pendiente

El operador debe verificar versión/contrato vigente de su app, publicar/suscribir
callback y autorizar un envío a un número de prueba bajo su control. Se mantiene la
restricción de no hacer conexiones externas durante esta entrega. Los tests offline
no sustituyen el criterio de aceptación de mensaje real controlado del playbook.

Comprobar WAMID, callbacks de entrega/lectura, firma inválida, supresión y replay del
mismo job sin otro envío. No introducir credenciales reales en tests o repositorio.
