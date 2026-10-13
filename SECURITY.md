# Reporte de vulnerabilidades

Reporta problemas de acceso entre workspaces, CSRF, archivos privados, SSRF,
secretos o envío sin autorización por un canal privado del mantenedor del
repositorio. Si el alojamiento ofrece reporte privado de vulnerabilidades, usa
esa función. Aún no se ha configurado una dirección de contacto pública.

No publiques credenciales, datos comerciales ni una prueba de explotación
contra una instancia real en una issue pública. Si no existe canal privado,
solicita uno sin incluir detalles sensibles.

Incluye versión o commit afectado, impacto, condiciones necesarias y pasos para
reproducir con datos ficticios. Evita enviar bases, `.env`, PDFs comerciales o
payloads que contengan secretos. No hay un SLA de respuesta definido.

El desarrollo se concentra en la línea actual del repositorio; no se promete
mantenimiento de versiones antiguas. La configuración entregada es local,
con simulación por defecto y sin settings de producción.

La [seguridad técnica](docs/SECURITY.md) documenta permisos y controles.
[Providers](docs/PROVIDERS.md) explica las barreras de activación externa;
[Operaciones](docs/OPERATIONS.md) cubre recuperación y copias.
