# Checklist de hardening y permisos

| Operación | OWNER / ADMIN | SALES / MEMBER | VIEWER |
| --- | --- | --- | --- |
| Consultar datos del workspace activo | Sí | Sí | Sí |
| Crear/editar CRM, preparar y encolar trabajo permitido | Sí | Sí | No |
| Archivar/eliminar y recuperar jobs | Sí | No | No |
| Aprobar mensajes/propuestas, configurar live y límites | Sí | No | No |
| Consultar auditoría, exportar CSV, encolar retención | Sí | No | No |

Los services verifican usuario, workspace y membership activos en cada operación.
El worker revalida permisos. La UI no es la barrera de seguridad. Los UUID ajenos
se resuelven dentro del workspace y producen 404; un rol sin permiso produce 403.
El superusuario requiere membership para la UI de workspace. El admin de plataforma
es explícitamente global y solo para superusuarios; los modelos de dominio son readonly.

## Controles verificados por tests

- [x] Sesiones, CSRF y logout POST; sin API anónima ni bypass de workspace.
- [x] Permisos por rol activo en servicios, exportación, auditoría y retención.
- [x] CSP nativa con self/nonces; sin unsafe-inline/eval; PDFs con sandbox.
- [x] Autoescape de datos externos; markup tratado como texto y CSV como datos.
- [x] Cache-Control privado/no-store en páginas autenticadas y documentos.
- [x] Upload acotado a un archivo, bytes, extensión/MIME, PDF estructural y activo.
- [x] UUIDs de archivo, paths privados, rechazo de symlinks y junctions, hashes.
- [x] Auditoría append-only, filtros por workspace y redacción de claves sensibles.
- [x] Providers mock y sockets/DNS bloqueados en tests; aprobación humana y policy.
- [x] Backup/restore verifican referencias, hashes, ZIP traversal y destino nuevo.
- [x] Retención acotada, confirmada, auditada y con rollback de cambios de BD.
- [x] Listados y relaciones paginados; select_related e índices de filtros frecuentes.

La redacción de metadata protege claves de credenciales, no detecta cualquier secreto
en texto libre. Los services solo deben pasar identificadores, contadores y códigos
curados. No pasar bodies, URLs de OAuth ni payloads del proveedor a auditoría/logs.

## Revisión de despliegue pendiente del operador

TLS/proxy, dominio definitivo, preload HSTS, ACL/cifrado de backups, almacenamiento,
rotación de secretos y conservación de logs deben verificarse en el entorno real.
`check --deploy` no demuestra que un proxy externo esté correctamente configurado.
No se realizó despliegue ni se enviaron comunicaciones reales en esta fase.
El ensayo real de WhatsApp de FASE 10 permanece pendiente, según WHATSAPP.md.
Playwright es opcional y no se añadió una dependencia para esta entrega.
