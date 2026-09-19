/**
 * Los módulos de datos del CRM: causas, plazos, agenda, clientes y usuarios.
 *
 * Son listados y formularios sobre la API, que es la que decide (permisos, cálculo de
 * vencimientos con el Art. 66 CPC, avisos, bitácora). Acá no se recalcula nada: se muestra
 * lo que el servidor ya resolvió, y cuando algo se crea se muestra el resultado que devolvió.
 */
import {
  BooleanField,
  BooleanInput,
  Create,
  Datagrid,
  DateField,
  DateInput,
  Edit,
  FunctionField,
  List,
  NumberInput,
  ReferenceInput,
  SearchInput,
  SelectInput,
  SimpleForm,
  TextField,
  TextInput,
  TimeInput,
  required,
  useNotify,
  usePermissions,
  useRecordContext,
  useRefresh,
} from "react-admin";
import { Button, Chip, Stack } from "@mui/material";
import { useState } from "react";

import { enviar } from "../api/cliente";
import type { Causa, Plazo, Usuario } from "../tipos";
import { fechaLegible } from "../tipos";

/* --------------------------------------------------------------- causas */

export const CausaList = () => (
  <List perPage={25} sort={{ field: "id", order: "DESC" }} exporter={false}>
    <Datagrid rowClick={false} bulkActionButtons={false}>
      <TextField source="caratula" label="Carátula" />
      <TextField source="rol_rit" label="RIT" />
      <TextField source="tribunal" label="Tribunal" />
      <TextField source="materia" label="Materia" />
      <TextField source="estado_procesal" label="Estado" />
      <FunctionField<Causa>
        label="Próximo vencimiento"
        render={(causa) =>
          causa.proximo_vencimiento
            ? `${fechaLegible(causa.proximo_vencimiento.fecha_vencimiento)} · ${causa.proximo_vencimiento.descripcion}`
            : "—"
        }
      />
      <FunctionField<Causa> label="Equipo" render={(causa) => (causa.equipo ?? []).join(", ") || "sin asignar"} />
    </Datagrid>
  </List>
);

export const CausaCreate = () => (
  <Create redirect="list" title="Nueva causa">
    <SimpleForm>
      <TextInput
        source="caratula"
        label="Carátula"
        validate={required()}
        helperText="Herrera con Fondo del Norte"
        fullWidth
      />
      <ReferenceInput source="cliente_id" reference="clientes" label="Cliente">
        <SelectInput optionText="nombre" emptyText="— sin cliente —" />
      </ReferenceInput>
      <TextInput source="rol_rit" label="RIT / Rol" helperText="C-1234-2026" />
      <TextInput source="tribunal" label="Tribunal" fullWidth />
      <TextInput source="materia" label="Materia" helperText="civil, laboral, familia, penal…" />
      <TextInput source="observaciones" label="Observaciones" multiline rows={3} fullWidth />
    </SimpleForm>
  </Create>
);

/* --------------------------------------------------------------- plazos */

function Situacion() {
  const plazo = useRecordContext<Plazo>();
  if (!plazo?.fecha_vencimiento) return <span>—</span>;
  const dias = plazo.dias_restantes ?? 0;
  const color = dias < 0 ? "error" : dias <= 3 ? "warning" : "success";
  const texto = dias < 0 ? `vencido hace ${-dias} día(s)` : `${dias} día(s)`;
  return (
    <Stack direction="row" spacing={0.5} alignItems="center">
      {Boolean(plazo.es_fatal) && <Chip size="small" color="error" variant="outlined" label="fatal" />}
      <Chip size="small" color={color} variant="outlined" label={texto} />
    </Stack>
  );
}

function MarcarCumplido() {
  const plazo = useRecordContext<Plazo>();
  const notificar = useNotify();
  const refrescar = useRefresh();
  const [enviando, setEnviando] = useState(false);

  if (!plazo || plazo.estado !== "pendiente") return null;
  return (
    <Button
      size="small"
      variant="outlined"
      disabled={enviando}
      onClick={async () => {
        setEnviando(true);
        try {
          await enviar(`/api/plazos/${plazo.id}/cumplido`);
          notificar("Plazo marcado como cumplido", { type: "success" });
          refrescar();
        } catch (error) {
          notificar((error as Error).message, { type: "error" });
        } finally {
          setEnviando(false);
        }
      }}
    >
      {enviando ? "…" : "cumplido"}
    </Button>
  );
}

