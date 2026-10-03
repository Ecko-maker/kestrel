import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { bootstrapAuth } from "./api";
import { App } from "./App";
import "./index.css";

bootstrapAuth().finally(() => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
});
