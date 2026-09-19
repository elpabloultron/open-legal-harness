/**
 * Honorarios: la cuenta de dividendos de una causa, a la vista y sin planillas aparte.
 *
 * El orden de la pantalla es el de una rendición de cuentas: primero se elige la causa, después
 * se ve lo que se pactó, lo que gastó el estudio, lo que el cliente abonó y, al final, el saldo.
 * Los altas están abajo y sólo las ve quien puede registrarlas (`honorario.editar` / `gasto.editar`).
 *
 * Tres cosas que el módulo dice de sí mismo, porque son las que se prestan a confusión:
 *  1. Los montos son pesos enteros: 350000 son $350.000, sin decimales.
 *  2. La retención no la calcula el CRM: es la que el estudio copia de su boleta. Si un
 *     honorario no la tiene declarada, el servidor lo advierte y acá se muestra tal cual.
 *  3. El panel no borra: da de alta y lista. Un registro mal cargado se rectifica con otro
 *     registro y queda en la bitácora (el CRM no elimina plata ya rendida).
 *
 * El botón de la cuenta imprimible es un enlace común a la API: el HTML viaja con la sesión
 * por cookie, así que se abre en otra pestaña y se imprime sin tokens en JavaScript.
 */
import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Grid2 as Grid,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useNotify, usePermissions } from "react-admin";

import { enviar, obtener } from "../api/cliente";
import type { Causa, CuentaDividendos } from "../tipos";
import { clp, fechaLegible } from "../tipos";
import { TablaSimple } from "./Inicio";

const HONORARIO_VACIO = {
  modalidad: "fijo",
  monto_pactado: "",
  descripcion: "",
  fecha: "",
  monto_bruto: "",
  retencion_sii: "",
};

const GASTO_VACIO = { concepto: "", monto: "", fecha: "", comprobante: "", pagado_por_estudio: "si" };
const PAGO_VACIO = { monto: "", fecha: "", medio: "transferencia", referencia: "", honorario_id: "", nota: "" };

const numeroOTexto = (valor: string): number | null => (valor.trim() ? Number(valor) : null);

