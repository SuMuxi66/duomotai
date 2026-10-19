import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { DemoRouter } from "./demo/DemoRouter";
import { ThemeProvider } from "./contexts/ThemeContext";
import "./index.css";

function AppRoot() {
  const hash = window.location.hash;
  if (hash.startsWith("#/demo")) {
    return (
      <ThemeProvider>
        <DemoRouter />
      </ThemeProvider>
    );
  }
  return (
    <ThemeProvider>
      <App />
    </ThemeProvider>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppRoot />
  </React.StrictMode>
);