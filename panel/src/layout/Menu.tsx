/**
 * El menú del estudio, armado con los permisos que devuelve el servidor.
 *
 * Ningún ítem está escrito a mano dos veces: cada uno declara qué permiso pide, y se muestra
 * sólo si esta persona lo tiene. Si mañana se agrega un módulo y se le da permiso a un rol,
 * aparece sola — y si se le quita, desaparece. El servidor vuelve a exigirlo igual.
 */
import { Menu } from "react-admin";
import PeopleIcon from "@mui/icons-material/PeopleAltOutlined";
import GavelIcon from "@mui/icons-material/GavelOutlined";
import EventIcon from "@mui/icons-material/EventOutlined";
import AssignmentLateIcon from "@mui/icons-material/AssignmentLateOutlined";
import ContactPageIcon from "@mui/icons-material/ContactPageOutlined";
import NotificationsIcon from "@mui/icons-material/NotificationsNoneOutlined";
import ShieldIcon from "@mui/icons-material/ShieldOutlined";
import HourglassIcon from "@mui/icons-material/HourglassEmptyOutlined";
import FolderSharedIcon from "@mui/icons-material/FolderSharedOutlined";
import PaymentsIcon from "@mui/icons-material/PaymentsOutlined";
import SmartToyIcon from "@mui/icons-material/SmartToyOutlined";
import { usePermissions } from "react-admin";

interface Item {
  name: string;
  label: string;
  permiso: string;
  icono: React.ReactElement;
  a: string;
}

const ITEMS: Item[] = [
  { name: "causas", label: "Causas", permiso: "causa.leer", icono: <GavelIcon />, a: "/causas" },
  { name: "plazos", label: "Plazos", permiso: "plazo.leer", icono: <AssignmentLateIcon />, a: "/plazos" },
  { name: "audiencias", label: "Agenda", permiso: "audiencia.leer", icono: <EventIcon />, a: "/audiencias" },
  { name: "clientes", label: "Clientes", permiso: "cliente.leer", icono: <ContactPageIcon />, a: "/clientes" },
  // Los honorarios se piden con `honorario.leer`; el socio además tiene `.leer.todas`, que el
  // filtro de abajo ya contempla como el resto de los módulos.
  { name: "honorarios", label: "Honorarios", permiso: "honorario.leer", icono: <PaymentsIcon />, a: "/honorarios" },
  { name: "avisos", label: "Avisos", permiso: "aviso.gestionar", icono: <NotificationsIcon />, a: "/avisos" },
  { name: "usuarios", label: "Usuarios", permiso: "usuario.gestionar", icono: <PeopleIcon />, a: "/usuarios" },
  { name: "seguridad", label: "Seguridad", permiso: "usuario.gestionar", icono: <ShieldIcon />, a: "/seguridad" },
  { name: "retencion", label: "Retención", permiso: "usuario.gestionar", icono: <HourglassIcon />, a: "/retencion" },
  { name: "titulares", label: "Datos del titular", permiso: "titular.gestionar", icono: <FolderSharedIcon />, a: "/titulares" },
  // Lo que salió hacia un modelo: con qué proveedor, hacia qué país, con qué huella y bajo qué
  // autorización. Lo ven los mismos que pueden enviar (`ia.leer`): socio y abogado.
  { name: "ia", label: "IA y transferencias", permiso: "ia.leer", icono: <SmartToyIcon />, a: "/ia" },
];

export function MenuDelEstudio() {
  const { permissions } = usePermissions<string[]>();
  const permisos = permissions ?? [];

  return (
    <Menu>
      {ITEMS.filter((item) => permisos.includes(item.permiso) || permisos.includes("causa.leer.todas")).map((item) => (
        <Menu.Item key={item.name} to={item.a} primaryText={item.label} leftIcon={item.icono} />
      ))}
    </Menu>
  );
}
