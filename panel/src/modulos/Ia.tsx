/**
 * IA y transferencias: qué salió hacia un modelo, hacia dónde y bajo qué autorización.
 *
 * El CRM tenía el registro de envíos desde el principio, pero sólo anotaba lo que salía **por
 * el CRM**. El harness habla directo con el proveedor del modelo, así que los envíos de verdad
 * —los del día a día— no aparecían por ninguna parte. Esta pantalla es para eso: muestra el
 * proxy local al que hay que apuntarle el harness (con su dirección, para copiarla) y todo lo
 * que pasó por ahí, incluidos los envíos que el proxy **bloqueó** por falta de autorización.
 *
 * Tres cosas que la pantalla dice de sí misma, porque son las que se prestan a confusión:
 *  1. Son **metadatos**: proveedor, modelo, país, caracteres, hash y si iba minimizado. El
 *     contenido de lo que se mandó no se guarda en ninguna parte —y por eso acá no se puede
 *     leer—. El hash sirve para demostrar que lo que salió es lo que está en el expediente.
 *  2. El panel es de **sólo lectura**: no borra ni edita envíos. Un registro de comunicaciones
 *     que se puede editar no prueba nada; lo que se hace para corregir es revocar la
 *     autorización (que sí queda anotado, con fecha).
 *  3. La `api_key` del proveedor no viaja al navegador: el servidor informa si está
 *     configurada o falta, nunca su valor.
 */
import { useCallback, useEffect, useState } from "react";
import { Alert, Box, Button, Card, CardContent, Chip, Grid2 as Grid, Stack, Typography } from "@mui/material";

import { obtener } from "../api/cliente";
import type { EnvioIA, ModuloIA } from "../tipos";
import { sellosLegibles } from "../tipos";
import { TablaSimple } from "./Inicio";

/** El hash se muestra corto: entero ocupa media pantalla y no se compara de a ojo. */
const hashCorto = (hash?: string | null) => (hash ? `${hash.slice(0, 12)}…` : "—");

const causaDe = (envio: EnvioIA) =>
  envio.caratula ?? (envio.causa_id === null ? "sin causa" : `causa ${envio.causa_id}`);

const si = (valor: boolean) => (valor ? "sí" : "no");

