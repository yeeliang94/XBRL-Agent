import { describe, test, expect } from "vitest";
import { displayModelId } from "../lib/modelId";

describe("displayModelId", () => {
  test("null and empty render em-dash", () => {
    expect(displayModelId(null)).toBe("—");
    expect(displayModelId(undefined)).toBe("—");
    expect(displayModelId("")).toBe("—");
  });

  test("clean id passes through unchanged", () => {
    expect(displayModelId("gemini-3-flash-preview")).toBe("gemini-3-flash-preview");
    expect(displayModelId("google-gla:gemini-3-flash-preview")).toBe("google-gla:gemini-3-flash-preview");
    expect(displayModelId("gpt-5.4")).toBe("gpt-5.4");
  });

  test("PydanticAI repr with model_name kwarg is unwrapped", () => {
    expect(
      displayModelId("GoogleModel(model_name='gemini-3-flash-preview', provider=...)"),
    ).toBe("gemini-3-flash-preview");
    expect(
      displayModelId(`OpenAIChatModel(model_name="gpt-5.4", provider=OpenAIProvider(...))`),
    ).toBe("gpt-5.4");
  });

  test("repr with positional first arg is unwrapped", () => {
    expect(displayModelId("GoogleModel(gemini-3-flash-preview)")).toBe("gemini-3-flash-preview");
    expect(displayModelId("AnthropicModel(claude-sonnet-4-6, provider=...)")).toBe("claude-sonnet-4-6");
  });
});
