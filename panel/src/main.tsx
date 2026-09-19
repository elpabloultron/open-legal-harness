import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";

const raiz = document.getElementById("raiz");
if (!raiz) throw new Error("falta el contenedor #raiz en el HTML");

createRoot(raiz).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
