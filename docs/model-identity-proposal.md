# Propuesta: separar identidad de modelo, de instancia y de nombre público

> Estado: **aprobada** (2026-09-11) — las 9 decisiones están resueltas en la sección 9.
> Implementación en RM-68 … RM-73. Punto de retorno seguro:
> [v1.4.0](https://github.com/Root1V/prometheus-inference-platform/releases/tag/v1.4.0).

---

## 1. El problema en una frase

Hoy un solo string cumple cuatro papeles distintos, y por eso los cuatro huecos que
detectaste son el **mismo** hueco visto desde cuatro lados.

| Papel | Dónde vive hoy |
|---|---|
| Identidad del modelo (catálogo) | `models.id` (manager) |
| Identidad del proceso que sirve | `instances.id` (manager) — global, por eso necesita sufijo `-1`/`-2` |
| Nombre que manda el cliente | `body.model` (gateway) |
| Clave de precio, scope y factura | `pricing.yaml`, `model:<id>`, `usage_events.model_id` |

[[RM-57]] separó el **ruteo** de la identidad de instancia, pero reusó `models.id` como
nombre de grupo. Y como manager-api hace `model_id = id` al registrar un modelo directo,
el nombre del grupo **casi siempre es también el id de la primera instancia**. La colisión
sobrevivió: por eso en el picker te aparecen `-1` y `-2` en vez de un solo modelo.

Los cuatro síntomas:

1. **Crear una réplica obliga a re-tipear todo** — porque el formulario crea una *instancia
   nueva desde cero*, no una réplica de algo que ya existe.
2. **El picker de acceso muestra las dos instancias** — porque no hay un objeto "modelo"
   presentable, solo instancias.
3. **El consumo no es transparente** — porque el cliente manda un string que a veces es un
   grupo y a veces es un proceso.
4. **El billing hereda la ambigüedad** — `usage_events.model_id` guarda "el nombre que
   mandaron", sin saber si era grupo o instancia, y sin registrar qué réplica sirvió.

---

## 2. Qué hace la industria

Investigué cinco plataformas, deliberadamente sin mirar cómo lo tenemos nosotros.

**LiteLLM** — un `model_name` (el *model group*) agrupa N *deployments*. Cada deployment
tiene su propio id: un hash determinístico de sus parámetros, o uno explícito en
`model_info.id`. Los spend logs guardan **el nombre del grupo y además el deployment que
realmente sirvió**. La respuesta trae un header `x-litellm-model-id` diciendo quién
atendió. Estrategias: `simple-shuffle`, `least-busy`, `latency-based`, `usage-based`
(TPM/RPM) y `cost-based`, todas filtrando primero por salud y cooldown (429 → cooldown
inmediato, >50% de fallos en el minuto → cooldown).

**Kubernetes Gateway API Inference Extension** — un `InferencePool` es un conjunto de pods
con configuración de cómputo idéntica. Un *Endpoint Picker* elige el endpoint mirando
**utilización de KV-cache, largo de la cola de pendientes y adaptadores LoRA activos**. La
identidad del pool y la del pod están completamente separadas.

**AWS Bedrock** — un *inference profile* es un identificador ruteable que reparte entre
regiones. Y los *application inference profiles* existen **exclusivamente como capa de
atribución de costo**: mismo modelo, mismo ruteo, distinto id para poder facturar por
aplicación.

**OpenAI** — `gpt-4o` es un alias estable que apunta a un snapshot fechado
(`gpt-4o-2024-08-06`). El alias es lo que manda el cliente; el snapshot es la identidad
real e inmutable.

**vLLM** — `--served-model-name` desacopla explícitamente el nombre servido de la ruta de
los pesos. Su router hace round-robin, afinidad de sesión y ruteo prefix-aware.

**La regla que comparten las cinco:** *el nombre que manda el cliente nunca es la identidad
del proceso que lo atiende.* Y para facturación, la práctica es guardar **un id inmutable
para el registro contable y un slug legible en paralelo** para logs y soporte.

---

## 3. Modelo de identidad propuesto

Tres objetos, cada uno con un id opaco y un nombre humano separados.

### Modelo (entrada de catálogo)

| Campo | Ejemplo | Rol |
|---|---|---|
| `id` | `mdl_01J8XQ...` | Opaco, inmutable, **interno**. FK e integridad contable. |
| `slug` | `qwen3-0.6b` | **Lo que manda el cliente.** Único, inmutable mientras el modelo exista. |
| `name` | `Qwen3 0.6B Instruct` | Etiqueta para mostrar. Mutable, sin consecuencias. |

### Instancia (réplica / deployment)

| Campo | Ejemplo | Rol |
|---|---|---|
| `id` | `ins_01J8XR...` | Opaco. **Tu idea, y es la correcta.** |
| `label` | `qwen3-0.6b#2` | Handle humano para ops y CLI. Autoincremental **dentro del modelo**. |
| `model_id` | `mdl_01J8XQ...` | FK al modelo. |
| | `node`, `backend`, `port`, `context_length` | Config del proceso. |

### Sobre tu propuesta del UUID

Tenías razón en el diagnóstico, con un matiz que vale la pena: **UUID sí, pero no UUID
solo**. Un id opaco puro destruye la operabilidad — `pmgr logs 3f2b8c...` es inusable, y
la tabla de instancias del dashboard se vuelve ilegible. Por eso las cinco plataformas
ponen **id opaco + nombre humano en el mismo objeto**, no uno u otro. El `label`
autoincremental es además lo que resuelve tu hueco #1: crear la réplica #2 no pide nombre
porque el sistema ya sabe cuál sigue.

El segundo matiz: **el cliente debería seguir mandando el slug legible, no el id opaco**.
Ninguna de las cinco plataformas hace que el cliente mande un UUID — OpenAI manda
`gpt-4o`, LiteLLM manda el `model_name`, Bedrock manda un ARN legible. El id opaco existe
para integridad interna y estabilidad contable, no para el contrato público.

### Por qué el slug inmutable

La alternativa (slug renombrable) obliga a una tabla `slug_history` **y** a reescribir los
grants `model:<slug>` en auth-service, que es otro servicio y otra base de datos. Una
escritura distribuida por cada renombre, para ganar poco. Hacer el slug inmutable elimina
las dos cosas: renombrar = crear un modelo nuevo. Es lo que hace OpenAI, y `name` queda
libre para cambiar cuando quieras porque no significa nada.

### Por qué igual hace falta el id opaco del modelo

Si el slug fuera la única identidad, borrar `qwen3-0.6b` y recrearlo después haría que el
modelo nuevo **heredara en silencio** las filas de uso viejas, el precio viejo y los grants
viejos. Con un id opaco, el modelo recreado es otro objeto y el historial no se mezcla. Es
el mismo argumento que la industria da para facturación: el registro contable se ancla a
algo que no se puede re-apuntar.

---

## 4. Cómo se resuelve cada hueco

**#1 — Crear una réplica.** El modelo pasa a ser una fila con acción propia: *Add
instance*. Pide solo lo que de verdad cambia entre réplicas — **nodo, motor y puerto** — y
hereda pesos, familia, cuantización y modalidad del catálogo. El `label` se autoincrementa.
El formulario completo queda reservado para registrar un **modelo nuevo**, que es
exactamente la distinción que planteaste.

**#2 — Picker de acceso.** Lista **modelos** (slugs), nunca instancias. La pastilla muestra
las instancias corriendo. Y desaparece el efecto raro que te señalé en RM-57: hoy conceder
`model:qwen3-0-6b-iq4-nl-local-2` pasó de significar "una instancia" a "las dos" sin que
nadie hiciera nada.

**#3 — Consumo transparente.** El cliente manda el slug. El gateway balancea. Para
debugging, la respuesta trae `X-Prometheus-Instance: qwen3-0.6b#2` (precedente:
`x-litellm-model-id`) — así se sabe quién sirvió sin que el cliente tenga que elegir.

**#4 — Billing.** `usage_events` guarda **las tres cosas**: `model_id` (opaco, sobrevive
renombres), `model_slug` (legible, lo que se ve en la factura) e `instance_id` (qué réplica
sirvió). El precio se ancla al modelo, no a la réplica — dos réplicas del mismo modelo
cuestan lo mismo aunque corran en nodos distintos. Esto formaliza lo que ya decidiste en
RM-57 (facturar al nombre lógico) y además agrega la trazabilidad que hoy falta.

