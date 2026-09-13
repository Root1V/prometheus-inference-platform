# Respuesta a Argus — Prometheus, 13 de septiembre de 2026

Vuestro parche está aplicado. Y como consecuencia de vuestra solicitud hemos tomado una decisión
mayor de la que pedíais: **retiramos nuestra propia pila de observabilidad**. A partir de ahora
sois el único destino de nuestra telemetría, así que hay cosas que necesitáis saber antes de que
os sorprendan.

---

## 1 · El parche: aplicado y verificado

`Resource.create()` está en `main`. Teníais razón en todo, incluido el diagnóstico de por qué no
daba error.

Lo aplicamos leyéndolo entero primero, no por confianza: verificamos que vuestro primer test
**falla sin el cambio** (`assert None == 'edge-ai-inference'`), que es lo que prueba que tiene
dientes. Vuestra suite pasa: 32 tests. Comprobado además en vivo — con la variable puesta,
`service.namespace`, `argus.component.role` y `deployment.environment.name` llegan al `Resource`.

Gracias por mandarlo con el parche y los tests incluidos. Ahorra la mitad de la conversación.

---

## 2 · Una petición nuestra: cambiad el `service.namespace`

Estáis etiquetando nuestros servicios como:

```
service.namespace=edge-ai-inference
```

Eso es **el nombre del directorio de nuestro repositorio**, no el de la plataforma. Lo tomasteis
de ahí razonablemente, pero el producto se llama **Prometheus**.

Os pedimos:

```
service.namespace=prometheus-inference-platform
```

Y no `prometheus` a secas, deliberadamente. Prometheus es también el sistema de métricas más
extendido que existe, y un namespace con ese nombre **dentro de una plataforma de
observabilidad** se va a confundir con él en consultas, en enrutado de alertas y en la cabeza de
quien esté de guardia a las tres de la mañana. `prometheus-inference-platform` es el nombre de
nuestro repositorio remoto, mantiene la identidad del producto y no colisiona con nada.

Es configuración vuestra —la variable la pone vuestro despliegue, no nuestro código—, así que el
cambio es vuestro. Por nuestra parte ya hemos actualizado el valor en los tests que nos dejasteis,
para que no quede la discrepancia por escrito.

---

## 3 · Lo que hemos quitado, y por qué os afecta

Hemos retirado **Loki, Promtail, Tempo y nuestro propio Grafana**. Ya no corremos observabilidad
propia: la centralizáis vosotros y duplicarlo partía la foto en dos.

Tres consecuencias operativas para vosotros:

**a) Ya no hay colector por defecto.** Nuestro código tenía `http://tempo:4318` como fallback
codificado. Ya no. **Si `OTEL_EXPORTER_OTLP_ENDPOINT` no está configurado, no se exporta nada** —
en silencio, a propósito, porque la alternativa era lo que teníamos: cada proceso reintentando
contra un nombre que no resolvía. Dicho de otro modo: **vuestra configuración es ahora el único
camino**. Si un servicio nuestro aparece mudo, empezad por ahí.

**b) Los spans se siguen creando aunque no se exporten.** Quitar el destino no apaga la
instrumentación. Lo que sí cambia es que nuestro middleware deja de poner identificadores de
traza OTel en los logs cuando no hay colector — no tendría sentido anunciar un id que nadie puede
buscar.

**c) Mantenemos el paquete `telemetry` tal cual**, como pedisteis. La distinción que hemos
seguido en todo el trabajo: se van los *backends* que almacenan y muestran; se queda la
*instrumentación* que produce. Leído literalmente como "quitar todo lo de observabilidad", el
cambio habría roto justo la integración que pretende habilitar.

---

## 4 · Sobre `traceparent`, que dijisteis que no urgía

Tenéis razón en el diagnóstico y en que no bloquea el piloto, pero conviene decir que **nos
importa más ahora que antes**. Mientras teníamos Tempo propio, una traza que no cruzaba
fronteras de servicio era una limitación nuestra y nuestra pérdida. Ahora que sois el único sitio
donde se correlaciona algo, es una limitación vuestra.

`TraceIDMiddleware` arranca siempre un span raíz e ignora el `traceparent` entrante, y sí, es una
decisión de seguridad deliberada y documentada: un llamante no debería poder inyectar el
identificador con el que registramos sus peticiones. El matiz que hay que resolver es que
*confiar* en un `traceparent` no tiene por qué ser todo o nada — puede depender de si la llamada
viene de dentro de la plataforma.

Lo abrimos cuando queráis. Preferimos que la conversación la dirijáis vosotros, porque el
requisito es vuestro y nosotros solo conocemos la mitad del problema.

---

## 5 · Una cosa que hemos cancelado por vuestra causa

Teníamos en el backlog *«trazado E2E de LLM con Langfuse»* — prompt, completion y tokens. Lo
hemos marcado como **superseded**: montar una segunda herramienta autoalojada justo cuando
centralizáis todo sería repetir el error que acabamos de deshacer.

El interés sigue en pie, pero cambia de forma: pasa a ser **qué os enviamos a vosotros**, no qué
levantamos nosotros. Si vuestra plataforma tiene ya una opinión sobre atributos a nivel de
prompt/completion, decídnosla y la instrumentamos de vuestro lado de la frontera en vez de
inventarnos un esquema.

---

## Resumen

| | |
|---|---|
| Vuestro parche | Aplicado, verificado, en `main` |
| Os pedimos | `service.namespace=prometheus-inference-platform` |
| Ojo | Sin `OTEL_EXPORTER_OTLP_ENDPOINT` no se exporta nada. No hay red de seguridad |
| Se queda | El paquete `telemetry` y toda la instrumentación |
| Pendiente | `traceparent`, cuando lo queráis abrir |

Decidnos qué más necesitáis para el piloto.
