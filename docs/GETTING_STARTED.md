# Inicio local

AgencyCRM utiliza Python 3.14, SQLite y archivos locales. La instalación inicial
trabaja en simulación y no requiere credenciales de Google, SMTP ni WhatsApp.
La descarga de dependencias requiere acceso al índice de paquetes o una caché local.

## 1. Crear el entorno

Desde la raíz del repositorio, en Windows PowerShell:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock
Copy-Item .env.example .env
```

En Linux/macOS, con Python 3.14 disponible:

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
cp .env.example .env
```

Estos comandos de copia son para una instalación nueva. Si existe `.env`,
conserva su contenido y compara las variables con la plantilla.

Si PowerShell impide activar scripts, usa directamente
`.\.venv\Scripts\python.exe` en los comandos Python. Para ejecutar el worker
en otro terminal también puedes usar esa ruta sin cambiar políticas del sistema.

## 2. Configurar

Genera una clave local:

```bash
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

Pega el resultado en `SECRET_KEY` de `.env`. Nunca lo publiques. La plantilla
[.env.example](../.env.example) es la referencia de variables disponibles.

| Variable | Uso local |
|---|---|
| `DJANGO_SETTINGS_MODULE` | `config.settings.local` |
| `DEBUG` | `True` solo en desarrollo local |
| `ALLOWED_HOSTS` | `127.0.0.1,localhost` |
| `DATABASE_NAME` | `db.sqlite3`, relativa a la raíz |
| `DEFAULT_TIMEZONE` | `Europe/Madrid`; fechas internas con timezone |
| `DEFAULT_COUNTRY` | `ES`, para normalización telefónica |
| `OUTREACH_MODE` | `simulation` |
| `EMAIL_PROVIDER`, `WHATSAPP_PROVIDER`, `PLACES_PROVIDER`, `AI_PROVIDER` | `mock` |

Mantén las integraciones externas desactivadas y sus secretos vacíos para la demo.
La activación de adapters requiere pasos adicionales documentados en
[Providers](PROVIDERS.md); cambiar solo una variable no sustituye la revisión.

## 3. Preparar la base y la demo

```bash
python manage.py migrate
python manage.py check
python manage.py seed_demo --create-user demo
python manage.py changepassword demo
```

`seed_demo` genera diez empresas ficticias y un workspace **Demo Agency**.
Es idempotente: repetirlo conserva las identidades. El usuario opcional nace sin
contraseña utilizable; `changepassword` establece la que elijas. No hay contraseña
compartida, envíos ni llamadas a providers reales desde el seed.

Para gestionar usuarios y configuración en Django Admin:

```bash
python manage.py createsuperuser
```

El administrador de plataforma puede gestionar workspaces y memberships.
Para operar la aplicación comercial también necesita una membership activa en
el workspace. La cuenta `demo` tiene rol OWNER en su demo, pero no es staff.

## 4. Usar dos terminales

Terminal A, desde la raíz y con el entorno activado:

```bash
python manage.py runserver 127.0.0.1:8000
```

Terminal B, desde la misma raíz y con el mismo entorno:

```bash
python manage.py run_worker
```

Abre `http://127.0.0.1:8000/`, inicia sesión y selecciona tu workspace.
Django Admin está en `/admin/`; `/health/` comprueba acceso a la base de datos.
Solo debe existir **un worker por base SQLite**. Para procesar una iteración usa
`python manage.py run_worker --once`. Detén los procesos con `Ctrl+C`.

## Problemas habituales

| Síntoma | Comprobación |
|---|---|
| Error sobre `SECRET_KEY` | Clave generada en `.env`, sin placeholder y entorno correcto |
| Tablas inexistentes | Ejecutar `migrate` con los mismos settings y base |
| Workspace no visible | Usuario, workspace y membership activos |
| Job pendiente | Worker arrancado; consultar disponibilidad y estado en Trabajos |
| Worker bloqueado | Otro worker puede estar usando esa base; no iniciar un segundo |
| Puerto ocupado | Usar otro puerto local, por ejemplo `127.0.0.1:8001` |

Antes de actualizar, detén procesos, revisa el [changelog](../CHANGELOG.md),
crea un backup según [Operaciones](OPERATIONS.md), instala el lock y aplica
migraciones. No borres SQLite para resolver una migración con datos que quieras conservar.

El servidor Django de desarrollo no sirve para exposición pública. El despliegue
requiere settings de producción, HTTPS, gestión de secretos y revisión específica.
