import { describe, test, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { ReconciliationQueue } from "../components/ReconciliationQueue";

const originalFetch = globalThis.fetch;

beforeEach(() => {
  globalThis.fetch = vi.fn();
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

const conflict = {
  id: 7,
  concept_uuid: "leaf-1",
  period: "CY",
  entity_scope: "Company",
  kind: "partial_state",
  residual: 3000.0,
  detail: "parent 50000 vs sum 47000",
  status: "open",
  canonical_label: "Total non-current assets",
};

function mockFetch(impl: (url: string, init?: RequestInit) => unknown) {
  (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(
    async (url: string, init?: RequestInit) => {
      const result = impl(url, init);
      return {
        ok: true,
        status: 200,
        json: async () => result,
      } as Response;
    }
  );
}

describe("ReconciliationQueue", () => {
  test("renders when a run has open conflicts", async () => {
    mockFetch(() => ({ conflicts: [conflict] }));
    render(<ReconciliationQueue runId={42} />);
    await waitFor(() => screen.getByTestId("conflict-7"));
    expect(screen.getByText(/Reconciliation queue \(1\)/)).toBeTruthy();
  });

  test("renders empty state when no conflicts", async () => {
    mockFetch(() => ({ conflicts: [] }));
    render(<ReconciliationQueue runId={42} />);
    await waitFor(() => screen.getByTestId("reconciliation-empty"));
  });

  test("lists residual on partial_state conflicts", async () => {
    mockFetch(() => ({ conflicts: [conflict] }));
    render(<ReconciliationQueue runId={42} />);
    await waitFor(() => screen.getByTestId("conflict-7"));
    expect(screen.getByText(/residual 3000\.00/)).toBeTruthy();
  });

  test("failed Resolve (500) keeps the row visible", async () => {
    // Peer-review #10: a 500 must NOT optimistically remove the row.
    let resolveCalled = false;
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(
      async (url: string, init?: RequestInit) => {
        if (init?.method === "POST" && url.includes("/resolve")) {
          resolveCalled = true;
          return {
            ok: false,
            status: 500,
            json: async () => ({ detail: "boom" }),
          } as Response;
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({ conflicts: [conflict] }),
        } as Response;
      }
    );
    render(<ReconciliationQueue runId={42} />);
    const btn = await waitFor(() => screen.getByTestId("resolve-btn-7"));
    fireEvent.click(btn);
    // Wait until the POST has actually completed, THEN assert the row
    // is still present — guards against a waitFor that succeeds before
    // the optimistic removal would have fired.
    await waitFor(() => expect(resolveCalled).toBe(true));
    expect(screen.queryByTestId("conflict-7")).not.toBeNull();
  });

  test("clicking Resolve calls endpoint and removes the row", async () => {
    let resolvedCalled = false;
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation(
      async (url: string, init?: RequestInit) => {
        if (init?.method === "POST" && url.includes("/resolve")) {
          resolvedCalled = true;
          return {
            ok: true,
            status: 200,
            json: async () => ({ ok: true }),
          } as Response;
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({ conflicts: [conflict] }),
        } as Response;
      }
    );

    render(<ReconciliationQueue runId={42} />);
    const btn = await waitFor(() => screen.getByTestId("resolve-btn-7"));
    fireEvent.click(btn);

    await waitFor(() => {
      expect(resolvedCalled).toBe(true);
      expect(screen.queryByTestId("conflict-7")).toBeNull();
    });
  });
});
