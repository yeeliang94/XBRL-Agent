import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { UploadPanel } from "../components/UploadPanel";

const noop = vi.fn().mockResolvedValue({ session_id: "s1", filename: "test.pdf" });

describe("UploadPanel — P1 enhancements", () => {
  test("shows CSS spinner when isRunning=true", () => {
    const { container } = render(
      <UploadPanel
        onUpload={noop}
        isRunning={true}
        filename="test.pdf"

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

        startTime={null}
      />,
    );
    const button = screen.getByText("Choose file");
    // jsdom converts hex #FD5108 to rgb
    expect(button.style.backgroundColor).toBe("rgb(253, 81, 8)");
  });

  test("accepts a .docx file (converted server-side) and calls onUpload", async () => {
    const onUpload = vi.fn().mockResolvedValue({ session_id: "s1", filename: "fs.docx" });
    render(
      <UploadPanel onUpload={onUpload} isRunning={false} filename={null} startTime={null} />,
    );
    const input = screen.getByLabelText(/upload document/i) as HTMLInputElement;
    expect(input.getAttribute("accept")).toBe(".pdf,.docx");
    const docx = new File(["x"], "fs.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });
    fireEvent.change(input, { target: { files: [docx] } });
    await waitFor(() => expect(onUpload).toHaveBeenCalledWith(docx));
    expect(screen.queryByText(/only pdf/i)).not.toBeInTheDocument();
  });

  test("rejects an .xlsx file client-side without calling onUpload", () => {
    const onUpload = vi.fn();
    render(
      <UploadPanel onUpload={onUpload} isRunning={false} filename={null} startTime={null} />,
    );
    const input = screen.getByLabelText(/upload document/i) as HTMLInputElement;
    const xlsx = new File(["x"], "book.xlsx", { type: "application/octet-stream" });
    fireEvent.change(input, { target: { files: [xlsx] } });
    expect(onUpload).not.toHaveBeenCalled();
    expect(screen.getByText(/only pdf or word/i)).toBeInTheDocument();
  });

  test("drag-drop zone uses grey50 background with grey200 dashed border", () => {
    const { container } = render(
      <UploadPanel
        onUpload={noop}
        isRunning={false}
        filename={null}

        startTime={null}
      />,
    );
    const dropZone = container.querySelector("[data-testid='drop-zone']");
    expect(dropZone).toBeInTheDocument();
    // jsdom converts hex to rgb in style attributes
    expect(dropZone?.getAttribute("style")).toContain("rgb(250, 250, 250)"); // grey50 #FAFAFA
    expect(dropZone?.getAttribute("style")).toContain("rgb(222, 222, 222)"); // grey200 #DEDEDE
  });

  test("drag highlight stays active while crossing children and clears on exit", () => {
    render(
      <UploadPanel onUpload={noop} isRunning={false} filename={null} startTime={null} />,
    );
    const dropZone = screen.getByTestId("drop-zone");
    const child = dropZone.querySelector("span")!;

    fireEvent.dragEnter(dropZone);
    expect(dropZone.style.backgroundColor).toBe("rgb(255, 245, 237)");

    fireEvent.dragEnter(child);
    fireEvent.dragLeave(dropZone);
    expect(dropZone.style.backgroundColor).toBe("rgb(255, 245, 237)");

    fireEvent.dragLeave(child);
    expect(dropZone.style.backgroundColor).toBe("rgb(250, 250, 250)");
  });
});
