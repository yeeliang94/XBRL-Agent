import "@testing-library/jest-dom/vitest";

// Newer Node versions have their own localStorage, which hides jsdom's and is
// unusable without a storage file. Tests must always get the browser-like one.
const dom = (globalThis as unknown as { jsdom?: { window: Window } }).jsdom;
if (dom) {
  for (const key of ["localStorage", "sessionStorage"] as const) {
    Object.defineProperty(globalThis, key, {
      value: dom.window[key],
      configurable: true,
      writable: true,
    });
  }
}
