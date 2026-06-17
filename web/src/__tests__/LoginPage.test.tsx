import { describe, test, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";

// Mock the api client so the form never hits a real backend.
vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return { ...actual, loginPassword: vi.fn() };
});

import { LoginPage } from "../pages/LoginPage";
import * as api from "../lib/api";

const loginPassword = vi.mocked(api.loginPassword);

function fillAndSubmit(email = "you@firm.com", password = "correct-horse") {
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: email } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
}

describe("LoginPage", () => {
  beforeEach(() => loginPassword.mockReset());
  afterEach(() => cleanup());

  test("renders email + password fields and a sign-in button", () => {
    render(<LoginPage onAuthenticated={() => {}} />);
    expect(screen.getByLabelText("Email")).toBeTruthy();
    expect(screen.getByLabelText("Password")).toBeTruthy();
    expect(screen.getByRole("button", { name: /sign in/i })).toBeTruthy();
  });

  test("successful login calls onAuthenticated", async () => {
    loginPassword.mockResolvedValue({ ok: true });
    const onAuthenticated = vi.fn();
    render(<LoginPage onAuthenticated={onAuthenticated} />);
    fillAndSubmit();
    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledTimes(1));
    expect(loginPassword).toHaveBeenCalledWith("you@firm.com", "correct-horse");
  });

  test("401 shows the generic credential error and does not authenticate", async () => {
    loginPassword.mockResolvedValue({ ok: false, status: 401, detail: "Invalid email or password." });
    const onAuthenticated = vi.fn();
    render(<LoginPage onAuthenticated={onAuthenticated} />);
    fillAndSubmit();
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/invalid email or password/i));
    expect(onAuthenticated).not.toHaveBeenCalled();
  });

  test("429 shows a lockout message", async () => {
    loginPassword.mockResolvedValue({ ok: false, status: 429, detail: "Too many attempts. Please try again later." });
    render(<LoginPage onAuthenticated={() => {}} />);
    fillAndSubmit();
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/too many attempts/i));
  });
});
