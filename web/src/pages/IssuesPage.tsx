import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { deleteIssue, getIssue, issueFileUrl, listIssues, updateIssue } from "../lib/api";
import type { IssueContext, IssueDetail, IssueKind, IssueStatus, IssueSummary } from "../lib/api";
import { ApiError, userMessage } from "../lib/errors";
import { ds, tokens } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Disclosure } from "../components/Disclosure";
import { ISSUE_KIND_LABELS } from "../components/IssueDialog";

// ---------------------------------------------------------------------------
// IssuesPage — "Issues" (admin, every issue) and "Your issues" (user, own
// issues) from plan 6.3, plus the issue detail from 6.4. The parent owns the
// selected issue id and renders the "Back to issues" link in its top bar.
// Authorization is enforced server-side; `can_manage` only picks the view.
// ---------------------------------------------------------------------------

export const ISSUE_STATUS_LABELS: Record<IssueStatus, string> = {
  new: "New",
  in_progress: "In progress",
  resolved: "Resolved",
};

const STATUSES: IssueStatus[] = ["new", "in_progress", "resolved"];
const KINDS = Object.keys(ISSUE_KIND_LABELS) as IssueKind[];

type StatusFilter = IssueStatus | "all";

export interface IssuesPageProps {
  isAdmin: boolean;
  issueId: number | null;
  onOpenIssue: (id: number | null) => void;
  /** Opens the run; `path` is the page the issue was raised on, when stored. */
  onOpenRun: (runId: number, path: string | null) => void;
}

export function IssuesPage({ isAdmin, issueId, onOpenIssue, onOpenRun }: IssuesPageProps) {
  if (issueId !== null) {
    return <IssueDetailView key={issueId} issueId={issueId} onOpenIssue={onOpenIssue} onOpenRun={onOpenRun} />;
  }
  return <IssueList isAdmin={isAdmin} onOpenIssue={onOpenIssue} />;
}

