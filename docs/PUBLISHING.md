# Preparar un remoto Git

Esta guía prepara la publicación del código. Subir el repositorio no despliega
la aplicación ni activa providers. El alojamiento y su visibilidad los decide
el propietario; la licencia del código propio es [MIT](../LICENSE).

## Revisar el contenido

Desde la raíz:

```bash
git status --short
git diff --check
git diff --stat
git ls-files
```

Comprueba que solo se versionan código, migraciones, recursos estáticos,
fixtures ficticias y documentación. `.gitignore` excluye configuración privada,
datos y artefactos locales, pero no elimina archivos ya rastreados ni historial.
Revisa también commits anteriores si el repositorio va a hacerse público.

```bash
git check-ignore .env db.sqlite3 media/example.pdf backups/example.zip
git log --oneline
```

Revisa secretos sin copiar sus valores a informes. Ante una credencial expuesta,
revócala y resuelve el historial con el propietario antes de publicar.
Confirma los [checks](DEVELOPMENT.md), el lock, las licencias y los enlaces.
Haz staging explícito de los archivos propios del cambio y revisa:

```bash
git diff --cached --check
git diff --cached --stat
git diff --cached
```

## Configurar el alojamiento

Crea un repositorio vacío en el alojamiento elegido y copia su URL. Los siguientes
comandos son una plantilla; sustituye los marcadores antes de ejecutarlos.

```bash
git remote add origin <URL-del-repositorio>
git push -u origin <rama-actual>
```

Si `origin` ya existe, revisa su destino antes de cambiarlo. No incluyas tokens
en la URL ni los guardes en archivos. No es necesario renombrar la rama actual
para esta preparación; elige la rama por defecto en el alojamiento.

## Después de publicar

- Añade descripción y temas coherentes con Python, Django y CRM B2B.
- Activa revisión de cambios y protección de la rama principal cuando estén disponibles.
- Configura un canal privado de seguridad y actualiza [SECURITY](../SECURITY.md).
- Integra los checks de [Desarrollo](DEVELOPMENT.md) en el CI del alojamiento.
- Guarda credenciales solo en el gestor de secretos del entorno correspondiente.

No se incluye un workflow específico de un proveedor de Git: aún no se ha elegido
alojamiento. No añadas badges de CI hasta tener ejecuciones reales verificables.
