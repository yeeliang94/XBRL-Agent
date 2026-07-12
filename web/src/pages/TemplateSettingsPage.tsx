import { useEffect, useMemo, useState, useCallback } from "react";
import { ApiError, userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { PageHeader } from "../components/PageHeader";
import { templateGroupLabel, templatePickerLabel } from "../lib/sheetLabels";

// ---------------------------------------------------------------------------
// TemplateSettingsPage — Phase 5.1 global template settings.
//
// Run-independent label customisation: pick a template, rename its
// display_labels once, and the change applies to every future run (the
// override lives on concept_nodes, not per-run). This is the "master
// template" surface, distinct from the per-run review (which edits values).
//
// display_label is UI-only and never exported into column A of the workbook
// (Phase 5.2 — the exporter always writes the canonical label). Inline
// styles only (gotcha #7).
// ---------------------------------------------------------------------------

interface TemplateRow {
  template_id: string;
  shape: string;
}

interface TemplateConcept {
  concept_uuid: string;
  kind: string;
  canonical_label: string;
  display_label: string | null;
  render_sheet: string;
  render_row: number;
}

export function TemplateSettingsPage() {
  const [templates, setTemplates] = useState<TemplateRow[]>([]);
  const [activeTemplate, setActiveTemplate] = useState<string | null>(null);
  const [concepts, setConcepts] = useState<TemplateConcept[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Case-insensitive filter over the visible label (E8) — large templates have
  // dozens of rows, so a filter beats scrolling. Empty query shows everything.
  const filteredConcepts = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return concepts;
    return concepts.filter((c) =>
      (c.display_label || c.canonical_label || "").toLowerCase().includes(q),
    );
  }, [concepts, query]);

  // Group templates by "MFRS · Company" etc. so the picker uses <optgroup>
  // with human labels instead of a flat list of 45 cryptic ids (D3). Groups
  // and options keep the templates' incoming order (already reading-order).
  const templateGroups = useMemo(() => {
    const groups: { label: string; templates: TemplateRow[] }[] = [];
    const byLabel = new Map<string, TemplateRow[]>();
    for (const t of templates) {
      const g = templateGroupLabel(t.template_id);
      if (!byLabel.has(g)) {
        const bucket: TemplateRow[] = [];
        byLabel.set(g, bucket);
        groups.push({ label: g, templates: bucket });
      }
      byLabel.get(g)!.push(t);
    }
    return groups;
  }, [templates]);

  // Load the template list once.
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/templates", { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject(ApiError.fromResponse(r.status, null))))
      .then((data) => {
        const list: TemplateRow[] = data.templates || [];
        setTemplates(list);
        // Default to a primary statement (SOFP) rather than list[0], which is
        // the first template_id alphabetically — a minor note like "Issued
        // capital" (UX-QA #19). Fall back to the first template if no SOFP.
        const sofp = list.find((t) => /-sofp-/.test(t.template_id));
        setActiveTemplate((sofp ?? list[0])?.template_id || null);
        setLoading(false);
      })
      .catch((err) => {
        if (err?.name !== "AbortError") {
          setError(userMessage(err));
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, []);

  // Load the active template's concepts whenever it changes.
  useEffect(() => {
    if (!activeTemplate) return;
    setQuery(""); // a fresh template starts unfiltered
    const controller = new AbortController();
    fetch(`/api/templates/${activeTemplate}/concepts`, { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject(ApiError.fromResponse(r.status, null))))
      .then((data) => setConcepts(data.concepts || []))
      .catch((err) => {
        if (err?.name !== "AbortError") setError(userMessage(err));
      });
    return () => controller.abort();
  }, [activeTemplate]);

  const onRename = useCallback(
    async (uuid: string, display_label: string | null) => {
      // Commit to state only after the PATCH succeeds (mirrors the per-run
      // rename's optimistic-but-verified pattern).
      try {
        const resp = await fetch(`/api/concepts/${uuid}/display_label`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ display_label }),
        });
        if (!resp.ok) {
          setError(`Rename failed (HTTP ${resp.status})`);
          return;
        }
        setConcepts((prev) =>
          prev.map((c) =>
            c.concept_uuid === uuid ? { ...c, display_label } : c
          )
        );
      } catch (err) {
        setError(`Rename failed: ${userMessage(err)}`);
      }
    },
    []
  );

  return (
    <div data-testid="template-settings-page" className="responsive-page" style={styles.page}>
      <PageHeader
        title="Field labels"
        description="Rename how individual template line items are labelled on screen. This doesn't change the XBRL — only the display text."
      />
      {error && (
        <div role="alert" style={ui.alertError}>
          <span aria-hidden="true" style={ui.alertIcon(pwc.error)}>✕</span>
          <div>{error}</div>
        </div>
      )}
      {loading ? (
        <div style={{ color: pwc.grey700, padding: `${pwc.space.md}px 0` }}>
          Loading templates…
        </div>
      ) : templates.length === 0 ? (
        <div
          data-testid="ts-empty"
          style={{ color: pwc.grey700, padding: `${pwc.space.md}px 0` }}
        >
          No templates yet. Run an extraction first — the templates it uses will
          appear here for you to relabel.
        </div>
      ) : (
        <>
      <div className="quality-toolbar" style={styles.toolbar}>
        <label htmlFor="ts-template" style={ui.fieldLabel}>
          Template
        </label>
        <select
          id="ts-template"
          data-testid="ts-template-selector"
          value={activeTemplate || ""}
          onChange={(e) => setActiveTemplate(e.target.value || null)}
          style={ui.select}
        >
          {templateGroups.map((group) => (
            <optgroup key={group.label} label={group.label}>
              {group.templates.map((t) => (
                <option
                  key={t.template_id}
                  value={t.template_id}
                  title={t.template_id}
                >
                  {group.label} · {templatePickerLabel(t.template_id)}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        <label htmlFor="ts-search" style={ui.fieldLabel}>
          Search
        </label>
        <input
          id="ts-search"
          type="search"
          data-testid="ts-search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter labels…"
          aria-label="Filter labels"
          style={{ ...ui.input, minWidth: 200 }}
        />
      </div>
      {/* Legend — explains the two things a first-time user can't infer: why
          some rows are greyed out (and un-renamable), and what the leading
          asterisk means (E8). */}
      <p style={styles.legend} data-testid="ts-legend">
        <span style={styles.legendSwatch} aria-hidden="true" />
        Greyed rows are section headers — they can&apos;t be renamed.
        {"  "}
        A leading <strong>*</strong> marks a mandatory MBRS field.
      </p>
      <div
        role="table"
        style={styles.tableWrap}
      >
        {concepts.length === 0 ? (
          <div style={styles.emptyRow}>
            No fields for this template.
          </div>
        ) : filteredConcepts.length === 0 ? (
          <div style={styles.emptyRow} data-testid="ts-no-matches">
            No labels match “{query}”.
          </div>
        ) : (
          filteredConcepts.map((c) => (
            <TemplateConceptRow
              key={c.concept_uuid}
              concept={c}
              onRename={onRename}
            />
          ))
        )}
      </div>
        </>
      )}
    </div>
  );
}

function TemplateConceptRow({
  concept,
  onRename,
}: {
  concept: TemplateConcept;
  onRename: (uuid: string, label: string | null) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const isAbstract = concept.kind === "ABSTRACT";
  const label = concept.display_label || concept.canonical_label;

  // Seed the edit box with the label the user actually sees, so renaming
  // "Notes – Issued capital" doesn't start from an empty field (it used to
  // seed from display_label alone, which is empty for the common no-override
  // row). Entering edit mode is the only place draft is reset.
  const startEditing = () => {
    setDraft(label);
    setEditing(true);
  };

  const commit = () => {
    setEditing(false);
    const next = draft.trim();
    // Clearing the field, or typing the canonical text back, means "no
    // override" — send null so the row reverts to the taxonomy default
    // rather than storing a redundant custom label.
    const nextLabel = next && next !== concept.canonical_label ? next : null;
    if (nextLabel !== (concept.display_label || null)) {
      void onRename(concept.concept_uuid, nextLabel);
    }
  };

  const cancel = () => setEditing(false);

  return (
    <div
      data-testid={`ts-row-${concept.concept_uuid}`}
      style={{
        display: "grid",
        gridTemplateColumns: "minmax(0, 1fr) 168px",
        gap: pwc.space.lg,
        padding: `${pwc.space.lg}px ${pwc.space.xl}px`,
        borderBottom: `1px solid ${pwc.grey100}`,
        background: isAbstract ? pwc.grey100 : pwc.white,
        alignItems: "center",
        fontFamily: pwc.fontBody,
        fontSize: 15,
        fontWeight: isAbstract ? pwc.weight.medium : pwc.weight.regular,
        lineHeight: 1.55,
      }}
    >
      <div>
        {editing ? (
          <input
            data-testid={`ts-rename-input-${concept.concept_uuid}`}
            value={draft}
            autoFocus
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              // Enter commits, Escape cancels — the affordances the bare
              // textbox used to lack.
              if (e.key === "Enter") {
                e.preventDefault();
                commit();
              } else if (e.key === "Escape") {
                e.preventDefault();
                cancel();
              }
            }}
            style={{ ...ui.input, width: "100%" }}
          />
        ) : (
          <div>
            <div style={styles.labelLine}>
              <span style={styles.labelKey}>Original</span>
              <span>{concept.canonical_label}</span>
            </div>
            {concept.display_label && (
              <div style={styles.labelLine}>
                <span style={styles.labelKey}>Custom</span>
                <span>
                  {label}
            {/* Flag a customised label so it's easy to spot what's been
                changed from the taxonomy default (E8). */}
                  <span style={styles.edited} data-testid={`ts-edited-${concept.concept_uuid}`}>
                    <span aria-hidden="true" style={ui.statusSymbol}>✓</span>
                    Edited
                  </span>
                </span>
              </div>
            )}
          </div>
        )}
      </div>
      <div style={{ display: "flex", gap: pwc.space.sm, justifyContent: "flex-end" }}>
        {!isAbstract && !editing && (
          <>
            {concept.display_label && (
              <button
                data-testid={`ts-reset-btn-${concept.concept_uuid}`}
                onClick={() => void onRename(concept.concept_uuid, null)}
                className={uiClass.btnQuiet}
                style={{ ...ui.buttonQuiet, ...ui.buttonSm }}
              >
                Reset
              </button>
            )}
            <button
              data-testid={`ts-rename-btn-${concept.concept_uuid}`}
              onClick={startEditing}
              className={uiClass.btnSecondary}
              style={{ ...ui.buttonSecondary, ...ui.buttonSm }}
            >
              Rename
            </button>
          </>
        )}
        {editing && (
          <>
            <button
              data-testid={`ts-rename-cancel-${concept.concept_uuid}`}
              onClick={cancel}
              className={uiClass.btnQuiet}
              style={{ ...ui.buttonQuiet, ...ui.buttonSm }}
            >
              Cancel
            </button>
            <button
              data-testid={`ts-rename-save-${concept.concept_uuid}`}
              onClick={commit}
              className={uiClass.btnPrimary}
              style={{ ...ui.buttonPrimary, ...ui.buttonSm }}
            >
              Save
            </button>
          </>
        )}
      </div>
    </div>
  );
}

const styles = {
  // Wide-list mode (design-system Layouts): shared 1440px cap, no bespoke
  // clamp-based padding.
  page: {
    ...ui.pageWide,
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xl,
  } as React.CSSProperties,
  toolbar: {
    ...ui.filterToolbar,
    alignItems: "center",
  } as React.CSSProperties,
  legend: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.sm,
    margin: `${pwc.space.sm}px 0 ${pwc.space.md}px`,
    color: pwc.grey700,
    fontFamily: pwc.fontBody,
    fontSize: 13,
  } as React.CSSProperties,
  legendSwatch: {
    display: "inline-block",
    width: 14,
    height: 14,
    background: pwc.grey100,
    border: `1px solid ${pwc.grey300}`,
    borderRadius: pwc.radius.sm,
    flexShrink: 0,
  } as React.CSSProperties,
  // Neutral edited marker: standard symbol + explicit label, no chip.
  edited: {
    ...ui.status,
    marginLeft: pwc.space.sm,
    fontSize: 12,
    color: pwc.grey700,
  } as React.CSSProperties,
  labelLine: {
    display: "grid",
    gridTemplateColumns: "64px minmax(0, 1fr)",
    gap: pwc.space.sm,
    alignItems: "baseline",
  } as React.CSSProperties,
  labelKey: {
    color: pwc.grey700,
    fontSize: 12,
    fontWeight: pwc.weight.medium,
    textTransform: "uppercase" as const,
    letterSpacing: "0.04em",
  } as React.CSSProperties,
  tableWrap: {
    ...ui.card,
    overflow: "hidden",
  } as React.CSSProperties,
  emptyRow: {
    color: pwc.grey700,
    padding: `${pwc.space.lg}px ${pwc.space.xl}px`,
    fontSize: 15,
  } as React.CSSProperties,
} as const;
