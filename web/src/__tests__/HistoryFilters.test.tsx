import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { HistoryFilters } from "../components/HistoryFilters";
import type { RunsFilterParams } from "../lib/types";

describe("HistoryFilters", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  test("renders search input, status dropdown, and two date inputs", () => {
    render(<HistoryFilters value={{}} onChange={() => {}} />);
    expect(screen.getByPlaceholderText(/search.*filename/i)).toBeTruthy();
    expect(screen.getByLabelText(/status/i)).toBeTruthy();
    expect(screen.getByLabelText(/from/i)).toBeTruthy();
    expect(screen.getByLabelText(/to/i)).toBeTruthy();
  });

  test("shows current value of q from the value prop", () => {
    render(<HistoryFilters value={{ q: "FINCO" }} onChange={() => {}} />);
    const input = screen.getByPlaceholderText(/search.*filename/i) as HTMLInputElement;
    expect(input.value).toBe("FINCO");
  });

  test("typing in the search box fires onChange debounced", () => {
    const onChange = vi.fn<(next: RunsFilterParams) => void>();
    render(<HistoryFilters value={{}} onChange={onChange} />);

    const input = screen.getByPlaceholderText(/search.*filename/i);
    fireEvent.change(input, { target: { value: "F" } });
    fireEvent.change(input, { target: { value: "FI" } });
    fireEvent.change(input, { target: { value: "FIN" } });

    // Debounced — should not have fired yet (well under 300ms)
    expect(onChange).not.toHaveBeenCalled();

    // Flush the debounce timer
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ q: "FIN" }));
  });

  test("status dropdown change fires onChange immediately", () => {
    const onChange = vi.fn<(next: RunsFilterParams) => void>();
    render(<HistoryFilters value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText(/status/i), { target: { value: "completed" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ status: "completed" }));
  });

  test("status dropdown includes 'completed_with_errors' option", () => {
    // Backend emits this status (server.py:876) when extraction succeeded but
    // a cross-check or merge step failed. Users must be able to filter for it.
    render(<HistoryFilters value={{}} onChange={() => {}} />);
    const select = screen.getByLabelText(/status/i) as HTMLSelectElement;
    const optionValues = Array.from(select.options).map((o) => o.value);
    expect(optionValues).toContain("completed_with_errors");
  });

  test("date-from change fires onChange", () => {
    const onChange = vi.fn<(next: RunsFilterParams) => void>();
    render(<HistoryFilters value={{}} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText(/from/i), { target: { value: "2026-01-01" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ dateFrom: "2026-01-01" }));
  });

  test("clearing the search input passes empty string (debounced)", () => {
    const onChange = vi.fn<(next: RunsFilterParams) => void>();
    render(<HistoryFilters value={{ q: "FINCO" }} onChange={onChange} />);

    const input = screen.getByPlaceholderText(/search.*filename/i);
    fireEvent.change(input, { target: { value: "" } });

    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ q: "" }));
  });

  test("status change during debounce window is preserved when q fires", () => {
    // Regression for the stale-closure bug. Sequence:
    //   1. Parent value = {q: "", status: undefined}
    //   2. User types "F" → debounced q update scheduled
    //   3. BEFORE debounce fires, parent value updates to
    //      {q: "", status: "completed"} (e.g., user picks status)
    //   4. Debounce fires — must NOT clobber the new status
    let value: RunsFilterParams = { q: "", status: undefined };
    const onChange = vi.fn((next: RunsFilterParams) => {
      value = next;
    });
    const { rerender } = render(<HistoryFilters value={value} onChange={onChange} />);

    const input = screen.getByPlaceholderText(/search.*filename/i);
    fireEvent.change(input, { target: { value: "F" } });

    // Parent updates value to add a status mid-debounce. Re-render with the
    // updated prop so the component sees the latest value.
    value = { q: "", status: "completed" };
    rerender(<HistoryFilters value={value} onChange={onChange} />);

    act(() => {
      vi.advanceTimersByTime(400);
    });

    // The debounced fire must include BOTH the new q AND the new status.
    const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0];
    expect(lastCall.q).toBe("F");
    expect(lastCall.status).toBe("completed");
  });
});
