import { describe, test, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AgentTabs, type AgentTabState } from "../components/AgentTabs";

function makeAgentStates(): Record<string, AgentTabState> {
  return {
    scout: { agentId: "scout", label: "Scout", status: "complete", role: "scout" },
    sofp_0: { agentId: "sofp_0", label: "SOFP", status: "running", role: "SOFP" },
    sopl_0: { agentId: "sopl_0", label: "SOPL", status: "pending", role: "SOPL" },
    soci_0: { agentId: "soci_0", label: "SOCI", status: "pending", role: "SOCI" },
    socf_0: { agentId: "socf_0", label: "SOCF", status: "pending", role: "SOCF" },
    socie_0: { agentId: "socie_0", label: "SOCIE", status: "pending", role: "SOCIE" },
    validator: { agentId: "validator", label: "Validator", status: "pending", role: "validator" },
  };
}

describe("AgentTabs", () => {
  test("renders all tabs", () => {
    const agents = makeAgentStates();
    render(
      <AgentTabs
        agents={agents}
        tabOrder={Object.keys(agents)}
        activeTab="sofp_0"
        onTabClick={() => {}}
      />,
    );

    expect(screen.getByText("Scout")).toBeTruthy();
    expect(screen.getByText("SOFP")).toBeTruthy();
    expect(screen.getByText("SOPL")).toBeTruthy();
    expect(screen.getByText("SOCI")).toBeTruthy();
    expect(screen.getByText("SOCF")).toBeTruthy();
    expect(screen.getByText("SOCIE")).toBeTruthy();
    expect(screen.getByText("Validator")).toBeTruthy();
  });

  test("clicking a tab calls onTabClick with the agentId", () => {
    const agents = makeAgentStates();
    const clicked: string[] = [];
    render(
      <AgentTabs
        agents={agents}
        tabOrder={Object.keys(agents)}
        activeTab="sofp_0"
        onTabClick={(id) => clicked.push(id)}
      />,
    );

    fireEvent.click(screen.getByText("SOPL"));
    expect(clicked).toEqual(["sopl_0"]);
  });

  test("active tab has aria-selected=true", () => {
    const agents = makeAgentStates();
    render(
      <AgentTabs
        agents={agents}
        tabOrder={Object.keys(agents)}
        activeTab="sofp_0"
        onTabClick={() => {}}
      />,
    );

    const sofpTab = screen.getByRole("tab", { name: /SOFP/ });
    expect(sofpTab.getAttribute("aria-selected")).toBe("true");

    const soplTab = screen.getByRole("tab", { name: /SOPL/ });
    expect(soplTab.getAttribute("aria-selected")).toBe("false");
  });

  test("status badges reflect agent state", () => {
    const agents = makeAgentStates();
    render(
      <AgentTabs
        agents={agents}
        tabOrder={Object.keys(agents)}
        activeTab="sofp_0"
        onTabClick={() => {}}
      />,
    );

    // Complete shows checkmark
    const scoutTab = screen.getByRole("tab", { name: /Scout/ });
    expect(scoutTab.textContent).toContain("Scout");

    // Running shows spinner indicator
    const sofpTab = screen.getByRole("tab", { name: /SOFP/ });
    expect(sofpTab.querySelector("[data-status='running']")).toBeTruthy();

    // Pending shows dot
    const soplTab = screen.getByRole("tab", { name: /SOPL/ });
    expect(soplTab.querySelector("[data-status='pending']")).toBeTruthy();
  });

  test("failed status renders error badge", () => {
    const agents = makeAgentStates();
    agents.sofp_0.status = "failed";
    render(
      <AgentTabs
        agents={agents}
        tabOrder={Object.keys(agents)}
        activeTab="sofp_0"
        onTabClick={() => {}}
      />,
    );

    const sofpTab = screen.getByRole("tab", { name: /SOFP/ });
    expect(sofpTab.querySelector("[data-status='failed']")).toBeTruthy();
  });

  test("skeleton tabs render for unselected statements", () => {
    const agents: Record<string, AgentTabState> = {
      sofp_0: { agentId: "sofp_0", label: "SOFP", status: "running", role: "SOFP" },
    };
    const skeletonTabs = ["SOPL", "SOCI"];
    render(
      <AgentTabs
        agents={agents}
        tabOrder={["sofp_0"]}
        activeTab="sofp_0"
        onTabClick={() => {}}
        skeletonTabs={skeletonTabs}
      />,
    );

    // Skeleton tabs render but are disabled
    const soplTab = screen.getByRole("tab", { name: /SOPL/ });
    expect(soplTab).toBeTruthy();
    expect(soplTab.getAttribute("aria-disabled")).toBe("true");
  });
});
