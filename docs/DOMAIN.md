# Dominio actual

Los IDs principales son UUID. Empresa y lead son conceptos distintos: una
Company puede tener un Lead por campaña, sin duplicarse por participar en varias.
Los datos tenant llevan workspace y sus relaciones deben pertenecer al mismo.

## Entidades e invariantes

| Grupo | Entidades | Invariante principal |
|---|---|---|
| Agencia | User, Workspace, Membership, AgencyProfile, ServiceOffering | Membership única por workspace/user; servicio por workspace/code |
| Empresas | Company, Contact, SocialProfile, CompanyExternalRef | Referencia única por workspace/provider/external_id; nombre no es identidad única |
| Captación | Campaign, CampaignService, CampaignChannel | Un canal por campaña; servicios del mismo workspace |
| Búsqueda | SearchPlan, SearchPlanItem, SearchRun, SearchResult, PlacesCache, DuplicateCandidate | Peticiones idempotentes y resultados con procedencia/errores por fila |
| CRM | Lead, LeadStatusHistory, Opportunity | Un Lead campaign/company; transiciones permitidas e historial append-only |
| Score | ScoringRule, ScoringRun, LeadScoreItem | Reglas declarativas, score 0–100 y snapshot/breakdown reproducible |
| Venta | Proposal, SalesAsset, ProposalAttachment | Versión por lead; PDF/hash y revisión antes de aprobar |
| Contacto | OutreachMessage, MessageAttachment, MessageEvent, SalesTask | Clave por workspace, aprobación congelada y eventos idempotentes |
| Política | SuppressionEntry, ContactEligibilityDecision, OutreachSettings | Supresión/cooldown/límites y doble activación live |
| Integraciones | EmailConnection, WhatsAppConnection, WhatsAppTemplate, InboundWhatsAppMessage | Autorización tenant sin secretos en DB; callbacks aislados/idempotentes |
| Operación | Job, JobAttempt, AuditEvent, FollowUpRule | Intentos con ownership, auditoría y recordatorios internos acotados |

Las constraints exactas y sus migraciones viven junto a cada app; los servicios
validan además relaciones y reglas que no se representan como constraints simples.

## Normalización y dedupe

Dominio/email/nombre/teléfono/URL se normalizan con helpers sin red. URL solo
HTTP(S) sin credenciales; el crawler añade validación de red/SSRF. Un teléfono
ordinario no implica que sea WhatsApp.

La adquisición conserva provider ID, origen y observación. External refs tienen
unicidad por workspace. Dominio/teléfono pueden ayudar al enlace de alta confianza;
nombre solo no provoca merge automático. Coincidencias ambiguas quedan como
DuplicateCandidate. El payload de proveedor no es la fuente transversal del dominio.

## Estados comerciales

El pipeline usa la lista de leads, filtros y transiciones; el dashboard muestra
estados actuales, no conversiones históricas acumuladas. No hay tablero Kanban
con arrastrar/soltar ni endpoints que permitan asignar un estado arbitrario.

| Estado | Siguientes estados admitidos por LeadStateService |
|---|---|
| NEW | ENRICHING, QUALIFIED, DISQUALIFIED, LOST |
| ENRICHING | QUALIFIED, DISQUALIFIED, LOST |
| QUALIFIED | READY, DISQUALIFIED, LOST |
| READY | CONTACTED, DISQUALIFIED, LOST |
| CONTACTED | REPLIED, MEETING, LOST |
| REPLIED | MEETING, PROPOSAL, LOST |
| MEETING | PROPOSAL, WON, LOST |
| PROPOSAL | WON, LOST |
| WON / LOST / DISQUALIFIED | Ninguna |

Cada transición aumenta revisión y añade historial/auditoría. Pérdida/descarte
requieren motivo. Registros archivados bloquean operaciones nuevas conservando
evidencia. Un contacto registrado puede proyectar READY a CONTACTED; no significa
que un mensaje simulado pruebe entrega real.

El score no cambia por sí solo el estado comercial. ScoringRun guarda reglas e
inputs; sus items explican cada coincidencia. El nuevo cálculo sustituye el score
activo y retira oportunidades generadas anteriores sin borrar runs históricos.
Las oportunidades manuales se conservan; los detectores requieren evidencia y
distinguen datos insuficientes de una ausencia realmente observada.

## Propuestas y mensajes

La propuesta sigue GENERATED → REVIEWED → APPROVED → SENT. Nuevas versiones
sustituyen las anteriores no enviadas; una versión enviada conserva su historia.
La generación por template funciona sin IA. Aprobar exige versión vigente,
revisión y documento válido; no envía nada.

Preparar un OutreachMessage deja PENDING_APPROVAL, conserva texto/recipient y
referencias de adjuntos. Aprobar vincula la revisión, actor y digest al contenido.
Encolar evalúa policy: crea/devuelve SEND_OUTREACH o deja SUPPRESSED sin Job.
El worker vuelve a comprobar todo antes del transporte. Una clave repetida no
cambia identidad ni reenvía un mensaje que ya tiene receipt/Job asociado.

SENT, DELIVERED, READ y REPLIED conservan IDs de proveedor y eventos. UNKNOWN
representa aceptación incierta y bloquea el replay. La reconciliación no fabrica
no-entrega a partir de un timeout; los flujos SMTP/Cloud mantienen prueba de
aceptación aunque cambios posteriores de permisos impidan actualizar el CRM.

Eligibility comprueba workspace/campaña/empresa/contacto, supresión, destinatario,
aprobación actual, canal, cooldown, intentos, límites, modo y pausa. Las respuestas,
reuniones y cierres también bloquean nuevos contactos a la misma empresa en el
workspace. Tener datos públicos no equivale a aprobación de política.

## Recordatorios, auditoría y retención

FollowUpRule programa SalesTasks de revisión interna después de contacto
confirmado; no prepara/aprueba/envía mensajes automáticamente. Respuesta, reunión,
cierre, supresión o pausa cancela el seguimiento. Las fechas derivadas conservan
fechas manuales y respetan cooldown.

LeadStatusHistory, MessageEvent, ScoringRun y AuditEvent conservan historia
append-only. La retención tiene una excepción explícita para vaciar texto entrante
antiguo, manteniendo IDs/contexto que impiden recontacto, y elimina cachés expiradas.
No elimina auditoría, supresiones ni evidencia de envíos.

Decisiones de seguridad y permisos: [SECURITY.md](SECURITY.md). Operación, lotes,
backup/restore y recuperación: [OPERATIONS.md](OPERATIONS.md).
