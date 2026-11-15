import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";

// The canvas draws room signs with the pixel font, so wait for both faces before the first frame.
Promise.allSettled([document.fonts.load('600 14px "Pixelify Sans"'), document.fonts.load('500 14px "Atkinson Hyperlegible Next Variable"')]).finally(() => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
});
