// Normalise a persisted model identifier for display in the history UI.
//
// Early runs sometimes stored the PydanticAI model repr() instead of a clean
// id — e.g. `GoogleModel(model_name='gemini-3-flash-preview')` or
// `GoogleModel(gemini-3-flash-preview)`. New runs persist the canonical id,
// so this function is a defensive no-op for them. Extracted out of
// `RunDetailView.tsx` for shared testability.

export function displayModelId(raw: string | null | undefined): string {
  if (!raw) return "—";
  // Pattern 1: named kwarg — `Model(model_name='gemini-3-flash-preview', ...)`
  const reprMatch = /^[A-Za-z_][A-Za-z0-9_]*\(.*model_name=['"]([^'"]+)['"]/.exec(raw);
  if (reprMatch) return reprMatch[1];
  // Pattern 2: positional arg — `Model(gemini-3-flash-preview)` / `Model(id, …)`
  const positional = /^[A-Za-z_][A-Za-z0-9_]*\(([A-Za-z0-9_\-.:/]+)[,)]/.exec(raw);
  if (positional) return positional[1];
  return raw;
}
