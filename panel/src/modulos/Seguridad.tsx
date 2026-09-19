/**
 * Seguridad: quién entra, con qué, y qué hacer cuando algo se traba.
 *
 * El alta del segundo factor es en dos pasos a propósito: primero se genera el secreto y se
 * carga en el teléfono, y recién cuando llega un código que sirve queda activo. Si se
 * activara de una, un secreto mal copiado dejaría a la persona afuera de su propia cuenta.
 */
import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
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
import type { CuentaSeguridad } from "../tipos";

interface Informe {
  intentos: {
    total: number;
    ventana_minutos: number;
    sospechoso: boolean;
    umbral: number;
    por_cuenta: Record<string, number>;
  };
  cuentas: CuentaSeguridad[];
  umbrales: { intentos_alerta: number; bloqueo_intentos: number; bloqueo_minutos: number };
}

export function Seguridad() {
  const notificar = useNotify();
  const [informe, setInforme] = useState<Informe | null>(null);
  const [alta, setAlta] = useState<{ email: string; secreto: string; uri: string } | null>(null);
  const [codigo, setCodigo] = useState("");
  const [ocupado, setOcupado] = useState(false);

  const cargar = useCallback(async () => {
    setInforme(await obtener<Informe>("/api/seguridad"));
  }, []);

  useEffect(() => {
    cargar().catch((error: Error) => notificar(error.message, { type: "error" }));
  }, [cargar, notificar]);

  async function preparar(email: string) {
    try {
      const respuesta = await enviar<{ email: string; secreto: string; uri: string }>(
        "/api/seguridad/2fa/preparar",
        { email },
      );
      setAlta(respuesta);
      setCodigo("");
    } catch (error) {
      notificar((error as Error).message, { type: "error" });
    }
  }

  async function confirmar() {
    if (!alta) return;
    setOcupado(true);
    try {
      await enviar("/api/seguridad/2fa/confirmar", { email: alta.email, codigo });
      notificar(`Segundo factor activado para ${alta.email}`, { type: "success" });
      setAlta(null);
      await cargar();
    } catch (error) {
      notificar((error as Error).message, { type: "error" });
    } finally {
      setOcupado(false);
    }
  }

  async function apagar(email: string) {
    const motivo = window.prompt("¿Por qué se apaga el segundo factor? (queda en la bitácora)");
    if (!motivo) return;
    try {
      await enviar("/api/seguridad/2fa/apagar", { email, motivo });
      notificar("Segundo factor apagado", { type: "info" });
      await cargar();
    } catch (error) {
      notificar((error as Error).message, { type: "error" });
    }
  }

  async function destrabar(email: string) {
    try {
      await enviar("/api/seguridad/desbloquear", { email });
      notificar(`Cuenta destrabada: ${email}`, { type: "success" });
      await cargar();
    } catch (error) {
      notificar((error as Error).message, { type: "error" });
    }
  }

  return (
    <Box sx={{ p: 2 }}>
      <Typography variant="h5" gutterBottom>
        Seguridad del estudio
      </Typography>

      {informe && (
        <Card sx={{ mb: 2 }}>
          <CardContent>
            <Typography variant="h6" gutterBottom>
              Accesos
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Intentos fallidos en los últimos {informe.intentos.ventana_minutos} minutos:{" "}
              <strong>{informe.intentos.total}</strong>
            </Typography>
            {Object.keys(informe.intentos.por_cuenta).length > 0 && (
              <Typography variant="body2" color="text.secondary">
                Por cuenta:{" "}
                {Object.entries(informe.intentos.por_cuenta)
                  .map(([cuenta, veces]) => `${cuenta} (${veces})`)
                  .join(" · ")}
              </Typography>
            )}
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              A los {informe.umbrales.bloqueo_intentos} intentos fallidos la cuenta se bloquea{" "}
              {informe.umbrales.bloqueo_minutos} minutos — también con la contraseña correcta.
            </Typography>
            {informe.intentos.sospechoso && (
              <Alert severity="warning" sx={{ mt: 1 }}>
                Pasó el umbral de {informe.intentos.umbral} intentos. Revisá con el estudio si fue un olvido o algo más.
              </Alert>
            )}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardContent>
          <Typography variant="h6" gutterBottom>
            Segundo factor y bloqueos
          </Typography>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Persona</TableCell>
                <TableCell>Rol</TableCell>
                <TableCell>Segundo factor</TableCell>
                <TableCell>Bloqueo</TableCell>
                <TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {(informe?.cuentas ?? []).map((cuenta) => (
                <TableRow key={cuenta.email}>
                  <TableCell>{cuenta.nombre}</TableCell>
                  <TableCell>{cuenta.rol}</TableCell>
                  <TableCell>
                    {cuenta.totp_activo ? (
                      <Chip size="small" color="success" variant="outlined" label="activo" />
                    ) : cuenta.esperado ? (
                      <Chip size="small" color="error" variant="outlined" label="falta" />
                    ) : (
                      <Chip size="small" variant="outlined" label="no" />
                    )}
                  </TableCell>
                  <TableCell>
                    {cuenta.bloqueo?.bloqueada ? (
                      <Chip size="small" color="error" variant="outlined" label={`${cuenta.bloqueo.faltan_minutos} min`} />
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell>
                    <Stack direction="row" spacing={1}>
                      {!cuenta.totp_activo && (
                        <Button size="small" variant="outlined" onClick={() => preparar(cuenta.email)}>
                          activar
                        </Button>
                      )}
                      {Boolean(cuenta.totp_activo) && (
                        <Button size="small" variant="outlined" onClick={() => apagar(cuenta.email)}>
                          apagar
                        </Button>
                      )}
                      {cuenta.bloqueo?.bloqueada && (
                        <Button size="small" color="error" variant="outlined" onClick={() => destrabar(cuenta.email)}>
                          destrabar
                        </Button>
                      )}
                    </Stack>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Dialog open={Boolean(alta)} onClose={() => setAlta(null)} fullWidth maxWidth="sm">
        <DialogTitle>Segundo factor para {alta?.email}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ mb: 2 }}>
            Que abra su app de autenticación y cargue esta clave (o pegue el enlace). Después hay que confirmar con el
            código de 6 dígitos que muestre la app: hasta entonces no se activa nada.
          </Typography>
          <Typography
            variant="h6"
            sx={{ fontFamily: "monospace", letterSpacing: 2, p: 1, bgcolor: "action.hover", borderRadius: 1 }}
          >
            {alta?.secreto?.match(/.{1,4}/g)?.join(" ")}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ wordBreak: "break-all", mt: 1 }}>
            {alta?.uri}
          </Typography>
          <TextField
            autoFocus
            fullWidth
            margin="dense"
            label="Código de 6 dígitos"
            value={codigo}
            onChange={(e) => setCodigo(e.target.value)}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAlta(null)}>cancelar</Button>
          <Button variant="contained" disabled={ocupado || codigo.trim().length < 6} onClick={confirmar}>
            Confirmar y activar
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
