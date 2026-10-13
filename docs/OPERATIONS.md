# Operación local y recuperación

Python 3.14, SQLite, un worker lógico. Mantener un terminal para `runserver` y otro
para `run_worker`. Los providers de una instalación inicial son mock/simulation.

## Recálculo de scores por lotes

Aplicar `migrate` antes de usar el tipo REBUILD_SCORES. Los comandos requieren
`--workspace`, `--actor` y `--idempotency-key` UUID; `rebuild_scores` requiere
además `--campaign`. Actor activo con rol OWNER/ADMIN/SALES/MEMBER; VIEWER no
encola y los superusuarios necesitan membership. Todos los IDs se resuelven
dentro del workspace y no se recorren automáticamente todos los espacios.

```powershell
python manage.py rebuild_scores --workspace <workspace-uuid> --actor <user-uuid> --campaign <campaign-uuid> --idempotency-key <uuid> --limit 100
python manage.py run_worker --once --kinds REBUILD_SCORES
python manage.py run_worker --kinds SCORE_LEAD
```

El padre prepara un lote de 1–200 leads en orden de UUID, excluyendo leads
archivados y empresas inactivas. Los leads cerrados pueden puntuarse, pero no
cambian de estado ni originan contacto. No se admite campaña archivada ni cursor
de otra campaña/workspace. El resultado del padre ofrece `next_after`; usarlo
con `--after` y una clave nueva para el siguiente lote. `next_after: null` indica
que no quedaban más candidatos en el momento de la expansión. Los nuevos leads
pueden requerir otra reconstrucción desde el principio con una clave nueva.

Los Jobs hijos y el manifiesto auditado se confirman en una sola transacción.
Una recuperación del padre vuelve a usar el lote confirmado y no duplica hijos.
Un fallo anterior al commit revierte toda la expansión. Los hijos conservan
el actor original; revocar su acceso impide calcular. Cambiar reglas/datos antes
de ejecutar un hijo afecta al score que se calcula entonces, no a la selección
ya congelada del lote. Progreso/historial/resultado se consultan en Trabajos.

Repetir un comando con la misma clave devuelve el mismo Job sin resucitar uno
cancelado/fallido. Cambiar campaña, cursor o límite con la misma clave falla.
Para reparar fallos de dominio, resolver la causa y pedir un Job con otra clave.
Para una ejecución RUNNING interrumpida, detener el worker y usar la recuperación
existente `requeue_stale_jobs`; nunca ejecutar dos workers sobre SQLite.

## Instantáneas de métricas

```powershell
python manage.py refresh_dashboard_metrics --workspace <workspace-uuid> --actor <user-uuid> --idempotency-key <uuid>
python manage.py run_worker --once --kinds REFRESH_METRICS
```

REFRESH_METRICS guarda agregados y fecha UTC `measured_at` en el resultado del Job
y en auditoría. No incluye contactos, mensajes, payloads externos ni secretos.
Repetir/recuperar el Job mantiene esa medición; usar una clave nueva para medir
otra vez. La página del dashboard consulta datos vivos y no depende de este Job.
Su semántica de conteo es la documentada en README: empresas totales, leads no
archivados por estado actual, jobs en FAILED y mensajes en PENDING_APPROVAL.
No hay cron ni nuevos servicios externos. El worker con `--kinds` permite limitar
esta operación a los tipos internos anteriores sin procesar trabajos de envío.

## Antes de una copia

Detener servidor HTTP y worker para que base y archivos permanezcan consistentes.
El comando toma además el mismo bloqueo OS del worker; falla si hay otro worker.
Es una operación de plataforma para el operador con acceso al equipo: contiene
todos los workspaces, usuarios, hashes de contraseña, sesiones y datos comerciales.
No se ofrece por HTTP ni a roles de workspace. Guardar fuera de static/media,
con acceso restringido y cifrado gestionado por el operador. `backups/` está ignorado
en Git. Los archivos ZIP no están cifrados por la aplicación.

```powershell
New-Item -ItemType Directory backups -Force
python manage.py backup_database --output backups/agencycrm-20261012.zip
```