export function Ia() {
  const [datos, setDatos] = useState<ModuloIA | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(false);

  const cargar = useCallback(async () => {
    setCargando(true);
    try {
      setDatos(await obtener<ModuloIA>("/api/ia?limite=50"));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setCargando(false);
    }
  }, []);

  useEffect(() => {
    cargar().catch((e: Error) => setError(e.message));
  }, [cargar]);

  if (error) return <Alert severity="error">{error}</Alert>;
  if (!datos) return <Typography sx={{ p: 2 }}>cargando el registro de IA…</Typography>;

  const { proxy, envios, bloqueados, autorizaciones, proveedores } = datos;

  return (
    <Box sx={{ p: 2 }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1 }}>
        <Typography variant="h5">IA y transferencias</Typography>
        <Button variant="outlined" size="small" disabled={cargando} onClick={() => cargar()}>
          actualizar
        </Button>
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {datos.aviso}. El panel sólo lee: un registro que se puede editar no prueba nada. Para cortar los envíos de
        una causa se revoca su autorización, y eso queda anotado con fecha y quién lo hizo.
      </Typography>

      <Grid container spacing={2}>
        <Grid size={{ xs: 12 }}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                El proxy de la IA
              </Typography>
              <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 1 }}>
                <Chip
                  size="small"
                  variant="outlined"
                  color={proxy.activo ? "success" : "warning"}
                  label={proxy.activo ? "escuchando" : "no está corriendo"}
                />
                <Chip size="small" variant="outlined" label={`proveedor: ${proxy.proveedor}`} />
                <Chip size="small" variant="outlined" label={`destino: ${proxy.destino_pais}`} />
                <Chip
                  size="small"
                  variant="outlined"
                  color={proxy.minimizar ? "success" : "warning"}
                  label={`minimizar: ${si(proxy.minimizar)}`}
                />
                <Chip
                  size="small"
                  variant="outlined"
                  color={proxy.permitir_sin_autorizacion ? "warning" : "success"}
                  label={`permitir sin autorización: ${si(proxy.permitir_sin_autorizacion)}`}
                />
                <Chip size="small" variant="outlined" label={`api_key: ${proxy.api_key}`} />
              </Stack>
              <Typography variant="body2">
                Apunta el harness acá —en el perfil de <code>dsh</code>, cambia la dirección base y pon una{" "}
                <code>api_key</code> cualquiera, porque la de verdad vive en el proxy—:
              </Typography>
              <Typography variant="body2">
                · protocolo <code>messages</code> (el de Anthropic, el que usa <code>dsh</code> por defecto):{" "}
                <strong>{proxy.url_anthropic}</strong> — sin <code>/v1</code> ni <code>/anthropic</code>, porque
                el adaptador le agrega <code>/v1/messages</code> él mismo
              </Typography>
              <Typography variant="body2">
                · protocolo de OpenAI (<code>/v1/chat/completions</code>): <strong>{proxy.url}</strong>
              </Typography>
              <Typography variant="body2" color="text.secondary">
                aguas arriba: {proxy.base_url ?? "sin configurar"} · aguas arriba del dialecto de Anthropic:{" "}
                {proxy.base_url_anthropic ?? "sin configurar"} · modelo por defecto:{" "}
                {proxy.modelo_por_defecto ?? "el que traiga el pedido"} · causa por defecto:{" "}
                {proxy.causa_por_defecto ?? "ninguna"} · avisos de escritorio: {si(proxy.avisar_escritorio)}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                los dos dialectos van al mismo registro: <code>via</code> dice por cuál salió cada envío (
                {proxy.via} · {proxy.via_anthropic})
              </Typography>
              <Typography variant="body2" color="text.secondary">
                configuración: {proxy.archivo} (permisos {proxy.permisos}) · registro: {proxy.base_de_datos}
              </Typography>
              {!proxy.activo && (
                <Alert severity="info" sx={{ mt: 1 }}>
                  El proxy no está escuchando: arráncalo con <code>openlegal ia-proxy</code>. Sin él, las
                  herramientas que hablen directo con el proveedor (el harness, por ejemplo) no van a dejar rastro.
                </Alert>
              )}
              {proxy.avisos.map((aviso) => (
                <Alert severity="warning" key={aviso} sx={{ mt: 1 }}>
                  {aviso}
                </Alert>
              ))}
            </CardContent>
          </Card>
        </Grid>

        {bloqueados.length > 0 && (
          <Grid size={{ xs: 12 }}>
            <Card>
              <CardContent>
                <Typography variant="h6" gutterBottom>
                  Envíos bloqueados: los que NO salieron
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                  El intento también es parte de lo que hay que poder demostrar. Si acá hay algo, revisá si falta
                  autorizar la causa (<code>openlegal ia autorizar --causa N</code>) o si el pedido traía contenido
                  que no se puede minimizar.
                </Typography>
                <TablaSimple
                  cabeceras={["#", "Fecha", "Causa", "Proveedor", "Modelo", "Motivo"]}
                  filas={bloqueados.map((envio) => [
                    String(envio.id),
                    sellosLegibles(envio.creado_en),
                    causaDe(envio),
                    envio.proveedor,
                    envio.modelo ?? "—",
                    envio.motivo_bloqueo ?? "sin motivo anotado",
                  ])}
                />
              </CardContent>
            </Card>
          </Grid>
        )}

        <Grid size={{ xs: 12 }}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Últimos envíos
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                Proveedor, modelo, país de destino, tamaño y huella de lo que salió. El contenido no se guarda: el
                hash es lo que permite probar que el texto que se mandó es el que está en el expediente.
              </Typography>
              <TablaSimple
                cabeceras={[
                  "#", "Fecha", "Origen", "Causa", "Proveedor", "Modelo",
                  "Destino", "Caracteres", "Minimizado", "Hash", "Quién",
                ]}
                filas={envios.map((envio) => [
                  String(envio.id),
                  sellosLegibles(envio.creado_en),
                  envio.origen,
                  causaDe(envio),
                  envio.proveedor,
                  envio.modelo ?? "—",
                  envio.destino_pais ?? "sin verificar",
                  String(envio.caracteres),
                  si(Number(envio.redactado) === 1),
                  hashCorto(envio.hash_payload),
                  envio.usuario ?? "el proxy (sin usuario)",
                ])}
              />
            </CardContent>
          </Card>
        </Grid>

        <Grid size={{ xs: 12, md: 6 }}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Autorizaciones por causa
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                Sin autorización vigente no sale nada: el proxy rechaza el envío y lo anota como bloqueado.
              </Typography>
              <TablaSimple
                cabeceras={["#", "Causa", "Alcance", "Titular", "Vigente", "Registró", "Creada"]}
                filas={autorizaciones.map((autorizacion) => [
                  String(autorizacion.id),
                  autorizacion.caratula ?? `causa ${autorizacion.causa_id}`,
                  autorizacion.alcance,
                  autorizacion.titular ?? "s/informar",
                  autorizacion.vigente ? "sí" : `revocada ${autorizacion.revocada_en ?? ""}`.trim(),
                  autorizacion.registrado_por_nombre ?? "—",
                  sellosLegibles(autorizacion.creado_en),
                ])}
              />
            </CardContent>
          </Card>
        </Grid>

        <Grid size={{ xs: 12, md: 6 }}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Qué sabemos de cada proveedor
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                El CRM no afirma nada que no esté por escrito: si un dato no está verificado, dice «sin verificar».
              </Typography>
              <TablaSimple
                cabeceras={["Proveedor", "País", "¿Entrena con los datos de la API?", "Retención", "ZDR"]}
                filas={proveedores.map((proveedor) => [
                  proveedor.proveedor,
                  proveedor.pais,
                  proveedor.entrena_con_api,
                  proveedor.retencion,
                  proveedor.zdr,
                ])}
              />
              <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                Un compromiso de no entrenamiento que no está por escrito no se puede afirmar en un aviso de
                privacidad.
              </Typography>
            </CardContent>
          </Card>
        </Grid>
      </Grid>
    </Box>
  );
}
