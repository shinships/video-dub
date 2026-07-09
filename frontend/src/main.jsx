import React from "react";
import { createRoot } from "react-dom/client";
// Font self-host: không phụ thuộc mạng khi chạy local, không chặn render.
import "@fontsource/be-vietnam-pro/400.css";
import "@fontsource/be-vietnam-pro/500.css";
import "@fontsource/be-vietnam-pro/600.css";
import "@fontsource/be-vietnam-pro/700.css";
import "@fontsource/manrope/600.css";
import "@fontsource/manrope/700.css";
import { App } from "./App.jsx";
import "./styles.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
