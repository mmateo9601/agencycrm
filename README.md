# AgencyCRM — FASE 2 · Jobs locales y providers mock

Base local de AgencyCRM siguiendo `IMPLEMENTATION_PLAYBOOK_AGENCYCRM.md`.
Implementadas FASE 0, FASE 1 y FASE 2: base Django, cuentas, espacios de trabajo,
CRM básico, cola local de trabajos y proveedores simulados.
Python **3.14**, Django 6.1.x y DRF 3.18.x. No se ha avanzado a FASE 3.

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
Los filtros de elegibilidad y breakdown de score se incorporarán con sus fases.

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

Los ocho tipos de trabajo del playbook están declarados, pero sus handlers de
negocio se implementarán en sus fases. No se pueden encolar todavía mediante
`JobService`: la única operación ejecutable es el diagnóstico anterior. No hay
búsquedas reales, scoring, propuestas ni outreach. Las comprobaciones de elegibilidad
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
- `integrations/`: contratos con dataclasses y mocks puros de Places/Email/WhatsApp;
  no acceden al ORM ni realizan llamadas de red.
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
RUNNING requiere la futura fase de búsquedas. No hay detección automática de
oportunidades ni cálculo de scoring todavía.

CSRF permanece activo en login, logout y admin. Los tests bloquean conexiones
salientes y DNS. No hay llamadas externas de aplicación ni envíos reales;
los settings de provider son valores mock fijos en esta fase. La factoría rechaza
cualquier modo distinto de simulation/mock y nunca recurre a un provider real.

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
| pytest / pytest-django | Pruebas y bases de datos de test aisladas |
| factory-boy | Datos ficticios reproducibles de test |
| coverage | Medición de cobertura (umbral 80%) |
| Ruff | Lint y formato de Python 3.14 |

El lock incluye las dependencias transitivas de estas herramientas. El worker usa
solo Django y la biblioteca estándar; FASE 2 no añade dependencias. HTTP clients,
parsers y generación documental se aplazan a sus fases.
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
