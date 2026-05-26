// Minimal ambient declaration so test files can read source files via the
// node runtime under vitest without pulling in the full @types/node package
// (the app itself ships no node code). Only the surface actually used is
// declared.
declare module "node:fs" {
  export function readFileSync(path: string, encoding: "utf8"): string;
}
