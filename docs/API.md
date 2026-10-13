# API interna V1

Prefijo `/api/v1/`. Todas las peticiones requieren una sesión Django activa y
un único `workspace_id=<uuid>` en la query string. No se combinan workspaces ni
hay bypass para staff/superusuarios. Se comprueban usuario, workspace y membresía
activos en cada petición y también dentro de los servicios de mutación.

La API acepta/responde JSON; no hay tokens, Basic Authentication, acceso anónimo
ni renderer de API navegable. El login y logout siguen siendo los de la aplicación.
Los POST requieren la cookie de sesión, `Content-Type: application/json` y
`X-CSRFToken` con el token CSRF vigente, obtenido después del login. Los controles
de origen y CSRF permanecen activos. No se añaden rutas de login alternativas.

## Endpoints

En todos los ejemplos hay que añadir `?workspace_id=<uuid>`.

| Método | Ruta | Entrada / resultado |
|---|---|---|
| GET | `/companies/` | Lista de empresas paginada |
| GET | `/companies/{id}/` | Empresa del workspace indicado |
| GET | `/leads/` | Lista de leads paginada |
| GET | `/leads/{id}/` | Lead del workspace indicado |
| GET | `/campaigns/` | Campañas y UUIDs de servicios, paginados |
| POST | `/campaigns/` | Crear campaña; `name`, `idempotency_key` UUID y campos opcionales |
| GET | `/jobs/` | Jobs paginados, sin payload/result ni secretos |
| GET | `/jobs/{id}/` | Estado y progreso del Job |
| POST | `/proposals/{id}/approve/` | `expected_revision` entero; exige revisión previa |
| POST | `/messages/{id}/approve/` | `expected_revision`, `policy_review_confirmed: true` |
| POST | `/messages/{id}/enqueue/` | `{}`; comprobar elegibilidad y encolar, sin ejecutar envío |

No hay PUT/PATCH/DELETE ni creación de empresas/leads/jobs desde estos endpoints.
Las demás acciones siguen disponibles en la UI. Un UUID ajeno devuelve el mismo
404 que uno inexistente, incluso con membresía en varios workspaces.

## Lecturas y filtros

Los listados devuelven `{ "count": 0, "next": null, "previous": null, "results": [] }`.
Cada página tiene como máximo 25 filas; `page` selecciona la página y los enlaces
conservan workspace/filtros. No se permite cambiar `page_size` ni ordenar mediante
campos arbitrarios. Los listados mantienen el orden estable del modelo.

| Lista | Filtros permitidos, además de `workspace_id` y `page` |
|---|---|
| Empresas | `q`, `city`, `sector`, `active` (`yes`/`no`) |
| Leads | `q`, `city`, `sector`, `status`, `company`, `campaign`, `assigned_to`, `min_score`, `max_score`, `has_email`, `has_phone`, `has_opportunity`, `archived` |
| Campañas | `q`, `status` |
| Jobs | `status`, `kind` |

Los UUIDs de filtros se resuelven dentro del workspace. `has_email`, `has_phone` y
`has_opportunity` aceptan `yes`/`no`; `archived` acepta `yes`/`all` y por defecto
lista leads no archivados. Texto de búsqueda/ciudad/sector: máximo 200 caracteres.
Score: 0–100; mínimo no puede superar máximo. Estados/tipos usan los enums del
dominio. Filtros inválidos, desconocidos o repetidos fallan con 400; no devuelven
un listado sin filtrar como alternativa. Las fichas/acciones solo aceptan
`workspace_id` en la query.

Las lecturas no escriben auditoría, crean jobs ni llaman providers. Las respuestas
autenticadas usan `Cache-Control: private, no-store`, Vary Cookie y CSP.
Los serializers usan campos explícitos: no exponen payloads, snapshots,
credenciales, hashes de aprobación ni rutas de archivos privados. Los PDFs se
consultan desde sus rutas protegidas existentes en la UI.

## Crear campaña

Campos opcionales: `description`, `target_query`, `target_sector`,
`target_location_text`, `target_country_code`, `desired_leads`, `minimum_score`,
`places_request_limit`, `require_manual_approval` y `services` (lista de UUIDs,
máximo 100). Se usan los defaults del modelo, incluida aprobación manual.
Los servicios deben pertenecer al workspace. El estado inicial es DRAFT y
`created_by` es el usuario de sesión; no pueden enviarse esos campos ni `id`,
`workspace` o cualquier otro campo no declarado.

Ejemplo de cuerpo:

```json
{
  "name": "Dentistas Galicia API",
  "idempotency_key": "2f737316-5e6f-4b0d-a5db-7e399d622a97",
  "target_sector": "Dentistas",
  "target_location_text": "A Coruña",
  "services": []
}
```

Generar un UUID distinto para cada intención nueva y conservarlo al reintentar.
La combinación workspace/clave produce un UUID estable para la campaña; su PK
impide duplicados y un evento auditado conserva el digest de la solicitud inicial.
El servicio hace un INSERT explícito: una colisión nunca sobrescribe otro registro.
Los duplicados/orden de `services` no cambian la intención. Una repetición devuelve
201 y la campaña actual, conservando cambios posteriores del operador. La misma
clave con otros datos devuelve 400. No se inicia prospección ni se crean jobs.

## Aprobaciones y encolado

Todos los roles activos leen. OWNER/ADMIN/SALES/MEMBER crean campañas y encolan
mensajes ya aprobados; VIEWER no muta. Solo OWNER/ADMIN aprueban propuestas y
mensajes. Los servicios existentes vuelven a comprobar estos permisos.

Una propuesta debe estar REVIEWED antes de pedir APPROVED; la revisión se realiza
en la UI. El cliente envía su revisión actual; un estado/revisión inválido devuelve
400. No se salta a SENT ni se aprueban versiones sustituidas.

Para aprobar un mensaje:

```json
{"expected_revision": 0, "policy_review_confirmed": true}
```

Se exige propuesta aprobada, revisión de política y adjuntos privados válidos.
La aprobación congela el contenido mediante el digest existente. Repetir una
aprobación ya alcanzada conserva la identidad y no duplica eventos; no reemplaza
la aprobación por la de otro usuario.

Encolar devuelve:

```json
{"message": {"id": "...", "status": "QUEUED"}, "job": {"id": "...", "status": "PENDING"}}
```

Los objetos reales incluyen los demás campos explícitos de sus serializers.
202 indica un Job nuevo o previamente asociado; repetir devuelve el mismo Job,
incluso si este ya terminó. El worker se ejecuta aparte y vuelve a comprobar
elegibilidad, aprobación, modo, supresión, cooldown y límites. Esta view no llama
ningún provider ni inicia hilos. Si la política bloquea el primer encolado, devuelve
200 con el mensaje SUPPRESSED, motivos en `error_code` y `job: null`. Un mensaje
sin aprobación devuelve 400 sin Job.

## Errores

- 400: entrada, estado, revisión, filtro o reutilización de clave inválidos.
- 403: falta sesión/permisos, o falla CSRF/origen.
- 404: workspace/objeto no accesible o página fuera de rango.
- 405: método no soportado.
- 415: cuerpo con Content-Type distinto de JSON.

Las validaciones de dominio se convierten en errores JSON de campo o `detail`;
no se ocultan como éxitos. No hay nuevas dependencias ni migraciones de esquema.
