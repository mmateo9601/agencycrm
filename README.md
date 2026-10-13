# AgencyCRM — FASE 12 · Hardening y dashboard

Base local de AgencyCRM siguiendo `IMPLEMENTATION_PLAYBOOK_AGENCYCRM.md`.
Implementadas FASE 0 a FASE 12: base Django, CRM, cola local, prospección mock,
CSV, Google Places, enriquecimiento web, scoring, propuestas, contacto simulado
y backends opcionales de email SMTP y WhatsApp Cloud API, con reglas de seguimiento
y recordatorios internos. Python **3.14**, Django 6.1.x y DRF 3.18.x.
Incluye CSP, permisos operativos, auditoría, CSV, retención y backup/restore local.
WhatsApp está verificado con HTTP mocks; el ensayo real controlado queda pendiente
de configuración y autorización del operador.

## Dashboard por workspace

Al entrar en un espacio desde la portada se abre `/w/<uuid>/dashboard/`.
Todos los roles con membresía activa pueden consultar empresas totales, leads
activos por estado, trabajos fallidos y mensajes pendientes de aprobación.
Los enlaces llevan al CRM filtrado por estado, a los jobs fallidos y al outbox.
La navegación del workspace permite volver al dashboard desde cualquier ficha.

Las cifras son estados actuales, no conversiones acumuladas: cada lead cuenta
una vez y los archivados se excluyen. Las empresas incluyen registros inactivos;
los jobs fallidos incluyen el historial. El enlace de mensajes abre el outbox
completo. Las cifras se recalculan al recargar con cuatro consultas agregadas,
sin caché, jobs, mutaciones ni llamadas externas. No hacen falta migraciones.

Este complemento cubre el dashboard de las secciones 22 y 33 del playbook.
Los complementos V1 incluyen dashboard, datos demo, API interna y comandos
operativos. No existe una FASE 13 en el playbook. Los ensayos externos de los
providers siguen pendientes de configuración y autorización del operador.

## Comandos operativos de scores y métricas

Ambos comandos encolan y requieren un actor con membresía activa de escritura,
workspace UUID y clave UUID de idempotencia. No ejecutan providers, envíos ni
el worker dentro del comando. Sin bypass por ser superusuario.

```powershell
python manage.py rebuild_scores --campaign <campaign-uuid> --workspace <workspace-uuid> --actor <user-uuid> --idempotency-key <uuid> --limit 100
python manage.py refresh_dashboard_metrics --workspace <workspace-uuid> --actor <user-uuid> --idempotency-key <uuid>
python manage.py run_worker --kinds REBUILD_SCORES,SCORE_LEAD,REFRESH_METRICS
```

`rebuild_scores` crea un Job padre REBUILD_SCORES. El worker selecciona hasta
200 leads no archivados de esa campaña con empresa activa, en orden de UUID,
y crea un Job SCORE_LEAD por lead. El score se calcula después por los servicios
existentes, conservando historial y sin cambiar el estado comercial.
La expansión es atómica; repetir/recuperar el padre conserva el lote y los IDs
de los hijos, aunque aparezcan nuevos leads. El resultado muestra `count`,
`job_ids` y `next_after`. Si `next_after` tiene valor, encolar otro lote con
`--after <next_after>` y una clave nueva. Para una reconstrucción nueva desde
el principio, usar otra clave. Se validan campaña/cursor y permisos al encolar
y ejecutar; cada Job hijo vuelve a comprobar permisos y elegibilidad al calcular.

`refresh_dashboard_metrics` crea un Job REFRESH_METRICS y guarda en su resultado
los mismos agregados del dashboard, el workspace y `measured_at`. La recuperación
reutiliza esa instantánea; una clave nueva obtiene una medición nueva. No crea
una caché ni modifica las cifras de la página: el dashboard sigue consultando
estados actuales. Resultados y progreso se consultan en **Trabajos**, dentro del
workspace. La API de jobs expone progreso, pero omite el resultado completo.

La migración `jobs/0003_alter_job_kind` añade REBUILD_SCORES; no añade tablas ni
dependencias. Más detalles y recuperación en [OPERATIONS.md](docs/OPERATIONS.md).

## API interna

Implementados los once endpoints de la sección 23 bajo `/api/v1/`: lectura de
empresas/leads/campañas/jobs, creación idempotente de campañas, aprobación de
propuestas y mensajes, y encolado de contacto. Todas las peticiones requieren
sesión y un único `workspace_id` en la query; los POST usan JSON y CSRF vigente.
No hay bypass para superusuarios ni unión de workspaces.

Los listados tienen páginas fijas de 25 filas y reutilizan filtros/selectors.
Las mutaciones llaman a los servicios existentes; aprobar no envía y encolar
solo crea/devuelve un Job. Se conservan permisos, revisiones, auditoría y políticas.
Campos desconocidos/repetidos fallan; no se exponen payloads, secretos ni rutas PDF.
La creación de campañas requiere `idempotency_key` UUID y conserva el registro
original al reintentar; reutilizarla con otra solicitud falla. No requiere migración.

Contrato, filtros, cuerpos y códigos de respuesta en [API.md](docs/API.md).

## Datos demo

Después de `migrate`, ejecutar en modo `OUTREACH_MODE=simulation`:

```powershell
python manage.py seed_demo
```

Crea **Demo Agency**, agencia y servicios SEO Local/Google Ads, campaña
**Dentistas Galicia**, diez empresas y contactos ficticios, oportunidades,
scores explicables y leads en diez estados con historial. Incluye un PDF de
propuesta generada, dos mensajes mock pendientes de aprobación, una supresión
y dos jobs diagnósticos cancelados mediante servicios, sin ejecutar proveedores.
No aprueba propuestas ni mensajes, no encola envíos y no ejecuta el worker.
Los estados comerciales representan un historial ficticio, no contactos reales.
Los dominios y emails usan `.invalid`; no se asignan números de teléfono.
El mensaje WhatsApp muestra la ausencia de destinatario hasta configurarlo
explícitamente para una prueba mock; no está listo para envío por defecto.

Por defecto solo crea un actor técnico `_agencycrm_demo_seed`, sin contraseña
utilizable ni acceso al admin. Para acceder por sesión con una cuenta nueva:

```powershell
python manage.py seed_demo --create-user demo
python manage.py changepassword demo
```

La cuenta opcional es OWNER de Demo Agency, sin permisos staff/superusuario.
`changepassword` pide una contraseña propia; no hay credenciales por defecto.
También se puede asignar una membresía a un usuario existente desde el admin.
El seed rechaza nombres de usuarios existentes que no haya creado él mismo.

Repetir el comando conserva UUIDs, archivos, aprobaciones y cambios del operador.
Puede añadir la cuenta opcional después del primer seed, pero nunca restablece
contraseñas, roles ni membresías revocadas. Usa el bloqueo del worker: detenerlo
antes de ejecutar el seed. Una colisión con los IDs/slug reservados falla sin
adoptar registros existentes. La creación es transaccional y limpia su nuevo
PDF si hay rollback. Un seed completado no repara datos borrados ni editados;
para una demo nueva, usar una instalación/base de datos independiente.

## Uso de FASE 12

OWNER/ADMIN dispone de **Auditoría**, **Exportar CSV** y **Retención** dentro de su
workspace. Las acciones comprueban el permiso actual en servicios y también en el
worker; no hay bypass por ser superusuario en la UI de workspaces. La auditoría
se consulta con filtros y paginación, y los logins de aplicación quedan registrados.
La exportación usa POST con CSRF, filtros del workspace, columnas explícitas y hasta
5000 filas; neutraliza celdas que podrían interpretarse como fórmulas.

La retención requiere confirmación y crea un Job del worker existente. Elimina hasta
1000 cachés caducadas y vacía hasta 1000 textos entrantes anteriores al periodo elegido.
Conserva los IDs, relaciones, fechas y la evidencia que impide nuevos contactos tras
una respuesta. Es la única excepción de contenido al modelo entrante append-only,
limitada al servicio de retención con auditoría en la misma transacción. No se purgan
supresiones, propuestas, cuerpos de mensajes salientes ni eventos/auditoría.

CSP usa el middleware nativo de Django, recursos locales y nonces para el admin,
sin scripts inline libres. Las páginas autenticadas y los documentos usan no-store.
Los PDFs privados rechazan symlinks/junctions y archivos con formato o hash inválidos.
Las relaciones de fichas, eventos y plantillas tienen páginas independientes que
conservan los filtros. Se añaden índices para auditoría, cooldown, eventos y retención.

