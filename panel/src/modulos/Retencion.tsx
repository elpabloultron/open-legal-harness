/**
 * Retención: cuánto se conserva cada dato, y qué corresponde hacer.
 *
 * El orden de la pantalla es la regla del módulo: primero se declara la política, después se
 * mira qué expedientes cumplieron su plazo, y sólo al final se ejecuta — y por defecto se
 * simula. El sistema no inventa obligaciones de borrar: lo que trae son sugerencias con su
 * anclaje, y el estudio decide.
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
import { fechaLegible } from "../tipos";

interface Cumplido {
  cliente_id: number;
  nombre: string;
  causas: number;
  ultimo_movimiento: string | null;
  dias_sin_movimiento: number;
}

interface Informe {
  politica: Record<string, { meses: number; motivo: string; origen: string; actualizado_en: string | null }>;
  corte_meses?: number;
  cumplidos: Cumplido[];
  total: number;
  conservado: { tabla: string; filas: number; motivo: string }[];
  tipos: string[];
  aviso?: string;
}

export function Retencion() {
  const notificar = useNotify();
  const [informe, setInforme] = useState<Informe | null>(null);
  const [politica, setPolitica] = useState({ tipo: "datos_de_persona", meses: "60", motivo: "" });
  const [ejecucion, setEjecucion] = useState({ motivo: "", modo: "simular", confirmar: "" });
  const [ocupado, setOcupado] = useState(false);

  const cargar = useCallback(async () => {
    setInforme(await obtener<Informe>("/api/retencion"));
  }, []);

  useEffect(() => {
    cargar().catch((error: Error) => notificar(error.message, { type: "error" }));
  }, [cargar, notificar]);

  async function definir() {
    setOcupado(true);
    try {
      await enviar("/api/retencion", { tipo: politica.tipo, meses: Number(politica.meses), motivo: politica.motivo });
      notificar("Plazo declarado", { type: "success" });
      await cargar();
    } catch (error) {
      notificar((error as Error).message, { type: "error" });
    } finally {
      setOcupado(false);
    }
  }

  async function ejecutar() {
    const simular = ejecucion.modo === "simular";
    if (!simular && ejecucion.confirmar.trim().toUpperCase() !== "ANONIMIZAR") {
      notificar("Para ejecutar de verdad hay que escribir ANONIMIZAR en la confirmación", { type: "warning" });
      return;
    }
    setOcupado(true);
    try {
      const resultado = await enviar<{ simulado: boolean; anonimizados: { nombre: string; hecho: boolean }[] }>(
        "/api/retencion/ejecutar",
        { motivo: ejecucion.motivo, simular },
      );
      const hechos = resultado.anonimizados ?? [];
      notificar(
        `${resultado.simulado ? "Simulación" : "Ejecutado"}: ` +
          (hechos.length
            ? hechos.map((h) => `${h.nombre}${h.hecho ? " (anonimizado)" : " (se anonimizaría)"}`).join(", ")
            : "no hay expedientes que hayan cumplido su plazo"),
        { type: resultado.simulado ? "info" : "success" },
      );
      await cargar();
    } catch (error) {
      notificar((error as Error).message, { type: "error" });
    } finally {
      setOcupado(false);
    }
  }

  const tipos = informe?.tipos ?? [];
  const politica_actual = informe?.politica ?? {};

  return (
    <Box sx={{ p: 2 }}>
      <Typography variant="h5" gutterBottom>
        Retención de datos
      </Typography>

      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            Cuánto se conserva cada dato
          </Typography>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Tipo de dato</TableCell>
                <TableCell>Plazo</TableCell>
                <TableCell>Motivo</TableCell>
                <TableCell>Origen</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {Object.entries(politica_actual).map(([tipo, fila]) => (
                <TableRow key={tipo}>
                  <TableCell>{tipo}</TableCell>
                  <TableCell>{fila.meses} mes(es)</TableCell>
                  <TableCell>{fila.motivo}</TableCell>
                  <TableCell>
                    {fila.origen === "declarada por el estudio" ? (
                      <Chip size="small" color="success" variant="outlined" label="declarado" />
                    ) : (
                      <Chip size="small" variant="outlined" label="sugerencia" />
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Grid container spacing={2}>
        <Grid size={{ xs: 12, md: 6 }}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Declarar un plazo
              </Typography>
              <Stack spacing={2}>
                <TextField
                  select
                  label="Tipo de dato"
                  value={politica.tipo}
                  onChange={(e) => setPolitica({ ...politica, tipo: e.target.value })}
                >
                  {(tipos.length ? tipos : ["datos_de_persona"]).map((tipo) => (
                    <MenuItem key={tipo} value={tipo}>
                      {tipo}
                    </MenuItem>
                  ))}
                </TextField>
                <TextField
                  label="Meses de conservación"
                  type="number"
                  value={politica.meses}
                  onChange={(e) => setPolitica({ ...politica, meses: e.target.value })}
                />
                <TextField
                  label="Motivo"
                  value={politica.motivo}
                  onChange={(e) => setPolitica({ ...politica, motivo: e.target.value })}
                  helperText="por qué se conserva ese tiempo (queda en la bitácora)"
                  multiline
                  rows={2}
                />
                <Button variant="contained" disabled={ocupado || !politica.motivo.trim()} onClick={definir}>
                  Guardar plazo
                </Button>
              </Stack>
            </CardContent>
          </Card>
        </Grid>

        <Grid size={{ xs: 12, md: 6 }}>
          <Card>
            <CardContent>
              <Typography variant="h6" gutterBottom>
                Qué corresponde hacer
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                Corte: datos sin movimiento desde hace <strong>{informe?.corte_meses ?? "—"}</strong> meses · cumplidos:{" "}
                <strong>{informe?.total ?? 0}</strong>
              </Typography>
              {(informe?.cumplidos ?? []).length ? (
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Cliente</TableCell>
                      <TableCell>Causas</TableCell>
                      <TableCell>Último movimiento</TableCell>
                      <TableCell>Días</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {(informe?.cumplidos ?? []).map((c) => (
                      <TableRow key={c.cliente_id}>
                        <TableCell>{c.nombre}</TableCell>
                        <TableCell>{c.causas}</TableCell>
                        <TableCell>{fechaLegible(c.ultimo_movimiento)}</TableCell>
                        <TableCell>{c.dias_sin_movimiento}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  Ningún expediente cumplió su plazo todavía.
                </Typography>
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      <Card sx={{ mt: 2 }}>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            Ejecutar
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            Se anonimizan los identificadores directos. Los honorarios, los gastos, la bitácora y las pruebas de
            tratamiento de IA se conservan: son los que permiten responder por lo que se hizo.
          </Typography>
          <Stack spacing={2} sx={{ maxWidth: 520 }}>
            <TextField
              label="Motivo"
              value={ejecucion.motivo}
              onChange={(e) => setEjecucion({ ...ejecucion, motivo: e.target.value })}
              multiline
              rows={2}
            />
            <TextField
              select
              label="Modo"
              value={ejecucion.modo}
              onChange={(e) => setEjecucion({ ...ejecucion, modo: e.target.value })}
            >
              <MenuItem value="simular">ver qué se haría (no cambia nada)</MenuItem>
              <MenuItem value="ejecutar">ejecutar de verdad</MenuItem>
            </TextField>
            {ejecucion.modo === "ejecutar" && (
              <TextField
                label="Para ejecutar de verdad, escribí ANONIMIZAR"
                value={ejecucion.confirmar}
                onChange={(e) => setEjecucion({ ...ejecucion, confirmar: e.target.value })}
              />
            )}
            <Button
              variant="contained"
              color={ejecucion.modo === "ejecutar" ? "error" : "primary"}
              disabled={ocupado || !ejecucion.motivo.trim()}
              onClick={ejecutar}
            >
              {ejecucion.modo === "ejecutar" ? "Ejecutar" : "Simular"}
            </Button>
          </Stack>
          {informe?.aviso && (
            <Alert severity="info" sx={{ mt: 2 }}>
              {informe.aviso}
            </Alert>
          )}
        </CardContent>
      </Card>
    </Box>
  );
}
