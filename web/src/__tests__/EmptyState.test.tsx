import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { EmptyState } from "../components/EmptyState";

describe("EmptyState", () => {
  test("renders title, explanation, and action", () => {
    render(
      <EmptyState
        title="No evaluation suites yet"
        explanation="Create one to score extraction accuracy across a document set."
        action={<button>Create suite</button>}
      />,
    );
    expect(screen.getByText("No evaluation suites yet")).toBeInTheDocument();
    expect(
      screen.getByText("Create one to score extraction accuracy across a document set."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create suite" })).toBeInTheDocument();
  });

  test("title and explanation are separate elements (no run-together text)", () => {
    render(<EmptyState title="No runs" explanation="Upload a PDF to start." />);
    const title = screen.getByText("No runs");
    const explanation = screen.getByText("Upload a PDF to start.");
    expect(title).not.toBe(explanation);
    expect(title.textContent).toBe("No runs");
  });

  test("action region is omitted when no action exists", () => {
    render(<EmptyState title="Nothing here" />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