Para copias offline y restauración en una carpeta nueva, consultar
[OPERATIONS.md](docs/OPERATIONS.md). El checklist y la matriz de roles están en
[SECURITY.md](docs/SECURITY.md). No se añaden dependencias ni Docker.

```powershell
New-Item -ItemType Directory backups -Force
python manage.py backup_database --output backups/agencycrm.zip
python manage.py restore_database --input backups/agencycrm.zip --destination backups/restored
python manage.py check --deploy --settings=config.settings.deployment
```

Detener servidor y worker antes de copiar/restaurar. La copia contiene todos los
workspaces, la base y los PDFs referenciados; `.env` se conserva por separado.
El perfil `deployment` es una plantilla HTTPS para futuro despliegue, no un servidor
ya desplegado. Preload HSTS permanece desactivado por defecto y produce W021: se
decide al preparar el dominio definitivo, sin silenciar checks.

## Uso de FASE 11

En **Seguimientos**, OWNER/ADMIN configura reglas por campaña o para todo el
workspace. Se crean deshabilitadas por defecto. Al habilitarlas, cada contacto
confirmado posterior programa revisiones internas (máximo 10 reglas activas y
10 revisiones por regla). También se puede programar un contacto anterior desde
el formulario. Repetir la programación conserva las tareas existentes y su estado.
Cada revisión conserva el origen, secuencia y configuración usada, con auditoría.

**Tareas y recordatorios** permite filtrar por estado y responsable, completar
una revisión con resultado, cancelarla o reprogramarla. Completar una revisión
no registra un contacto ni envía mensajes. Un nuevo contacto requiere preparar
otra propuesta/mensaje y su aprobación vigente mediante el flujo habitual.
La próxima acción del lead usa la primera tarea pendiente, conservando una fecha
manual si el operador la ha introducido.

La programación y cada acción comprueban el cooldown del workspace y de los
canales, contando los contactos de la empresa en todas sus campañas. Una respuesta,
reunión, propuesta activa, WON/LOST, descarte, supresión, archivo, pausa o regla
deshabilitada cancela los recordatorios aplicables. Una respuesta WhatsApp firmada
de un contacto identificado también cancela revisiones sin contexto de respuesta.
Un nuevo contacto confirmado sustituye el plan anterior. Reanudar una regla o
expirar una supresión no resucita tareas canceladas. Las reglas editadas no cambian
las fechas ya planificadas; las políticas actuales pueden ampliarlas o cancelarlas.

Los servicios de aplicación reconcilian estos cambios explícitamente, sin signals.
Para cambios directos de permisos/configuración y como revisión periódica del
operador, ejecutar este comando con un actor activo autorizado del workspace:

```powershell
python manage.py reconcile_followups --workspace <uuid> --actor <user_uuid>
```

No hay cron, hilo de fondo ni envío automático añadido. Los recordatorios se crean
dentro de la operación de contacto confirmado; el comando solo reconcilia los
existentes. Las comprobaciones se repiten al actuar, aunque el comando no se haya
ejecutado. Se añaden `crm/0003_lead_next_action_managed` y
`outreach/0004_salestask_cancellation_reason_salestask_revision_and_more`.
Las migraciones anteriores se conservan. No se añaden dependencias.

## Instalación (PowerShell)

