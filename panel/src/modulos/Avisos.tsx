/**
 * Avisos: el módulo que decide si el estudio se entera a tiempo de sus plazos.
 *
 * Tres cosas juntas en una pantalla, en el orden en que importan:
 *  1. Si el circuito está sano (qué canal está listo y cuál falta).
 *  2. Cómo se configura (correo y SMS), con la clave que nunca se devuelve: el campo vacío
 *     conserva la que estaba guardada.
 *  3. Qué pasó (la cola, con el error de lo que falló) y los botones para probar y despachar.
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import { useNotify } from "react-admin";

import { enviar, obtener } from "../api/cliente";
import type { AvisoEnCola, CanalDeAviso } from "../tipos";
import { sellosLegibles } from "../tipos";

interface Respuesta {
  canales: CanalDeAviso[];
  ajustes: { dias_de_aviso: number; canales_por_defecto: string[]; avisar_asignaciones: boolean };
  formulario: { email: Record<string, string | number>; sms: Record<string, string | number> };
  estado: { conteos: Record<string, number>; ultimas_fallas: { canal: string; destino: string; ultimo_error: string }[] };
  cola: AvisoEnCola[];
  archivo: string;
}

export function Avisos() {
  const notificar = useNotify();
  const [datos, setDatos] = useState<Respuesta | null>(null);
  const [correo, setCorreo] = useState<Record<string, string>>({});
  const [sms, setSms] = useState<Record<string, string>>({});
  const [ajustes, setAjustes] = useState({ dias: "3", canales: "email", asignaciones: "si" });
  const [ocupado, setOcupado] = useState<string | null>(null);

  const cargar = useCallback(async () => {
    const respuesta = await obtener<Respuesta>("/api/avisos");
    setDatos(respuesta);
    setCorreo(
      Object.fromEntries(Object.entries(respuesta.formulario.email ?? {}).map(([k, v]) => [k, String(v ?? "")])),
    );
    setSms(Object.fromEntries(Object.entries(respuesta.formulario.sms ?? {}).map(([k, v]) => [k, String(v ?? "")])));
    setAjustes({
      dias: String(respuesta.ajustes.dias_de_aviso),
      canales: respuesta.ajustes.canales_por_defecto.join(","),
      asignaciones: respuesta.ajustes.avisar_asignaciones ? "si" : "no",
    });
  }, []);

  useEffect(() => {
    cargar().catch((error: Error) => notificar(error.message, { type: "error" }));
  }, [cargar, notificar]);

  async function guardar(seccion: string, cuerpo: unknown) {
    setOcupado(seccion);
    try {
      await enviar("/api/avisos/config", cuerpo);
      notificar("Configuración guardada", { type: "success" });
      await cargar();
    } catch (error) {
      notificar((error as Error).message, { type: "error" });
    } finally {
      setOcupado(null);
    }
  }

  async function probar(canal: string) {
    setOcupado(`probar-${canal}`);
    try {
      const resultado = await enviar<{ enviado: boolean; destino: string; error?: string | null }>("/api/avisos/probar", {
        canal,
      });
      if (resultado.enviado) notificar(`Salió la prueba a ${resultado.destino}`, { type: "success" });
      else notificar(`No salió: ${resultado.error ?? "sin detalle"}`, { type: "error" });
      await cargar();
    } catch (error) {
      notificar((error as Error).message, { type: "error" });
    } finally {
      setOcupado(null);
    }
  }

  async function accion(nombre: string, ruta: string, resumen: (r: Record<string, unknown>) => string) {
    setOcupado(nombre);
    try {
      const resultado = await enviar<Record<string, unknown>>(ruta, {});
      notificar(resumen(resultado), { type: "info" });
      await cargar();
    } catch (error) {
      notificar((error as Error).message, { type: "error" });
    } finally {
      setOcupado(null);
    }
  }

  const conteos = datos?.estado?.conteos ?? {};

  return (
    <Box sx={{ p: 2 }}>
      <Typography variant="h5" gutterBottom>
        Avisos por correo y SMS
      </Typography>

      {datos && (
        <Card sx={{ mb: 2 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              Cómo está el circuito
            </Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              {datos.canales.map((canal) => (
                <Chip
                  key={canal.canal}
                  color={canal.listo ? "success" : "error"}
                  variant="outlined"
                  label={`${canal.canal}: ${canal.detalle}`}
                />
              ))}
            </Stack>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              Archivo de configuración: <code>{datos.archivo}</code> (permisos 600; las claves no se muestran nunca)
            </Typography>
          </CardContent>
        </Card>
      )}

      <Grid container spacing={2}>
        <Grid size={{ xs: 12, md: 6 }}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Correo (SMTP)
              </Typography>
              <Stack spacing={2}>
                <TextField
                  label="Servidor"
                  value={correo.host ?? ""}
                  onChange={(e) => setCorreo({ ...correo, host: e.target.value })}
                  helperText="smtp.estudio.cl"
                />
                <TextField
                  label="Puerto"
                  type="number"
                  value={correo.puerto ?? ""}
                  onChange={(e) => setCorreo({ ...correo, puerto: e.target.value })}
                />
                <TextField
                  label="Usuario"
                  value={correo.usuario ?? ""}
                  onChange={(e) => setCorreo({ ...correo, usuario: e.target.value })}
                  helperText="avisos@estudio.cl"
                />
                <TextField
                  label="Clave"
                  type="password"
                  value={correo.clave ?? ""}
                  onChange={(e) => setCorreo({ ...correo, clave: e.target.value })}
                  helperText="clave de aplicación; si la dejás vacía se conserva la guardada"
                />
                <TextField
                  label="Remitente"
                  value={correo.de ?? ""}
                  onChange={(e) => setCorreo({ ...correo, de: e.target.value })}
                  helperText="Estudio Soto <avisos@estudio.cl>"
                />
                <TextField
                  select
                  label="Cifrado"
                  value={correo.seguridad ?? "starttls"}
                  onChange={(e) => setCorreo({ ...correo, seguridad: e.target.value })}
                >
                  <MenuItem value="starttls">STARTTLS (recomendado)</MenuItem>
                  <MenuItem value="ninguna">sin cifrado (sólo relay local)</MenuItem>
                </TextField>
                <Button
                  variant="contained"
                  disabled={ocupado === "correo"}
                  onClick={() => guardar("correo", { email: correo })}
                >
                  Guardar correo
                </Button>
              </Stack>
            </CardContent>
          </Card>
        </Grid>

        <Grid size={{ xs: 12, md: 6 }}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                SMS
              </Typography>
              <Stack spacing={2}>
                <TextField
                  select
                  label="Proveedor"
                  value={sms.proveedor ?? "consola"}
                  onChange={(e) => setSms({ ...sms, proveedor: e.target.value })}
                >
                  <MenuItem value="consola">consola (prueba, no envía)</MenuItem>
                  <MenuItem value="twilio">Twilio</MenuItem>
                  <MenuItem value="webhook">webhook propio</MenuItem>
                </TextField>
                <TextField
                  label="Cuenta (Twilio)"
                  value={sms.cuenta ?? ""}
                  onChange={(e) => setSms({ ...sms, cuenta: e.target.value })}
                />
                <TextField
                  label="Token"
                  type="password"
                  value={sms.token ?? ""}
                  onChange={(e) => setSms({ ...sms, token: e.target.value })}
                  helperText="si lo dejás vacío se conserva el guardado"
                />
                <TextField
                  label="URL del webhook"
                  value={sms.webhook ?? ""}
                  onChange={(e) => setSms({ ...sms, webhook: e.target.value })}
                />
                <TextField
                  label="Número o nombre de origen"
                  value={sms.de ?? ""}
                  onChange={(e) => setSms({ ...sms, de: e.target.value })}
                />
                <Button variant="contained" disabled={ocupado === "sms"} onClick={() => guardar("sms", { sms })}>
                  Guardar SMS
                </Button>
              </Stack>
            </CardContent>
          </Card>
        </Grid>

        <Grid size={{ xs: 12, md: 6 }}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Cuándo avisar
              </Typography>
              <Stack spacing={2}>
                <TextField
                  label="Días de anticipación"
                  type="number"
                  value={ajustes.dias}
                  onChange={(e) => setAjustes({ ...ajustes, dias: e.target.value })}
                  helperText="con cuántos días antes empieza a avisar un plazo"
                />
                <TextField
                  label="Canales por defecto"
                  value={ajustes.canales}
                  onChange={(e) => setAjustes({ ...ajustes, canales: e.target.value })}
                  helperText="email, sms, consola — separados por coma"
                />
                <TextField
                  select
                  label="Avisar cuando le asignan un plazo o audiencia a alguien"
                  value={ajustes.asignaciones}
                  onChange={(e) => setAjustes({ ...ajustes, asignaciones: e.target.value })}
                >
                  <MenuItem value="si">sí</MenuItem>
                  <MenuItem value="no">no</MenuItem>
                </TextField>
                <Button
                  variant="contained"
                  disabled={ocupado === "ajustes"}
                  onClick={() =>
                    guardar("ajustes", {
                      dias_de_aviso: Number(ajustes.dias),
                      canales_por_defecto: ajustes.canales.split(",").map((c) => c.trim()).filter(Boolean),
                      avisar_asignaciones: ajustes.asignaciones === "si",
                    })
                  }
                >
                  Guardar ajustes
                </Button>
              </Stack>
            </CardContent>
          </Card>
        </Grid>

        <Grid size={{ xs: 12, md: 6 }}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Probar y despachar
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                La prueba sale ahora mismo, a tu propio correo o teléfono.
              </Typography>
              <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                <Button variant="outlined" disabled={ocupado === "probar-email"} onClick={() => probar("email")}>
                  probar correo
                </Button>
                <Button variant="outlined" disabled={ocupado === "probar-sms"} onClick={() => probar("sms")}>
                  probar SMS
                </Button>
                <Button variant="outlined" disabled={ocupado === "probar-consola"} onClick={() => probar("consola")}>
                  probar sin enviar
                </Button>
              </Stack>
              <Stack direction="row" spacing={1} sx={{ mt: 2 }} flexWrap="wrap" useFlexGap>
                <Button
                  variant="contained"
                  disabled={ocupado === "generar"}
                  onClick={() =>
                    accion("generar", "/api/avisos/generar", (r) =>
                      `Revisé ${r.plazos_revisados} plazo(s) y ${r.audiencias_revisadas} audiencia(s): encolados ${r.encoladas}, ya estaban ${r.repetidas}` +
                      (Number(r.sin_destino) ? `, sin destino ${r.sin_destino}` : ""),
                    )
                  }
                >
                  generar recordatorios
                </Button>
                <Button
                  variant="contained"
                  disabled={ocupado === "despachar"}
                  onClick={() =>
                    accion(
                      "despachar",
                      "/api/avisos/despachar",
                      (r) => `Revisados ${r.revisadas} · enviados ${r.enviadas} · fallidos ${r.fallidas}`,
                    )
                  }
                >
                  despachar la cola
                </Button>
              </Stack>
              <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
                <Chip size="small" label={`en cola: ${conteos.pendiente ?? 0}`} />
                <Chip size="small" color="success" variant="outlined" label={`enviados: ${conteos.enviada ?? 0}`} />
                {(conteos.fallida ?? 0) > 0 && (
                  <Chip size="small" color="error" variant="outlined" label={`fallidos: ${conteos.fallida}`} />
                )}
              </Stack>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      <Card sx={{ mt: 2 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            Cola de avisos (últimos 25)
          </Typography>
          {datos?.cola?.length ? (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>#</TableCell>
                  <TableCell>Canal</TableCell>
                  <TableCell>Destino</TableCell>
                  <TableCell>Asunto</TableCell>
                  <TableCell>Estado</TableCell>
                  <TableCell>Intentos</TableCell>
                  <TableCell>Creado</TableCell>
                  <TableCell>Error</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {datos.cola.map((aviso) => (
                  <TableRow key={aviso.id}>
                    <TableCell>{aviso.id}</TableCell>
                    <TableCell>{aviso.canal}</TableCell>
                    <TableCell>{aviso.destino}</TableCell>
                    <TableCell>{aviso.asunto}</TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        variant="outlined"
                        color={aviso.estado === "enviada" ? "success" : aviso.estado === "fallida" ? "error" : "default"}
                        label={aviso.estado}
                      />
                    </TableCell>
                    <TableCell>{aviso.intentos}</TableCell>
                    <TableCell>{sellosLegibles(aviso.creada_en)}</TableCell>
                    <TableCell>{aviso.ultimo_error ?? ""}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <Typography variant="body2" color="text.secondary">
              no hay avisos en la cola.
            </Typography>
          )}
        </CardContent>
      </Card>

      {datos?.estado?.ultimas_fallas?.length ? (
        <Alert severity="error" sx={{ mt: 2 }}>
          {datos.estado.ultimas_fallas.map((f) => (
            <div key={`${f.canal}-${f.destino}`}>
              falló {f.canal} a {f.destino}: {f.ultimo_error}
            </div>
          ))}
        </Alert>
      ) : null}
    </Box>
  );
}