export const PlazoList = () => (
  <List
    perPage={50}
    sort={{ field: "fecha_vencimiento", order: "ASC" }}
    exporter={false}
    filters={[<NumberInput key="dias" source="dias" label="Días hacia adelante" defaultValue={60} />]}
  >
    <Datagrid rowClick={false} bulkActionButtons={false}>
      <TextField source="descripcion" label="Plazo" />
      <TextField source="caratula" label="Causa" />
      <DateField source="fecha_notificacion" label="Notificación" locales="es-CL" />
      <DateField source="fecha_vencimiento" label="Vence" locales="es-CL" />
      <FunctionField label="Situación" render={() => <Situacion />} />
      <TextField source="estado" label="Estado" />
      <FunctionField label="" render={() => <MarcarCumplido />} />
    </Datagrid>
  </List>
);

export const PlazoCreate = () => (
  <Create redirect="list" title="Nuevo plazo">
    <SimpleForm>
      <ReferenceInput source="causa_id" reference="causas" label="Causa">
        <SelectInput optionText="caratula" validate={required()} />
      </ReferenceInput>
      <TextInput
        source="descripcion"
        label="Descripción"
        validate={required()}
        helperText="Contestar traslado"
        fullWidth
      />
      <DateInput source="fecha_notificacion" label="Fecha de notificación" />
      <NumberInput
        source="dias"
        label="Días hábiles"
        defaultValue={8}
        helperText="Art. 66 CPC: días hábiles, con sábado hábil y feriados"
      />
      <BooleanInput source="es_fatal" label="¿Es fatal?" defaultValue />
    </SimpleForm>
  </Create>
);

/* ---------------------------------------------------------------- agenda */

export const AudienciaList = () => (
  <List
    perPage={50}
    sort={{ field: "fecha", order: "ASC" }}
    exporter={false}
    filters={[<NumberInput key="dias" source="dias" label="Días hacia adelante" defaultValue={60} />]}
  >
    <Datagrid rowClick={false} bulkActionButtons={false}>
      <DateField source="fecha" label="Fecha" locales="es-CL" />
      <TextField source="hora" label="Hora" />
      <TextField source="caratula" label="Causa" />
      <TextField source="tipo" label="Tipo" />
      <TextField source="modalidad" label="Modalidad" />
      <TextField source="lugar_o_url" label="Lugar o enlace" />
      <TextField source="estado" label="Estado" />
    </Datagrid>
  </List>
);

export const AudienciaCreate = () => {
  const { permissions } = usePermissions<string[]>();
  const puedeAsignar = (permissions ?? []).includes("audiencia.editar");
  return (
    <Create redirect="list" title="Agendar audiencia">
      <SimpleForm>
        <ReferenceInput source="causa_id" reference="causas" label="Causa">
          <SelectInput optionText="caratula" validate={required()} />
        </ReferenceInput>
        <TextInput
          source="tipo"
          label="Tipo"
          defaultValue="preparatoria"
          validate={required()}
          helperText="preparatoria, juicio, prueba…"
        />
        <DateInput source="fecha" label="Fecha" />
        <TimeInput source="hora" label="Hora" />
        <SelectInput
          source="modalidad"
          label="Modalidad"
          defaultValue="presencial"
          choices={[
            { id: "presencial", name: "presencial" },
            { id: "remota", name: "remota" },
            { id: "mixta", name: "mixta" },
          ]}
        />
        <TextInput source="lugar_o_url" label="Lugar o enlace" fullWidth />
        <TextInput source="minuta" label="Minuta" multiline rows={3} fullWidth helperText="qué hay que llevar o preparar" />
        {puedeAsignar && (
          <ReferenceInput source="responsable_id" reference="usuarios" label="Responsable">
            <SelectInput optionText="nombre" emptyText="— el que la agenda —" />
          </ReferenceInput>
        )}
        <p style={{ color: "#5b6675", fontSize: 13 }}>
          Al agendarla, quien quede como responsable recibe su aviso por correo o SMS.
        </p>
      </SimpleForm>
    </Create>
  );
};

/* -------------------------------------------------------------- clientes */

export const ClienteList = () => (
  <List
    perPage={25}
    exporter={false}
    filters={[<SearchInput key="q" source="q" alwaysOn placeholder="nombre, RUT o correo" />]}
  >
    <Datagrid rowClick={false} bulkActionButtons={false}>
      <TextField source="nombre" label="Nombre o razón social" />
      <TextField source="rut" label="RUT" />
      <TextField source="email" label="Correo" />
      <TextField source="telefono" label="Teléfono" />
      <TextField source="tipo_persona" label="Tipo" />
    </Datagrid>
  </List>
);

