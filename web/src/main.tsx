import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { OperatorProvider } from "./lib/state";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Error langsung ditampilkan; tidak diulang diam-diam.
      retry: false,
      refetchOnWindowFocus: false,
    },
    mutations: {
      // Panggilan yang memindahkan uang tidak boleh diulang otomatis.
      retry: false,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <OperatorProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </OperatorProvider>
    </QueryClientProvider>
  </StrictMode>,
);
