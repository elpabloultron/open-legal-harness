/**
 * El panel del CRM: React-Admin sobre la API local del estudio.
 *
 * Estructura:
 *  - Un recurso por módulo de datos (causas, plazos, audiencias, clientes, usuarios).
 *  - Una página propia por módulo que no es un listado (honorarios y su cuenta de dividendos,
 *    avisos, seguridad, retención, derechos del titular), porque su trabajo no es «filas de una tabla».
 *  - El menú y las rutas se arman con los permisos que devuelve el servidor: un módulo que
 *    esta persona no puede usar no aparece.
 */
import { Admin, CustomRoutes, Resource } from "react-admin";
import { Route } from "react-router-dom";

import { authProvider } from "./api/authProvider";
import { dataProvider } from "./api/dataProvider";
import { tema } from "./tema";
import { BarraDelEstudio } from "./layout/Barra";
import { Inicio } from "./modulos/Inicio";
import { Avisos } from "./modulos/Avisos";
import { Honorarios } from "./modulos/Honorarios";
import { Seguridad } from "./modulos/Seguridad";
import { Retencion } from "./modulos/Retencion";
import { Titulares } from "./modulos/Titulares";
import {
  CausaCreate,
  CausaList,
  ClienteCreate,
  ClienteList,
  PlazoCreate,
  PlazoList,
  AudienciaCreate,
  AudienciaList,
  UsuarioCreate,
  UsuarioEdit,
  UsuarioList,
} from "./modulos/recursos";

export function App() {
  return (
    <Admin
      dataProvider={dataProvider}
      authProvider={authProvider}
      theme={tema}
      layout={BarraDelEstudio}
      dashboard={Inicio}
      requireAuth
      title="CRM Jurídico"
    >
      <Resource name="causas" options={{ label: "Causas" }} list={CausaList} create={CausaCreate} recordRepresentation="caratula" />
      <Resource name="plazos" options={{ label: "Plazos" }} list={PlazoList} create={PlazoCreate} recordRepresentation="descripcion" />
      <Resource name="audiencias" options={{ label: "Agenda" }} list={AudienciaList} create={AudienciaCreate} recordRepresentation="tipo" />
      <Resource name="clientes" options={{ label: "Clientes" }} list={ClienteList} create={ClienteCreate} recordRepresentation="nombre" />
      <Resource name="usuarios" options={{ label: "Usuarios" }} list={UsuarioList} create={UsuarioCreate} edit={UsuarioEdit} recordRepresentation="nombre" />

      <CustomRoutes>
        <Route path="/honorarios" element={<Honorarios />} />
        <Route path="/avisos" element={<Avisos />} />
        <Route path="/seguridad" element={<Seguridad />} />
        <Route path="/retencion" element={<Retencion />} />
        <Route path="/titulares" element={<Titulares />} />
      </CustomRoutes>
    </Admin>
  );
}
