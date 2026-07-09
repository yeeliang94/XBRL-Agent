import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { FileDropzone } from "../components/FileDropzone";

function makeFile(name: string) {
  return new File(["x"], name, { type: "application/octet-stream" });
}

describe("FileDropzone", () => {
  test("renders the label + button and fires onFile when a file is chosen", () => {
    const onFile = vi.fn();
    render(
      <FileDropzone accept=".xlsx" label="Drop your file" buttonLabel="Choose it" onFile={onFile} />,
    );
    expect(screen.getByText("Drop your file")).toBeTruthy();
    const input = screen.getByLabelText("Choose file") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [makeFile("book.xlsx")] } });
    expect(onFile).toHaveBeenCalledOnce();
    expect(onFile.mock.calls[0][0].name).toBe("book.xlsx");
  });

  test("fires onFile on drop", () => {
    const onFile = vi.fn();
    render(<FileDropzone accept=".pdf" label="Drop" onFile={onFile} testId="dz" />);
    const zone = screen.getByTestId("dz");
    fireEvent.drop(zone, { dataTransfer: { files: [makeFile("doc.pdf")] } });
    expect(onFile).toHaveBeenCalledOnce();
    expect(onFile.mock.calls[0][0].name).toBe("doc.pdf");
  });

  test("disabled blocks drop and the button", () => {
    const onFile = vi.fn();
    render(<FileDropzone accept=".pdf" label="Drop" onFile={onFile} disabled testId="dz" />);
    fireEvent.drop(screen.getByTestId("dz"), { dataTransfer: { files: [makeFile("doc.pdf")] } });
    expect(onFile).not.toHaveBeenCalled();
    expect(screen.getByRole("button")).toBeDisabled();
  });
});
