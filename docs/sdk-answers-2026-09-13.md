# Respuesta a la tercera tanda — Axonium, septiembre 2026

Vuestro hallazgo era mucho más grande de lo que pensabais, y de lo que pensábamos nosotros.

Dijisteis explícitamente que no pedíais nada: que lo reportabais porque *«la diferencia entre
"funciona" y "funciona a veces" es la que decide si alguien puede construir encima»*. Al
reproducirlo encontramos que el replay poco fiable era el síntoma visible de otra cosa: **toda
generación en streaming hacia un SDK normal se estaba facturando a cero**.

Esto es lo que pasó, qué está arreglado, y las dos cosas que os cambian.

---

## 1 · Lo que encontramos al reproducir vuestra medición

Vuestros números eran exactos. En nuestro despliegue, con vuestro mismo protocolo —misma clave,
mismo cuerpo, 3 segundos de espera, seis rondas— salía lo mismo: 0 replays en streaming, 6 de 6
en no-streaming.

Lo que no salía era con `curl`. Con `curl` daba 6 de 6. Esa diferencia era la pista, y no tenía
nada que ver con la duración de la generación.

Tres cosas se combinaban:

**a) Enviábamos dos frames terminales.** El backend termina su propio stream con `data: [DONE]`,
nosotros lo reenviábamos tal cual, y además añadíamos el nuestro al final. Cada respuesta en
streaming llevaba **dos** `[DONE]`.

**b) Vuestros SDKs paran en el primero, que es lo correcto.** `curl` no: drena el cuerpo hasta
EOF. Ahí estaba la diferencia entre vuestras mediciones y las nuestras iniciales.

**c) Todo lo que registra lo ocurrido vivía después de ese punto.** Un cliente que deja de leer
deja nuestro generador suspendido en ese `yield`. Lo que venía después solo se ejecutaba al
desmontarlo, dentro de una tarea que nuestro servidor ya había cancelado — es decir, no se
ejecutaba. Y ahí dentro estaban: el registro de uso, las métricas, la liquidación de la clave de
idempotencia y la del tope de gasto.

De ahí vuestro `idempotency-in-progress` persistente: la clave nunca se liquidaba. Y de ahí
también la que «regeneró»: cuando el desmontaje por fin ocurría, llegaba como una cancelación, se
interpretaba como stream roto, y la clave se devolvía sin guardar nada. Vuestras dos
observaciones, una misma causa.

Vuestra hipótesis sobre `max_tokens: 12` apuntaba en la dirección correcta por otro motivo: no era
una ventana de finalización proporcionalmente grande, era que el frame terminal llegaba **antes**
de que existiera contabilidad alguna.

---

## 2 · Lo que estaba roto de verdad

Medido antes del arreglo, contra el despliegue:

```
3 generaciones en streaming, cliente que para en [DONE]  ->  0 filas de uso
   (y seguían siendo 0 quince segundos después)
3 generaciones idénticas, cliente que drena hasta EOF    ->  3 filas de uso
1 generación larga abandonada a mitad                    ->  0 filas de uso
```

Es decir: **no os estábamos cobrando el streaming**. Y un cliente que abandonaba una generación a
mitad no pagaba nada, lo que convertía desconectarse en la forma más barata de usar la
plataforma.

Os lo decimos con todas las letras porque el arreglo os va a subir la factura, y preferimos que
sepáis exactamente por qué antes de verlo. No es un cambio de tarifa ni de política: es que
estábamos facturando mal, en vuestro favor, y lo hemos corregido.

Nada de esto lo habríamos encontrado nosotros. Nuestras propias pruebas usaban `curl`, que drena
el cuerpo, así que pasaban.

---

## 3 · Qué está arreglado

**El frame terminal se emite una sola vez, por nosotros, y solo cuando la petición ya está
contabilizada.** Ya no reenviamos el `[DONE]` del backend.

**La contabilidad ya no depende de que sigáis conectados.** Se despacha de forma desacoplada, así
que sobrevive a la cancelación que provoca una desconexión. Un cliente que se va a mitad es
precisamente el caso que hay que registrar, no uno que dejar pasar.

**La liquidación de la clave de idempotencia sigue siendo síncrona en el camino normal**, para que
un reintento inmediato encuentre el resultado almacenado y no una clave a medio escribir.

Medido después, mismo despliegue:

| Escenario | Antes | Ahora |
|---|---|---|
| Vuestra medición exacta (6 rondas, 3 s) | 0/6 replay | **6/6 replay** |
| Reintento **sin espera ninguna** | — | **3/3 replay** |
| SDK que para en `[DONE]`, facturado | 0/3 | **3/3** |
| Cliente que drena, facturado | 3/3 | 3/3 |
| Abandono a mitad, facturado | 0 | **1/1** |
| Frames `[DONE]` por respuesta | 2 | **1** |

