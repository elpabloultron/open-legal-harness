# Entregar el expediente a la IA: cómo hacerlo bien

La gracia del sistema es darle la causa al modelo para que la analice con las
herramientas de `open-legal-chile`. Eso **no se prohíbe: se documenta**. Este
documento dice qué exige la ley, qué se firmó con cada proveedor y cómo lo cubre
el software.

## 1. Qué acto jurídico es esto

Mandar el expediente a un modelo externo es una **comunicación de datos a un
tercero que los trata por cuenta del responsable** (el proveedor es *encargado*,
no responsable) y, con servidores fuera de Chile, una **transferencia
internacional**.

| Requisito | Norma | Cómo se cumple |
|---|---|---|
| Base de licitud sin consentimiento del titular | art. 13 letra e): tratamiento necesario «para la formulación, ejercicio o defensa de un derecho ante los tribunales de justicia u órganos públicos» | La causa en juicio es exactamente ese supuesto |
| Deber de secreto del estudio | art. 14 bis | El encargado queda obligado por contrato; el proveedor no usa los datos para fines propios |
| Contrato con el encargado | ley 19.628 reformada, régimen de encargados | Aceptar y **archivar** los términos de datos (DPA) del proveedor: objeto, duración, categorías, finalidades, medidas de seguridad, prohibición de subdelegar sin autorización, destino al término |
| Transferencia internacional | arts. 27 a 29 | Nivel adecuado de protección **o** garantías: cláusulas contractuales tipo. Ya hay **cláusulas modelo aprobadas** (Resolución Exenta 2025-3748, 19-dic-2025, Ministerio de Economía) |
| Informar al titular | art. 14 ter | El aviso de privacidad del estudio debe declarar esta finalidad, los destinatarios y la transferencia |
| Poder demostrarlo | principio de responsabilidad proactiva | Registro de comunicaciones del sistema (§3) y el proxy de la §4, que cubre también lo que no pasa por el CRM |

Práctica recomendada: **cláusula en la hoja de encargo** con el cliente («el
estudio podrá usar herramientas de inteligencia artificial, contratadas bajo
deber de confidencialidad, para analizar y redactar en su causa»), además del
aviso de privacidad. Cubre el flanco del secreto profesional y el del art. 14 ter.

## 2. Qué se firmó con cada proveedor (verificado en fuentes públicas)

| Proveedor | ¿Entrena con los datos de la API? | Retención por defecto | ZDR | Contrato |
|---|---|---|---|---|
| **Anthropic** (Claude) | No, en comercial (API, Team, Enterprise) | **7 días** (bajó de 30 el 14-09-2025) | Sí, a pedido | DPA con cláusulas contractuales tipo; ley irlandesa |
| **OpenAI** (GPT) | No, en API y Enterprise | 30 días (monitoreo de abuso) | Sí, a pedido (1 a 3 semanas) | DPA con cláusulas contractuales tipo; residencia UE vía Azure OpenAI |
| **DeepSeek** | **SIN VERIFICAR** | sin verificar | sin verificar | pendiente |
| **Modelo local** | no aplica | la que fije el estudio | no aplica | no hay encargado ni transferencia |

Tres trampas que conviene tener presentes:

1. **Tier, no marca.** Las promesas valen para la API y los planes empresariales.
   En consumo cambia todo: `claude.ai` free/Pro pasó a opt-in para entrenamiento
   con retención de hasta 5 años, y ChatGPT free/Plus entrena salvo que el usuario
   lo desactive. Google rompe la regla en el tier gratis de AI Studio.
2. **Las CLI son API.** Claude Code, Codex o Copilot CLI mandan el contenido por
   la misma API del proveedor. Si el expediente pasa por ahí, es la misma
   transferencia y aplica lo mismo.
3. **DeepSeek no está verificado.** Hasta tener sus términos y su contrato de
   encargado a la vista, no se manda expediente con él. El sistema lo dice en
   `openlegal ia proveedores`.

## 3. Cómo lo cubre el software

Flujo en tres pasos, y ninguno es opcional:

