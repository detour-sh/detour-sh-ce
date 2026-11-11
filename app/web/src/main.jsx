import "@fontsource/ibm-plex-sans/300.css";
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-mono/400.css";
import "./styles.scss";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import { RouterProvider } from "./router.jsx";
import { applyTheme, readThemeChoice } from "./util.js";

applyTheme(readThemeChoice());
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => applyTheme(readThemeChoice()));

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <RouterProvider>
      <App />
    </RouterProvider>
  </StrictMode>,
);
