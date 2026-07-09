import { useEffect, useState, useCallback } from "react";
import { ApiError, userMessage } from "../lib/errors";
import { pwc } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { PageHeader } from "../components/PageHeader";

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
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Load the template list once.
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/templates", { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject(ApiError.fromResponse(r.status, null))))
      .then((data) => {
        const list: TemplateRow[] = data.templates || [];
        setTemplates(list);
        setActiveTemplate(list[0]?.template_id || null);
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
    <div data-testid="template-settings-page" style={styles.page}>
      <PageHeader
        title="Field labels"
        description="Rename how individual template line items are labelled on screen. This doesn't change the XBRL — only the display text."
      />
      {error && (
        <div style={{ color: pwc.error, marginBottom: pwc.space.md }}>
          {error}
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
      <div style={styles.toolbar}>
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
          {templates.map((t) => (
            <option key={t.template_id} value={t.template_id}>
              {t.template_id}
            </option>
          ))}
        </select>
      </div>
      <div
        role="table"
        style={styles.tableWrap}
      >
        {concepts.length === 0 ? (
          <div style={styles.emptyRow}>
            No fields for this template.
          </div>
        ) : (
          concepts.map((c) => (
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
      <div title={`canonical: ${concept.canonical_label}`}>
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
          <span>{label}</span>
        )}
      </div>
      <div style={{ display: "flex", gap: pwc.space.sm, justifyContent: "flex-end" }}>
        {!isAbstract && !editing && (
          <button
            data-testid={`ts-rename-btn-${concept.concept_uuid}`}
            onClick={startEditing}
            className={uiClass.btnSecondary}
            style={{ ...ui.buttonSecondary, ...ui.buttonSm }}
          >
            Rename
          </button>
        )}
        {editing && (
          <>
            <button
              data-testid={`ts-rename-cancel-${concept.concept_uuid}`}
              onClick={cancel}
              className={uiClass.btnSubtle}
              style={{ ...ui.buttonSubtle, ...ui.buttonSm }}
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
  page: {
    padding: `${pwc.space.lg}px calc(clamp(${pwc.space.xxl}px, 3vw, ${pwc.space.xxxl}px) - ${pwc.space.lg}px)`,
    display: "flex",
    flexDirection: "column" as const,
    gap: pwc.space.xxl,
  } as React.CSSProperties,
  toolbar: {
    display: "flex",
    alignItems: "center",
    gap: pwc.space.lg,
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