export function Honorarios() {
  const notificar = useNotify();
  const { permissions } = usePermissions<string[]>();
  const permisos = permissions ?? [];
  const puedeRegistrarHonorarios = permisos.includes("honorario.editar");
  const puedeRegistrarGastos = permisos.includes("gasto.editar");

  const [causas, setCausas] = useState<Causa[]>([]);
  const [causaId, setCausaId] = useState("");
  const [cuenta, setCuenta] = useState<CuentaDividendos | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState<string | null>(null);

  const [honorario, setHonorario] = useState({ ...HONORARIO_VACIO });
  const [gasto, setGasto] = useState({ ...GASTO_VACIO });
  const [pago, setPago] = useState({ ...PAGO_VACIO });

  useEffect(() => {
    obtener<Causa[]>("/api/causas")
      .then((filas) => {
        setCausas(filas);
        if (filas.length) setCausaId((actual) => actual || String(filas[0].id));
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  const cargarCuenta = useCallback(async (id: string) => {
    if (!id) {
      setCuenta(null);
      return;
    }
    setCuenta(await obtener<CuentaDividendos>(`/api/cuenta?causa=${id}`));
  }, []);

  useEffect(() => {
    cargarCuenta(causaId).catch((e: Error) => setError(e.message));
  }, [causaId, cargarCuenta]);

  async function registrar(nombre: string, ruta: string, cuerpo: unknown, resumen: (r: Record<string, unknown>) => string) {
    setOcupado(nombre);
    try {
      const respuesta = await enviar<Record<string, unknown>>(ruta, cuerpo);
      notificar(resumen(respuesta), { type: respuesta.aviso ? "warning" : "success" });
      await cargarCuenta(causaId);
    } catch (e) {
      notificar((e as Error).message, { type: "error" });
    } finally {
      setOcupado(null);
    }
  }

  const totales = cuenta?.totales;
  const sinMostrar = cuenta?.honorarios.filter((h) => h.monto_liquido === null) ?? [];

  if (error) return <Alert severity="error">{error}</Alert>;

  return (
    <Box sx={{ p: 2 }}>
      <Typography variant="h5" gutterBottom>
        Honorarios y cuenta de dividendos
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Los montos son pesos enteros (350000 son $350.000). El CRM no calcula la retención —la copia el estudio
        de su boleta— y no emite boletas: esta cuenta es interna y no es un documento tributario.
      </Typography>

      <Stack direction="row" spacing={2} sx={{ mb: 2 }} alignItems="center" flexWrap="wrap" useFlexGap>
        <TextField
          select
          label="Causa"
          value={causaId}
          onChange={(e) => setCausaId(e.target.value)}
          sx={{ minWidth: 340 }}
        >
          {causas.map((causa) => (
            <MenuItem key={causa.id} value={String(causa.id)}>
              {causa.caratula}
            </MenuItem>
          ))}
        </TextField>
        {causaId && (
          <Button
            component="a"
            href={`/api/cuenta?causa=${causaId}&formato=html`}
            target="_blank"
            rel="noopener"
            variant="outlined"
          >
            abrir la cuenta imprimible
          </Button>
        )}
      </Stack>

      {!causaId && (
        <Alert severity="info">
          Todavía no hay causas en el estudio: la cuenta de dividendos se arma por causa, así que primero hay que
          abrir una.
        </Alert>
      )}

      {cuenta && (
        <Grid container spacing={2}>
          <Grid size={{ xs: 12, md: 6 }}>
            <Card>
              <CardContent>
                <Typography variant="h6" gutterBottom>
                  Cliente
                </Typography>
                <Typography variant="body2">
                  <strong>{cuenta.cliente?.nombre ?? "sin cliente asignado a la causa"}</strong>
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  RUT {cuenta.cliente?.rut ?? "s/informar"} · {cuenta.cliente?.direccion ?? "domicilio s/informar"}
                </Typography>
              </CardContent>
            </Card>
          </Grid>
          <Grid size={{ xs: 12, md: 6 }}>
            <Card>
              <CardContent>
                <Typography variant="h6" gutterBottom>
                  Causa
                </Typography>
                <Typography variant="body2">
                  <strong>{cuenta.causa.caratula}</strong>
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  ROL/RIT {cuenta.causa.rol_rit ?? "s/informar"} · {cuenta.causa.tribunal ?? "tribunal s/informar"}
                </Typography>
              </CardContent>
            </Card>
          </Grid>

          <Grid size={{ xs: 12 }}>
            <Card>
              <CardContent>
                <Typography variant="h6" gutterBottom>
                  Honorarios pactados
                </Typography>
                <TablaSimple
                  cabeceras={["#", "Fecha", "Modalidad", "Qué se pactó", "Pactado", "Líquido", "Pagado", "Estado"]}
                  filas={cuenta.honorarios.map((h) => [
                    String(h.id),
                    fechaLegible(h.fecha),
                    h.modalidad,
                    h.descripcion ?? "—",
                    clp(h.monto_pactado),
                    clp(h.monto_liquido),
                    clp(h.monto_pagado),
                    h.estado_pago,
                  ])}
                />
              </CardContent>
            </Card>
          </Grid>

          <Grid size={{ xs: 12, md: 6 }}>
            <Card>
              <CardContent>
                <Typography variant="h6" gutterBottom>
                  Gastos de la causa
                </Typography>
                <TablaSimple
                  cabeceras={["Fecha", "Concepto", "Comprobante", "Monto", "Al cliente"]}
                  filas={cuenta.gastos.map((g) => [
                    fechaLegible(g.fecha),
                    g.concepto,
                    g.comprobante ?? "sin respaldo",
                    clp(g.monto),
                    Number(g.pagado_por_estudio) ? "sí" : "no (lo pagó el cliente)",
                  ])}
                />
              </CardContent>
            </Card>
          </Grid>

          <Grid size={{ xs: 12, md: 6 }}>
            <Card>
              <CardContent>
                <Typography variant="h6" gutterBottom>
                  Abonos recibidos
                </Typography>
                <TablaSimple
                  cabeceras={["Fecha", "Monto", "Medio", "Honorario", "Referencia", "Registró"]}
                  filas={cuenta.pagos.map((p) => [
                    fechaLegible(p.fecha),
                    clp(p.monto),
                    p.medio,
                    p.honorario_id === null ? "sin imputar" : `#${p.honorario_id}`,
                    p.referencia ?? "—",
                    p.registrado_por_nombre ?? "—",
                  ])}
                />
              </CardContent>
            </Card>
          </Grid>

          <Grid size={{ xs: 12 }}>
            <Card>
              <CardContent>
                <Typography variant="h6" gutterBottom>
                  Cuenta
                </Typography>
                <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 1 }}>
                  <Chip size="small" variant="outlined" label={`pactado: ${clp(totales?.honorarios_pactado)}`} />
                  <Chip size="small" variant="outlined" label={`líquido: ${clp(totales?.honorarios_liquido)}`} />
                  <Chip size="small" variant="outlined" label={`gastos al cliente: ${clp(totales?.gastos_por_cuenta_del_cliente)}`} />
                  <Chip size="small" variant="outlined" color="success" label={`pagos: ${clp(totales?.pagos)}`} />
                </Stack>
                <Typography variant="h6">
                  Saldo {Number(totales?.saldo ?? 0) < 0 ? "a favor del cliente" : "por pagar"}:{" "}
                  <span style={{ color: Number(totales?.saldo ?? 0) < 0 ? "#a3121f" : undefined }}>
                    {clp(totales?.saldo)}
                  </span>
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  saldo = honorarios líquidos + gastos por cuenta del cliente − pagos recibidos
                </Typography>
                {sinMostrar.length > 0 && (
                  <Alert severity="warning" sx={{ mt: 1 }}>
                    {sinMostrar.length === 1 ? "El honorario" : "Los honorarios"}{" "}
                    {sinMostrar.map((h) => `#${h.id}`).join(", ")} {sinMostrar.length === 1 ? "no tiene" : "no tienen"}{" "}
                    monto líquido declarado (falta el bruto de la boleta): no {sinMostrar.length === 1 ? "entra" : "entran"} en
                    el saldo, que queda subestimado.
                  </Alert>
                )}
                {(cuenta.advertencias ?? []).map((aviso) => (
                  <Alert severity="info" key={aviso} sx={{ mt: 1 }}>
                    {aviso}
                  </Alert>
                ))}
              </CardContent>
            </Card>
          </Grid>

          {(puedeRegistrarHonorarios || puedeRegistrarGastos) && (
            <Grid size={{ xs: 12 }}>
              <Card>
                <CardContent>
                  <Typography variant="h6" gutterBottom>
                    Registrar
                  </Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                    El panel sólo da de alta y lista: no borra. Si un monto quedó mal cargado, se registra la
                    corrección y queda en la bitácora con el nombre de quien la escribió.
                  </Typography>
                  <Grid container spacing={2}>
                    {puedeRegistrarHonorarios && (
                      <Grid size={{ xs: 12, md: 4 }}>
                        <Typography variant="subtitle2" gutterBottom>
                          Honorario
                        </Typography>
                        <Stack spacing={1}>
                          <TextField
                            select
                            label="Modalidad"
                            value={honorario.modalidad}
                            onChange={(e) => setHonorario({ ...honorario, modalidad: e.target.value })}
                          >
                            <MenuItem value="fijo">fijo</MenuItem>
                            <MenuItem value="hora">por hora</MenuItem>
                            <MenuItem value="cuota_litis">cuota litis</MenuItem>
                            <MenuItem value="mixto">mixto</MenuItem>
                          </TextField>
                          <TextField
                            label="Monto pactado (CLP)"
                            type="number"
                            value={honorario.monto_pactado}
                            onChange={(e) => setHonorario({ ...honorario, monto_pactado: e.target.value })}
                            helperText="350000 = $350.000"
                          />
                          <TextField
                            label="Bruto de la boleta (CLP)"
                            type="number"
                            value={honorario.monto_bruto}
                            onChange={(e) => setHonorario({ ...honorario, monto_bruto: e.target.value })}
                          />
                          <TextField
                            label="Retención (CLP)"
                            type="number"
                            value={honorario.retencion_sii}
                            onChange={(e) => setHonorario({ ...honorario, retencion_sii: e.target.value })}
                            helperText="la copias de tu boleta; el CRM no la calcula"
                          />
                          <TextField
                            label="Qué se pactó"
                            value={honorario.descripcion}
                            onChange={(e) => setHonorario({ ...honorario, descripcion: e.target.value })}
                            helperText="Demanda civil, primera instancia"
                          />
                          <Button
                            variant="contained"
                            disabled={ocupado === "honorario" || (!honorario.monto_pactado && !honorario.monto_bruto)}
                            onClick={() =>
                              registrar("honorario", "/api/honorarios", {
                                causa_id: Number(causaId),
                                modalidad: honorario.modalidad,
                                monto_pactado: numeroOTexto(honorario.monto_pactado),
                                monto_bruto: numeroOTexto(honorario.monto_bruto),
                                retencion_sii: numeroOTexto(honorario.retencion_sii),
                                descripcion: honorario.descripcion || null,
                                fecha: honorario.fecha || null,
                              }, (r) => `Honorario #${r.id} registrado${r.aviso ? ` — ${r.aviso}` : ""}`)
                            }
                          >
                            registrar honorario
                          </Button>
                        </Stack>
                      </Grid>
                    )}

                    {puedeRegistrarGastos && (
                      <Grid size={{ xs: 12, md: 4 }}>
                        <Typography variant="subtitle2" gutterBottom>
                          Gasto
                        </Typography>
                        <Stack spacing={1}>
                          <TextField
                            label="Concepto"
                            value={gasto.concepto}
                            onChange={(e) => setGasto({ ...gasto, concepto: e.target.value })}
                            helperText="Notaría, receptor, tasas, peritaje…"
                          />
                          <TextField
                            label="Monto (CLP)"
                            type="number"
                            value={gasto.monto}
                            onChange={(e) => setGasto({ ...gasto, monto: e.target.value })}
                          />
                          <TextField
                            label="Comprobante"
                            value={gasto.comprobante}
                            onChange={(e) => setGasto({ ...gasto, comprobante: e.target.value })}
                            helperText="boleta, factura o recibo"
                          />
                          <TextField
                            select
                            label="¿Quién lo pagó?"
                            value={gasto.pagado_por_estudio}
                            onChange={(e) => setGasto({ ...gasto, pagado_por_estudio: e.target.value })}
                          >
                            <MenuItem value="si">lo adelantó el estudio</MenuItem>
                            <MenuItem value="no">lo pagó el cliente</MenuItem>
                          </TextField>
                          <Button
                            variant="contained"
                            disabled={ocupado === "gasto" || !gasto.concepto.trim() || !gasto.monto}
                            onClick={() =>
                              registrar("gasto", "/api/gastos", {
                                causa_id: Number(causaId),
                                concepto: gasto.concepto,
                                monto: Number(gasto.monto),
                                comprobante: gasto.comprobante || null,
                                fecha: gasto.fecha || null,
                                pagado_por_estudio: gasto.pagado_por_estudio === "si",
                              }, (r) => `Gasto #${r.id} registrado${r.aviso ? ` — ${r.aviso}` : ""}`)
                            }
                          >
                            registrar gasto
                          </Button>
                        </Stack>
                      </Grid>
                    )}

                    {puedeRegistrarHonorarios && (
                      <Grid size={{ xs: 12, md: 4 }}>
                        <Typography variant="subtitle2" gutterBottom>
                          Abono del cliente
                        </Typography>
                        <Stack spacing={1}>
                          <TextField
                            label="Monto (CLP)"
                            type="number"
                            value={pago.monto}
                            onChange={(e) => setPago({ ...pago, monto: e.target.value })}
                            helperText="el del comprobante; no lo redondees"
                          />
                          <TextField
                            select
                            label="Medio"
                            value={pago.medio}
                            onChange={(e) => setPago({ ...pago, medio: e.target.value })}
                          >
                            <MenuItem value="transferencia">transferencia</MenuItem>
                            <MenuItem value="efectivo">efectivo</MenuItem>
                            <MenuItem value="cheque">cheque</MenuItem>
                            <MenuItem value="tarjeta">tarjeta</MenuItem>
                            <MenuItem value="otro">otro</MenuItem>
                          </TextField>
                          <TextField
                            label="Referencia"
                            value={pago.referencia}
                            onChange={(e) => setPago({ ...pago, referencia: e.target.value })}
                            helperText="n° de transferencia o cheque"
                          />
                          <TextField
                            select
                            label="Imputar al honorario"
                            value={pago.honorario_id}
                            onChange={(e) => setPago({ ...pago, honorario_id: e.target.value })}
                          >
                            <MenuItem value="">sin imputar</MenuItem>
                            {(cuenta?.honorarios ?? []).map((h) => (
                              <MenuItem key={h.id} value={String(h.id)}>
                                #{h.id} · {h.modalidad} · {clp(h.monto_liquido)} ({h.estado_pago})
                              </MenuItem>
                            ))}
                          </TextField>
                          <Button
                            variant="contained"
                            disabled={ocupado === "pago" || !pago.monto}
                            onClick={() =>
                              registrar("pago", "/api/pagos", {
                                causa_id: Number(causaId),
                                monto: Number(pago.monto),
                                medio: pago.medio,
                                referencia: pago.referencia || null,
                                nota: pago.nota || null,
                                honorario_id: pago.honorario_id ? Number(pago.honorario_id) : null,
                              }, (r) => `Abono #${r.id} registrado`)
                            }
                          >
                            registrar abono
                          </Button>
                          <Typography variant="caption" color="text.secondary">
                            El abono baja lo que el cliente debe y queda en la bitácora con tu nombre: confirmá el
                            monto y la fecha antes de guardarlo.
                          </Typography>
                        </Stack>
                      </Grid>
                    )}
                  </Grid>
                </CardContent>
              </Card>
            </Grid>
          )}
        </Grid>
      )}
    </Box>
  );
}