---

## 5. Balanceo inteligente: qué señales tenemos *de verdad*

Probé en vivo contra el llama-server que está corriendo, en lugar de asumir.

**`GET /metrics` expone** (verificado):
- `llamacpp:requests_processing` — requests en vuelo
- `llamacpp:requests_deferred` — **cola de pendientes**
- `llamacpp:n_busy_slots_per_decode`, throughput de prompt y de generación

**`GET /slots` expone** (verificado — 4 slots por instancia):
- `is_processing` por slot → **capacidad libre real**
- `n_ctx`, `n_prompt_tokens` → **saturación de ventana de contexto**, que es exactamente la
  señal que mencionaste
- `n_prompt_tokens_cache` → afinidad de prefix-cache

Es decir: tenemos disponibles las dos señales que usa el Endpoint Picker de Kubernetes
(cola + saturación de caché). **Pero sd.cpp no expone `/metrics` en absoluto** — verificado,
responde vacío. Cualquier estrategia tiene que degradar limpio en motores heterogéneos.

Escalera propuesta:

| Fase | Estrategia | Requiere del backend |
|---|---|---|
| v1 | **Menos requests en vuelo** (contador del propio gateway) + saltar circuitos abiertos | Nada — funciona también para sd.cpp |
| v2 | Slots libres y cola, desde `/slots` y `/metrics` | llama.cpp; fallback a v1 |
| v3 | Afinidad de sesión para multi-turno (prefix cache) | llama.cpp |

