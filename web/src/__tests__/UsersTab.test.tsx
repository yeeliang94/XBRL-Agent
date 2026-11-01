import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
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
    adminAuditLog: vi.fn(),
  };
});

import { UsersTab } from "../components/UsersTab";
import * as api from "../lib/api";
import { ApiError } from "../lib/errors";

const NOW = new Date("2026-11-01T12:00:00");
const daysAgo = (n: number) => new Date(NOW.getTime() - n * 86_400_000).toISOString();

function user(overrides: Partial<api.AdminUser>): api.AdminUser {
  return {
    email: "x@firm.com", display_name: "X", disabled: false, is_admin: false,
    has_password: true, created_at: "", password_set_at: null,
    must_change_password: false, last_sign_in_at: null, ...overrides,
  };
}

const USERS: api.AdminUser[] = [
  user({ email: "ana@firm.com", display_name: "Ana Lim", is_admin: true, last_sign_in_at: daysAgo(0) }),
  user({ email: "ben@firm.com", display_name: "Ben Tan", last_sign_in_at: daysAgo(2) }),
  user({ email: "chris@firm.com", display_name: "Chris Ong", must_change_password: true }),
  user({ email: "dee@firm.com", display_name: "Dee Lee", disabled: true, last_sign_in_at: "2026-09-03T09:00:00" }),
  user({ email: "eve@firm.com", display_name: "Eve Ng", must_change_password: true, last_sign_in_at: daysAgo(1) }),
];

const mocked = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  vi.clearAllMocks();
  mocked(api.adminListUsers).mockResolvedValue(USERS);
  mocked(api.adminAddUser).mockResolvedValue({
    ok: true, user: user({ email: "new@firm.com", display_name: "New Person", must_change_password: true }),
    temporary_password: "k7Q-m2xP-9vRa",
  });
  mocked(api.adminSetDisabled).mockResolvedValue({ ok: true });
  mocked(api.adminResetPassword).mockResolvedValue({ ok: true, temporary_password: "Zz9-reset-Pw" });
  mocked(api.adminSetAdmin).mockResolvedValue({ ok: true, user: USERS[1] });
  mocked(api.adminAuditLog).mockResolvedValue({
    events: [
      { id: 7, at: daysAgo(0), actor_email: "ana@firm.com", actor_name: "Ana Lim", action: "password_reset",
        target: "ben@firm.com", target_name: "Ben Tan", detail: null },
      { id: 6, at: daysAgo(3), actor_email: "ana@firm.com", actor_name: "Ana Lim", action: "issue_updated",
        target: "38", target_name: "", detail: { from: "new", to: "resolved", reply_changed: false } },
    ],
    has_more: false,
  });
});

afterEach(() => {
  vi.useRealTimers();
});

async function renderTab(currentEmail?: string) {
  render(<UsersTab currentEmail={currentEmail} />);
  await screen.findByRole("button", { name: "Manage Ben Tan" });
}

function row(name: string): HTMLElement {
  return screen.getByRole("button", { name: `Manage ${name}` }).closest("tr")!;
}

function openManage(name: string): HTMLElement {
  fireEvent.click(screen.getByRole("button", { name: `Manage ${name}` }));
  return screen.getByRole("dialog", { name });
}