```bash
# 1. autorizar la causa (queda quién autorizó y con qué base de licitud)
openlegal ia autorizar --causa 1 --alcance analisis --titular "Jorge Fuentes (Constructora Andes SpA)"

# 2. minimizar: se manda el texto con marcadores, no con nombres ni RUT
openlegal ia redactar --causa 1 --archivo demanda.txt
#    → [RUT·1], [CORREO·2], [NOMBRE·3]…  y el mapa queda en el estudio

# 3. registrar el envío (exige autorización vigente; sin ella se niega y queda el intento)
openlegal ia registrar --causa 1 --proveedor anthropic --modelo claude-opus-4.7 \
  --documentos "demanda.pdf (minimizada)" --redactado --archivo demanda_minimizada.txt

openlegal ia transferencias   # bitácora: qué salió, a quién, cuándo y con qué hash
```

Lo que guarda el registro: causa, proveedor, modelo, **país de destino**,
documentos, cantidad de caracteres, **hash SHA-256 del payload** (para probar qué
se mandó sin duplicar el expediente dentro de la base), si iba minimizado, quién
lo mandó y bajo qué autorización. También registra los intentos bloqueados
(`ia.sin_autorizacion`) y las revocaciones.

> **Ojo con el alcance de esto.** Los tres pasos anteriores sólo se cumplen cuando el
> envío pasa **por el CRM** (`openlegal ia registrar`, las herramientas `crm_ia_*`). El
> harness y cualquier otra herramienta que le hable al modelo por su cuenta no pasan
> por ahí, y en ese camino no hay ni aviso ni registro: el estudio creería que tiene todo
> documentado y tendría un hueco por donde salen expedientes sin dejar rastro. Ese hueco
> lo cierra el **proxy local** de la §4, que es donde conviene trabajar de verdad.

Reglas de uso que conviene respetar:

- **Minimizar por defecto.** Igual que se manda un escrito sin el anexo que no
  corresponde, se manda el texto sin RUT ni nombres: el modelo razona igual.
- **Sensibles con cuidado extra**: salud, menores de 16 (requieren consentimiento
  del representante legal), causas penales o de familia con reserva. Ahí, modelo
  local.
- **Revocable**: si el cliente retira la autorización, `openlegal ia revocar
  --causa N` bloquea los envíos siguientes y queda la fecha en el registro.
- **El mapa de reidentificación nunca sale del estudio**: es lo que permite leer
  la respuesta del modelo sin haberle entregado los datos.

## 4. El proxy local: que no se escape ninguna llamada

El CRM puede advertir, minimizar y registrar — pero sólo lo que pasa por el CRM. Una
herramienta que hable directo con el proveedor del modelo (el harness, un script, una CLI
del proveedor) manda el expediente sin dejar rastro, y el registro de comunicaciones queda
mintiendo por omisión. El proxy cierra eso con un cambio de una línea en la herramienta:

```bash
openlegal ia-proxy                     # arranca el proxy local (Ctrl-C para cortarlo)
#   proxy de IA escuchando en http://127.0.0.1:8790 (sólo esta máquina)
#     dialecto OpenAI:    base_url=http://127.0.0.1:8790/v1 → https://api.deepseek.com/v1
#     dialecto Anthropic: baseURL=http://127.0.0.1:8790   → https://api.deepseek.com/anthropic
#     proveedor: deepseek · país de destino: China · minimizar: sí

openlegal ia-proxy --estado            # configuración vigente y últimos envíos (sin claves)
```

El proxy habla **los dos dialectos** que usan las herramientas de verdad —el de OpenAI
(`POST /v1/chat/completions`, con streaming SSE, y `GET /v1/models`) y el de Anthropic
(`POST /v1/messages`, también con SSE, y su `GET /v1/models`)— y escucha **sólo en
127.0.0.1**. Atrás puede estar cualquier proveedor —o un modelo local—: lo que se configura son
**dos direcciones aguas arriba**, una por dialecto (`base_url` y `base_url_anthropic`), porque
el mismo proveedor publica cada dialecto en una dirección distinta (DeepSeek, por ejemplo, en
`https://api.deepseek.com/v1` y en `https://api.deepseek.com/anthropic`).