v1 sirve desde el día uno y no depende del motor. v3 **entra en conflicto** con "menos
cargado" — la afinidad manda a propósito al mismo backend. Por eso LiteLLM hace la
estrategia configurable por grupo, y propongo lo mismo en vez de elegir una global.

---

## 6. Impacto por componente

| Componente | Cambio | ¿Rompe? |
|---|---|---|
| `manager-core/registry.py` | `models` +`slug`,`name`; `instances` id opaco +`label`; migración de filas | Interno |
| `manager-api/control.py` | Separar "agregar instancia" de "registrar modelo" | API interna |
| `pmgr` CLI / TUI | 6 comandos toman `model_id` posicional — ahora ambiguo (¿slug o label?) | **Sí, UX de CLI** |
| `gateway/models/registry.py` | `ModelEntry`/`ModelResolution` con los campos nuevos | Interno |
| `gateway/models/manager_sync.py` | Sincronizar campos nuevos | Interno |
| `gateway/router.py` | 3 sitios de resolución; el chequeo de scope pasa de `body.model` al modelo resuelto; `_record_usage` recibe instancia; header nuevo | Interno |
| `auth/claims.py` + `auth-service/schemas.py` | `_MODEL_SCOPE_RE` y semántica de `model:<slug>`; grants existentes que apuntan a ids de instancia hay que migrarlos | **Sí, tokens vivos** |
| `pricing.py`, `pricing.yaml`, `model_price_config` | Re-clavar al modelo | Migración de datos |
| `db.py` | `usage_daily`/`usage_events` columnas nuevas; cambia el unique constraint | Migración de datos |
| `telemetry.py` | Métricas van por `backend_id` = id de instancia, que ahora es opaco; la UI debe unir con `label` | Interno |
| `admin-ui` | 13 archivos tocan `model_id` | Interno |
| `docs/sdk-integration-guide.md` | Cambia el contrato público | **Sí — Axonium** |