describe("UsersTab", () => {
  test("lists role, status and last sign-in in plain wording", async () => {
    await renderTab("ana@firm.com");
    expect(within(row("Ana Lim")).getByText("(You)", { exact: false })).toBeInTheDocument();
    expect(within(row("Ana Lim")).getByText("Administrator")).toBeInTheDocument();
    expect(within(row("Ana Lim")).getByText("Today")).toBeInTheDocument();
    expect(within(row("Ana Lim")).getByText("Active")).toBeInTheDocument();
    expect(within(row("Ben Tan")).getByText("User")).toBeInTheDocument();
    expect(within(row("Ben Tan")).getByText("2 days ago")).toBeInTheDocument();
    expect(within(row("Chris Ong")).getByText("Not signed in yet")).toBeInTheDocument();
    expect(within(row("Chris Ong")).getByText("—")).toBeInTheDocument();
    expect(within(row("Dee Lee")).getByText("Disabled")).toBeInTheDocument();
    expect(within(row("Dee Lee")).getByText("3 Sep")).toBeInTheDocument();
    // Signed in with a temporary password but has not replaced it yet.
    expect(within(row("Eve Ng")).getByText("Waiting for new password")).toBeInTheDocument();
    expect(within(row("Eve Ng")).getByText("Yesterday")).toBeInTheDocument();
    expect(screen.queryByText(/standard user/i)).toBeNull();
  });

  test("Add person shows the temporary password once and sends no password", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    await renderTab();
    fireEvent.click(screen.getByRole("button", { name: "Add person" }));
    const dialog = screen.getByRole("dialog", { name: "Add person" });
    fireEvent.change(within(dialog).getByLabelText("Email"), { target: { value: "new@firm.com" } });
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "New Person" } });
    expect(within(dialog).getByRole("button", { name: "User" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(within(dialog).getByRole("button", { name: "Add person" }));

    const done = await screen.findByRole("dialog", { name: "New Person can now sign in" });
    expect(api.adminAddUser).toHaveBeenCalledWith({ email: "new@firm.com", display_name: "New Person", is_admin: false });
    expect(within(done).getByLabelText("Temporary password")).toHaveValue("k7Q-m2xP-9vRa");
    fireEvent.click(within(done).getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(within(done).getByRole("button", { name: "Copied" })).toBeInTheDocument());
    expect(writeText).toHaveBeenCalledWith("k7Q-m2xP-9vRa");
    expect(api.adminListUsers).toHaveBeenCalledTimes(2);

    fireEvent.click(within(done).getByRole("button", { name: "Done" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByDisplayValue("k7Q-m2xP-9vRa")).toBeNull();
  });

  test("a failed copy says so and selects the password", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    Object.assign(navigator, { clipboard: { writeText } });
    await renderTab();
    fireEvent.click(screen.getByRole("button", { name: "Add person" }));
    const dialog = screen.getByRole("dialog", { name: "Add person" });
    fireEvent.change(within(dialog).getByLabelText("Email"), { target: { value: "new@firm.com" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add person" }));
    const done = await screen.findByRole("dialog", { name: "New Person can now sign in" });
    fireEvent.click(within(done).getByRole("button", { name: "Copy" }));
    expect(await within(done).findByText("Couldn't copy. Select the password and copy it.")).toBeInTheDocument();
    const field = within(done).getByLabelText("Temporary password") as HTMLInputElement;
    expect(field).toHaveFocus();
    expect(field.selectionStart).toBe(0);
    expect(field.selectionEnd).toBe("k7Q-m2xP-9vRa".length);
    expect(within(done).queryByRole("button", { name: "Copied" })).toBeNull();
  });

  test("Add person shows an existing-email error in the dialog", async () => {
    mocked(api.adminAddUser).mockRejectedValue(
      new ApiError("An account with this email already exists.", { status: 409 }));
    await renderTab();
    fireEvent.click(screen.getByRole("button", { name: "Add person" }));
    const dialog = screen.getByRole("dialog", { name: "Add person" });
    fireEvent.change(within(dialog).getByLabelText("Email"), { target: { value: "ben@firm.com" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add person" }));
    expect(await within(dialog).findByText("An account with this email already exists.")).toBeInTheDocument();
  });

  test("Reset password confirms, then shows a new temporary password", async () => {
    await renderTab("ana@firm.com");
    const dialog = openManage("Ben Tan");
    fireEvent.click(within(dialog).getByRole("button", { name: "Reset password" }));
    const confirm = screen.getByRole("dialog", { name: "Reset Ben Tan's password?" });
    expect(api.adminResetPassword).not.toHaveBeenCalled();
    const confirmButton = within(confirm).getByRole("button", { name: "Reset password" });
    expect(confirmButton).not.toHaveClass("app-btn-danger");
    fireEvent.click(confirmButton);
    const done = await screen.findByRole("dialog", { name: "Ben Tan can now sign in" });
    expect(api.adminResetPassword).toHaveBeenCalledWith("ben@firm.com");
    expect(within(done).getByLabelText("Temporary password")).toHaveValue("Zz9-reset-Pw");
  });

  test("Disable account asks for confirmation before signing the person out", async () => {
    await renderTab("ana@firm.com");
    const dialog = openManage("Ben Tan");
    fireEvent.click(within(dialog).getByRole("button", { name: "Disable account" }));
    const confirm = screen.getByRole("dialog", { name: "Disable Ben Tan?" });
    expect(confirm).toHaveTextContent("They are signed out now and cannot sign in.");
    expect(api.adminSetDisabled).not.toHaveBeenCalled();
    fireEvent.click(within(confirm).getByRole("button", { name: "Disable account" }));
    await waitFor(() => expect(api.adminSetDisabled).toHaveBeenCalledWith("ben@firm.com", true));
  });

  test("a disabled person can be enabled", async () => {
    await renderTab("ana@firm.com");
    const dialog = openManage("Dee Lee");
    const enable = within(dialog).getByRole("button", { name: "Enable account" });
    // Enabling is not destructive: secondary button, no destructive class.
    expect(enable).toHaveClass("app-btn-secondary");
    expect(enable).not.toHaveClass("app-btn-danger");
    fireEvent.click(enable);
    await waitFor(() => expect(api.adminSetDisabled).toHaveBeenCalledWith("dee@firm.com", false));
  });

  test("your own row cannot be disabled or demoted", async () => {
    await renderTab("ana@firm.com");
    const dialog = openManage("Ana Lim");
    const disable = within(dialog).getByRole("button", { name: "Disable account" });
    const demote = within(dialog).getByRole("button", { name: "User" });
    for (const control of [disable, demote]) {
      // Focusable so keyboard and screen-reader users reach the reason.
      expect(control).not.toBeDisabled();
      expect(control).toHaveAttribute("aria-disabled", "true");
      expect(control).toHaveAttribute("data-tooltip", "You can't change your own access.");
      expect(control).toHaveAccessibleDescription("You can't change your own access.");
      control.focus();
      expect(control).toHaveFocus();
      fireEvent.click(control);
    }
    expect(screen.queryByRole("dialog", { name: /Disable Ana Lim|Remove administrator role/ })).toBeNull();
    expect(api.adminSetDisabled).not.toHaveBeenCalled();
    expect(api.adminSetAdmin).not.toHaveBeenCalled();
    expect(within(dialog).queryByRole("button", { name: "Reset password" })).toBeNull();
    expect(within(dialog).getByText("Change your password in Account.")).toBeInTheDocument();
    expect(api.adminResetPassword).not.toHaveBeenCalled();
  });

  test("a role change confirms and shows the last-admin refusal in the dialog", async () => {
    mocked(api.adminSetAdmin).mockRejectedValue(
      new ApiError("Cannot demote the only remaining admin.", { status: 409 }));
    await renderTab();
    const dialog = openManage("Ana Lim");
    fireEvent.click(within(dialog).getByRole("button", { name: "User" }));
    const confirm = screen.getByRole("dialog", { name: "Remove administrator role from Ana Lim?" });
    fireEvent.click(within(confirm).getByRole("button", { name: "Remove administrator role" }));
    expect(await within(dialog).findByText("Cannot demote the only remaining admin.")).toBeInTheDocument();
    expect(api.adminSetAdmin).toHaveBeenCalledWith("ana@firm.com", false);
  });

  test("Escape closes Manage person", async () => {
    await renderTab();
    openManage("Ben Tan");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  test("audit log loads only when opened and reads as sentences", async () => {
    await renderTab();
    expect(api.adminAuditLog).not.toHaveBeenCalled();
    const toggle = screen.getByRole("button", { name: "Audit log" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(await screen.findByText("Ana Lim reset Ben Tan's password")).toBeInTheDocument();
    expect(screen.getByText("Ana Lim marked issue 38 resolved")).toBeInTheDocument();
    expect(api.adminAuditLog).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "Show more" })).toBeNull();
  });

  test("audit log pages with Show more and has an empty state", async () => {
    mocked(api.adminAuditLog)
      .mockResolvedValueOnce({
        events: [{ id: 51, at: daysAgo(1), actor_email: "ana@firm.com", actor_name: "", action: "user_added",
          target: "ben@firm.com", target_name: "", detail: { is_admin: false } }],
        has_more: true,
      })
      .mockResolvedValueOnce({
        events: [{ id: 50, at: daysAgo(9), actor_email: "ana@firm.com", actor_name: "Ana Lim", action: "role_changed",
          target: "ben@firm.com", target_name: "Ben Tan", detail: { is_admin: true } }],
        has_more: false,
      });
    await renderTab();
    fireEvent.click(screen.getByRole("button", { name: "Audit log" }));
    expect(await screen.findByText("ana@firm.com added ben@firm.com")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show more" }));
    expect(await screen.findByText("Ana Lim made Ben Tan an administrator")).toBeInTheDocument();
    expect(api.adminAuditLog).toHaveBeenLastCalledWith(51);
  });

  test("empty audit log says so", async () => {
    mocked(api.adminAuditLog).mockResolvedValue({ events: [], has_more: false });
    await renderTab();
    fireEvent.click(screen.getByRole("button", { name: "Audit log" }));
    expect(await screen.findByText("No changes recorded yet.")).toBeInTheDocument();
  });
});
