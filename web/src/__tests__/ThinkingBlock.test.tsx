import { describe, test, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThinkingBlock } from "../components/ThinkingBlock";
import type { ThinkingBlock as ThinkingBlockType } from "../lib/types";

const block: ThinkingBlockType = {
  id: "think_1",
  content: "Let me analyze the SOFP fields in this financial statement. I need to identify...",
  summary: "Let me analyze the SOFP fields in this financial statement",
  timestamp: Date.now() - 5000,
  phase: "reading_template",
  durationMs: 2400,
};

describe("ThinkingBlock", () => {
  test("renders streaming thinking text in monospace font", () => {
    const { container } = render(
      <ThinkingBlock block={null} isStreaming={true} streamingContent="Analyzing..." />,
    );
    const text = container.querySelector("[data-testid='thinking-content']");
    expect(text).toBeInTheDocument();
    expect(text?.textContent).toContain("Analyzing...");
    expect(text?.getAttribute("style")).toContain("SF Mono");
  });

  test('shows "Thinking..." label with purple left border while streaming', () => {
    const { container } = render(
      <ThinkingBlock block={null} isStreaming={true} streamingContent="Analyzing..." />,
    );
    expect(screen.getByText("Thinking...")).toBeInTheDocument();
    // Purple left border (#7C3AED → rgb(124, 58, 237))
    const wrapper = container.querySelector("[data-testid='thinking-wrapper']");
    expect(wrapper?.getAttribute("style")).toContain("rgb(124, 58, 237)");
  });

  test("auto-collapses when thinking_end event received (block provided, not streaming)", () => {
    render(
      <ThinkingBlock block={block} isStreaming={false} streamingContent="" />,
    );
    // Should show summary, not full content
    expect(screen.getByText(/Let me analyze the SOFP/)).toBeInTheDocument();
    // Should show "Thought for" duration text
    expect(screen.getByText(/Thought for/)).toBeInTheDocument();
  });

  test("collapsed state shows summary text and duration", () => {
    render(
      <ThinkingBlock block={block} isStreaming={false} streamingContent="" />,
    );
    expect(screen.getByText(/Thought for/)).toBeInTheDocument();
    expect(screen.getByText(block.summary)).toBeInTheDocument();
  });

  test("expands on click to show full thinking text", () => {
    render(
      <ThinkingBlock block={block} isStreaming={false} streamingContent="" />,
    );
    // Click the collapsed block to expand
    const toggle = screen.getByRole("button");
    fireEvent.click(toggle);
    // Full content should now be visible
    expect(screen.getByText(block.content)).toBeInTheDocument();
  });

  test("uses pwc.thinking color for left border", () => {
    const { container } = render(
      <ThinkingBlock block={block} isStreaming={false} streamingContent="" />,
    );
    const wrapper = container.querySelector("[data-testid='thinking-wrapper']");
    // #7C3AED → rgb(124, 58, 237)
    expect(wrapper?.getAttribute("style")).toContain("rgb(124, 58, 237)");
  });

  test("uses pwc.grey50 background", () => {
    const { container } = render(
      <ThinkingBlock block={block} isStreaming={false} streamingContent="" />,
    );
    const wrapper = container.querySelector("[data-testid='thinking-wrapper']");
    // #F5F7F8 → rgb(245, 247, 248)
    expect(wrapper?.getAttribute("style")).toContain("rgb(245, 247, 248)");
  });

  test("shows fixed server-measured thinking duration, not time-since-end", () => {
    // 2400ms → "2.4s"
    const { rerender, unmount } = render(
      <ThinkingBlock block={block} isStreaming={false} streamingContent="" />,
    );
    expect(screen.getByText("Thought for 2.4s")).toBeInTheDocument();

    // Small durations render in ms
    const fastBlock = { ...block, durationMs: 450 };
    rerender(<ThinkingBlock block={fastBlock} isStreaming={false} streamingContent="" />);
    expect(screen.getByText("Thought for 450ms")).toBeInTheDocument();

    // Missing duration → label hidden (never shows misleading info)
    const noDurBlock = { ...block, durationMs: null };
    rerender(<ThinkingBlock block={noDurBlock} isStreaming={false} streamingContent="" />);
    expect(screen.queryByText(/Thought for/)).not.toBeInTheDocument();

    unmount();
  });

  test("applies fade-in animation when appearing", () => {
    const { container } = render(
      <ThinkingBlock block={null} isStreaming={true} streamingContent="test" />,
    );
    const wrapper = container.querySelector("[data-testid='thinking-wrapper']");
    expect(wrapper?.getAttribute("style")).toContain("fade-in");
  });
});