**Lo que rompe hacia afuera**, y hay que decidirlo conscientemente:

- El **ruteo directo a instancia** (`model: "qwen3-0-6b-iq4-nl-local-1"`) desaparece del
  campo `model`. Hoy funciona y RM-57 lo mantuvo a propósito.
- El **cliente de prueba de Axonium** tiene hoy grants como
  `model:qwen3-0-6b-iq4-nl-local-2`, o sea el id de instancia. Esos grants hay que
  reemitirlos.
- No hay Alembic en el repo: `create_tables()` es un `create_all` pelado que **nunca altera
  tablas existentes**. RM-60 ya necesitó un `ALTER TABLE` a mano. Este cambio toca varias
  tablas con datos reales, y hacerlo a mano otra vez es acumular deuda; **conviene adoptar
  Alembic antes**, no durante.

---

## 7. Auditoría del resto del sistema

Los cuatro huecos originales eran los visibles desde la UI. Barriendo el resto del stack
aparecieron ocho más. Todos verificados en código, no supuestos.

### A. El circuit breaker no falla a otra réplica — y esto ya está en producción

`router.py` chequea el breaker de **`resolution.members[0]` únicamente**, después de haber
elegido esa réplica. Si ese circuito está abierto, la respuesta es 503 **aunque la otra
réplica esté perfectamente sana**.

Dicho de frente: **hoy agregar una réplica no agrega tolerancia a fallos.** Tampoco agrega
throughput, porque el pick es determinista. Ahora mismo una réplica extra solo consume RAM.
No es que RM-57 haya roto algo — nunca prometió balanceo, lo dejó explícitamente para
RM-58 — pero sí significa que la palabra "réplica" en el dashboard promete más de lo que
entrega, y eso vale corregirlo pronto.

### B. Los reintentos martillan la instancia muerta

`BackendPool.forward()` reintenta `retry_max + 1` veces contra **la misma URL**. Con
réplicas disponibles, un reintento debería ir a otra. Hoy los tres intentos van al mismo
backend caído; lo único que logran es abrir su breaker más rápido.

### C. Ventana de hasta 30 segundos de caída total

`ManagerRegistrySync` hace poll cada **30 s**, y el gateway no tiene health check activo
propio contra los backends. Una réplica que muere sigue listada como miembro activa hasta
medio minuto. Combinado con A y B: si la que muere es `members[0]`, **el modelo queda caído
esos 30 segundos aunque haya una réplica sana esperando**.

### D. Rutear por id de instancia evade el precio *y* el tope de gasto

Esta es la más delicada. `PricingTable` es un dict plano por string exacto. Si el precio
está configurado para el nombre del catálogo y alguien manda el id de la instancia:

1. `estimate_cost_usd()` devuelve `None` → la fila de uso se guarda con costo NULL.
2. En el reserve del presupuesto, `if est_cost is not None` **no se cumple** → no se
   reserva nada → **el tope mensual de gasto no se aplica a esa request**.

Esto **no** es la política que ya aceptaste ("un modelo sin precio es invisible al tope").
Es el mismo modelo accesible por dos nombres, uno tarifado y otro gratis. Cualquier cliente
con un grant a un id de instancia — **Axonium tiene exactamente eso hoy** — cae en el
camino no tarifado. El diseño de identidad de la sección 3 lo cierra por construcción,
porque el precio se ancla al modelo y la instancia deja de ser ruteable.

### E. Los rate limits no escalan con las réplicas

Las claves son `identity` + `endpoint`; nunca modelo ni instancia. Duplicar réplicas
duplica la capacidad real de la plataforma, pero **ningún cliente puede usarla**: su techo
RPM/TPM es el mismo. Y los límites de RM-56 son globales y estáticos, así que escalar
horizontalmente no mueve ninguna de las dos cosas.

Que la cuota sea por cliente me parece **correcto** y no lo cambiaría. Lo que falta es que
el límite *global* de plataforma se derive de la capacidad instalada en vez de ser un
número fijo escrito a mano.

