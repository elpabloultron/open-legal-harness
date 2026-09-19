/**
 * La barra del estudio: quién está trabajando y en qué estudio.
 *
 * Sirve para lo que en una oficina se pregunta todo el tiempo: «¿en qué estudio estoy
 * entrando?». Cuando hay más de una instalación (un estudio chico con dos bases), el nombre
 * en la barra evita el error de cargar un plazo en la base equivocada.
 */
import { AppBar, Layout, TitlePortal, useGetIdentity } from "react-admin";
import { useEffect, useState } from "react";
import { Chip, Stack, Typography } from "@mui/material";

import { sesionActual } from "../api/authProvider";
import { MenuDelEstudio } from "./Menu";
import type { Sesion } from "../tipos";

export function BarraDelEstudio(props: React.ComponentProps<typeof Layout>) {
  // El menú y la barra se arman acá (no en <Admin>): los dos dependen de los permisos que
  // devuelve el servidor al entrar.
  return <Layout {...props} appBar={BarraPropia} menu={MenuDelEstudio} />;
}

function BarraPropia() {
  const { data: identidad } = useGetIdentity();
  const [sesion, setSesion] = useState<Sesion | null>(null);

  useEffect(() => {
    sesionActual().then(setSesion).catch(() => setSesion(null));
  }, []);

  return (
    <AppBar>
      <TitlePortal />
      <Stack direction="row" spacing={2} alignItems="center" sx={{ ml: 3, flex: 1 }}>
        <Typography variant="body2" sx={{ opacity: 0.9 }}>
          {sesion?.estudio?.nombre ?? ""}
        </Typography>
        {sesion?.estudio?.modo === "oficina" && <Chip size="small" label="oficina" variant="outlined" />}
        {sesion?.via === "token" && <Chip size="small" label="token local" variant="outlined" />}
      </Stack>
      <Typography variant="body2" sx={{ opacity: 0.9, mr: 2 }}>
        {identidad?.fullName ?? ""}
      </Typography>
    </AppBar>
  );
}
