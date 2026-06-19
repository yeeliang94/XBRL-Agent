import { describe, test, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    adminListUsers: vi.fn(),
    adminAddUser: vi.fn(),
    adminSetDisabled: vi.fn(),
    adminResetPassword: vi.fn(),
    adminSetAdmin: vi.fn(),
  };
});

import { UsersTab } from "../components/UsersTab";
import * as api from "../lib/api";

const USERS = [
  { email: "admin@firm.com", display_name: "Admin", disabled: false, is_admin: true, has_password: true, created_at: "", password_set_at: null },
  { email: "user@firm.com", display_name: "User", disabled: false, is_admin: false, has_password: true, created_at: "", password_set_at: null },
];

beforeEach(() => {
  vi.clearAllMocks();
  (api.adminListUsers as ReturnType<typeof vi.fn>).mockResolvedValue(USERS);
  (api.adminAddUser as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true, user: USERS[1] });
  (api.adminSetDisabled as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
  (api.adminResetPassword as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
  (api.adminSetAdmin as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true, user: USERS[1] });
});

describe("UsersTab", () => {
  test("renders the user list", async () => {
    render(<UsersTab />);
    await waitFor(() => expect(screen.getByText("admin@firm.com")).toBeInTheDocument());
    expect(screen.getByText("user@firm.com")).toBeInTheDocument();
  });

  test("disable button calls adminSetDisabled(email, true)", async () => {
    render(<UsersTab />);
    await waitFor(() => expect(screen.getByText("user@firm.com")).toBeInTheDocument());
    const userRow = screen.getByText("user@firm.com").closest("tr")!;
    fireEvent.click(within(userRow).getByRole("button", { name: /disable/i }));
    await waitFor(() => expect(api.adminSetDisabled).toHaveBeenCalledWith("user@firm.com", true));
  });

  test("make admin button calls adminSetAdmin(email, true)", async () => {
    render(<UsersTab />);
    await waitFor(() => expect(screen.getByText("user@firm.com")).toBeInTheDocument());
    const userRow = screen.getByText("user@firm.com").closest("tr")!;
    fireEvent.click(within(userRow).getByRole("button", { name: /make admin/i }));
    await waitFor(() => expect(api.adminSetAdmin).toHaveBeenCalledWith("user@firm.com", true));
  });

  test("add user submits email + name + password + is_admin", async () => {
    render(<UsersTab />);
    await waitFor(() => expect(screen.getByText("admin@firm.com")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/new user email/i), { target: { value: "new@firm.com" } });
    fireEvent.change(screen.getByLabelText(/new user display name/i), { target: { value: "New" } });
    fireEvent.change(screen.getByLabelText(/new user password/i), { target: { value: "longenough" } });
    fireEvent.click(screen.getByRole("button", { name: /^add user$/i }));
    await waitFor(() => expect(api.adminAddUser).toHaveBeenCalledWith({
      email: "new@firm.com", display_name: "New", password: "longenough", is_admin: false,
    }));
  });

  test("add user with a too-short password is blocked client-side", async () => {
    render(<UsersTab />);
    await waitFor(() => expect(screen.getByText("admin@firm.com")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/new user email/i), { target: { value: "new@firm.com" } });
    fireEvent.change(screen.getByLabelText(/new user password/i), { target: { value: "short" } });
    fireEvent.click(screen.getByRole("button", { name: /^add user$/i }));
    await waitFor(() => expect(screen.getByText(/at least 8 characters/i)).toBeInTheDocument());
    expect(api.adminAddUser).not.toHaveBeenCalled();
  });

  test("the 409 last-admin guard error is surfaced inline", async () => {
    (api.adminSetAdmin as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("Cannot demote the only remaining admin. Promote another account first."));
    render(<UsersTab />);
    await waitFor(() => expect(screen.getByText("admin@firm.com")).toBeInTheDocument());
    const adminRow = screen.getByText("admin@firm.com").closest("tr")!;
    fireEvent.click(within(adminRow).getByRole("button", { name: /revoke admin/i }));
    await waitFor(() =>
      expect(screen.getByText(/only remaining admin/i)).toBeInTheDocument());
  });
});
