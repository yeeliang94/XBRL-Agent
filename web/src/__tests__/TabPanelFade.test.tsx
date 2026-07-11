import { describe, test, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { TabPanelFade } from "../components/TabPanelFade";

afterEach(cleanup);

describe("TabPanelFade", () => {
  test("renders children and carries the fade-in animation", () => {
    render(
      <TabPanelFade tabKey="overview">
        <p>panel body</p>
      </TabPanelFade>,
    );
    const body = screen.getByText("panel body");
    const wrapper = body.parentElement!;
    expect(wrapper.style.animation).toContain("fade-in");
  });

  test("remounts the wrapper when tabKey changes (restarts the animation)", () => {
    const { rerender } = render(
      <TabPanelFade tabKey="a">
        <p data-testid="child">a</p>
      </TabPanelFade>,
    );
    const first = screen.getByTestId("child");
    rerender(
      <TabPanelFade tabKey="b">
        <p data-testid="child">b</p>
      </TabPanelFade>,
    );
    const second = screen.getByTestId("child");
    // A changed key means a brand-new DOM node — the animation plays afresh.
    expect(second).not.toBe(first);
    expect(second.textContent).toBe("b");
  });

  test("does not add a tabpanel role (child keeps its own role)", () => {
    render(
      <TabPanelFade tabKey="x">
        <section role="tabpanel">content</section>
      </TabPanelFade>,
    );
    // Exactly one tabpanel — the section, not the wrapper.
    expect(screen.getAllByRole("tabpanel")).toHaveLength(1);
  });
});
