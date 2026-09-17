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
| Poder demostrarlo | principio de responsabilidad proactiva | Registro de comunicaciones del sistema (§4) |

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

## 4. Lo que falta (tarea del estudio, no del software)

- Contrato de encargado archivado por proveedor (y su DPA), con fecha.
- Aviso de privacidad que declare la finalidad, los destinatarios y la
  transferencia internacional, con las garantías aplicables.
- Cláusula de IA en la hoja de encargo del cliente.
- Procedimiento de brechas que incluya al proveedor (art. 14 sexies).
- Revisar cada seis meses: retención, ZDR y términos de cada proveedor cambian
  (Anthropic bajó su retención de 30 a 7 días; el tier de consumo cambió de
  política dos veces en un año).