El registro es **uno solo** para los dos dialectos: `via` dice por cuál salió cada envío
(`openai-compat` o `anthropic-compat`) y todo lo demás —autorización, minimización, hash,
aviso— es idéntico. Cambia el proveedor, cambia la herramienta, cambia el dialecto: el registro
sigue siendo el mismo lugar, y esa es la idea.

### Apuntarle el harness (o cualquier herramienta)

Son **dos protocolos y dos direcciones base distintas**, y cuál va depende de por dónde hable la
herramienta. El harness (`dsh`) usa por defecto el protocolo `messages` (`@deepseek-ai/dsh-llm-deepseek`
lo arma como `baseURL + /v1/messages`), así que su dirección es el proxy **sin** `/v1` y
**sin** `/anthropic`:

```yaml
llm-deepseek:
  protocol: messages                 # el dialecto de Anthropic
  apiKeyEnv: DEEPSEEK_API_KEY        # la referencia, no la clave
  baseURL: http://127.0.0.1:8790     # el proxy: él agrega /v1/messages
```

```bash
export DEEPSEEK_BASE_URL='http://127.0.0.1:8790'      # la misma palanca, por entorno
```

Si en cambio la herramienta habla el dialecto de OpenAI, su dirección base termina en `/v1`
(porque la ruta que agrega es `/chat/completions`):

```bash
export OPENAI_BASE_URL='http://127.0.0.1:8790/v1'
```

El error de dedo que hay que evitar es ponerle `/v1` o `/anthropic` a la dirección del
protocolo `messages`: quedaría en `http://127.0.0.1:8790/v1/v1/messages` y el proxy contestaría
un 404 con la explicación (nunca reenvía a ciegas). `openlegal ia-proxy --estado` imprime las
dos direcciones para copiar.

La `api_key` que tenga la herramienta deja de importar: **la clave real vive en el proxy**
(`~/.openlegal/ia_proxy.json`, permisos 600) y viaja de ahí aguas arriba —en la cabecera del
dialecto que corresponda: `Authorization: Bearer` para OpenAI, `x-api-key` (y `Authorization`)
para Anthropic—. Por eso el harness puede llevar una cualquiera, o ninguna.

La causa del envío sale de la cabecera `X-OpenLegal-Causa: <n>`, o de la `causa_por_defecto`
de la configuración. Si no hay ninguna, el envío se registra igual, con la causa vacía: no
todo uso de IA es de un expediente, y forzar una causa que no es sería peor.

### Qué hace con cada llamada, en orden

1. **Cuenta y huella.** Caracteres del texto que sale y su **SHA-256** (`ia.hash_payload`). El
   texto se saca del pedido según su dialecto: en OpenAI, de los `messages`; en Anthropic,
   además, del `system`, de los bloques de cada `content` (incluidos los `tool_result`, que es
   por donde vuelven los datos que sacaron las herramientas) y de las descripciones de las
   `tools`.
2. **Minimiza** (por defecto sí): aplica `ia.redactar` con los términos de la causa y avisa
   cuántos marcadores puso. Si el pedido trae algo que no se puede minimizar (una imagen en
   base64, un documento adjunto), **no reenvía**: contesta un error y lo anota. En el dialecto
   de Anthropic se minimizan el `system`, los bloques de texto, los `tool_result`, los `input`
   de los `tool_use` y las `description` de las herramientas; los **esquemas** de las
   herramientas (`input_schema`) no se tocan, porque son su estructura y enmascarar un valor de
   ahí la rompería.
3. **Comprueba la autorización** de la causa. Sin autorización vigente y con
   `permitir_sin_autorizacion` apagado, **no reenvía**:

   ```text
   este envío no tiene autorización de IA para la causa 12: autorizala con
   `openlegal ia autorizar --causa 12` (indicando quién autoriza) o pedile al
   responsable del estudio que la registre. El intento quedó anotado.
   ```

