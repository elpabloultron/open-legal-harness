/**
 * El tema del panel: sobrio, de trabajo, con contraste alto.
 *
 * Un CRM jurídico se lee muchas horas y se imprime: nada de grises suaves ni de colores
 * que se pierdan en papel. Los estados (fatal, por vencer, cumplido) tienen color propio y
 * también texto, porque el color solo no comunica.
 */
import { createTheme } from "@mui/material/styles";

export const colores = {
  fatal: "#b3261e",
  pronto: "#8a5a00",
  ok: "#1b6e4a",
  tinta: "#141b25",
  tenue: "#5b6675",
};

export const tema = createTheme({
  palette: {
    mode: "light",
    primary: { main: "#1f3a63" },
    secondary: { main: "#5b6675" },
    error: { main: colores.fatal },
    warning: { main: colores.pronto },
    success: { main: colores.ok },
    background: { default: "#f6f7f9", paper: "#ffffff" },
  },
  typography: {
    fontFamily: '-apple-system, "Segoe UI", Inter, Roboto, system-ui, sans-serif',
    h5: { fontWeight: 600 },
    h6: { fontWeight: 600 },
    subtitle2: { fontWeight: 600 },
  },
  components: {
    MuiTableCell: { styleOverrides: { root: { paddingTop: 6, paddingBottom: 6 } } },
    MuiCard: { styleOverrides: { root: { borderRadius: 10 } } },
    MuiButton: { defaultProps: { disableElevation: true } },
  },
});