export const ClienteCreate = () => (
  <Create redirect="list" title="Nuevo cliente">
    <SimpleForm>
      <TextInput source="nombre" label="Nombre o razón social" validate={required()} fullWidth />
      <TextInput source="rut" label="RUT" helperText="12.345.678-9" />
      <SelectInput
        source="tipo_persona"
        label="Tipo"
        defaultValue="natural"
        choices={[
          { id: "natural", name: "persona natural" },
          { id: "juridica", name: "persona jurídica" },
        ]}
      />
      <TextInput source="email" label="Correo" type="email" />
      <TextInput source="telefono" label="Teléfono" />
      <TextInput source="direccion" label="Domicilio" fullWidth helperText="dato personal: sólo lo necesario" />
    </SimpleForm>
  </Create>
);

/* -------------------------------------------------------------- usuarios */

function SegundoFactor() {
  const usuario = useRecordContext<Usuario>();
  if (!usuario) return null;
  return usuario.totp_activo ? (
    <Chip size="small" color="success" variant="outlined" label="activo" />
  ) : (
    <Chip size="small" variant="outlined" label="no" />
  );
}

function Acceso() {
  const usuario = useRecordContext<Usuario>();
  if (!usuario) return null;
  return usuario.activo ? <span>sí</span> : <Chip size="small" color="error" variant="outlined" label="inactivo" />;
}

export const UsuarioList = () => (
  <List perPage={25} sort={{ field: "nombre", order: "ASC" }} exporter={false}>
    <Datagrid rowClick="edit" bulkActionButtons={false}>
      <TextField source="nombre" label="Nombre" />
      <TextField source="email" label="Correo" />
      <TextField source="rol" label="Rol" />
      <TextField source="telefono" label="Teléfono" />
      <FunctionField label="Segundo factor" render={() => <SegundoFactor />} />
      <FunctionField label="Acceso" render={() => <Acceso />} />
    </Datagrid>
  </List>
);

export const UsuarioCreate = () => (
  <Create redirect="list" title="Nueva persona">
    <SimpleForm>
      <TextInput source="nombre" label="Nombre" validate={required()} fullWidth />
      <TextInput source="email" label="Correo" type="email" validate={required()} fullWidth />
      <SelectInput
        source="rol"
        label="Rol"
        validate={required()}
        defaultValue="abogado"
        choices={[
          { id: "socio", name: "socio — ve todo y administra" },
          { id: "administrador", name: "administrador — gestiona usuarios y opera el CRM" },
          { id: "abogado", name: "abogado — sus causas" },
          { id: "paralegal", name: "paralegal — apoyo en sus causas" },
          { id: "administrativo", name: "secretaría — agenda y facturación" },
          { id: "cliente", name: "cliente — sólo su causa" },
        ]}
      />
      <TextInput source="telefono" label="Teléfono" helperText="para los avisos por SMS" />
      <TextInput
        source="password"
        label="Contraseña"
        type="password"
        validate={required()}
        helperText="al menos 10 caracteres; se guarda cifrada y nadie puede leerla después"
      />
    </SimpleForm>
  </Create>
);

export const UsuarioEdit = () => (
  <Edit redirect="list" title="Editar persona">
    <SimpleForm>
      <TextInput source="nombre" label="Nombre" disabled fullWidth />
      <TextInput source="email" label="Correo" disabled fullWidth />
      <SelectInput
        source="rol"
        label="Rol"
        choices={[
          { id: "socio", name: "socio" },
          { id: "administrador", name: "administrador" },
          { id: "abogado", name: "abogado" },
          { id: "paralegal", name: "paralegal" },
          { id: "administrativo", name: "secretaría" },
          { id: "cliente", name: "cliente" },
        ]}
      />
      <TextInput source="telefono" label="Teléfono" />
      <BooleanField source="activo" label="Acceso" /> {/* sólo lectura: se edita con el interruptor de abajo */}
      <BooleanInput source="activo" label="Puede entrar al CRM" />
      <TextInput
        source="password"
        label="Nueva contraseña"
        type="password"
        helperText="dejala vacía para no cambiarla; al cambiarla se cierran sus sesiones abiertas"
      />
    </SimpleForm>
  </Edit>
);