El comando usa la API de backup SQLite, verifica integridad y foreign keys, y
acompaña la base con los PDFs referenciados y comprobados contra sus hashes.
El manifiesto conserva tamaños y SHA-256, y el contenido final se vuelve a verificar.
No incluye `.env`, logs, cachés del filesystem ni PDFs huérfanos. Conservar secretos
por un canal privado separado. Límites: 1 GB de base, 2 GB totales, 10000 PDFs.
SHA-256 comprueba integridad; no autentica quién creó la copia. Restaurar únicamente
copias procedentes del operador de confianza. PostgreSQL requerirá otro adaptador.

## Restaurar y ensayar

```powershell
python manage.py restore_database --input backups/agencycrm-20261012.zip --destination backups/recovery-20261012
```

El destino debe ser nuevo. Se validan formato, tamaño, hashes, CRC de ZIP, entradas
duplicadas, symlinks, rutas y todos los PDFs referenciados antes de crear el destino.
Nunca sobrescribe la instalación activa. Se obtiene `database.sqlite3` y `media/`.
Si falla la escritura final por disco/permisos, el destino puede quedar incompleto:
conservarlo para diagnóstico y repetir en otra carpeta nueva.

Antes de arrancar una instancia restaurada, configurar un entorno independiente
con `OUTREACH_MODE=simulation`, providers mock y adquisiciones externas desactivadas.
Usar otra instalación del código de la versión de la copia: copiar allí
`database.sqlite3` como `db.sqlite3` y la carpeta `media/` restaurada, en destinos
vacíos. Crear una `.env` nueva con otra SECRET_KEY: las sesiones firmadas con la
clave anterior dejarán de ser válidas. Así MEDIA_ROOT y las rutas relativas de los
documentos apuntan a la nueva instalación, sin usar los archivos de la original.
No ejecutar automáticamente jobs recuperados. Aplicar migraciones/checks en la
nueva instancia, comprobar login, un documento, listados y auditoría, y solo después
decidir el cambio de instalación. Revisar sesiones recuperadas y permisos de usuarios.
Conservar la instalación original y la copia hasta completar el ensayo.

## Exportación y retención

OWNER/ADMIN descarga CSV mediante POST con CSRF; el recurso, filtros y filas son
del workspace seleccionado. Máximo 5000 filas. No se exportan secretos, snapshots,
cuerpos de mensajes ni notas internas. Guardar los CSV como datos de negocio privados.

Retención exige confirmación del operador, periodo entre 7 y 3650 días y un Job.
Ejecutar el worker o `run_worker --once --kinds RETENTION`. El permiso se revalida
al ejecutar; revocarlo impide la limpieza. Cada lote borra hasta 1000 cachés expiradas
y vacía hasta 1000 textos WhatsApp anteriores al periodo. Encolar otro lote si el
resultado indica límite alcanzado. No existe cron ni limpieza automática por defecto.
El ID, remitente, contacto y contexto entrante se conservan para impedir recontacto.
La auditoría y los mensajes/eventos salientes siguen siendo inmutables.

Los PDFs huérfanos tras una caída se mantienen privados para revisión manual;
no se borran archivos por coincidencia de nombre ni se incluyen en backups.
Los logs y copias requieren política de conservación propia del operador.

## Futuro despliegue HTTPS

`config.settings.deployment` es una plantilla: DEBUG=False, cookies seguras,
redirección HTTPS, HSTS y CSP. El proxy no se declara automáticamente confiable.
Configurar el dominio en ALLOWED_HOSTS, TLS y la cabecera de esquema en un proxy
controlado antes de modificar SECURE_PROXY_SSL_HEADER. Servir solo staticfiles;
media debe permanecer privada y descargarse a través de Django. Ejecutar collectstatic.
El mailer de plataforma reutiliza configuración SMTP del entorno con TLS/SSL; el
outreach sigue usando su propia aprobación, policy y doble activación.

```powershell
python manage.py check --deploy --settings=config.settings.deployment
```

W021 es la advertencia esperada mientras `SECURE_HSTS_PRELOAD=false`. No se silencian
checks. Preload y su periodo HSTS se deciden sobre el dominio definitivo. El perfil
local mantiene cookies HTTP solo para desarrollo; no debe usarse en producción.
