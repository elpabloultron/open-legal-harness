/**
 * Derechos del titular: acceso, portabilidad y supresión, con el motivo por delante.
 *
 * La pantalla está pensada como la usaría un abogado con el cliente al teléfono: buscar,
 * mirar qué se haría (sin tocar nada), y recién después ejecutar. La anonimización pide
 * escribir la palabra de confirmación, y el expediente se entrega como archivo con su hash.
 */
import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Divider,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useNotify } from "react-admin";

import { descargar, enviar, obtener } from "../api/cliente";
import type { Cliente } from "../tipos";

interface InformeAnonimizacion {
  identificadores_borrados: { tabla: string; id: number }[];
  textos_redactados: { tabla: string; id: number }[];
  conservado?: { tabla: string; filas: number; motivo: string }[];
}

export function Titulares() {
  const notificar = useNotify();
  const [texto, setTexto] = useState("");
  const [resultados, setResultados] = useState<Cliente[] | null>(null);
  const [ocupado, setOcupado] = useState<string | null>(null);

  async function buscar() {
    setOcupado("buscar");
    try {
      setResultados(await obtener<Cliente[]>(`/api/titulares?texto=${encodeURIComponent(texto)}`));
    } catch (error) {
      notificar((error as Error).message, { type: "error" });
    } finally {
      setOcupado(null);
    }
  }

  function criterio(cliente: Cliente): Record<string, string> {
    return cliente.rut ? { rut: cliente.rut } : { nombre: cliente.nombre };
  }

  async function entregar(cliente: Cliente) {
    setOcupado(`entregar-${cliente.id}`);
    try {
      const hash = await descargar("/api/titulares/exportar", criterio(cliente), "expediente-titular.json");
      notificar(`Expediente entregado · sha256 ${hash.slice(0, 16)}…`, { type: "success" });
    } catch (error) {
      notificar((error as Error).message, { type: "error" });
    } finally {
      setOcupado(null);
    }
  }

  async function simular(cliente: Cliente) {
    const motivo = window.prompt("Motivo (queda en la bitácora):", "solicitud del titular");
    if (!motivo) return;
    setOcupado(`simular-${cliente.id}`);
    try {
      const informe = await enviar<InformeAnonimizacion>("/api/titulares/anonimizar", {
        ...criterio(cliente),
        motivo,
        simular: true,
      });
      notificar(
        `Se borrarían ${informe.identificadores_borrados.length} identificador(es) y se redactaría el nombre en ` +
          `${informe.textos_redactados.length} texto(s). No se cambió nada.`,
        { type: "info" },
      );
    } catch (error) {
      notificar((error as Error).message, { type: "error" });
    } finally {
      setOcupado(null);
    }
  }

  async function anonimizar(cliente: Cliente) {
    const motivo = window.prompt("Motivo de la supresión (queda en la bitácora):");
    if (!motivo) return;
    const confirmacion = window.prompt("Esto NO se puede deshacer. Escribí ANONIMIZAR para confirmar:");
    if (confirmacion?.trim().toUpperCase() !== "ANONIMIZAR") {
      notificar("No se anonimizó: falta la confirmación", { type: "warning" });
      return;
    }
    setOcupado(`anonimizar-${cliente.id}`);
    try {
      const informe = await enviar<InformeAnonimizacion>("/api/titulares/anonimizar", {
        ...criterio(cliente),
        motivo,
        simular: false,
        confirmar: "ANONIMIZAR",
      });
      notificar(
        `Anonimizado: ${informe.identificadores_borrados.length} identificador(es) borrado(s), nombre redactado en ` +
          `${informe.textos_redactados.length} texto(s). El expediente y la bitácora quedan.`,
        { type: "success" },
      );
      setResultados(null);
      setTexto("");
    } catch (error) {
      notificar((error as Error).message, { type: "error" });
    } finally {
      setOcupado(null);
    }
  }

  return (
    <Box sx={{ p: 2 }}>
      <Typography variant="h5" gutterBottom>
        Derechos del titular
      </Typography>
      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            El derecho de acceso (que le digan qué se guardó), el de portabilidad (que se lo lleven en un archivo) y el
            de supresión (que se borren sus identificadores). Todo queda en la bitácora, con el motivo.
          </Typography>
          <Stack direction="row" spacing={1}>
            <TextField
              fullWidth
              label="Buscar por nombre, RUT o correo"
              value={texto}
              onChange={(e) => setTexto(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && texto.trim()) void buscar();
              }}
            />
            <Button variant="contained" disabled={ocupado === "buscar" || !texto.trim()} onClick={buscar}>
              Buscar
            </Button>
          </Stack>
        </CardContent>
      </Card>

      {resultados && !resultados.length && (
        <Alert severity="info">Ningún titular con ese criterio.</Alert>
      )}

      {(resultados ?? []).map((cliente) => (
        <Card key={cliente.id} sx={{ mb: 2 }}>
          <CardContent>
            <Typography variant="h6">{cliente.nombre}</Typography>
            <Typography variant="body2" color="text.secondary">
              {cliente.rut ?? "sin RUT"} · {cliente.email ?? "sin correo"} · {cliente.telefono ?? "sin teléfono"}
            </Typography>
            <Divider sx={{ my: 1 }} />
            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
              <Button
                variant="outlined"
                disabled={ocupado === `entregar-${cliente.id}`}
                onClick={() => entregar(cliente)}
              >
                entregar expediente
              </Button>
              <Button variant="outlined" disabled={ocupado === `simular-${cliente.id}`} onClick={() => simular(cliente)}>
                ver anonimización
              </Button>
              <Button
                variant="outlined"
                color="error"
                disabled={ocupado === `anonimizar-${cliente.id}`}
                onClick={() => anonimizar(cliente)}
              >
                anonimizar
              </Button>
            </Stack>
          </CardContent>
        </Card>
      ))}
    </Box>
  );
}
