import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { bootstrapAuth } from "./api";
import { App } from "./App";
// Orbitron (SIL OFL 1.1), bundled locally; only downloaded when the HUD look uses it.
import "@fontsource-variable/orbitron/wght.css";
import "./index.css";

bootstrapAuth().finally(() => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
});