4. **Registra** el envío en `transferencias_ia` (`origen = 'proxy'`, `via = 'openai-compat'` o
   `'anthropic-compat'` según el dialecto, proveedor, modelo, país, caracteres, hash, si iba
   minimizado, causa y autorización) y deja su entrada en la bitácora. Los envíos **bloqueados**
   también se registran, con su motivo (`motivo_bloqueo`): el intento es justamente lo que hay
   que poder demostrar.
5. **Avisa**: una línea por la terminal del proxy y, si `avisar_escritorio` está activo, un
   aviso de escritorio con el mismo resumen (con un tope de 5 minutos para no tapar la
   pantalla; los bloqueados se avisan siempre).

### Qué NO guarda (y por qué)

- **El contenido.** Ni el prompt, ni la respuesta del modelo, ni un archivo temporal: sólo
  metadatos. La respuesta pasa de largo, por trozos, sin guardarse. El hash permite demostrar
  que lo que salió es lo que está en el expediente, sin duplicar el expediente en la base.
- **La `api_key`.** Vive en `~/.openlegal/ia_proxy.json` (600), se usa para la cabecera
  `Authorization` aguas arriba, y no aparece en ningún registro, aviso ni salida. El panel
  dice si está configurada o si falta —nunca cuál es—.
- **Nada si no puede registrar.** Si la base del CRM no está disponible, el proxy **no
  reenvía** y lo dice: sin registro no hay aviso, y el aviso es el punto.

El archivo de configuración, completo:

```json
{
  "proveedor": "deepseek",
  "base_url": "https://api.deepseek.com/v1",
  "base_url_anthropic": "https://api.deepseek.com/anthropic",
  "modelo_por_defecto": "",
  "api_key": "…",
  "destino_pais": "China",
  "causa_por_defecto": null,
  "minimizar": true,
  "permitir_sin_autorizacion": false,
  "avisar_escritorio": true,
  "puerto": 8790,
  "base_de_datos": null
}
```

`base_url` es la dirección del dialecto de OpenAI y `base_url_anthropic` la del dialecto de
Anthropic: **ninguna de las dos es la del harness**. La del harness es
`http://127.0.0.1:8790` (el proxy), y el proxy reenvía a la que corresponda según el dialecto
del pedido. Si a `base_url_anthropic` se le escapa un `/v1` al final, el proxy lo tolera (la
ruta que agrega ya lo trae); si falta, los pedidos de ese dialecto se rechazan con un error que
lo dice, en vez de salir a ciegas.

### La regla que no se puede saltar

**Un compromiso de no-entrenamiento que no está por escrito no se puede afirmar en un aviso
de privacidad.** El proxy registra y advierte, y el panel muestra qué sabemos de cada
proveedor; pero «no entrenan con nuestros datos» es una afirmación jurídica, y sólo se puede
hacer con los términos o el DPA archivados, con fecha. Mientras DeepSeek figure como
*sin verificar*, el aviso de privacidad no puede decir que no entrena: puede decir que el
estudio lo está verificando, y punto.

## 5. Lo que falta (tarea del estudio, no del software)

- Contrato de encargado archivado por proveedor (y su DPA), con fecha.
- Aviso de privacidad que declare la finalidad, los destinatarios y la
  transferencia internacional, con las garantías aplicables: sin el papel del
  proveedor, no se puede afirmar que no entrena con los datos.
- El harness (y cualquier otra herramienta que use un modelo) apuntado al proxy del
  estudio (`baseURL: http://127.0.0.1:8790` para el protocolo `messages`, o
  `base_url: http://127.0.0.1:8790/v1` para el de OpenAI), y comprobar en el panel que los
  envíos de la semana aparecen con su hash y su `via`. Una herramienta que se saltee el proxy
  es un registro incompleto, aunque el aparato del CRM esté impecable.
- Cláusula de IA en la hoja de encargo del cliente.
- Procedimiento de brechas que incluya al proveedor (art. 14 sexies).
- Revisar cada seis meses: retención, ZDR y términos de cada proveedor cambian
  (Anthropic bajó su retención de 30 a 7 días; el tier de consumo cambió de
  política dos veces en un año).
