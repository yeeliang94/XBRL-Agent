import { describe, test, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StreamingText } from "../components/StreamingText";

describe("StreamingText", () => {
  test("renders text_delta content as it arrives", () => {
    render(<StreamingText text="I found the SOFP data." isStreaming={false} />);
    expect(screen.getByText("I found the SOFP data.")).toBeInTheDocument();
  });

  test("shows blinking caret at end while streaming", () => {
    const { container } = render(
      <StreamingText text="Analyzing..." isStreaming={true} />,
    );
    const caret = container.querySelector("[data-testid='streaming-caret']");
    expect(caret).toBeInTheDocument();
    expect(caret?.getAttribute("style")).toContain("blink-caret");
  });

  test("removes caret when not streaming", () => {
    const { container } = render(
      <StreamingText text="Done." isStreaming={false} />,
    );
    const caret = container.querySelector("[data-testid='streaming-caret']");
    expect(caret).not.toBeInTheDocument();
  });

  test("uses body font from theme", () => {
    const { container } = render(
      <StreamingText text="test" isStreaming={false} />,
    );
    const el = container.firstElementChild as HTMLElement;
    expect(el.style.fontFamily).toContain("Arial");
  });
});
