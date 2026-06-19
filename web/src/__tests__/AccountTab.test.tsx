import { describe, test, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, changePassword: vi.fn() };
});

import { AccountTab } from "../components/AccountTab";
import * as api from "../lib/api";

beforeEach(() => vi.clearAllMocks());

function fill(label: RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe("AccountTab", () => {
  test("happy path calls changePassword with current + new", async () => {
    (api.changePassword as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
    render(<AccountTab />);
    fill(/current password/i, "old-password");
    fill(/^new password$/i, "brand-new-password");
    fill(/confirm new password/i, "brand-new-password");
    fireEvent.click(screen.getByRole("button", { name: /change password/i }));

    await waitFor(() =>
      expect(api.changePassword).toHaveBeenCalledWith("old-password", "brand-new-password"));
    await waitFor(() => expect(screen.getByText(/password changed/i)).toBeInTheDocument());
  });

  test("mismatched confirmation blocks the call and shows an error", async () => {
    render(<AccountTab />);
    fill(/current password/i, "old-password");
    fill(/^new password$/i, "brand-new-password");
    fill(/confirm new password/i, "different-password");
    fireEvent.click(screen.getByRole("button", { name: /change password/i }));

    await waitFor(() => expect(screen.getByText(/do not match/i)).toBeInTheDocument());
    expect(api.changePassword).not.toHaveBeenCalled();
  });

  test("too-short new password blocks the call", async () => {
    render(<AccountTab />);
    fill(/current password/i, "old-password");
    fill(/^new password$/i, "short");
    fill(/confirm new password/i, "short");
    fireEvent.click(screen.getByRole("button", { name: /change password/i }));

    await waitFor(() =>
      expect(screen.getByText(/new password must be at least 8 characters/i)).toBeInTheDocument());
    expect(api.changePassword).not.toHaveBeenCalled();
  });

  test("server error (wrong current) surfaces the detail message", async () => {
    (api.changePassword as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("Current password is incorrect."));
    render(<AccountTab />);
    fill(/current password/i, "WRONG");
    fill(/^new password$/i, "brand-new-password");
    fill(/confirm new password/i, "brand-new-password");
    fireEvent.click(screen.getByRole("button", { name: /change password/i }));

    await waitFor(() =>
      expect(screen.getByText(/current password is incorrect/i)).toBeInTheDocument());
  });
});