```powershell
python --version  # Debe ser Python 3.14.x
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

Pegar la clave generada en `SECRET_KEY` dentro de `.env`. No compartirla ni
versionarla. Las variables del proceso tienen prioridad sobre `.env`.
Si `.env` ya existe, conservar su configuración. En esta instalación se ha
generado una clave local aleatoria y se ha aplicado la migración a SQLite.

```powershell
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8000
```

En Linux/macOS, activar con `source .venv/bin/activate` y copiar con
`cp .env.example .env`. No se necesita Docker ni ningún servicio adicional.

- `/accounts/login/`: acceso por usuario y contraseña.
- `/`: portada protegida por sesión.
- `/accounts/logout/`: cierre de sesión mediante POST y CSRF.
- `/admin/`: administración de plataforma para superusuarios.
- `/health/`: JSON con disponibilidad de aplicación y DB; 200 si responde la
  base de datos, 503 si falla. No revela detalles de excepciones.

Crear desde el admin un `Workspace`, usuarios, sus `Membership`, un
`AgencyProfile` y sus `ServiceOffering`. No se crean cuentas con contraseñas
por defecto. Los roles de membership son OWNER, ADMIN, SALES, MEMBER y VIEWER.
OWNER y ADMIN pueden crear, editar, archivar y eliminar; SALES y MEMBER pueden
crear y editar; VIEWER solo consulta. `seed_demo` permite crear un espacio ficticio
con datos de ejemplo y una cuenta opcional sin contraseña por defecto.

## Uso de FASE 1

1. Crear una membership activa para el usuario, incluso si es superusuario.
2. Entrar en `/` y seleccionar el espacio de trabajo.
3. Crear una empresa en **Empresas** y una campaña en **Campañas**; asignar
   servicios del mismo espacio. Configurar canales desde **Canales de campaña**.
4. Crear un lead seleccionando la campaña y empresa. La pareja no puede repetirse
   ni cambiarse después; una empresa puede participar en distintas campañas.
5. Abrir el lead y registrar una transición permitida. Su historial muestra
   estado anterior, nuevo estado, motivo, usuario y fecha. Perder o descartar
   exige un motivo. Registrar CONTACTED es manual y no envía comunicaciones.
6. Gestionar contactos, referencias externas, perfiles sociales y oportunidades
   desde sus listados. Los formularios solo ofrecen relaciones del espacio actual.

Las URLs de trabajo son `/w/<workspace_uuid>/<recurso>/`. Todos los listados
están paginados (25 registros) y conservan filtros al cambiar de página.
Leads permite filtrar por campaña, empresa, estado, responsable, sector, ciudad,
score, email, teléfono, oportunidad activa y archivo. El historial se pagina
de 20 en 20; las relaciones en fichas tienen páginas independientes de 25 elementos.
El desglose de score está disponible en FASE 6; elegibilidad corresponde a su fase.

**Archivar** conserva empresas, campañas y leads con sus relaciones e historial.
No se crean nuevos leads en empresas/campañas archivadas ni se cambian estados
de leads archivados. Contactos, referencias, perfiles y oportunidades permiten
borrado confirmado, registrado en auditoría. No hay borrados mediante GET.

## Uso de FASE 2

Mantener dos terminales con el mismo entorno y `.env`: una para `runserver`
y otra para el worker. El servidor HTTP solo encola y consulta trabajos.

```powershell
python manage.py run_worker
# Procesar como máximo un trabajo disponible y salir:
python manage.py run_worker --once
# Filtrar tipos y ajustar la espera cuando no hay trabajo:
python manage.py run_worker --kinds CHECK_MOCK_PROVIDERS --sleep 2
```

En un workspace, **Trabajos → Nuevo diagnóstico** permite comprobar Places,
Email y WhatsApp mock. El job `CHECK_MOCK_PROVIDERS` usa exclusivamente fixtures
ficticias y destinatarios `.invalid`; no crea empresas/leads ni contacta a nadie.
Los escenarios éxito, error temporal inicial y error permanente permiten observar
progreso, intentos y errores seguros. El detalle consulta progreso cada dos segundos
mediante un endpoint de la propia aplicación. VIEWER consulta; los roles con
escritura encolan; OWNER/ADMIN cancelan pendientes o recuperan interrumpidos.
Cada formulario genera una clave de idempotencia: repetir su POST no duplica el job.
La misma clave dentro de un workspace conserva el trabajo original y nunca lo
reprograma, incluso después de un reintento. Cambiar tipo, payload, prioridad o
presupuesto con esa clave produce un error.

Los tipos de trabajo del playbook están declarados. Se ejecutan el diagnóstico
y `RUN_SEARCH_ITEM`, `ENRICH_COMPANY`, `SCORE_LEAD`, `GENERATE_PROPOSAL` y `SEND_OUTREACH`.
El contacto simulado comprueba elegibilidad y aprobación humana. No existe un handler
de envío que permita saltárselas.

Solo un worker puede usar una base de datos local. Un bloqueo del sistema operativo
en `<database>.worker.lock` impide arrancar un segundo proceso y se libera al salir,
incluso si el proceso termina inesperadamente. **No borrar ese archivo para forzar
un desbloqueo.** Cada ejecución tiene además un token: callbacks antiguos no pueden
modificar un trabajo recuperado. Los handlers se ejecutan fuera de transacciones;
los cambios de estado, intento y auditoría usan transacciones cortas. El worker
vuelve a comprobar la membership del creador antes de ejecutar.

Solo los errores temporales declarados permiten reintentos, con espera exponencial
de 5 segundos hasta 300 segundos; los límites del proveedor pueden alargarla.
El presupuesto predeterminado es tres intentos (configurable entre uno y diez).
Errores permanentes o inesperados finalizan el job. Nunca se persisten mensajes
crudos de excepciones, payloads o credenciales en el historial de errores.

Tras interrumpir un worker, **detenerlo antes de recuperar** jobs RUNNING de más de
15 minutos. La recuperación utiliza el mismo bloqueo; la antigüedad por sí sola
no permite tomar un trabajo que otro proceso sigue ejecutando.

```powershell
python manage.py requeue_stale_jobs --workspace <UUID>
# El operador local puede ajustar la antigüedad mínima en segundos:
python manage.py requeue_stale_jobs --workspace <UUID> --older-than 900
```

La CLI es una herramienta privilegiada del operador; omitir `--workspace` abarca
toda la base. La UI siempre limita la recuperación al job del workspace autorizado.
Recuperar cierra el intento interrumpido y no restablece el presupuesto: si está
agotado, el job termina FAILED. Esta arquitectura admite un solo worker en una
máquina y un disco local; no pretende coordinar varias máquinas. PostgreSQL o
Celery/RQ requerirán un adaptador de coordinación apropiado, conservando los
contratos de encolado y de proveedores.

## Uso de FASE 3

1. Crear una campaña con consulta, localidad y objetivo de leads; cambiarla de
   DRAFT a READY desde la ficha de campaña.
2. Abrir **Prospección → Nuevo plan o CSV** y seleccionar esa campaña.
3. Para Places mock, dejar CSV vacío e indicar opcionalmente localidades, una
   por línea. Para importar, subir un CSV UTF-8 con esta cabecera exacta:

   ```csv
   name,website,email,phone,address,city,province,country
   Clínica Ficticia,https://clinica.example.invalid,info@example.invalid,,Calle Demo 1,A Coruña,A Coruña,ES
   ```

4. Pulsar **Crear plan y encolar**. La view guarda un plan acotado y sus jobs;
   las empresas/contactos/leads se crean exclusivamente al procesar el worker.
5. Mantener `python manage.py run_worker` en la segunda terminal, o procesar
   un elemento con `python manage.py run_worker --once --kinds RUN_SEARCH_ITEM`.
6. Abrir el trabajo para consultar progreso, o actualizar el plan para consultar
   ejecuciones, contadores y resultados por fila con enlaces al CRM.
7. **Reejecutar / ver pendiente** reutiliza el trabajo activo o encola una nueva
   ejecución. Repetir el mismo POST no duplica jobs; repetir resultados no duplica
   empresas/leads identificados. Los historiales anteriores se conservan.

El planner TEXT_QUERIES acepta como máximo veinte localidades, elimina repeticiones
y reparte un presupuesto de 1–500 resultados, con un máximo de 100 por consulta.
El objetivo no es una garantía: una sola consulta solicita como máximo 100 aunque
la campaña pida más. El mock devuelve siempre las tres empresas ficticias del
escenario E2E (Clínica Norte, Clínica Atlántico y Dental Demo), independientemente
de la localidad; no simula una base real de negocios. GEO_GRID/HYBRID y paginación
externa siguen pendientes; Google real se configura como se explica en FASE 4.

CSV admite hasta **200 KB y 200 filas**. Solo se usa el nombre para crear una
empresa; los demás campos pueden estar vacíos. Las URLs requieren HTTP(S), los
teléfonos se validan y normalizan a E.164 y los emails se validan antes de escribir.
Un teléfono nunca se interpreta automáticamente como WhatsApp. Filas con valores
incorrectos o distinto número de columnas aparecen con su línea y campos afectados;
las válidas se importan. Una cabecera incorrecta, codificación inválida o CSV roto
rechaza el fichero completo antes de encolar. Se valida extensión, tipo declarado,
tamaño y contenido; el nombre original no se utiliza como ruta ni se guarda un
archivo público. Entradas y snapshots limitados permanecen en la base de datos.

**Identidad y procedencia:** primero se busca `provider + external_id` dentro del
workspace. Los fixtures usan `MOCK_PLACES`, nunca `GOOGLE_PLACES`. En CSV, el
identificador corresponde al documento normalizado y su línea; importar el mismo
documento de nuevo conserva esa identidad. Fuera de esa identidad de origen,
solo se enlaza automáticamente una coincidencia única de dominio/teléfono con
nombre normalizado compatible (en esta fase, igualdad exacta). Los nombres solos
nunca provocan una fusión. Coincidencias ambiguas de dominio/teléfono o nombre y
dirección generan `DuplicateCandidate`, visible en **Posibles duplicados**, y
conservan empresas separadas. La resolución/fusión manual no se implementa aún.

Se conserva una fila de procedencia por resultado y ejecución, con empresa, lead,
fecha y disposición. No se sobrescriben campos manuales existentes ni flags de
contacto; solo se completan campos vacíos. Una empresa archivada genera error
de fila y nunca se reactiva. Cambiar o reordenar un CSV genera un nuevo documento:
las empresas sin identificadores fiables pueden requerir revisión manual.

El provider se invoca fuera de transacciones. Cada ejecución conserva un snapshot
limitado y cada fila se confirma junto con empresa, referencia externa, contacto,
lead e historial, en una transacción corta. Tras un fallo temporal o recuperación,
las filas ya confirmadas se reutilizan y sus contadores no se duplican. Estado de
plan, elemento y ejecución sigue a los jobs, incluso en cancelación y recuperación.
Una ejecución SUCCEEDED con errores de validación significa **importación parcial**:
revisar siempre el contador de errores. Completar una búsqueda no completa la
campaña comercial: esta fase no cambia su estado ni inicia outreach.

El planner es síncrono porque genera como máximo veinte elementos; no necesita
`BUILD_SEARCH_PLAN` ni un proceso adicional. Toda adquisición/ingestión se realiza
como `RUN_SEARCH_ITEM`. Mock y CSV siguen funcionando sin credenciales ni red.

## Uso de FASE 4

La configuración por defecto continúa siendo mock. Para habilitar Google, el operador
debe configurar **las tres condiciones** siguientes en `.env` y reiniciar servidor
y worker (nunca introducir la clave en formularios, Git o comandos compartidos):

```env
PLACES_PROVIDER=google
GOOGLE_PLACES_ENABLED=true
GOOGLE_PLACES_API_KEY=valor_privado
PLACES_CACHE_SECONDS=300
```

Con una condición ausente, Google queda desactivado; no hay fallback silencioso.
La UI muestra el estado sin consultar la API ni revelar credenciales. CSV sigue
disponible. Email y WhatsApp permanecen mock con `OUTREACH_MODE=simulation`:
habilitar adquisición en Google no habilita mensajes reales.

En la campaña, configurar **Límite total de peticiones Google** (0–1000,
predeterminado 20; cero bloquea nuevas peticiones). Crear un plan desde Prospección
y ejecutar el worker. Cada plan conserva el proveedor elegido y el país de la
campaña en el momento de crearlo. Los planes mock anteriores y el diagnóstico
siempre siguen siendo mock, aunque cambie la configuración global.

La UI utiliza Text Search de Places API (New), una página de hasta veinte resultados
por consulta, sin paginación automática. Si Google indica más resultados, la ejecución
lo muestra. No se guarda ni se reutiliza el token de página. Nearby Search y Place
Details están implementados y probados como contratos de adaptador; no se añade
una UI geográfica ni se solicitan Details automáticamente después de Text Search.
El FieldMask es explícito: identidad/nombre, dirección/componentes, coordenadas,
web, teléfono internacional, rating/número de reseñas y URL de Maps. Estos datos
se normalizan y se utilizan en Company y CompanyExternalRef. No se piden fotos
ni contenido de reseñas.

**Presupuesto:** se reserva una petición en una transacción corta **antes** de HTTP.
Éxitos, timeouts, 429 y 5xx consumen reserva; si el proceso cae entre la reserva
y HTTP, esa reserva se conserva por prudencia. Los reintentos y nuevas ejecuciones
comparten el límite total de campaña. Editar la campaña no reinicia el contador.
El límite puede aumentarse explícitamente desde su formulario. Las fichas de plan,
campaña y ejecución muestran contadores; son reservas de solicitudes, no importes
monetarios ni una reproducción de la factura de Google. No se codifican tarifas.

**Caché:** snapshots normalizados por workspace y fingerprint completo de consulta,
localidad, país, límite y versión de máscara. La ventana es configurable entre cero
(desactivada) y 3600 segundos; predeterminado 300. Un hit no consume presupuesto.
Entradas expiradas nunca se reutilizan y se eliminan durante la siguiente adquisición
del workspace. El historial normalizado de la ejecución sigue persistiendo como
procedencia. Reejecutar usa la deduplicación existente por `GOOGLE_PLACES + place_id`;
la fuente mock mantiene su espacio de identificadores separado.

**Transporte y errores:** `httpx`, HTTPS con origen fijo `places.googleapis.com`,
verificación TLS, sin proxies heredados ni redirects. Límite de respuesta 1 MB,
JSON y respuesta sin compresión. Timeout de conexión 5 s y lectura 10 s; se comprueba
el presupuesto de tiempo transcurrido de 20 s al recibir cabeceras y cada fragmento
(una lectura en curso puede tardar hasta su timeout). 401/403 son permanentes;
429 respeta Retry-After acotado a un día; timeout/5xx usan los reintentos limitados
del job. Mensajes externos, headers de autenticación y tokens no se registran en
JobAttempt ni audit. No se invoca HTTP desde views ni dentro de transacciones.

La validación de esta entrega usa exclusivamente `httpx.MockTransport` con fixtures
ficticias y bloqueo global de sockets/DNS. **No se ha realizado una consulta con
credenciales reales**; la comprobación operativa contra una cuenta Google queda
pendiente de que el operador configure sus credenciales y presupuesto. No se
modifica `.env` para activar Google durante la implementación.

## Uso de FASE 5

Desde la ficha de una empresa, abrir **Enriquecimiento web → Encolar análisis web**.
La acción POST con CSRF encola `ENRICH_COMPANY`; consultar su progreso en Trabajos
y volver a la ficha para ver el historial paginado de observaciones. El worker
existente procesa también estos trabajos:

```powershell
python manage.py run_worker --kinds ENRICH_COMPANY
```

El proveedor predeterminado `WEBSITE_PROVIDER=mock` utiliza HTML ficticio local,
sin DNS ni sockets. Solo acepta empresas con web en un dominio `.invalid` para
evitar atribuir datos ficticios a empresas reales. Las observaciones identifican
claramente el proveedor. Las fixtures incluyen correo, teléfono y perfil social
de demostración; ningún teléfono se convierte automáticamente en WhatsApp.

El operador puede habilitar el rastreo público mediante **ambos** valores:

```env
WEBSITE_PROVIDER=web
WEB_CRAWLER_ENABLED=true
```

Reiniciar servidor y worker tras cambiar el entorno. Cada trabajo conserva su
empresa, URL y proveedor; los trabajos mock pendientes siguen siendo mock.
Desactivar el proveedor real impide ejecutar sus trabajos pendientes. Los permisos,
la empresa activa y su URL se revalidan antes de adquirir y antes de guardar.

**Política de rastreo:** GET secuenciales, sin JavaScript, autenticación, cookies,
proxies heredados ni envío de formularios. Se consulta robots.txt antes de cada
página y de cada salto de redirección. Un 404 de robots permite continuar; otros
errores impiden el rastreo. Se respeta Crawl-delay y Request-rate. Se visita la
portada y enlaces internos de contacto, servicios, nosotros y reservas. Los enlaces
sociales se registran sin visitarlos. Privacidad, reservas, tienda, formularios y
analytics se registran como señales observadas; scoring usa las señales descritas en FASE 6.

**Límites:** 8 páginas como máximo, 5 redirecciones por recurso, 54 peticiones
incluyendo robots y redirects, 2 MB acumulados de cuerpos y 60 segundos totales
predeterminados. `.env.example` documenta tamaño, páginas, delay y timeouts;
hay techos de 8 páginas, 2 MB y 120 segundos. El delay mínimo real es 0,1 s.
La resolución DNS usa un subproceso síncrono acotado por el plazo restante,
exclusivamente desde el worker. Conexión, TLS y lecturas comparten ese plazo.
El worker único evita peticiones simultáneas al mismo dominio.

**SSRF:** se comprueban todas las IP resueltas y se conecta directamente a una IP
pública validada, preservando hostname/SNI y verificación TLS. Se comprueba la IP
del socket; cada conexión resuelve y valida de nuevo. Se bloquean IP privadas,
reservadas, metadata, loopback y mecanismos IPv6 de transición. Solo HTTP/HTTPS
en puertos estándar; se rechazan credenciales y queries. No se permiten cambios
de hostname (incluido `www`) ni downgrade HTTPS. Las rutas de acceso/administración
se excluyen también cuando están codificadas. Solo se aceptan HTML/texto sin
compresión. Estas restricciones pueden impedir analizar sitios legítimos; no
se ofrece un bypass de seguridad.

**Persistencia:** `WebsiteObservation` conserva datos normalizados, procedencia,
fechas, páginas y errores seguros por intento. No se guarda HTML. Se añaden
contactos y perfiles ausentes sin modificar datos manuales ni `do_not_contact`.
Observación, contactos y auditoría se guardan juntos después de HTTP. Recuperar
un trabajo con éxito ya confirmado no repite la adquisición ni crea duplicados.
Timeout, 429 (incluido Retry-After HTTP-date) y 5xx usan los reintentos limitados
del job. Un fallo aborta la observación de ese intento, deja un error visible y
no cambia campañas ni impide procesar otras empresas.

Se ha validado con fixtures y transportes simulados; **no se han visitado webs
reales**. Quedan fuera renderizado JS, sitios con login/CAPTCHA, extracción de
datos personales ajenos al contexto comercial.

Migraciones nuevas:

- `companies/0002_websiteobservation`: observaciones UUID, índice por workspace
  y empresa, relaciones protegidas y unicidad por job/intento.
- `companies/0003_websiteobservation_website_attempt_positive_and_more`: constraints
  de intento positivo, proveedor, límite de páginas y orden temporal.

## Uso de FASE 6

En la ficha de un lead, abrir **Calcular / consultar histórico y desglose** y
**Encolar nuevo cálculo**. El POST con CSRF encola `SCORE_LEAD`; el worker único
lo procesa sin red. La ficha muestra el score activo y sus aportaciones; la
página de scoring permite abrir cada ejecución histórica y sus datos/reglas.

```powershell
python manage.py run_worker --kinds SCORE_LEAD
```

Cada ejecución guarda un `ScoringRun` UUID, inputs normalizados, reglas y versiones,
huella SHA-256, suma original y puntuación limitada a **0–100**. La fórmula de
`scoring-v1` es `max(0, min(100, suma_de_aportaciones))`; no reescala los pesos
cuando se deshabilita una regla. Cada regla activa genera un `LeadScoreItem`,
incluyendo cero puntos si no coincide o faltan datos. Motor, snapshots y desglose
permiten reproducir un cálculo aunque cambien posteriormente empresa o reglas.

Las nueve reglas predeterminadas se crean explícitamente al primer cálculo o
mediante el botón de **Reglas de scoring**, sin signals ni sobrescrituras:

| Regla | Puntos | Criterio |
| --- | ---: | --- |
| WEBSITE | 10 | Web registrada; no implica que se haya verificado su disponibilidad |
| EMAIL | 12 | Email normalizado de contacto empresarial registrado |
| PHONE | 8 | Teléfono E.164 de empresa/contacto empresarial |
| SOCIAL | 5 | Perfil social registrado |
| HIGH_OPPORTUNITY | 15 | Oportunidad manual HIGH activa |
| MEDIUM_OPPORTUNITY | 8 | Oportunidad manual MEDIUM o detector coincidente |
| LOW_REVIEWS | 10 | Número de reseñas conocido menor que el umbral configurado |
| WEAK_CTA | 10 | Observación válida sin contacto, reserva ni formulario observado |
| MISSING_CHANNEL | 7 | Canal habilitado en campaña sin datos empresariales registrados |

OWNER/ADMIN editan nombre, puntos entre −100 y 100, activación, orden y configuración.
Cada cambio incrementa la versión; una edición antigua se rechaza. El catálogo
de códigos es cerrado. `LOW_REVIEWS` acepta `{"max_reviews": 20}` como criterio
del workspace aplicado a sus campañas (rango 0–100000). `MISSING_CHANNEL` acepta
`{"channel": "EMAIL"}`; también PHONE, LINKEDIN o INSTAGRAM. Las otras reglas usan
`{}`. No se aceptan expresiones ni configuración arbitraria. Los roles con escritura
pueden encolar cálculos; VIEWER consulta. Leer las páginas no crea reglas ni jobs.

Detectores iniciales: pocas reseñas conocidas, conversión no observada y canal
objetivo sin datos. Crean oportunidades MEDIUM con evidencia y confianza explícitas;
pueden recomendar REPUTATION o WEB_DESIGN si ese servicio activo pertenece a la
campaña. No tener web, datos desconocidos o un crawl fallido no se interpretan como
una oportunidad HIGH. El detector web usa únicamente la última observación exitosa
si también es la última observación de la empresa y coincide con su URL actual.
Las conclusiones negativas se limitan a las páginas analizadas y requieren revisión.

Score, histórico, oportunidades automáticas y auditoría se guardan en una sola
transacción. Un nuevo cálculo sustituye el puntero activo del lead y desactiva las
oportunidades automáticas anteriores, conservando sus evidencias. Las oportunidades
manuales se mantienen. Las generadas no se editan ni eliminan desde CRUD; se reemplazan
mediante scoring. Repetir la clave de idempotencia devuelve la ejecución original;
recuperar un job ya calculado no duplica histórico ni reactiva un score antiguo.

El cálculo usa reglas y datos vigentes al ejecutarse en el worker. No cambia estados
comerciales, no usa `minimum_score` para cualificar automáticamente y no autoriza
envíos. No incorpora propuestas ni funcionalidades de FASE 7. Sin dependencias nuevas.

Migraciones:

- `scoring/0001_initial`: ScoringRule, ScoringRun y LeadScoreItem, unicidad,
  rangos e índice de histórico por workspace/lead.
- `crm/0002_lead_current_score_run_opportunity_scoring_run`: puntero al score
  activo y vínculo de oportunidades con su ejecución; preserva los datos existentes.

## Uso de FASE 7

Configurar `AgencyProfile` y de uno a cinco servicios activos en la campaña.
Abrir **Propuestas → Generar propuesta**, seleccionar lead, proveedor y hasta
cinco adjuntos. El POST con CSRF encola `GENERATE_PROPOSAL` en el worker existente:

```powershell
python manage.py run_worker --kinds GENERATE_PROPOSAL
```

La opción predeterminada es **Plantilla determinista**: texto generado localmente
con Django Templates a partir de agencia, empresa, servicios de campaña y hasta
cinco oportunidades activas. No inventa importes ni promete resultados; alcance,
calendario y presupuesto quedan para la reunión. **IA simulada** ejecuta
`MockAIProvider` sin red y etiqueta el texto como demostración. El contrato
`AIProvider` queda preparado para una fase futura; no existe proveedor IA real.

La generación congela un snapshot acotado y la versión de plantilla `proposal-v1`.
ReportLab produce el PDF fuera de transacciones, con fuentes locales, texto escapado,
márgenes A4 y paginación. Texto HTML de fuentes externas se trata como texto;
no se ejecuta ni se descargan imágenes o recursos remotos. Glifos no soportados
por la fuente se sustituyen por `?`. La vista HTML imprimible y el PDF excluyen
`internal_notes`. La entrega incluye validación estructural y revisión visual de
un PDF ficticio de dos páginas con contenido largo.

Cada propuesta tiene UUID, versión por lead y clave de idempotencia por workspace.
Repetir el job devuelve la misma versión sin repetir archivos ni adjuntos.
Generar una nueva versión conserva las anteriores y su aprobación histórica,
marcando SUPERSEDED las anteriores que todavía no figuren como SENT. La propuesta
generada es inmutable: para corregir contenido, actualizar los datos fuente y
generar otra versión. La UI lista todas las versiones con paginación.

**Revisión y aprobación:** un rol con escritura confirma GENERATED → REVIEWED;
OWNER/ADMIN confirma REVIEWED → APPROVED. La revisión usa la versión y adjuntos
mostrados; cambios concurrentes se rechazan mediante revisión incremental.
Se comprueba integridad de PDF y adjuntos antes de aprobar y se registra actor,
fecha y auditoría. Una propuesta sustituida no es elegible. `require_approved`
y el service de estados rechazan registrar SENT sin aprobación; este contrato
servirá al futuro flujo de outreach. La UI no ofrece envíos ni registra SENT.
Aprobar no cambia el estado del lead ni contacta a nadie.

**Activos y adjuntos:** OWNER/ADMIN sube PDFs estáticos en **Activos comerciales**.
Tipos: presentación de agencia, caso de estudio, tarifas, folleto u otro.
Se validan extensión, MIME, cabecera y estructura con pypdf; se rechazan cifrado,
formularios, adjuntos embebidos, acciones/enlaces activos y PDFs dañados. Máximo
1,5 MB y 100 páginas; el límite se aplica durante la recepción de chunks y de
nuevo en el service. Solo se recibe un archivo por subida. El límite general de
archivos permanece en 2 MB, compatible con la importación CSV.

Los activos predeterminados se preseleccionan al generar; el usuario puede elegir
otros activos activos del mismo espacio. Las relaciones `ProposalAttachment`
quedan protegidas. Archivar un activo impide adjuntarlo a nuevas propuestas y
conserva su archivo para propuestas históricas. Para reemplazarlo se sube otro.
PPTX y generación dinámica de presentaciones se aplazan; las presentaciones
corporativas de esta fase se suben como PDF.

Los archivos se almacenan con nombres aleatorios bajo
`MEDIA_ROOT/proposals/<workspace>/<proposal>/` y `sales_assets/<workspace>/<asset>/`.
No hay ruta pública de media. Cada descarga autentica, verifica el workspace,
la ruta esperada y SHA-256 antes de abrir el archivo; devuelve attachment y
`Cache-Control: private, no-store`. Nunca se utiliza un nombre o ruta del usuario
para resolver un archivo local. VIEWER puede consultar y descargar documentos
de sus espacios, pero no generar, revisar, aprobar o subir activos.

Versiones, adjuntos y auditoría se confirman en una transacción; si falla, se
elimina el archivo recién creado. DB y filesystem no ofrecen una transacción
distribuida: una interrupción abrupta puede dejar archivos huérfanos sin acceso
desde la aplicación; limpieza y retención operativa se aplazan a hardening.
Los backups futuros deben incluir SQLite y media. No hay envíos, mensajes,
elegibilidad comercial ni funcionalidades de FASE 8.

Migración `proposals/0001_initial`: Proposal, SalesAsset y ProposalAttachment,
relaciones protegidas, unicidad por lead/versión y workspace/clave, límite de
tamaño, estados válidos y requisito de aprobación para APPROVED/SENT.

## Uso de FASE 8

La instalación sigue en **simulation**. Email y WhatsApp utilizan exclusivamente
MockEmailProvider/MockWhatsAppProvider: no SMTP, HTTP ni mensajes externos.
El flujo de esta sección describe la simulación. El email real opcional de FASE 9
requiere remitente autorizado y doble activación explícita, descrita más abajo.

Flujo de trabajo:

1. Preparar la campaña (READY/RUNNING), habilitar sus canales y marcar el lead READY
   mediante las transiciones de CRM. Campañas DRAFT/PAUSED y leads con respuesta,
   reunión o cierre bloquean contacto en esta fase.
2. Generar, revisar y aprobar una propuesta de la versión vigente.
3. Abrir **Outbox → Preparar mensaje**, elegir propuesta, contacto empresarial y canal.
   El texto vacío usa resumen y próximos pasos; se conservan destinatario, texto y PDF
   con los adjuntos originales. El contenido es inmutable; cambios requieren otro mensaje.
4. OWNER/ADMIN revisa contenido, destinatario, adjuntos y la política aplicable y confirma
   la aprobación humana. La aprobación vincula todo por SHA-256 y revisión incremental.
5. Un rol con escritura encola la simulación/tarea. El worker reevalúa la política antes
   de reservar un intento, después de validar archivos y antes de confirmar el resultado.
6. Un recibo mock registra SENT, eventos y auditoría, pasa READY → CONTACTED y actualiza
   `last_contact_at`. La propuesta pasa a SENT. Este estado registra **simulación**.

```powershell
python manage.py run_worker --kinds SEND_OUTREACH
```

El job solo contiene `message_id`, siempre resuelto en su workspace. Un job que no
esté asociado al mensaje no puede procesarlo. Cada mensaje conserva una clave
idempotente; repetir preparación/encolado devuelve el original. Recuperar un job
ya confirmado reutiliza su recibo sin llamar de nuevo al provider. Los mocks generan
recibos estables por workspace, canal y clave. No se implementa PREPARE_OUTREACH como
job: preparar texto local es un service corto y no requiere worker.

**Elegibilidad:** ContactEligibilityDecision es append-only y guarda versión de
política, fecha, actor y motivos. La preview GET es de solo lectura; un POST o el
worker registra la evaluación. Se comprueban workspace, usuario/membership activos,
empresa/campaña/lead, datos empresariales, destinatario vigente, propuesta y mensaje
aprobados, aprobador activo OWNER/ADMIN, hash de contenido/adjuntos, confirmación
de revisión, canal habilitado, fecha programada, pausa, límites y modo del mensaje.
Datos públicos no implican autorización comercial; la política/jurisdicción aplicable
debe revisarla el operador. No se automatiza esa decisión ni se afirma cumplimiento jurídico.

WhatsApp necesita `whatsapp_e164` explícito. Un teléfono ordinario no se copia a
WhatsApp. El cambio de destinatario o contenido después de aprobar bloquea el mensaje.
Los mensajes con propuesta sustituida o sin aprobación vigente tampoco se procesan.
Después de registrar SENT, una nueva propuesta/mensaje requiere nueva aprobación;
no se reenvía automáticamente la propuesta ya enviada.

**Supresión:** OWNER/ADMIN crea bloqueos EMAIL, PHONE, DOMAIN o COMPANY con motivo,
fuente y caducidad opcional. Se normalizan email, teléfono E.164, dominio y UUID de
empresa. El bloqueo pertenece al workspace y se aplica entre sus campañas; dominio
incluye subdominios. Se comparan todos los identificadores conocidos del contacto y
empresa, de forma conservadora entre canales. Desactivar fija la caducidad y conserva
registro/auditoría; reactivar reutiliza el mismo valor normalizado. No hay borrado por GET.

**Límites:** OutreachSettings parte de `.env.example` (25 email/día, 10 WhatsApp/día,
14 días de cooldown) y máximo 3 intentos por empresa. OWNER/ADMIN puede configurar
límites y cooldown; aprobación humana permanece obligatoria. El día se calcula según
`DEFAULT_TIMEZONE`, por defecto Europe/Madrid, independientemente del timezone del
usuario. El cooldown usa el mayor del workspace y del canal, y consulta último contacto
de la empresa entre campañas. Los intentos se limitan por empresa y por canal y consumen
cuota al reservarse, incluidos fallos; una reserva conservada en SENDING no se cuenta
dos veces al recuperar el mismo intento. Límites diarios cero deshabilitan ese canal.

Las reservas, estados, decisiones y auditoría se guardan en transacciones cortas;
la llamada mock y lectura de archivos quedan fuera. Un fallo temporal mock puede
reintentarse dentro del presupuesto del job y del canal/empresa. Un fallo permanente
queda FAILED. Esta recuperación depende de la idempotencia conocida del mock;
SMTP usa reservas y reconciliación de aceptación incierta, sin replay ciego.
Cancelar/fallar un job proyecta el estado
en su mensaje con eventos; no quedan mensajes encolados por revocación de acceso.

**Pausa de emergencia:** en **Outbox → Límites y pausa**, OWNER/ADMIN puede pausar o
reanudar. La pausa no borra jobs ni datos. Mensajes aprobados/encolados se bloquean
cuando se evalúan; terminan SUPPRESSED sin llamar al provider y requieren un nuevo
mensaje/aprobación tras corregir el motivo. Esto evita reactivar envíos antiguos al reanudar.

**Canales manuales:** PHONE crea CALL; CONTACT_FORM, LINKEDIN, INSTAGRAM y OTHER crean
SalesTask. No se llama, publica ni envía formularios automáticamente. El operador ve
destino e instrucciones, comprueba la elegibilidad y registra el resultado de su acción.
Completar reevalúa la política y registra contacto/eventos una sola vez; si aparece un
bloqueo, la tarea se cancela. No hay reglas de seguimiento ni programación automática
de FASE 11. El estado final manual representa una acción declarada por el operador.

Los adjuntos MessageAttachment referencian únicamente PDF/activos protegidos de la
propuesta y su workspace; se verifica integridad antes de aprobar y procesar. No se
guardan cuerpos ni destinatarios en metadata de auditoría/eventos. Las pantallas tienen
CSRF, autoescape, permisos por workspace y paginación. Las fichas de lead incluyen
propuestas, mensajes y tareas. El admin de estos modelos es de solo lectura.

Migraciones:

- `outreach/0001_initial`: OutreachSettings, OutreachMessage, MessageAttachment,
  MessageEvent y SalesTask; rangos, aprobación obligatoria, proveedores locales,
  claves/eventos/adjuntos únicos y referencias protegidas.
- `compliance/0001_initial` y `0002_initial`: SuppressionEntry y
  ContactEligibilityDecision, relaciones por workspace y unicidad de supresión.

La prueba E2E recorre mock Places → empresa/lead → enriquecimiento → scoring →
propuesta → aprobación → mensaje → aprobación → envío mock, y verifica que una
supresión posterior bloquea otro intento. Tests bloquean DNS/sockets; no hay
credenciales reales ni nuevas dependencias de FASE 8.

## Uso de FASE 9 — email SMTP

El backend real seleccionado es **SMTP autenticado con TLS**, usando `smtplib`,
`ssl` y `email` de Python 3.14. La alternativa SMTP permite completar la fase sin
OAuth Gmail, persistencia de tokens ni dependencias nuevas. Las credenciales se
administran exclusivamente en el entorno; no se guardan en SQLite, jobs,
auditoría, eventos ni formularios. No se ha modificado ni activado tu `.env`.

Configuración de una cuenta SMTP administrada por el operador:

```env
OUTREACH_SMTP_HOST=smtp.example.invalid
OUTREACH_SMTP_PORT=587
OUTREACH_SMTP_USERNAME=
OUTREACH_SMTP_PASSWORD=
OUTREACH_SMTP_SENDER=agency@example.invalid
OUTREACH_SMTP_SECURITY=starttls
OUTREACH_SMTP_TIMEOUT=15
```

Los valores `.invalid` son ficticios; sustituirlos por la configuración del servidor
elegido. `starttls` exige negociación TLS y certificado válido; `ssl` permite TLS
implícito (normalmente puerto 465). No se admite SMTP en claro ni TLS sin verificación.
El host es configuración del operador por entorno, nunca una URL introducida por
usuarios. No se activa debug SMTP ni se incluyen respuestas crudas en errores/logs.
Esta instalación tiene **una cuenta/remitente SMTP global**; cada workspace debe
autorizar expresamente su uso. No se ofrecen cuentas independientes por workspace.

1. Reiniciar Django y worker tras configurar el entorno. En **Conexión de email**,
   OWNER/ADMIN autoriza el remitente del servidor y su nombre visible para el workspace.
   La UI muestra configuración local completa/incompleta y autorización vigente;
   **no prueba conexiones, credenciales ni disponibilidad del servidor**.
2. En esa misma pantalla, permitir email real en el workspace. Separadamente,
   el operador debe establecer `OUTREACH_MODE=live` y `EMAIL_PROVIDER=smtp` en el
   entorno y reiniciar ambos procesos. Solo ambas activaciones permiten enviar.
3. Preparar un mensaje EMAIL y marcar explícitamente **Preparar email real**.
   Se congelan remitente, conexión/revisión y destinatario. OWNER/ADMIN revisa y
   aprueba contenido, adjuntos y política. Los mocks antiguos nunca pasan a reales.
4. Encolar **email real** y procesarlo con `run_worker --kinds SEND_OUTREACH`.
   Las políticas de FASE 8 permanecen activas; se reevalúan tras validar archivos
   e inmediatamente antes de reservar el transporte. La red queda fuera de las
   transacciones. Falta de credenciales, cambio de remitente/conexión/credenciales,
   revocación, supresión, pausa y límites bloquean el transporte.
5. La aceptación SMTP conserva `provider_message_id`, eventos y auditoría, actualiza
   contacto/CRM/propuesta y no se repite al recuperar un job. **SENT significa
   aceptado por el servidor SMTP, no entregado al destinatario ni leído.**

SMTP no devuelve un identificador de mensaje/thread portable del servidor: el campo
`provider_message_id` conserva el **RFC Message-ID estable** generado por workspace
y clave, también visible en la preview para comprobaciones. No es una garantía de
deduplicación del proveedor. No se implementan threading remoto, seguimiento de
respuestas, OAuth Gmail ni lectura de buzón. Son capacidades posteriores de otro
adaptador. WhatsApp Cloud se incorpora en FASE 10, descrita más abajo.

La conexión conserva únicamente identidad, revisión, autorización y una huella HMAC
de la configuración, ligada a `SECRET_KEY`; no conserva password/access/refresh tokens.
Rotar credenciales, SECRET_KEY o configuración exige volver a autorizar conexión y
preparar/aprobar nuevos mensajes. No copiar los secretos a referencias de comprobación.
En simulation con EMAIL_PROVIDER=mock, aun con permiso live del workspace, solo se
utiliza el mock. Con configuraciones incompatibles se bloquea; no hay fallback live.
WhatsApp en simulation requiere mock; Cloud exige su propia conexión y plantilla
autorizadas. Los canales manuales no utilizan transporte.

**Fallos y reconciliación:** autenticación/TLS inválidos y rechazo permanente quedan
FAILED sin reintentos automáticos. Fallos antes de DATA o rechazo SMTP explícito 4xx
pueden reintentarse con backoff dentro de presupuestos/cooldown/cuotas. Un timeout,
desconexión o error incierto durante la entrega quedan **UNKNOWN** y Job FAILED.
La marca `transport_started_at` se guarda antes de contactar con el provider; si el
worker cae o la persistencia del recibo falla, recuperación conserva esa incertidumbre.
Ni reencolar el job ni reiniciar el worker vuelve a enviar ese mensaje automáticamente.

Detener el worker y abrir la ficha del mensaje como OWNER/ADMIN. Comprobar por
Message-ID los registros del servidor SMTP y aportar referencia + confirmación:

- **Aceptación confirmada:** registra SENT/recibo sin enviar otra vez, incluso si
  ahora hay pausa/supresión; conserva un hecho ya sucedido.
- **Ausencia de aceptación confirmada:** permite reencolar el mismo job/mensaje
  solo si supera la elegibilidad actual. Ausencia de información no basta.
- **Fallo confirmado:** permite reintentar FAILED sin reserva incierta, sujeto a
  controles actuales y máximo absoluto de diez ejecuciones del job.
- **Actualizar CRM:** aplica transiciones pendientes de una aceptación ya registrada,
  usando permisos del operador actual y sin transporte. Se utiliza si el remitente
  perdió permisos durante el envío; el recibo y el cooldown ya quedaron conservados.

Se auditan resultado y SHA-256 de la referencia, evitando almacenar texto libre que
podría contener secretos. SMTP no permite garantizar entrega exactamente una vez ante
caída entre aceptación y persistencia: se prioriza no duplicar y revisión explícita.
La pausa bloquea nuevos envíos; no puede retirar un email ya aceptado durante la llamada.

Migración `outreach/0002_emailconnection_and_more`: conexión UUID por workspace,
remitente/revisión congelados, reserva de transporte, UNKNOWN y constraints de
identidad SMTP/proveedor/aprobación; conserva los mensajes y aprobaciones mock previos.
Las pruebas sustituyen **SMTP/SMTP_SSL**, no HTTP (este backend no usa HTTP), y bloquean
globalmente DNS/sockets. No se ha realizado ningún login ni envío SMTP externo.

## Uso de FASE 10 — WhatsApp Business Cloud

Configuración y ensayo controlado: [docs/WHATSAPP.md](docs/WHATSAPP.md).

- Backend con `httpx` ya instalado, token/app secret/verify token exclusivamente en
  entorno y versión Graph elegida expresamente por el operador. Instalación en mock;
  no se ha modificado ni activado tu `.env` ni realizado llamadas a Meta.
- **WhatsApp Business** permite autorizar un business phone para su workspace,
  registrar plantilla existente/aprobada en Meta y activar permiso de canales reales.
  El número no puede compartirse ni reasignarse entre espacios.
- Preparar WHATSAPP real seleccionando plantilla y parámetros como lista JSON de textos.
  Contenido, destinatario, plantilla, conexión/revisión y archivos quedan congelados
  y vinculados a aprobación humana. Solo se envía el PDF principal si tiene header DOCUMENT;
  activos de agencia quedan como referencias locales y asunto no se transmite.
- Envío exige OUTREACH_MODE=live, WHATSAPP_PROVIDER=cloud, permiso del workspace,
  conexión/plantilla vigentes y todas las políticas de FASE 8. Worker de SEND_OUTREACH;
  ningún HTTP durante transacciones ni desde views.
- Webhook `/integrations/whatsapp/<workspace_uuid>/webhook/` con verify token para
  suscripción y firma HMAC del cuerpo crudo en POST. Es una API de máquinas con
  autenticación propia; sesiones, CSRF y permisos de la UI permanecen activos.
- Eventos idempotentes y estados de entrega/lectura/fallo sin regresiones. Inbox
  con texto acotado/escapado y contexto de respuesta solo dentro del workspace;
  no descarga media ni envía respuestas o seguimientos automáticos.
- Recibo incierto queda UNKNOWN sin replay. Un callback firmado puede reconciliar
  WAMID perdido; revisión manual requiere resultado comprobado en Meta.

Migración `outreach/0003_inboundwhatsappmessage_whatsappconnection_and_more`: conexión,
plantilla, inbound, snapshots y relaciones, identidad Cloud, unicidad de WAMID y
ampliación de IDs. Preserva migraciones y datos anteriores. Sin nuevas dependencias.
La aceptación externa del playbook requiere un ensayo real controlado: queda pendiente
por falta de configuración/autorización y la restricción de no realizar conexiones externas.

## Estructura y decisiones

- `config/settings/base.py`: configuración común, autenticación DRF por sesión,
  permisos autenticados por defecto, validadores de contraseña, logging y límites.
- `config/settings/local.py`: SQLite, DEBUG configurable y cookies para HTTP local.
- `config/settings/test.py`: SQLite en memoria, hasher rápido, correo locmem,
  media temporal y proveedores obligatoriamente mock.
- `apps/accounts/`: custom User basado en AbstractUser desde `0001_initial`,
  modelos UUID, admin y selectores con aislamiento explícito.
- `apps/dashboard/`: portada, métricas por workspace en selectors, presentación
  de contadores/enlaces y view HTTP de health; la comprobación de DB vive en
  `common/health.py`. No hay lógica de negocio en views ni signals propias.
- `apps/companies/`, `apps/campaigns/`, `apps/crm/`: modelos, formularios,
  selectores y servicios del dominio. Las mutaciones son transaccionales y
  validan relaciones, permisos y campos explícitos antes de persistir.
- `apps/audit/`: registro mínimo append-only de las mutaciones y del ciclo de jobs.
  No guarda cuerpos, contraseñas ni payloads de formularios.
- `apps/jobs/`: JobService, runner, recuperación, historial de intentos, UI y CLI.
- `apps/prospecting/`: planificación, importación CSV, ingestión idempotente,
  procedencia, sugerencias de duplicados, consultas y UI por workspace.
- `integrations/`: contratos con dataclasses, mocks puros y adaptador Google Places;
  los providers no acceden al ORM ni cambian el dominio.
- `common/`: infraestructura compartida de CRUD, permisos, normalización y
  presentación. El registro HTTP de recursos es una lista cerrada de modelos.
- `templates/`, `static/vendor/bootstrap/`: HTML con autoescape y Bootstrap
  **5.3.2** vendorizado con licencia MIT. No hay CDN ni JavaScript remoto.
- `tests/`: factories, autenticación, CSRF, constraints, permisos y aislamiento.

El timezone se configura con `DEFAULT_TIMEZONE` (por defecto Europe/Madrid),
manteniendo `USE_TZ=True`. `DATABASE_NAME` permite otra ubicación SQLite.
El futuro PostgreSQL requiere un settings de despliegue y su driver, sin
cambiar estos modelos: UUID, foreign keys y constraints usan el ORM portable.

## Frontera de aislamiento

Todo acceso de aplicación a datos de un workspace debe pasar por los selectores
de las apps y `common/selectors.py`, con **usuario y workspace explícitos**.
Verifican usuario, workspace y membership activos; un UUID ajeno devuelve 404.
Tener varias memberships no mezcla los datos de sus espacios. No hay bypass
de superusuario en estos selectores. Las futuras consultas y mutations deberán
aplicar esta misma frontera, también al resolver relaciones por identificador.
El ORM directo es una API interna de confianza, no una barrera automática ni RLS.

El admin es una consola **global de plataforma**, reservada exclusivamente al
operador superusuario; puede gestionar todos los espacios para su configuración.
Un ADMIN de workspace no es un superusuario Django. Usuarios staff, incluso
con permisos Django asignados, no pueden acceder a esta consola. No conceder
`is_superuser` a usuarios de un workspace. Los modelos de FASE 1, jobs y auditoría son
de **solo lectura en admin**, incluso para superusuarios: las mutaciones pasan
por los servicios de la UI por workspace. No hay una API de dominio pública.

`CompanyExternalRef` almacena workspace explícito, validado contra su empresa,
para imponer `unique(workspace, provider, external_id)` en la base de datos.
Las demás relaciones verifican también coincidencia de workspace. El enlace
M2M `CampaignService` valida campaña y servicio mediante un modelo intermedio.
Los nombres normalizados de empresa no son únicos y nunca provocan merges.

Las operaciones de aplicación deben usar services; `QuerySet.update`, SQL y
bulk operations son herramientas internas de confianza que pueden omitir
validaciones Python. No se exponen en UI ni API. PostgreSQL RLS y triggers no
forman parte de esta fase; las constraints de unicidad y rangos sí están en DB.

`LeadStateService` implementa transiciones explícitas, revisión incremental y
actualización condicional para detectar escrituras concurrentes. Un reintento
al mismo estado no duplica historial. Estado, historial y auditoría se guardan
en una sola transacción. Los estados finales no se reabren en esta fase.
Las campañas solo pasan de DRAFT a READY, vuelven a DRAFT o se archivan;
los planes tienen su propio estado de ejecución y no completan automáticamente
la campaña comercial. Scoring prioriza la revisión y conserva estados comerciales.

CSRF permanece activo en login, logout y admin. Los tests bloquean conexiones
salientes y DNS. Google y SMTP solo operan tras activación explícita; email requiere
además permiso de workspace, conexión autorizada y aprobación humana.

El campo `AgencyProfile.logo` está reservado, no editable todavía. No se habilitan
uploads ni se publican URLs de media sin autorización; validación de imágenes,
nombres aleatorios y descarga autenticada quedan pendientes cuando se implemente
esa funcionalidad. Esto evita introducir Pillow o una ruta de archivos pública
solo para el bootstrap. `MEDIA_ROOT` ya está configurado y en tests es temporal.

Los settings base usan cookies seguras, CSP y DEBUG desactivado. `local.py` es solo
para desarrollo HTTP local. El perfil `deployment.py` exige HTTPS y activa HSTS;
configurar hosts, TLS, ficheros estáticos y SMTP antes de usarlo. No usar `runserver`
en producción. Ejecutar `check --deploy` con ese perfil y revisar el checklist.

## Dependencias justificadas

`requirements.txt` define rangos; `requirements.lock` fija todas las versiones
validadas para reproducir esta entrega.

| Dependencia directa | Motivo |
| --- | --- |
| Django 6.1.x | ORM, sesiones, CSRF, templates, admin y servidor local |
| DRF 3.18.x | API interna por sesión, CSRF y serializers/paginación explícitos |
| python-dotenv | Carga de `.env` local sin sobrescribir el entorno |
| tzdata | Base IANA para Europe/Madrid, necesaria en Windows |
| phonenumbers | Validación y normalización E.164 de empresas/contactos, sin red |
| httpx 0.28.x | HTTP de Places y webs con timeouts y transportes sustituibles en tests |
| httpcore 1.0.x | Backend público para fijar la conexión a una IP validada y conservar TLS |
| beautifulsoup4 | Parsing HTML acotado y extracción sin ejecutar JavaScript |
| reportlab 4.x | Generación local de propuestas PDF A4 con fuentes y paginación |
| pypdf 6.x | Validación estructural de PDFs/activos y pruebas de apertura y texto |
| pytest / pytest-django | Pruebas y bases de datos de test aisladas |
| factory-boy | Datos ficticios reproducibles de test |
| coverage | Medición de cobertura (umbral 80%) |
| Ruff | Lint y formato de Python 3.14 |

El lock incluye las dependencias transitivas de estas herramientas. El worker usa
solo Django y la biblioteca estándar para la cola; las integraciones usan los
clientes y parsers indicados. Pillow y charset-normalizer llegan como dependencias
transitivas de ReportLab; no se habilitan uploads de logos ni imágenes.
Bootstrap es el único recurso frontend externo vendorizado; se
copió de una distribución 5.3.2 ya disponible localmente conservando su cabecera.

## Verificación

Con `.venv` activado:

```powershell
python manage.py makemigrations --check --dry-run
python manage.py check
pytest
ruff check .
python -m coverage run -m pytest
python -m coverage report
python -m pip check
python manage.py runserver 127.0.0.1:8000 --noreload
```

Las pruebas HTTP utilizan el cliente Django en memoria, sin conexiones de red.
Comprueban login/logout con CSRF real, health 200/503, sesiones, intentos de
acceso a datos ajenos, revocaciones de acceso, UUID y constraints de BD.
El servidor se comprueba arrancándolo y deteniéndolo después de verificar su
mensaje de inicio. No se dejan procesos de desarrollo abiertos.

La migración `accounts/0001_initial.py` crea User, Workspace, Membership,
AgencyProfile y ServiceOffering. Se aplican además las migraciones estándar de
auth, contenttypes, sessions y admin. FASE 1 añade y aplica:

- `companies/0001_initial`: Company, CompanyExternalRef, Contact y SocialProfile,
  índices de búsqueda y constraints.
- `campaigns/0001_initial`: Campaign, CampaignChannel y CampaignService.
- `crm/0001_initial`: Lead, LeadStatusHistory y Opportunity; unicidad
  campaña/empresa, historial por revisión y rangos de score/confidence.
- `audit/0001_initial`: AuditEvent append-only de las operaciones de aplicación.

Las migraciones previas no se modifican. Las pruebas adicionales cubren CRUD
completo, relaciones manipuladas, acceso cruzado incluso con dos memberships,
roles, archivado, normalización, atomicidad y reintentos de transiciones.

FASE 2 añade `jobs/0001_initial`: Job y JobAttempt con UUID, índices de cola,
unicidad de clave por workspace y número de intento, constraints de presupuesto,
progreso, estado y token de ejecución. Las pruebas cubren bloqueo entre procesos,
idempotencia tras reintentos, recuperación y rollback, progreso HTTP, CSRF,
revocación de permisos, aislamiento y ausencia de llamadas a providers reales.

FASE 3 añade `prospecting/0001_initial`: SearchPlan, SearchPlanItem, SearchRun,
SearchResult y DuplicateCandidate. Incluye unicidad de petición por workspace,
elemento por secuencia, ejecución por job y fila por ejecución; constraints de
presupuestos, estados y contadores. Las migraciones anteriores se conservan.
Las pruebas cubren campaña → plan → worker → empresa/lead, reejecución, CSV parcial,
normalización, dedupe conservador, rollback, snapshot tras fallo, recuperación,
CSRF, roles e IDs ajenos incluso con varias memberships activas.

FASE 4 añade dos migraciones `0002`: campañas incorpora límite/contador de peticiones;
prospección incorpora país del plan (con backfill desde la campaña), contador de
peticiones por ejecución, indicador de caché y PlacesCache aislada por workspace.
No se modifican migraciones aplicadas anteriormente. Las pruebas de adaptador y
flujo cubren máscaras, normalización, fallos HTTP, credenciales ausentes, consumo
antes de dispatch, reintentos, caché/expiración/aislamiento y deduplicación.
