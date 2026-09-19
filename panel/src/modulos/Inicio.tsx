/**
 * Inicio: cómo está el estudio, en una pantalla.
 *
 * Es el tablero del socio y también lo primero que ve cualquiera al entrar: causas y carga
 * de trabajo, quién no tiene segundo factor, y si hay avisos esperando o algo que falló.
 * Cada bloque lleva a su módulo: informar sin un camino para actuar no sirve.
 */
import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Grid2 as Grid,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { usePermissions, useRedirect } from "react-admin";

import { obtener } from "../api/cliente";
import type { CuentaSeguridad } from "../tipos";

interface Panel {
  causas_por_estado: { estado_procesal: string; total: number }[];
  carga_por_abogado: { nombre: string; rol: string; causas: number }[];
  plazos_pendientes: number;
}

interface InformeSeguridad {
  intentos: { total: number; ventana_minutos: number; sospechoso: boolean; umbral: number };
  cuentas: CuentaSeguridad[];
}

interface InformeAvisos {
  estado: { conteos: Record<string, number>; ultimas_fallas: { canal: string; destino: string; ultimo_error: string }[] };
  ajustes: { dias_de_aviso: number; canales_por_defecto: string[] };
}

export function Inicio() {
  const { permissions } = usePermissions<string[]>();
  const permisos = permissions ?? [];
  const ir = useRedirect();

  const [panel, setPanel] = useState<Panel | null>(null);
  const [seguridad, setSeguridad] = useState<InformeSeguridad | null>(null);
  const [avisos, setAvisos] = useState<InformeAvisos | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const tareas: Promise<void>[] = [
      obtener<Panel>("/api/panel").then(setPanel).catch((e: Error) => setError(e.message)),
    ];
    if (permisos.includes("usuario.gestionar")) {
      tareas.push(obtener<InformeSeguridad>("/api/seguridad").then(setSeguridad).catch(() => undefined));
    }
    if (permisos.includes("aviso.gestionar")) {
      tareas.push(obtener<InformeAvisos>("/api/avisos").then(setAvisos).catch(() => undefined));
    }
    void Promise.all(tareas);
  }, []);

  if (error) return <Alert severity="error">{error}</Alert>;

  const sinSegundoFactor = (seguridad?.cuentas ?? []).filter((c) => c.esperado && !c.totp_activo);
  const bloqueadas = (seguridad?.cuentas ?? []).filter((c) => c.bloqueo?.bloqueada);
  const conteos = avisos?.estado?.conteos ?? {};

  return (
    <Box sx={{ p: 2 }}>
      <Typography variant="h5" gutterBottom>
        Cómo está el estudio
      </Typography>

      <Grid container spacing={2}>
        <Grid size={{ xs: 12, md: 6 }}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Causas por estado
              </Typography>
              <TablaSimple
                cabeceras={["Estado", "Causas"]}
                filas={(panel?.causas_por_estado ?? []).map((f) => [f.estado_procesal, String(f.total)])}
              />
              <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                Plazos pendientes: <strong>{panel?.plazos_pendientes ?? "—"}</strong>
              </Typography>
              <Button size="small" sx={{ mt: 1 }} onClick={() => ir("/plazos")}>
                ver plazos
              </Button>
            </CardContent>
          </Card>
        </Grid>

        <Grid size={{ xs: 12, md: 6 }}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Carga por abogado
              </Typography>
              <TablaSimple
                cabeceras={["Abogado", "Rol", "Causas"]}
                filas={(panel?.carga_por_abogado ?? []).map((f) => [f.nombre, f.rol, String(f.causas)])}
              />
            </CardContent>
          </Card>
        </Grid>

        {seguridad && (
          <Grid size={{ xs: 12, md: 6 }}>
            <Card>
              <CardContent>
                <Typography variant="h6" gutterBottom>
                  Seguridad
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  Intentos fallidos en {seguridad.intentos.ventana_minutos} minutos:{" "}
                  <strong>{seguridad.intentos.total}</strong>
                </Typography>
                {seguridad.intentos.sospechoso && (
                  <Alert severity="warning" sx={{ mt: 1 }}>
                    Pasó el umbral de {seguridad.intentos.umbral} intentos: revisá de dónde vienen.
                  </Alert>
                )}
                {sinSegundoFactor.length ? (
                  <Alert severity="warning" sx={{ mt: 1 }}>
                    Sin segundo factor: {sinSegundoFactor.map((c) => `${c.nombre} (${c.rol})`).join(", ")}
                  </Alert>
                ) : (
                  <Alert severity="success" sx={{ mt: 1 }}>
                    Socios y administradores con segundo factor: sí.
                  </Alert>
                )}
                {bloqueadas.length > 0 && (
                  <Alert severity="error" sx={{ mt: 1 }}>
                    Cuentas bloqueadas ahora: {bloqueadas.map((c) => c.email).join(", ")}
                  </Alert>
                )}
                <Button size="small" sx={{ mt: 1 }} onClick={() => ir("/seguridad")}>
                  ver seguridad
                </Button>
              </CardContent>
            </Card>
          </Grid>
        )}

        {avisos && (
          <Grid size={{ xs: 12, md: 6 }}>
            <Card>
              <CardContent>
                <Typography variant="h6" gutterBottom>
                  Avisos
                </Typography>
                <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
                  <Chip size="small" label={`en cola: ${conteos.pendiente ?? 0}`} />
                  <Chip size="small" color="success" variant="outlined" label={`enviados: ${conteos.enviada ?? 0}`} />
                  {(conteos.fallida ?? 0) > 0 && (
                    <Chip size="small" color="error" variant="outlined" label={`fallidos: ${conteos.fallida}`} />
                  )}
                </Stack>
                <Typography variant="body2" color="text.secondary">
                  Avisa {avisos.ajustes.dias_de_aviso} días antes, por {avisos.ajustes.canales_por_defecto.join(", ")}
                </Typography>
                {avisos.estado.ultimas_fallas.length > 0 && (
                  <Alert severity="error" sx={{ mt: 1 }}>
                    Último problema: {avisos.estado.ultimas_fallas[0].canal} a{" "}
                    {avisos.estado.ultimas_fallas[0].destino} — {avisos.estado.ultimas_fallas[0].ultimo_error}
                  </Alert>
                )}
                <Button size="small" sx={{ mt: 1 }} onClick={() => ir("/avisos")}>
                  ver avisos
                </Button>
              </CardContent>
            </Card>
          </Grid>
        )}
      </Grid>
    </Box>
  );
}

export function TablaSimple({ cabeceras, filas }: { cabeceras: string[]; filas: (string | number)[][] }) {
  if (!filas.length) {
    return (
      <Typography variant="body2" color="text.secondary">
        nada que mostrar.
      </Typography>
    );
  }
  return (
    <Table size="small">
      <TableHead>
        <TableRow>
          {cabeceras.map((c) => (
            <TableCell key={c}>{c}</TableCell>
          ))}
        </TableRow>
      </TableHead>
      <TableBody>
        {filas.map((fila, i) => (
          <TableRow key={i}>
            {fila.map((celda, j) => (
              <TableCell key={j}>{celda}</TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
