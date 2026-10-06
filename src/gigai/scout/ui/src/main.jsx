import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import UpdatedBanner from "./components/UpdatedBanner.jsx";
import { applyTheme, browserStorage, lockExtensionDarkMode, readStoredTheme } from "./theme.js";
import "./styles.css";

// uat-batch1 (N2): the theme chosen with the top bar's toggle, applied
// before the first paint; with none stored the CSS follows the system.
applyTheme(document.documentElement, readStoredTheme(browserStorage()));
lockExtensionDarkMode(document);

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <UpdatedBanner />
    <App />
  </StrictMode>,
);
