# AgencyCRM — FASE 6 · Scoring y oportunidades

Base local de AgencyCRM siguiendo `IMPLEMENTATION_PLAYBOOK_AGENCYCRM.md`.
Implementadas FASE 0 a FASE 6: base Django, CRM, cola local, prospección mock,
CSV, Google Places, enriquecimiento web y scoring explicable.
Python **3.14**, Django 6.1.x y DRF 3.18.x. No se ha avanzado a FASE 7.

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
crear y editar; VIEWER solo consulta. `seed_demo` no existe aún.

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
de 20 en 20; las relaciones en fichas muestran los primeros 25 elementos.
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
y `RUN_SEARCH_ITEM`, `ENRICH_COMPANY` y `SCORE_LEAD`; los demás handlers llegarán en sus fases.
Propuestas y outreach llegarán en sus fases. Las comprobaciones de elegibilidad
y consentimiento pertenecen a la futura fase de outreach; no existe un handler
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

## Estructura y decisiones

- `config/settings/base.py`: configuración común, autenticación DRF por sesión,
  permisos autenticados por defecto, validadores de contraseña, logging y límites.
- `config/settings/local.py`: SQLite, DEBUG configurable y cookies para HTTP local.
- `config/settings/test.py`: SQLite en memoria, hasher rápido, correo locmem,
  media temporal y proveedores obligatoriamente mock.
- `apps/accounts/`: custom User basado en AbstractUser desde `0001_initial`,
  modelos UUID, admin y selectores con aislamiento explícito.
- `apps/dashboard/`: portada y view HTTP de health; la comprobación de DB vive
  en `common/health.py`. No hay lógica de negocio en views ni signals propias.
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
salientes y DNS. No hay envíos reales; Google solo puede realizar búsquedas tras
activación explícita. La factoría de mensajes rechaza cualquier provider real.

El campo `AgencyProfile.logo` está reservado, no editable todavía. No se habilitan
uploads ni se publican URLs de media sin autorización; validación de imágenes,
nombres aleatorios y descarga autenticada quedan pendientes cuando se implemente
esa funcionalidad. Esto evita introducir Pillow o una ruta de archivos pública
solo para el bootstrap. `MEDIA_ROOT` ya está configurado y en tests es temporal.

Los settings base usan cookies seguras y DEBUG desactivado. Antes de desplegar
se requiere configuración de HTTPS, `SECURE_SSL_REDIRECT`, hosts y CSP; CSP y
hardening de despliegue corresponden a FASE 12. `local.py` es solo para desarrollo.
No usar `runserver` en producción. Ejecutar `check --deploy` con futuros settings
de despliegue, no interpretar el settings local como preparado para producción.

## Dependencias justificadas

`requirements.txt` define rangos; `requirements.lock` fija todas las versiones
validadas para reproducir esta entrega.

| Dependencia directa | Motivo |
| --- | --- |
| Django 6.1.x | ORM, sesiones, CSRF, templates, admin y servidor local |
| DRF 3.18.x | Base de API interna con SessionAuthentication |
| python-dotenv | Carga de `.env` local sin sobrescribir el entorno |
| tzdata | Base IANA para Europe/Madrid, necesaria en Windows |
| phonenumbers | Validación y normalización E.164 de empresas/contactos, sin red |
| httpx 0.28.x | HTTP de Places y webs con timeouts y transportes sustituibles en tests |
| httpcore 1.0.x | Backend público para fijar la conexión a una IP validada y conservar TLS |
| beautifulsoup4 | Parsing HTML acotado y extracción sin ejecutar JavaScript |
| pytest / pytest-django | Pruebas y bases de datos de test aisladas |
| factory-boy | Datos ficticios reproducibles de test |
| coverage | Medición de cobertura (umbral 80%) |
| Ruff | Lint y formato de Python 3.14 |

El lock incluye las dependencias transitivas de estas herramientas. El worker usa
solo Django y la biblioteca estándar para la cola; las integraciones usan los
clientes y parsers indicados. La generación documental se aplaza a su fase.
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
