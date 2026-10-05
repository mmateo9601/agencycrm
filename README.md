# AgencyCRM — FASE 0 · Bootstrap

Base local de AgencyCRM siguiendo `IMPLEMENTATION_PLAYBOOK_AGENCYCRM.md`.
Solo está implementada la FASE 0: cuentas, espacios de trabajo, configuración
de agencia y servicios, sesiones, administración, plantilla Bootstrap y health.
Python **3.14** prevalece sobre el 3.12 del playbook por instrucción del proyecto.

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
Los permisos finos de operaciones futuras quedan pendientes de su fase.
Los workers y `seed_demo` no existen en Bootstrap: solo se usa una terminal.

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
- `templates/`, `static/vendor/bootstrap/`: HTML con autoescape y Bootstrap
  **5.3.2** vendorizado con licencia MIT. No hay CDN ni JavaScript remoto.
- `tests/`: factories, autenticación, CSRF, constraints, permisos y aislamiento.

El timezone se configura con `DEFAULT_TIMEZONE` (por defecto Europe/Madrid),
manteniendo `USE_TZ=True`. `DATABASE_NAME` permite otra ubicación SQLite.
El futuro PostgreSQL requiere un settings de despliegue y su driver, sin
cambiar estos modelos: UUID, foreign keys y constraints usan el ORM portable.

## Frontera de aislamiento

Todo acceso de aplicación a datos de un workspace debe pasar por los selectores
de `apps/accounts/selectors.py`, que reciben **usuario y workspace explícitos**.
Verifican usuario, workspace y membership activos; un UUID ajeno devuelve 404.
Tener varias memberships no mezcla los datos de sus espacios. No hay bypass
de superusuario en estos selectores. Las futuras consultas y mutations deberán
aplicar esta misma frontera, también al resolver relaciones por identificador.
El ORM directo es una API interna de confianza, no una barrera automática ni RLS.

El admin es una consola **global de plataforma**, reservada exclusivamente al
operador superusuario; puede gestionar todos los espacios para su configuración.
Un ADMIN de workspace no es un superusuario Django. Usuarios staff, incluso
con permisos Django asignados, no pueden acceder a esta consola. No conceder
`is_superuser` a usuarios de un workspace. Las pantallas de gestión por workspace
se incorporarán en sus fases; no hay endpoints de dominio públicos en FASE 0.

CSRF permanece activo en login, logout y admin. Los tests bloquean conexiones
salientes y DNS. No hay providers, llamadas externas de aplicación, automatización
ni envíos reales; los settings de provider son valores mock fijos en esta fase.

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
| pytest / pytest-django | Pruebas y bases de datos de test aisladas |
| factory-boy | Datos ficticios reproducibles de test |
| coverage | Medición de cobertura (umbral 80%) |
| Ruff | Lint y formato de Python 3.14 |

El lock incluye las dependencias transitivas de estas herramientas. HTTP clients,
parsers, normalización telefónica, generación documental y workers se aplazan
a sus fases. Bootstrap es el único recurso frontend externo vendorizado; se
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
auth, contenttypes, sessions y admin. No hay modelos ni funcionalidades de FASE 1.
