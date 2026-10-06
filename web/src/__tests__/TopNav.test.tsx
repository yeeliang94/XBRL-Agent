import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { TopNav } from "../components/TopNav";

describe("Documents navigation", () => {
  test("has one stable workspace destination without processing indicators", () => {
    const onViewChange = vi.fn();
    const { rerender } = render(<TopNav view="extract" onViewChange={onViewChange} />);
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    expect(within(nav).getAllByRole("link")).toHaveLength(1);
    const documents = within(nav).getByRole("link", { name: "Documents" });
    expect(documents).toHaveAttribute("href", "/");
    expect(documents).toHaveAttribute("aria-current", "page");
    expect(within(nav).queryByRole("status")).toBeNull();
    fireEvent.click(documents);
    expect(onViewChange).toHaveBeenCalledWith("extract");
    rerender(<TopNav view="settings" onViewChange={onViewChange} />);
    expect(documents).not.toHaveAttribute("aria-current");
  });
  test("modified clicks retain normal browser link behaviour", () => {
    const onViewChange = vi.fn();
    render(<TopNav view="history" onViewChange={onViewChange} />);
    const documents = screen.getByRole("link", { name: "Documents" });
    fireEvent.click(documents, { metaKey: true });
    fireEvent.click(documents, { ctrlKey: true });
    fireEvent.click(documents, { button: 1 });
    expect(onViewChange).not.toHaveBeenCalled();
  });
});