// --- Formatting -------------------------------------------------------------

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function sameDay(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** "Today 10:12", "Yesterday", "29 Oct" (year added when not this year). */
export function formatRaised(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  if (sameDay(d, now)) {
    return `Today ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(d, yesterday)) return "Yesterday";
  const base = `${d.getDate()} ${MONTHS[d.getMonth()]}`;
  return d.getFullYear() === now.getFullYear() ? base : `${base} ${d.getFullYear()}`;
}

function documentName(issue: IssueSummary): string {
  if (issue.run_filename) return issue.run_filename.replace(/\.pdf$/i, "");
  if (issue.context?.document) return issue.context.document;
  return "—";
}

function reporterName(issue: IssueSummary): string {
  return issue.reporter_name || issue.reporter_email;
}

function formatSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

// --- Shared pieces ----------------------------------------------------------

/** Status pattern: orange dot for New only; In progress and Resolved stay
 *  monochrome. The dot is decorative; the text is the name. */
function IssueStatusLabel({ status }: { status: IssueStatus }) {
  const dot = status === "new" ? ds.orange500 : status === "resolved" ? ds.black : ds.grey500;
  return (
    <span style={ui.status}>
      <span aria-hidden="true" style={ui.badgeDot(dot)} />
      <span>{ISSUE_STATUS_LABELS[status]}</span>
    </span>
  );
}

function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div role="group" aria-label={label} className="segmented-control-group" style={styles.segmentWrap}>
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            className="segmented-control-button"
            aria-pressed={active}
            onClick={() => onChange(o.value)}
            style={{ ...ui.segmentButton, ...(active ? ui.segmentButtonActive : {}) }}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

// --- List -------------------------------------------------------------------

function IssueList({ isAdmin, onOpenIssue }: { isAdmin: boolean; onOpenIssue: (id: number) => void }) {
  const [status, setStatus] = useState<StatusFilter>(isAdmin ? "new" : "all");
  const [kind, setKind] = useState<IssueKind | "all">("all");
  const [issues, setIssues] = useState<IssueSummary[] | null>(null);
  const [counts, setCounts] = useState<Record<IssueStatus, number> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setIssues(null);
    listIssues({
      status: status === "all" ? undefined : status,
      kind: isAdmin && kind !== "all" ? kind : undefined,
    })
      .then((res) => {
        if (cancelled) return;
        setIssues(res.issues);
        setCounts(res.counts);
      })
      .catch((err) => {
        if (!cancelled) setError(userMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [status, kind, isAdmin]);

  const countLabel = (s: IssueStatus) => {
    const n = counts?.[s];
    return n ? `${ISSUE_STATUS_LABELS[s]} ${n}` : ISSUE_STATUS_LABELS[s];
  };
  const statusOptions: { value: StatusFilter; label: string }[] = [
    ...STATUSES.map((s) => ({ value: s as StatusFilter, label: countLabel(s) })),
    { value: "all", label: "All" },
  ];

  const emptyText = !isAdmin
    ? status === "all"
      ? "You haven't reported any issues."
      : "No issues."
    : status === "new"
      ? "No new issues."
      : "No issues.";

  return (
    <div style={ui.pageWide}>
      <h1 style={ui.pageTitle}>{isAdmin ? "Issues" : "Your issues"}</h1>
      <div style={{ ...ui.filterToolbar, alignItems: "center", margin: `${ds.space.xl}px 0 ${ds.space.lg}px` }}>
        <Segmented label="Status" options={statusOptions} value={status} onChange={setStatus} />
        {isAdmin && (
          <select
            aria-label="Kind"
            value={kind}
            onChange={(e) => setKind(e.target.value as IssueKind | "all")}
            style={ui.select}
          >
            <option value="all">All kinds</option>
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {ISSUE_KIND_LABELS[k]}
              </option>
            ))}
          </select>
        )}
      </div>

      {error ? (
        <p role="alert" style={styles.error}>
          Couldn't load issues. {error}
        </p>
      ) : issues === null ? (
        <p style={ui.metadata}>Loading issues…</p>
      ) : issues.length === 0 ? (
        <p style={{ ...ui.bodyText, margin: 0, color: tokens.color.text.secondary }}>{emptyText}</p>
      ) : (
        // Fits its pane: lower-priority columns hide as space shrinks
        // (index.css "Issues page"); #, What happened and Status always stay.
        <table style={styles.table}>
          <thead>
            <tr>
              <th style={{ ...ui.th, width: 56 }}>#</th>
              <th className="issues-col-raised" style={{ ...ui.th, width: 112 }}>
                Raised
              </th>
              {isAdmin && (
                <th className="issues-col-by" style={{ ...ui.th, width: 140 }}>
                  By
                </th>
              )}
              <th style={ui.th}>What happened</th>
              <th className="issues-col-document" style={{ ...ui.th, width: 160 }}>
                Document
              </th>
              <th style={{ ...ui.th, width: 120 }}>Status</th>
            </tr>
          </thead>
          <tbody>
            {issues.map((issue) => {
              const summary = issue.description.trim() || ISSUE_KIND_LABELS[issue.kind];
              return (
                <tr
                  key={issue.id}
                  className={`${uiClass.tableRow} issues-row`}
                  tabIndex={0}
                  aria-label={`Issue ${issue.id}: ${summary}`}
                  style={{ cursor: "pointer" }}
                  onClick={() => onOpenIssue(issue.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onOpenIssue(issue.id);
                    }
                  }}
                >
                  <td style={{ ...ui.td, ...ui.numeric, textAlign: "left" }}>{issue.id}</td>
                  <td className="issues-col-raised" style={{ ...ui.td, whiteSpace: "nowrap" }}>
                    {formatRaised(issue.created_at)}
                  </td>
                  {isAdmin && (
                    <td className="issues-col-by" style={{ ...ui.td, ...styles.ellipsis }}>
                      {reporterName(issue)}
                    </td>
                  )}
                  <td style={{ ...ui.td, ...styles.ellipsis }} title={summary}>
                    {summary}
                  </td>
                  <td className="issues-col-document" style={{ ...ui.td, ...styles.ellipsis }}>
                    {documentName(issue)}
                  </td>
                  <td style={ui.td}>
                    <IssueStatusLabel status={issue.status} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

// --- Detail -----------------------------------------------------------------

function contextParts(ctx: IssueContext | null): string[] {
  if (!ctx) return [];
  return [ctx.document, ctx.page, ctx.tab, ctx.statement].filter((p): p is string => !!p);
}

function IssueDetailView({
  issueId,
  onOpenIssue,
  onOpenRun,
}: {
  issueId: number;
  onOpenIssue: (id: number | null) => void;
  onOpenRun: (runId: number, path: string | null) => void;
}) {
  const [issue, setIssue] = useState<IssueDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [reply, setReply] = useState("");
  const [status, setStatus] = useState<IssueStatus>("new");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getIssue(issueId)
      .then((res) => {
        if (cancelled) return;
        setIssue(res);
        setReply(res.admin_reply ?? "");
        setStatus(res.status);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) setNotFound(true);
        else setLoadError(userMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [issueId]);

  useEffect(() => {
    if (!saved) return;
    const t = window.setTimeout(() => setSaved(false), 2500);
    return () => window.clearTimeout(t);
  }, [saved]);

  if (notFound) return <p style={ui.bodyText}>Issue not found.</p>;
  if (loadError)
    return (
      <p role="alert" style={styles.error}>
        Couldn't load this issue. {loadError}
      </p>
    );
  if (!issue) return <p style={ui.metadata}>Loading issue…</p>;

  const save = async () => {
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      const updated = await updateIssue(issue.id, { status, admin_reply: reply });
      setIssue({ ...issue, ...updated, files: issue.files, can_manage: issue.can_manage });
      setSaved(true);
    } catch (err) {
      setSaveError(userMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteIssue(issue.id);
      setConfirmDelete(false);
      onOpenIssue(null);
    } catch (err) {
      setDeleteError(userMessage(err));
      setConfirmDelete(false);
    } finally {
      setDeleting(false);
    }
  };

  const meta = [reporterName(issue), formatRaised(issue.created_at), ...contextParts(issue.context)];
  const ctx = issue.context;
  const technical: [string, string | undefined][] = [
    ["Browser", ctx?.browser],
    ["Window size", ctx?.window],
    ["App version", ctx?.app_version],
    ["Path", ctx?.path],
  ];
  const technicalRows = technical.filter((r): r is [string, string] => !!r[1]);
  const images = issue.files.filter((f) => f.content_type.startsWith("image/"));
  const others = issue.files.filter((f) => !f.content_type.startsWith("image/"));

  return (
    <div style={ui.pageStandard}>
      <div style={styles.titleRow}>
        <h1 style={ui.pageTitle}>
          Issue {issue.id} · {ISSUE_KIND_LABELS[issue.kind]}
        </h1>
        <IssueStatusLabel status={issue.status} />
      </div>
      <div style={styles.metaRow}>
        <span style={ui.metadata}>{meta.join(" · ")}</span>
        {issue.run_id !== null ? (
          <button type="button" style={styles.linkButton} onClick={() => onOpenRun(issue.run_id as number, issue.context?.path ?? null)}>
            Open in run
          </button>
        ) : ctx?.document ? (
          <span style={{ ...ui.metadata, color: tokens.color.text.muted }}>Document deleted</span>
        ) : null}
      </div>

      {issue.description && <p style={styles.description}>{issue.description}</p>}

      {issue.files.length > 0 && (
        <div style={styles.files}>
          {images.map((f) => {
            const url = issueFileUrl(issue.id, f.id);
            return (
              <a key={f.id} href={url} target="_blank" rel="noopener noreferrer" style={{ display: "block" }}>
                <img src={url} alt={f.original_name} style={styles.image} />
              </a>
            );
          })}
          {others.length > 0 && (
            <ul style={styles.fileList}>
              {others.map((f) => (
                <li key={f.id}>
                  <a href={issueFileUrl(issue.id, f.id)} download={f.original_name} style={styles.fileLink}>
                    {f.original_name}
                  </a>
                  <span style={{ ...ui.metadata, marginLeft: ds.space.sm }}>{formatSize(f.size_bytes)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {issue.can_manage ? (
        <section style={styles.section}>
          <div style={styles.field}>
            <label htmlFor="issue-reply" style={ui.fieldLabel}>
              Reply
            </label>
            <textarea
              id="issue-reply"
              value={reply}
              onChange={(e) => setReply(e.target.value)}
              rows={4}
              style={{ ...ui.textarea, width: "100%", boxSizing: "border-box" }}
            />
          </div>
          <div style={styles.saveRow}>
            <div style={{ display: "flex", alignItems: "center", gap: ds.space.md, flexWrap: "wrap" }}>
              <span style={ui.fieldLabel}>Status</span>
              <Segmented
                label="Status"
                options={STATUSES.map((s) => ({ value: s, label: ISSUE_STATUS_LABELS[s] }))}
                value={status}
                onChange={setStatus}
              />
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: ds.space.md }}>
              {saved && (
                <span role="status" style={ui.metadata}>
                  Saved
                </span>
              )}
              <button
                type="button"
                className={uiClass.btnPrimary}
                style={ui.buttonPrimary}
                onClick={save}
                disabled={saving}
              >
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
          </div>
          {saveError && (
            <p role="alert" style={{ ...styles.error, marginTop: ds.space.sm }}>
              {saveError}
            </p>
          )}
        </section>
      ) : issue.admin_reply.trim() ? (
        <section style={styles.section}>
          <h2 style={ui.subsectionTitle}>Reply</h2>
          <p style={styles.description}>{issue.admin_reply}</p>
        </section>
      ) : null}

      {technicalRows.length > 0 && (
        <Disclosure summary="Technical details" style={{ marginTop: ds.space.xl }}>
          <dl style={styles.technical}>
            {technicalRows.map(([label, value]) => (
              <div key={label} style={{ display: "contents" }}>
                <dt style={ui.metadata}>{label}</dt>
                <dd style={{ ...ui.metadata, margin: 0, color: tokens.color.text.primary, overflowWrap: "anywhere" }}>
                  {value}
                </dd>
              </div>
            ))}
          </dl>
        </Disclosure>
      )}

      {issue.can_manage && (
        <div style={{ marginTop: ds.space.xl }}>
          <button
            type="button"
            className={uiClass.btnDanger}
            style={ui.buttonDanger}
            onClick={() => setConfirmDelete(true)}
          >
            Delete issue
          </button>
          {deleteError && (
            <p role="alert" style={{ ...styles.error, marginTop: ds.space.sm }}>
              {deleteError}
            </p>
          )}
        </div>
      )}

      <ConfirmDialog
        isOpen={confirmDelete}
        title={`Delete issue ${issue.id}?`}
        message="Its files are deleted too. This can't be undone."
        confirmLabel="Delete issue"
        busyLabel="Deleting issue…"
        busy={deleting}
        onConfirm={remove}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
}

const styles: Record<string, CSSProperties> = {
  segmentWrap: {
    ...ui.segmentGroup,
    flexWrap: "wrap",
    maxWidth: "100%",
  },
  table: {
    width: "100%",
    borderCollapse: "collapse",
    tableLayout: "fixed",
    ...ui.bodyText,
  },
  ellipsis: {
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  error: {
    ...ui.bodyText,
    color: ds.orange700,
    margin: 0,
  },
  titleRow: {
    display: "flex",
    alignItems: "center",
    flexWrap: "wrap",
    gap: ds.space.lg,
  },
  metaRow: {
    display: "flex",
    alignItems: "center",
    flexWrap: "wrap",
    gap: ds.space.lg,
    marginTop: ds.space.sm,
  },
  linkButton: {
    padding: 0,
    border: "none",
    background: "transparent",
    cursor: "pointer",
    fontFamily: ds.fontBody,
    fontSize: 13,
    fontWeight: ds.weight.medium,
    color: tokens.color.text.primary,
    textDecoration: "underline",
    textUnderlineOffset: 3,
  },
  description: {
    ...ui.readingText,
    whiteSpace: "pre-wrap",
    marginTop: ds.space.xl,
    overflowWrap: "anywhere",
  },
  files: {
    display: "flex",
    flexDirection: "column",
    gap: ds.space.lg,
    marginTop: ds.space.xl,
  },
  image: {
    display: "block",
    maxWidth: "100%",
    height: "auto",
    border: `1px solid ${tokens.color.border.subtle}`,
    borderRadius: tokens.radius.control,
  },
  fileList: {
    listStyle: "none",
    margin: 0,
    padding: 0,
    display: "flex",
    flexDirection: "column",
    gap: ds.space.sm,
    ...ui.bodyText,
  },
  fileLink: {
    color: tokens.color.text.primary,
    textDecoration: "underline",
    textUnderlineOffset: 3,
    overflowWrap: "anywhere",
  },
  section: {
    marginTop: ds.space.xl,
    paddingTop: ds.space.xl,
    borderTop: `1px solid ${tokens.color.border.subtle}`,
  },
  field: {
    display: "flex",
    flexDirection: "column",
    gap: ds.space.sm,
  },
  saveRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    flexWrap: "wrap",
    gap: ds.space.md,
    marginTop: ds.space.lg,
  },
  technical: {
    display: "grid",
    gridTemplateColumns: "max-content minmax(0, 1fr)",
    columnGap: ds.space.lg,
    rowGap: ds.space.xs,
    margin: `${ds.space.sm}px 0 0`,
  },
};
