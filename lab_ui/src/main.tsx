import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient } from "@tanstack/react-query";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import { routeTree } from "./routeTree.gen";
import { AppShell } from "./components/lab/AppShell";
import "./styles.css";

const queryClient = new QueryClient();

// Served by PaperLab at /lab (app/core/api_lab.py); every client route lives under it.
const router = createRouter({
  routeTree,
  basepath: "/lab",
  context: { queryClient },
  scrollRestoration: true,
  defaultPreloadStaleTime: 0,
  // The first load fetches every program feed; show the shell instead of a blank page meanwhile.
  defaultPendingMs: 150,
  defaultPendingComponent: () => (
    <AppShell>
      <p className="animate-pulse text-sm text-muted-foreground">Loading live data from PaperLab…</p>
    </AppShell>
  ),
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
