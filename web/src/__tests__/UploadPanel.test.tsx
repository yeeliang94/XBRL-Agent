import { describe, test, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { UploadPanel } from "../components/UploadPanel";

const noop = vi.fn().mockResolvedValue({ session_id: "s1", filename: "test.pdf" });

describe("UploadPanel — P1 enhancements", () => {
  test("shows CSS spinner when isRunning=true", () => {
    const { container } = render(
      <UploadPanel
        onUpload={noop}
        isRunning={true}
        filename="test.pdf"
        onRun={() => {}}
        canRun={false}
        startTime={Date.now()}
      />,
    );
    const spinner = container.querySelector("[data-testid='run-spinner']");
    expect(spinner).toBeInTheDocument();
    expect(spinner?.getAttribute("style")).toContain("animation");
  });

  test("shows ElapsedTimer when isRunning=true with startTime", () => {
    render(
      <UploadPanel
        onUpload={noop}
        isRunning={true}
        filename="test.pdf"
        onRun={() => {}}
        canRun={false}
        startTime={Date.now()}
      />,
    );
    // Timer renders 00:00 initially
    expect(screen.getByText("00:00")).toBeInTheDocument();
  });

  test("hides spinner and timer when isRunning=false", () => {
    const { container } = render(
      <UploadPanel
        onUpload={noop}
        isRunning={false}
        filename="test.pdf"
        onRun={() => {}}
        canRun={true}
        startTime={null}
      />,
    );
    const spinner = container.querySelector("[data-testid='run-spinner']");
    expect(spinner).not.toBeInTheDocument();
    expect(screen.queryByText("00:00")).not.toBeInTheDocument();
  });

  test("upload button uses PwC orange500 background", () => {
    render(
      <UploadPanel
        onUpload={noop}
        isRunning={false}
        filename={null}
        onRun={() => {}}
        canRun={false}
        startTime={null}
      />,
    );
    const button = screen.getByRole("button", { name: /choose pdf/i });
    // jsdom converts hex #FD5108 to rgb
    expect(button.style.backgroundColor).toBe("rgb(253, 81, 8)");
  });

  test("drag-drop zone uses grey50 background with grey200 dashed border", () => {
    const { container } = render(
      <UploadPanel
        onUpload={noop}
        isRunning={false}
        filename={null}
        onRun={() => {}}
        canRun={false}
        startTime={null}
      />,
    );
    const dropZone = container.querySelector("[data-testid='drop-zone']");
    expect(dropZone).toBeInTheDocument();
    // jsdom converts hex to rgb in style attributes
    expect(dropZone?.getAttribute("style")).toContain("rgb(245, 247, 248)"); // grey50
    expect(dropZone?.getAttribute("style")).toContain("rgb(223, 227, 230)"); // grey200
  });
});
