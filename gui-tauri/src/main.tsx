import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

// StrictMode is intentionally off: its double effect run would open the
// sidecar / index twice on every dev reload, which this single-window pilot
// does not need.
const root = document.getElementById("root");
if (!root) {
  throw new Error("#root がありません");
}

createRoot(root).render(<App />);
