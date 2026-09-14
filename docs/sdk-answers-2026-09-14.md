# Respuesta a la cuarta tanda — Axonium, 14 de septiembre de 2026

Dos cosas, y las dos tenéis razón. La primera ya está arreglada; la segunda la vamos a hacer y
os contamos qué forma tendrá y por qué no está ya.

---

## 0 · Sobre `family`

Gracias por cerrarlo con datos en lugar de darlo por bueno. Un apunte para el archivo, porque
importa para interpretar lo que visteis: **no fue que rellenáramos los datos que faltaban.** Las
familias estaban pobladas en nuestro catálogo todo el tiempo. Lo que veíais en blanco venía de
que el gateway estaba sirviendo un catálogo que no podía resincronizar, por un defecto nuestro en
otro sitio, y se arregló cuando arreglamos aquello.

Lo que sí hicimos tras vuestro cuarto aviso fue impedir que vuelva a pasar: el registro ya no
puede almacenar una familia vacía, y cuando nadie la suministra se lee del propio fichero del
modelo en lugar de dejarla en blanco.

Por el camino tiramos a la basura una heurística que deducía la familia del nombre del modelo:
la medimos contra nuestros 28 modelos y acertaba 14. `llava-mistral-7b-q5` tiene arquitectura
`llama`, cosa que del nombre no sale jamás.

---

## 1 · Los ejemplos de la spec: arreglado

Teníais razón y no es tan "no es un fallo" como lo planteáis con generosidad. Un ejemplo que
falla al copiar y pegar **es** un defecto: lo primero que hace alguien nuevo es ejecutarlo, y lo
primero que nuestro documento le daba era un `unknown-model`.

Hecho las dos cosas que proponíais, porque son complementarias y ninguna sobra:

- **Los ejemplos usan ahora un slug real del catálogo** (`qwen3-0.6b`), con sus valores reales de
  `context_length`, `family` y `quantization`. Se ejecutan tal cual.
- **Y el documento dice explícitamente que `GET /v1/models` es la fuente de verdad**, que los
  nombres varían entre despliegues, y que un SDK no debe fijar en código un nombre que no haya
  leído de ahí.

Una confesión menor mientras lo hacíamos: al sustituir el ejemplo del catálogo pusimos un
`context_length` de 40960 que era el del modelo equivocado. Lo detectamos comprobando contra el
despliegue antes de daros esto por cerrado. Que un documento diga *"todo lo de abajo sale del
código, no de documentación que pudo derivar"* no sirve de nada si no se comprueba.

---

## 2 · Leer el uso de una petición: lo vamos a hacer

Aceptada, y con vuestro diseño, que es mejor que el que se nos habría ocurrido:
`GET /v1/usage/{request_id}`, solo la fila propia, sin agregados, sin scope de administración, y
**404 en lugar de 403** para una petición ajena — esa distinción es correcta y no la habríamos
hecho.

**Tenéis razón en el fondo, y es más incómodo de lo que decís.** En la tanda anterior os
escribimos que la columna existía para que *"un cliente que disputa un cargo tenga algo que
señalar"*. Con `admin:read` como única puerta, quien puede señalarlo es justamente quien no
necesita hacerlo. La promesa estaba a medias y no lo vimos hasta que lo pusisteis por escrito.

**Por qué no está ya**, que es el dato técnico que os debemos: no es que el endpoint fuera caro.
Es que **la fila de uso no guarda el `request_id`**. Os devolvemos `x-request-id` en cada
respuesta, y no lo escribimos en el registro de facturación — así que hoy, incluso con permisos
de administración, nadie puede ir de un identificador de petición a su fila. El enlace que pedís
no existe por nuestro lado.

Lo que implica, en orden:

1. Guardar el `request_id` en la fila de uso, con su migración.
2. El endpoint, filtrando por el `client_id` del token y devolviendo 404 para todo lo demás.
3. Documentarlo en la guía de integración.

Queda anotado en nuestro backlog como **RM-100**. No os damos fecha porque no la tenemos, pero sí
un compromiso: **no lo vamos a cerrar como "no lo haremos"**. La alternativa que planteabais
—documentar que `termination_reason` es un dato de operación y no algo consultable— nos parece
peor: dejaría por escrito que facturamos con un motivo que el pagador no puede ver.

Un detalle de alcance que conviene acordar ahora y no después: pensamos exponer **la fila, no la
petición** — tokens, modelo, `interrupted`, `termination_reason`, coste y marca de tiempo. Nada
del contenido ni de los parámetros. Si vuestro caso de uso necesitara algo más, decidlo antes de
que lo construyamos.

---

## Sobre vuestro contrato grabado con una sola tool call

Ese hallazgo es del mismo tipo que los dos que nos habéis provocado a nosotros, y merece la pena
nombrar el patrón porque ya van tres: **un test que pasa por una razón distinta de la que su
nombre afirma.** El vuestro correlacionaba una sola llamada, donde cualquier regla da el mismo
resultado. El nuestro del `[DONE]` usaba `curl`, que drena el cuerpo, y por eso nunca ejercitó el
caso que importaba. Y esta semana descubrimos que uno de nuestros tests de validación de token
llevaba años sin validar ningún token: apuntaba a una URL sin claves, así que todas sus
peticiones morían antes de llegar al token y el `401` que afirmaba venía de otro sitio.

En los tres casos el verde era real. Lo que no era real era la explicación. Mutar el código para
ver si el test se entera —que es lo que hicisteis— es la única comprobación que lo caza, y la
hacemos poco.

---

## Resumen

| | |
|---|---|
| `family` | Cerrado. No era dato faltante sino un catálogo desincronizado; ya no puede quedar vacío |
| Ejemplos de la spec | **Arreglado**: slug real y ejecutable, más `GET /v1/models` declarado fuente de verdad |
| Uso por `request_id` | **Aceptado** con vuestro diseño. Falta guardar el `request_id` en la fila; anotado como RM-100 |
| Lo que os pedimos | Nada. Solo que confirméis el alcance de la fila si necesitáis más campos |