### F. No hay agregado de métricas por modelo

`MetricsStore` indexa por `backend_id`, o sea por instancia. No existe rollup por modelo.
Con réplicas, el dashboard muestra N filas sueltas y no hay forma de ver el throughput o la
latencia **del modelo**, que es la única unidad que le importa a quien lo consume.

Relacionado: el Overview rotula *"Circuits open — of N models"* contando backends. Con dos
réplicas de un modelo dice "2 models". El rótulo ya era impreciso; con réplicas pasa a ser
directamente falso.

### G. Las métricas viven en memoria del proceso

Ya está advertido en la UI, pero con réplicas importa más: reiniciar el gateway borra
justamente la base de datos sobre la cual RM-58 tendría que decidir a quién rutear. Un
balanceo latency-aware arranca ciego después de cada reinicio.

### H. Estado del breaker en Redis por id de instancia

Si los ids pasan a opacos, las claves viejas quedan huérfanas. Cosmético — el TTL las
limpia — pero conviene contemplarlo en la migración.

### Lo importante de esta sección

**A, B y C se arreglan sin esperar el refactor grande.** Chequear el breaker de cada
miembro y devolver 503 solo cuando *todas* están abiertas, y hacer que el reintento haga
failover a otra réplica, son cambios acotados a `router.py` y `backends.py` que no dependen
del modelo de identidad. **Recomiendo hacerlos primero**, porque convierten las réplicas en
algo que sirve de verdad mientras se discute lo demás.

**D es la que más me preocupa** y también se puede mitigar antes: basta con resolver el
precio contra el modelo del grupo en vez de contra el string que mandó el cliente.

---

## 8. Migración por fases

La regla: **nadie se rompe hasta la fase 4**, y la 4 es opcional.

**Fase 0 — Columnas y backfill, sin cambio de comportamiento.** Se agregan `slug`, `name`,
ids opacos y `label`. Backfill: `slug` = id actual, `label` derivado del id actual. Todo
sigue resolviendo igual.

**Fase 1 — El gateway resuelve por slug, con alias.** Los ids viejos (de modelo y de
instancia) quedan como **alias** en una tabla. Cada token existente y cada llamada del SDK
siguen funcionando sin tocar nada.

**Fase 2 — UI y scopes.** El picker pasa a modelos. Los grants nuevos usan slugs; los
viejos siguen resolviendo por alias.

**Fase 3 — Balanceo** (esto absorbe [[RM-58]], que deja de ser independiente).

**Fase 4 — Deprecación.** Recién acá se retira el ruteo por id de instancia, con ventana
anunciada y después de que Axonium migre.

Los ítems quedan numerados **en orden de ejecución**, para que el roadmap se lea como se
va a construir:

| Ítem | Qué | Depende de |
|---|---|---|
| **RM-68** | Migraciones con Alembic (prerequisito) | — |
| **RM-69** | Failover entre réplicas + health check + precio por grupo (hallazgos A-D) | — |
| **RM-70** | Identidad: slug, ids opacos, label; reemisión de grants | RM-68 |
| **RM-71** | UX de réplicas: "Add instance" + picker por modelo | RM-70 |
| **RM-72** | Balanceo inteligente (**absorbe [[RM-58]]**, que queda superseded) | RM-70 |
| **RM-73** | Billing con trazabilidad de réplica + rollup de métricas por modelo | RM-70 |

RM-69 no depende de nada y es lo que hace que las réplicas que ya tenés sirvan para algo,
así que va primero junto con RM-68.

---

## 9. Decisiones tomadas

