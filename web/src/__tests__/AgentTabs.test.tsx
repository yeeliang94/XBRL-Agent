import { describe, test, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AgentTabs, areAgentTabsPropsEqual, type AgentTabState } from "../components/AgentTabs";
import type { AgentTabsProps } from "../components/AgentTabs";

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

  // Peer-review fix #5: the custom memo comparator must treat two prop
  // objects as equal when only non-tab fields differ — specifically, when
  // `state.agents` has been rebuilt after a token_update but the 4
  // tab-relevant fields (agentId/label/status/role) are unchanged. Without
  // this, React.memo ref-compares and re-renders on every event.
  describe("areAgentTabsPropsEqual", () => {
    const baseAgent: AgentTabState = {
      agentId: "sofp_0",
      label: "SOFP",
      status: "running",
      role: "SOFP",
    };
    const baseProps: AgentTabsProps = {
      agents: { sofp_0: baseAgent },
      tabOrder: ["sofp_0"],
      activeTab: "sofp_0",
      onTabClick: () => {},
    };

    test("identical props (same refs) are equal", () => {
      expect(areAgentTabsPropsEqual(baseProps, baseProps)).toBe(true);
    });

    test("tab-irrelevant rebuild (new refs, same content) is equal", () => {
      // Simulates the ExtractView useMemo rebuild after a token_update:
      // fresh object refs, but agent fields match the previous render.
      const next: AgentTabsProps = {
        ...baseProps,
        agents: { sofp_0: { ...baseAgent } },
        tabOrder: ["sofp_0"],
      };
      expect(areAgentTabsPropsEqual(baseProps, next)).toBe(true);
    });

    test("status change invalidates equality", () => {
      const next: AgentTabsProps = {
        ...baseProps,
        agents: { sofp_0: { ...baseAgent, status: "complete" } },
      };
      expect(areAgentTabsPropsEqual(baseProps, next)).toBe(false);
    });

    test("label change invalidates equality", () => {
      const next: AgentTabsProps = {
        ...baseProps,
        agents: { sofp_0: { ...baseAgent, label: "SOFP (renamed)" } },
      };
      expect(areAgentTabsPropsEqual(baseProps, next)).toBe(false);
    });

    test("adding an agent invalidates equality", () => {
      const next: AgentTabsProps = {
        ...baseProps,
        agents: {
          sofp_0: { ...baseAgent },
          sopl_0: { agentId: "sopl_0", label: "SOPL", status: "pending", role: "SOPL" },
        },
        tabOrder: ["sofp_0", "sopl_0"],
      };
      expect(areAgentTabsPropsEqual(baseProps, next)).toBe(false);
    });

    test("activeTab change invalidates equality", () => {
      const next: AgentTabsProps = { ...baseProps, activeTab: "other" };
      expect(areAgentTabsPropsEqual(baseProps, next)).toBe(false);
    });

    test("tabOrder reorder invalidates equality", () => {
      const a = { ...baseProps, tabOrder: ["a", "b"], agents: { a: baseAgent, b: baseAgent } };
      const b = { ...baseProps, tabOrder: ["b", "a"], agents: { a: baseAgent, b: baseAgent } };
      expect(areAgentTabsPropsEqual(a, b)).toBe(false);
    });

    // Peer-review regression: `areAgentTabsPropsEqual` compares callback
    // identity, which means parents that pass fresh inline arrows per render
    // neutralize the React.memo wrap. Encode the contract so reviewers see
    // the expectation: callers MUST pass stable refs (useCallback / ref-
    // shielded closures) for the three callback props.
    test("fresh inline callback on next render invalidates equality (stable refs required)", () => {
      const next: AgentTabsProps = {
        ...baseProps,
        // Same shape, different identity — simulates an inline arrow.
        onTabClick: () => {},
      };
      expect(areAgentTabsPropsEqual(baseProps, next)).toBe(false);
    });

    test("same callback reference across renders keeps equality", () => {
      const cb = () => {};
      const a: AgentTabsProps = { ...baseProps, onTabClick: cb, onRerunAgent: cb };
      const b: AgentTabsProps = { ...baseProps, onTabClick: cb, onRerunAgent: cb };
      expect(areAgentTabsPropsEqual(a, b)).toBe(true);
    });
  });

  test("skeleton tabs render in the order provided by the caller (#48)", () => {
    // The caller (App.tsx) is responsible for ordering skeleton tabs to match
    // the user-picked statement order. AgentTabs must preserve that order
    // verbatim so users don't see their selection reshuffled.
    render(
      <AgentTabs
        agents={{}}
        tabOrder={[]}
        activeTab=""
        onTabClick={() => {}}
        skeletonTabs={["SOPL", "SOFP", "SOCIE"]}
      />,
    );
    const tabs = screen.getAllByRole("tab");
    const labels = tabs.map((t) => t.textContent?.trim() ?? "");
    expect(labels).toEqual(["SOPL", "SOFP", "SOCIE"]);
  });

  // -------------------------------------------------------------------------
  // Phase 8: gate statement tabs before the run starts, but always preserve
  // scout/validator "special" tabs whose lifecycle is independent of the
  // statementsInRun array.
  // -------------------------------------------------------------------------

  test("no statement tabs rendered when statementsInRun is empty and agents is empty", () => {
    render(
      <AgentTabs
        agents={{}}
        tabOrder={[]}
        activeTab=""
        onTabClick={() => {}}
        statementsInRun={[]}
      />,
    );
    // No tabs at all — the scaffolding is gone until the user runs something.
    const tabs = screen.queryAllByRole("tab");
    expect(tabs.length).toBe(0);
  });

  test("only statement tabs in statementsInRun are rendered", () => {
    const agents = makeAgentStates();
    // Remove scout + validator so we isolate statement gating.
    delete (agents as Record<string, AgentTabState>).scout;
    delete (agents as Record<string, AgentTabState>).validator;
    render(
      <AgentTabs
        agents={agents}
        tabOrder={Object.keys(agents)}
        activeTab="sofp_0"
        onTabClick={() => {}}
        statementsInRun={["SOFP", "SOPL"]}
      />,
    );
    expect(screen.queryByText("SOFP")).toBeTruthy();
    expect(screen.queryByText("SOPL")).toBeTruthy();
    expect(screen.queryByText("SOCI")).toBeNull();
    expect(screen.queryByText("SOCF")).toBeNull();
    expect(screen.queryByText("SOCIE")).toBeNull();
  });

  test("validator tab still renders when present even if statementsInRun excludes it", () => {
    // Validator is added to agent state on run_complete — its lifecycle is
    // not tied to the statementsInRun array, so it must always show through.
    const agents: Record<string, AgentTabState> = {
      validator: {
        agentId: "validator",
        label: "Validator",
        status: "complete",
        role: "validator",
      },
    };
    render(
      <AgentTabs
        agents={agents}
        tabOrder={["validator"]}
        activeTab="validator"
        onTabClick={() => {}}
        statementsInRun={[]}
      />,
    );
    expect(screen.getByText("Validator")).toBeTruthy();
  });

  test("scout tab still renders when present even if statementsInRun excludes it", () => {
    const agents: Record<string, AgentTabState> = {
      scout: { agentId: "scout", label: "Scout", status: "complete", role: "scout" },
    };
    render(
      <AgentTabs
        agents={agents}
        tabOrder={["scout"]}
        activeTab="scout"
        onTabClick={() => {}}
        statementsInRun={[]}
      />,
    );
    expect(screen.getByText("Scout")).toBeTruthy();
  });

  // ---------------------------------------------------------------------
  // Phase D.3: notes tabs — mirror of the statement-gating contract but
  // keyed by agent_id prefix "notes:" and gated by `notesInRun`.
  // ---------------------------------------------------------------------

  test("notes tabs render after statement tabs and before validator", () => {
    const agents: Record<string, AgentTabState> = {
      sofp_0: { agentId: "sofp_0", label: "SOFP", status: "complete", role: "SOFP" },
      "notes:CORP_INFO": {
        agentId: "notes:CORP_INFO",
        label: "Notes 10: Corp Info",
        status: "running",
        role: "CORP_INFO",
      },
      validator: {
        agentId: "validator",
        label: "Validator",
        status: "complete",
        role: "validator",
      },
    };
    render(
      <AgentTabs
        agents={agents}
        tabOrder={["validator", "sofp_0", "notes:CORP_INFO"]}
        activeTab="sofp_0"
        onTabClick={() => {}}
        statementsInRun={["SOFP"]}
        notesInRun={["CORP_INFO"]}
      />,
    );
    const tabs = screen.getAllByRole("tab");
    const labels = tabs.map((t) => t.textContent?.trim() ?? "");
    // Expected order: [SOFP, Notes 10: Corp Info, Validator]
    expect(labels[0]).toBe("SOFP");
    expect(labels[1]).toContain("Notes 10");
    expect(labels[labels.length - 1]).toContain("Validator");
  });

  test("notes tab is hidden when its role isn't in notesInRun", () => {
    const agents: Record<string, AgentTabState> = {
      "notes:CORP_INFO": {
        agentId: "notes:CORP_INFO",
        label: "Notes 10: Corp Info",
        status: "running",
        role: "CORP_INFO",
      },
    };
    render(
      <AgentTabs
        agents={agents}
        tabOrder={["notes:CORP_INFO"]}
        activeTab="notes:CORP_INFO"
        onTabClick={() => {}}
        statementsInRun={[]}
        notesInRun={[]}
      />,
    );
    expect(screen.queryByText(/Notes 10/)).toBeNull();
  });

  test("notes skeleton tabs render for selected notes that haven't started", () => {
    render(
      <AgentTabs
        agents={{}}
        tabOrder={[]}
        activeTab=""
        onTabClick={() => {}}
        statementsInRun={[]}
        notesInRun={["CORP_INFO", "LIST_OF_NOTES"]}
        notesSkeletons={["Notes 10: Corp Info", "Notes 12: List of Notes"]}
      />,
    );
    expect(screen.getByText("Notes 10: Corp Info")).toBeTruthy();
    expect(screen.getByText("Notes 12: List of Notes")).toBeTruthy();
    // Skeleton tabs are disabled.
    expect(
      screen.getByRole("tab", { name: /Notes 10/ }).getAttribute("aria-disabled"),
    ).toBe("true");
  });

  test("notes skeletons render with the notes bucket, BEFORE scout/validator (peer-review LOW)", () => {
    // Before the fix, skeletons all landed in one trailing block after
    // scout/validator. Pin the new rule: a selected-but-not-yet-started
    // notes template must sit in the middle notes bucket, not at the
    // right edge next to Validator.
    const agents: Record<string, AgentTabState> = {
      sofp_0: { agentId: "sofp_0", label: "SOFP", status: "complete", role: "SOFP" },
      scout: { agentId: "scout", label: "Scout", status: "complete", role: "scout" },
      validator: {
        agentId: "validator",
        label: "Validator",
        status: "complete",
        role: "validator",
      },
    };
    render(
      <AgentTabs
        agents={agents}
        tabOrder={["sofp_0", "scout", "validator"]}
        activeTab="sofp_0"
        onTabClick={() => {}}
        statementsInRun={["SOFP"]}
        notesInRun={["CORP_INFO"]}
        notesSkeletons={["Notes 10: Corp Info"]}
      />,
    );
    const tabs = screen.getAllByRole("tab");
    const labels = tabs.map((t) => t.textContent?.trim() ?? "");
    const notesIdx = labels.findIndex((l) => l.includes("Notes 10"));
    const scoutIdx = labels.findIndex((l) => l === "Scout");
    const validatorIdx = labels.findIndex((l) => l === "Validator");
    expect(notesIdx).toBeGreaterThan(-1);
    expect(notesIdx).toBeLessThan(scoutIdx);
    expect(notesIdx).toBeLessThan(validatorIdx);
  });

  test("legacy callers (no notesInRun) still render notes tabs — backward compatible", () => {
    const agents: Record<string, AgentTabState> = {
      "notes:CORP_INFO": {
        agentId: "notes:CORP_INFO",
        label: "Notes 10: Corp Info",
        status: "complete",
        role: "CORP_INFO",
      },
    };
    render(
      <AgentTabs
        agents={agents}
        tabOrder={["notes:CORP_INFO"]}
        activeTab="notes:CORP_INFO"
        onTabClick={() => {}}
        // No notesInRun / statementsInRun — legacy contract: show everything.
      />,
    );
    expect(screen.getByText(/Notes 10/)).toBeTruthy();
  });

  test("validator tab is rendered last in tab order when present with statement tabs", () => {
    // Construct a mixed state where the tabOrder intentionally has validator
    // BEFORE the statement tabs. The gating code should still push validator
    // to the end of the render order so users always find it on the right.
    const agents: Record<string, AgentTabState> = {
      validator: {
        agentId: "validator",
        label: "Validator",
        status: "complete",
        role: "validator",
      },
      sofp_0: { agentId: "sofp_0", label: "SOFP", status: "complete", role: "SOFP" },
      sopl_0: { agentId: "sopl_0", label: "SOPL", status: "complete", role: "SOPL" },
    };
    render(
      <AgentTabs
        agents={agents}
        tabOrder={["validator", "sofp_0", "sopl_0"]}
        activeTab="sofp_0"
        onTabClick={() => {}}
        statementsInRun={["SOFP", "SOPL"]}
      />,
    );
    const tabs = screen.getAllByRole("tab");
    const labels = tabs.map((t) => t.textContent?.trim() ?? "");
    // Validator must be the last non-skeleton tab.
    expect(labels[labels.length - 1]).toContain("Validator");
  });

  test("rerun button visible on failed face-statement tab", () => {
    const agents: Record<string, AgentTabState> = {
      sofp_0: { agentId: "sofp_0", label: "SOFP", status: "failed", role: "SOFP" },
    };
    render(
      <AgentTabs
        agents={agents}
        tabOrder={["sofp_0"]}
        activeTab="sofp_0"
        onTabClick={() => {}}
        onRerunAgent={() => {}}
        isRunning={false}
      />,
    );
    // Rerun buttons use aria-label "Rerun {label}". Presence proves the gate.
    expect(screen.getByRole("button", { name: /rerun sofp/i })).toBeInTheDocument();
  });

  test("rerun button visible on failed notes tab (Phase D.3 symmetry)", () => {
    const agents: Record<string, AgentTabState> = {
      "notes:CORP_INFO": {
        agentId: "notes:CORP_INFO",
        label: "Corporate Information",
        status: "failed",
        role: "CORP_INFO",
      },
    };
    render(
      <AgentTabs
        agents={agents}
        tabOrder={["notes:CORP_INFO"]}
        activeTab="notes:CORP_INFO"
        onTabClick={() => {}}
        onRerunAgent={() => {}}
        notesInRun={["CORP_INFO"]}
        isRunning={false}
      />,
    );
    expect(
      screen.getByRole("button", { name: /rerun corporate information/i }),
    ).toBeInTheDocument();
  });

  test("rerun button hidden on scout and validator tabs even when failed", () => {
    // Peer-review finding #1: handleRerunAgent always built face-statement
    // payloads, so rerunning scout/validator produced guaranteed-fail POSTs.
    // The fix hides the button for those tabs entirely.
    const agents: Record<string, AgentTabState> = {
      scout: { agentId: "scout", label: "Scout", status: "failed", role: "scout" },
      validator: { agentId: "validator", label: "Validator", status: "failed", role: "validator" },
    };
    render(
      <AgentTabs
        agents={agents}
        tabOrder={["scout", "validator"]}
        activeTab="scout"
        onTabClick={() => {}}
        onRerunAgent={() => {}}
        isRunning={false}
      />,
    );
    expect(screen.queryByRole("button", { name: /rerun scout/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /rerun validator/i })).not.toBeInTheDocument();
  });
});
