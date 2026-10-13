# Desarrollo y calidad

Completa primero [Inicio](GETTING_STARTED.md). Usa Python 3.14 y el entorno
virtual del proyecto. El [playbook](../IMPLEMENTATION_PLAYBOOK_AGENCYCRM.md)
define los criterios del dominio; la [arquitectura](ARCHITECTURE.md) documenta
la implementación actual.

## Convenciones

- Queries en selectors; mutaciones en services; reglas sin I/O en policies.
- Views y serializers validan transporte y presentan resultados.
- Services y selectors exigen acceso al workspace, sin bypass de superusuario.
- Providers devuelven resultados normalizados y no mutan el dominio.
- Operaciones largas se representan como Jobs; no se crean threads desde views.
- Transacciones, constraints y claves de idempotencia protegen operaciones críticas.
- Tests y fixtures usan empresas ficticias y red bloqueada; no secretos reales.
- No usar signals para operaciones de aplicación ni desactivar CSRF.

`.editorconfig` define UTF-8, espacios y newline final. `.gitattributes` normaliza
archivos de texto al versionarlos. Ruff es la autoridad del lint Python.
No reformatees archivos históricos ni migraciones aplicadas en cambios ajenos.

## Checks antes de un commit

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python -m pytest
python -m ruff check .
python -m coverage run -m pytest
python -m coverage report
git diff --check
```

Para medir cobertura basta ejecutar la variante con `coverage` una vez, en lugar
de duplicar toda la suite. La configuración está en [pyproject.toml](../pyproject.toml):
settings de test, rutas, lint y mínimo global del 80 %. pytest usa base aislada,
filesystem temporal y mocks; no trabaja sobre la base comercial local.

`makemigrations --check --dry-run` detecta cambios de modelos sin migración.
Si cambias modelos, genera y revisa la migración con `makemigrations`, aplica
`migrate` y vuelve a ejecutar los checks. No edites migraciones ya compartidas.

Para una prueba concreta:

```bash
python -m pytest tests/test_acceptance.py -q
```

Consulta [Aceptación](ACCEPTANCE.md) para el nombre y contrato del escenario E2E.
Los adapters reales se prueban con mocks; una suite verde no acredita credenciales
externas, entrega real de mensajes ni un despliegue de producción.

## Dependencias

[requirements.txt](../requirements.txt) expresa rangos justificados;
[requirements.lock](../requirements.lock) fija el entorno completo reproducible.
Instala el lock para trabajar sobre las mismas versiones.

| Dependencia directa | Motivo |
|---|---|
| Django / DRF | Aplicación, ORM, sesiones, admin y API interna |
| python-dotenv / tzdata | Configuración local y zonas horarias portables |
| phonenumbers | Normalización y validación telefónica |
| httpx / httpcore | Adapters HTTP y transporte controlado del crawler |
| beautifulsoup4 | Parsing acotado de HTML público |
| reportlab / pypdf | Generación y validación de PDFs |
| pytest / pytest-django / factory-boy | Pruebas, integración Django y datos ficticios |
| coverage / Ruff | Cobertura y lint |

Para actualizar, explica el motivo, revisa rangos y actualiza el lock desde un
entorno limpio de Python 3.14. Ejecuta `python -m pip check` y toda la suite.
Evita generar el lock desde un entorno con paquetes ajenos; revisa el diff y las
licencias. No añadas infraestructura o dependencias que la funcionalidad no requiera.

## Documentación

README explica producto y arranque. `docs/` contiene contratos actuales;
CHANGELOG registra entregas y evidencia. El archivo histórico no es una guía vigente.
Comprueba enlaces relativos desde la ubicación de cada documento y conserva
los avisos de terceros al modificar recursos vendorizados.
