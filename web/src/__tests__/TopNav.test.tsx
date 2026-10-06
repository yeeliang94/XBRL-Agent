import { describe, test, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { TopNav } from "../components/TopNav";

describe("Classic workspace navigation", () => {
  test("routes queue, uploads and history with truthful current state", () => {
    const onViewChange = vi.fn();
    const onAdd = vi.fn();
    const { rerender } = render(<TopNav view="extract" onViewChange={onViewChange} onAdd={onAdd} />);
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    expect(within(nav).getAllByRole("link")).toHaveLength(3);
    const documents = within(nav).getByRole("link", { name: "Work queue" });
    expect(documents).toHaveAttribute("href", "/");
    expect(documents).toHaveAttribute("aria-current", "page");
    expect(within(nav).queryByRole("status")).toBeNull();
    fireEvent.click(documents);
    expect(onViewChange).toHaveBeenCalledWith("extract");
    fireEvent.click(within(nav).getByRole("link", { name: "Add documents" }));
    expect(onAdd).toHaveBeenCalledOnce();
    const history = within(nav).getByRole("link", { name: "History" });
    expect(history).toHaveAttribute("href", "/history");
    fireEvent.click(history);
    expect(onViewChange).toHaveBeenCalledWith("history");
    rerender(<TopNav view="extract" extractMode="new" onViewChange={onViewChange} onAdd={onAdd} />);
    expect(within(nav).getByRole("link", { name: "Add documents" })).toHaveAttribute("aria-current", "page");
    expect(documents).not.toHaveAttribute("aria-current");
    rerender(<TopNav view="history" hasDocument onViewChange={onViewChange} />);
    expect(history).not.toHaveAttribute("aria-current");
    rerender(<TopNav view="settings" onViewChange={onViewChange} />);
    expect(documents).not.toHaveAttribute("aria-current");
  });
  test("modified clicks retain normal browser link behaviour", () => {
    const onViewChange = vi.fn();
    render(<TopNav view="history" onViewChange={onViewChange} />);
    const documents = screen.getByRole("link", { name: "Work queue" });
    fireEvent.click(documents, { metaKey: true });
    fireEvent.click(documents, { ctrlKey: true });
    fireEvent.click(documents, { button: 1 });
    expect(onViewChange).not.toHaveBeenCalled();
    const upload = screen.getByRole("link", { name: "Add documents" });
    expect(upload).toHaveAttribute("href", "/#new-extraction");
    const click = new MouseEvent("click", { bubbles: true, cancelable: true });
    fireEvent(upload, click);
    expect(click.defaultPrevented).toBe(false);
  });
});