**Podéis quitar el «best-effort» de vuestra documentación.** El replay en streaming es ahora tan
fiable como el no-streaming, incluso sin espera entre llamadas. Seguid exponiendo
`Idempotent-Replay` —es información útil y honesta— pero ya no como advertencia.

---

## 4 · Lo que os cambia

### a) Un frame `[DONE]` menos

Si algún parser vuestro cuenta frames terminales, o tolera basura después del primero, ahora
recibe uno solo. Creemos que no os afecta —parar en el primero era lo correcto y es lo que
hacíais— pero es un cambio observable en el cuerpo y os lo señalamos antes de que lo veáis.

### b) Las filas de uso dicen *por qué* paró una petición

Vuestro reporte nos obligó a mirar una promesa que os habíamos hecho por escrito. En el documento
anterior dijimos:

> «Si eres cobrado por una respuesta que nunca recibiste completa, esa fila lo dice.»

No era cierto para el caso más común de todos. Un stream que **abandona el cliente** —el botón
*Stop* de cualquier chat— se facturaba sin marca alguna, indistinguible de una respuesta
completa. Y una vez empezamos a facturarlos (punto 3), el booleano `interrupted` tenía que cargar
con dos sucesos que no se parecen en nada: que lo cortáramos nosotros, o que colgarais vosotros.
Son conversaciones opuestas cuando se discute un cargo.

Hay una columna nueva, `termination_reason`, con los tres valores reales:

| Valor | Qué pasó |
|---|---|
| `complete` | El modelo terminó. La respuesta está entera. |
| `upstream_error` | Nuestro stream se rompió a mitad. Problema nuestro. |
| `client_disconnected` | Colgasteis a mitad. Ordinario, no excepcional. |

**`interrupted` no desaparece y no cambia de sitio.** Sigue en la misma posición del CSV, ahora
derivada de la razón (`true` para los dos últimos). Es decir: ahora significa exactamente lo que
os dijimos que significaba, y no puede contradecir a la columna nueva porque nunca se asigna por
separado. La razón se añade **al final** de la fila, así que si leéis por índice no os afecta
nada; si leéis por nombre, ganáis el *por qué*.

---

## 5 · Una corrección nuestra, sobre la factura

En el documento anterior escribimos que cobramos *«por lo que generó»* y que eso es *«lo que
hacen OpenAI y Anthropic cuando un cliente se desconecta»*.

Eso nos describe mal, y a ellos también. **Cobramos por los tokens que efectivamente os
enviamos**, no por todo lo que produjo la GPU. Es la medida más estricta de las dos posibles y la
única que podemos evidenciar línea a línea. La frase anterior nos pintaba más agresivos de lo que
somos, y como está en un documento que tenéis en la mano, la corregimos aquí explícitamente en
lugar de reescribirla en silencio.

Un detalle asociado: un modelo con razonamiento emite su cadena de pensamiento como
`reasoning_content`, no como `content`. Nuestro contador solo miraba el segundo, así que un stream
abandonado mientras el modelo aún razonaba contaba cero tokens. También corregido — esos tokens se
enviaron y se cobran.

---

## 6 · Lo menor que arrastramos

**`family` sigue vacío.** Cuarta vez que lo mencionáis. Tenéis razón y no tenemos excusa: es un
dato del catálogo, no del contrato, y sigue sin rellenar. No os bloquea, pero dejar de repetirlo
depende de nosotros, no de vosotros.

**Vuestra validación de longitud de clave en cliente:** de acuerdo con cómo lo habéis anotado.
Los 255 no se mueven sin avisaros primero.

---

## 7 · Lo que podéis dar por firme

Sin cambios respecto a la ronda anterior, más lo nuevo:

- Un slug **nunca** cambia y **nunca** se reutiliza.
- Los nombres antiguos siguen resolviendo. Habrá aviso antes de cualquier retirada.
- El `type` del error es el contrato; el `detail` es prosa.
- El uso se registra **una vez por respuesta devuelta**, nunca por intento. El failover interno lo
  pagamos nosotros.
- Un replay nunca llega al modelo, no registra uso y no cuenta contra un tope de gasto.
- **Se cobra lo enviado**, no lo generado.
- **Toda petición facturada tiene su fila**, y la fila dice cómo terminó. `interrupted` y
  `termination_reason` van juntas y no pueden discrepar.
- **Un frame terminal por respuesta.**

---

## Sobre cómo llegó esto

Lo vuestro de esta semana —documentar que Go necesitaba cancelar el `context`, y descubrir al
mutar el código que cerrar el cuerpo ya bastaba— es exactamente lo que nos pasó aquí, en grande.
Teníamos tests en verde para el streaming. Usaban `curl`. `curl` drena el cuerpo. El verde era
real y no probaba nada de lo que creíamos.

Tres rondas, y el patrón se repite: los defectos que encontráis no son los que buscábamos, y dos
de tres veces estaban escritos por nuestra parte como decisiones deliberadas. Esta vez ni siquiera
pedíais nada.

Seguid mandando lo que encontréis.