| # | Pregunta | Decisión |
|---|---|---|
| 1 | ¿Slug legible o id opaco en el contrato público? | **Slug.** El id opaco queda interno. |
| 2 | ¿Apuntar a una instancia concreta? | **Sí, por header `X-Prometheus-Instance`** — nunca por el campo `model`. |
| 3 | ¿Slug mutable o inmutable? | **Inmutable.** Renombrar = modelo nuevo. Sin `slug_history`, sin reescribir grants entre servicios. |
| 4 | ¿Grants existentes: migrar o reemitir? | **Reemitir.** Ningún acceso se amplía sin que un humano lo apruebe. |
| 5 | ¿`modality` y `context_length`? | **`modality` sube al catálogo; `context_length` se queda en la instancia.** |
| 6 | ¿Alembic primero? | **Sí** — RM-68 abre la fila. |
| 7 | ¿Failover ya? | **Sí** — RM-69, en paralelo con RM-68. |
| 8 | ¿Límite global derivado de la capacidad? | **No auto-derivar; mostrar la capacidad observada** junto al límite manual. |
| 9 | ¿Health check o bajar el poll? | **Health check activo**, dentro de RM-69. |

### Fundamento de la #5

`modality` es una propiedad de los **pesos**: el mismo GGUF no puede ser texto en una
réplica y embedding en otra — si lo es, hay un error de configuración. Subirla al catálogo
vuelve imposible por construcción el desacuerdo que RM-57 tuvo que detectar a mano, y
elimina ese camino de código (`_resolve_group` deja de necesitar el campo `mismatch`).

`context_length` es distinto: es el flag `-c` de llama-server, acotado por la RAM del nodo.
Dos réplicas en nodos distintos **pueden diferir legítimamente**, así que se queda por
instancia y la resolución sigue usando `min()` del grupo — un request que entra en la
réplica más chica entra en todas, que es lo que hace válido validar antes de elegir. El
catálogo guarda además el techo que soportan los pesos, como referencia al crear instancias.

### Fundamento de la #8

Inferir RPM/TPM desde la capacidad instalada es adivinar: depende del tamaño del modelo, la
cuantización, los slots y el nodo. Una fórmula equivocada estrangula o sobre-admite en
silencio, y sería justamente la "configurabilidad especulativa" que conviene evitar.

Lo que sí es medible es la concurrencia real: `/slots` reporta 4 slots por instancia
(verificado en vivo). Entonces el límite sigue siendo **manual** — RM-56 ya lo hizo editable
sin reinicio — pero el dashboard muestra al lado *"capacidad actual: N slots en M
instancias"* y advierte cuando el límite configurado está muy por debajo o por encima. El
operador decide informado, sin maquinaria nueva.

---

## 10. Fuera de alcance

- **Versionado/snapshots** tipo OpenAI (`qwen3-0.6b` → `qwen3-0.6b-2026-09-10`). El diseño
  de slug lo permite después; construirlo ahora sería especular.
- **Autoscaling y multi-nodo real.** Esto ordena la identidad, no agrega orquestación.
- **SUNAT** ([[RM-61]]) sigue intacto y bloqueado en tu decisión de negocio.

---

## Fuentes

- [LiteLLM — Router: Load Balancing](https://docs.litellm.ai/docs/routing)
- [LiteLLM — Proxy: Load Balancing](https://docs.litellm.ai/docs/proxy/load_balancing)
- [Kubernetes Gateway API Inference Extension — InferencePool](https://gateway-api-inference-extension.sigs.k8s.io/api-types/inferencepool/)
- [Introducing Gateway API Inference Extension — Kubernetes Blog](https://kubernetes.io/blog/2025/06/05/introducing-gateway-api-inference-extension/)
- [Amazon Bedrock — Inference profiles](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles.html)
- [Amazon Bedrock — Application inference profiles (cost attribution)](https://docs.aws.amazon.com/bedrock/latest/userguide/cost-mgmt-application-inference-profiles.html)
- [OpenAI — GPT-4o model & snapshots](https://developers.openai.com/api/docs/models/gpt-4o)
- [vLLM Router — prefill/decode aware load balancer](https://vllm-project.github.io/2025/12/13/vllm-router-release.html)
- [Lago — REST API design guide for billing](https://getlago.com/blog/rest-api-design-guide-for-billing-best-practices-2026)
