# Contribuir a AgencyCRM

Gracias por contribuir. Un cambio debe explicar el problema, mantenerse acotado
y conservar aislamiento, trazabilidad e idempotencia.

## Preparar un cambio

1. Lee el [playbook](IMPLEMENTATION_PLAYBOOK_AGENCYCRM.md) completo antes de modificar
   código y revisa la [arquitectura](docs/ARCHITECTURE.md).
2. Instala el entorno según [Inicio](docs/GETTING_STARTED.md).
3. Crea una rama descriptiva; las ramas de trabajo de Codex usan `codex/`.
4. Identifica riesgos de permisos, workspace, estados, archivos y reintentos.
5. Implementa el alcance acordado y pruebas de comportamiento cuando correspondan.
6. Ejecuta los [checks de desarrollo](docs/DEVELOPMENT.md), actualiza documentación
   y registra el cambio en [CHANGELOG](CHANGELOG.md).

Cada fase se entrega con un commit claro y sus checks verdes. No avances a otra
fase sin el alcance acordado ni mezcles refactors ajenos con una corrección.
No edites migraciones aplicadas. Justifica dependencias nuevas.

## Preparar la revisión

Explica qué problema resuelve el cambio y qué comportamiento produce. Incluye
checks ejecutados, migraciones, límites conocidos y capturas si cambia una pantalla.
Para permisos o envío, demuestra también denegación, replay y aislamiento entre
workspaces. Nunca uses datos personales reales en ejemplos, fixtures o capturas.

Revisa `git diff --check`, `git diff --cached` y `git status` antes de confirmar.
No incluyas `.env`, SQLite, media, backups, tokens ni logs. No uses `git add .`
sin revisar todos los cambios, especialmente en un workspace compartido.

Para vulnerabilidades sigue [SECURITY](SECURITY.md). El código propio usa
[MIT](LICENSE); conserva la atribución de [terceros](THIRD_PARTY_NOTICES.md).
