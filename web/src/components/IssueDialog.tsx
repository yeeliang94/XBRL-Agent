import { useEffect, useId, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { reportIssue } from "../lib/api";
import type { IssueContext, IssueKind } from "../lib/api";
import { ApiError, userMessage } from "../lib/errors";
import { ds, tokens } from "../lib/theme";
import { ui, uiClass } from "../lib/uiStyles";
import { Icon } from "./icons";
import { Close } from "./iconGlyphs";

// ---------------------------------------------------------------------------
// IssueDialog — "Report an issue" (plan 6.2). A centred dialog with the same
// look and motion as ConfirmDialog. The parent keeps it mounted and toggles
// `open`, so typed text and chosen files survive closing and reopening (a
// person can close it, take a missed screenshot and come back). Kind and text
// also persist in localStorage under the signed-in person's `draftKey`, so a
// reload keeps them but the next person on a shared browser never sees them;
// files cannot persist. The shell calls `clearIssueDrafts` on log out.
// Client-side file checks mirror the server limits; the server re-checks.
// ---------------------------------------------------------------------------

export const ISSUE_KIND_LABELS: Record<IssueKind, string> = {
  wrong_figure: "Wrong figure",
  notes: "Notes",
  formatting: "Formatting",
  app_error: "App error",
  other: "Other",
};

const KINDS: IssueKind[] = ["wrong_figure", "notes", "formatting", "app_error", "other"];
// An untouched issue is filed as "Other" rather than guessed into a category.
const DEFAULT_KIND: IssueKind = "other";

const MAX_FILES = 5;
const MAX_BYTES = 10 * 1024 * 1024;
const ALLOWED_TYPES = new Set([
  "image/png",
  "image/jpeg",
  "image/webp",
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
]);
const ALLOWED_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp", ".pdf", ".xlsx", ".docx"];

const DRAFT_KEY_PREFIX = "xbrl.issueDraft";

/** The localStorage key that holds one person's unsent issue draft. */
export function issueDraftKey(email: string): string {
  return `${DRAFT_KEY_PREFIX}:${email.toLowerCase()}`;
}

/** Remove every stored issue draft (called on log out). */
export function clearIssueDrafts(): void {
  try {
    const keys: string[] = [];
    for (let i = 0; i < window.localStorage.length; i++) {
      const key = window.localStorage.key(i);
      if (key?.startsWith(DRAFT_KEY_PREFIX)) keys.push(key);
    }
    keys.forEach((key) => window.localStorage.removeItem(key));
  } catch {
    // Storage unavailable — nothing was stored.
  }
}

/** Context fields that describe the browser, not the page. They are sent
 *  even when "Attach this page" is unticked. */
const NON_PAGE_FIELDS = ["browser", "window", "app_version"] as const;

function withoutPage(context: IssueContext | null): IssueContext | null {
  if (!context) return null;
  const kept: IssueContext = {};
  for (const field of NON_PAGE_FIELDS) {
    if (context[field]) kept[field] = context[field];
  }
  return Object.keys(kept).length > 0 ? kept : null;
}

const NETWORK_ERROR = "Couldn't send the issue. Check your connection and try again.";

interface Draft {
  kind: IssueKind;
  description: string;
}

function loadDraft(key: string): Draft {
  try {
    const raw = window.localStorage.getItem(key);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<Draft>;
      const kind = KINDS.includes(parsed.kind as IssueKind) ? (parsed.kind as IssueKind) : DEFAULT_KIND;
      return { kind, description: typeof parsed.description === "string" ? parsed.description : "" };
    }
  } catch {
    // Storage blocked or corrupt — start empty.
  }
  return { kind: DEFAULT_KIND, description: "" };
}

function saveDraft(key: string, draft: Draft) {
  try {
    if (!draft.description && draft.kind === DEFAULT_KIND) {
      window.localStorage.removeItem(key);
    } else {
      window.localStorage.setItem(key, JSON.stringify(draft));
    }
  } catch {
    // Storage unavailable — the in-memory draft still survives close/reopen.
  }
}

function clearDraft(key: string) {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // ignore
  }
}

function isAllowedType(file: File): boolean {
  if (file.type && ALLOWED_TYPES.has(file.type)) return true;
  const name = file.name.toLowerCase();
  return ALLOWED_EXTENSIONS.some((ext) => name.endsWith(ext));
}

function isImage(file: File): boolean {
  return file.type.startsWith("image/");
}

function makePreviewUrl(file: File): string | null {
  if (!isImage(file)) return null;
  if (typeof URL === "undefined" || typeof URL.createObjectURL !== "function") return null;
  try {
    return URL.createObjectURL(file);
  } catch {
    return null;
  }
}

function revokePreviewUrl(url: string | null) {
  if (!url) return;
  if (typeof URL !== "undefined" && typeof URL.revokeObjectURL === "function") URL.revokeObjectURL(url);
}

interface Attachment {
  key: number;
  file: File;
  previewUrl: string | null;
}

export interface IssueDialogProps {
  open: boolean;
  onClose: () => void;
  context: IssueContext | null;
  contextLabel: string | null;
  onSent: (id: number) => void;
  /** localStorage key for this person's draft, from `issueDraftKey(email)`. */
  draftKey: string;
}

const FOCUSABLE =
  'button:not([disabled]), textarea:not([disabled]), input:not([disabled]):not([type="file"]), select:not([disabled]), [href], [tabindex]:not([tabindex="-1"])';

export function IssueDialog({ open, onClose, context, contextLabel, onSent, draftKey }: IssueDialogProps) {
  const [initialDraft] = useState(() => loadDraft(draftKey));
  const [kind, setKind] = useState<IssueKind>(initialDraft.kind);
  const [description, setDescription] = useState(initialDraft.description);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [fileError, setFileError] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [attachContext, setAttachContext] = useState(true);
  const [sending, setSending] = useState(false);
  const [dragging, setDragging] = useState(false);
  // The person whose draft is on screen. When the signed-in person changes
  // while the dialog stays mounted, swap to their draft and drop the files.
  const [draftOwner, setDraftOwner] = useState(draftKey);
  if (draftOwner !== draftKey) {
    const next = loadDraft(draftKey);
    attachments.forEach((a) => revokePreviewUrl(a.previewUrl));
    setDraftOwner(draftKey);
    setKind(next.kind);
    setDescription(next.description);
    setAttachments([]);
    setFileError(null);
    setSendError(null);
    setAttachContext(true);
  }

  const titleId = useId();
  const kindId = useId();
  const descriptionId = useId();
  const fileErrorId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const chooseFilesRef = useRef<HTMLButtonElement>(null);
  const attachmentListRef = useRef<HTMLUListElement>(null);
  // Index of a just-removed file, so focus can land on its neighbour.
  const focusAfterRemove = useRef<number | null>(null);
  const nextKey = useRef(1);
  const attachmentsRef = useRef<Attachment[]>([]);
  attachmentsRef.current = attachments;
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const sendingRef = useRef(sending);
  sendingRef.current = sending;

  // Persist kind + text so a reload keeps the draft.
  useEffect(() => {
    saveDraft(draftOwner, { kind, description });
  }, [draftOwner, kind, description]);

  // After a removal, focus the next file's Remove button, else the previous
  // one, else "Choose files" — never let focus fall out of the dialog.
  useEffect(() => {
    const index = focusAfterRemove.current;
    if (index === null) return;
    focusAfterRemove.current = null;
    const buttons = attachmentListRef.current?.querySelectorAll<HTMLButtonElement>("button[data-remove-file]");
    const target = buttons && buttons.length > 0 ? buttons[Math.min(index, buttons.length - 1)] : chooseFilesRef.current;
    target?.focus();
  }, [attachments]);

  // Revoke every preview URL on unmount.
  useEffect(() => () => attachmentsRef.current.forEach((a) => revokePreviewUrl(a.previewUrl)), []);

  // Focus moves into the dialog on open and returns to the trigger on close.
  // Keyed on [open] only, so re-renders while open never steal focus.
  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    textareaRef.current?.focus();
    return () => previouslyFocused?.focus?.();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !sendingRef.current) onCloseRef.current();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open]);

  if (!open) return null;

  const addFiles = (incoming: File[]) => {
    if (incoming.length === 0) return;
    let error: string | null = null;
    const accepted: Attachment[] = [];
    let room = MAX_FILES - attachments.length;
    for (const file of incoming) {
      if (!isAllowedType(file)) {
        error ??= `${file.name} isn't a supported type. Use PNG, JPG, WebP, PDF, Excel or Word.`;
        continue;
      }
      if (file.size > MAX_BYTES) {
        error ??= `${file.name} is larger than 10 MB.`;
        continue;
      }
      if (room <= 0) {
        error ??= `Attach up to ${MAX_FILES} files.`;
        continue;
      }
      room -= 1;
      accepted.push({ key: nextKey.current++, file, previewUrl: makePreviewUrl(file) });
    }
    setFileError(error);
    if (accepted.length) setAttachments((prev) => [...prev, ...accepted]);
  };

  const removeAttachment = (key: number) => {
    focusAfterRemove.current = attachments.findIndex((a) => a.key === key);
    setAttachments((prev) => {
      const target = prev.find((a) => a.key === key);
      if (target) revokePreviewUrl(target.previewUrl);
      return prev.filter((a) => a.key !== key);
    });
    setFileError(null);
  };

  const canSend = (description.trim().length > 0 || attachments.length > 0) && !sending;

  const send = async () => {
    if (!canSend) return;
    setSending(true);
    setSendError(null);
    try {
      const result = await reportIssue({
        kind,
        description: description.trim(),
        // Off a document there is no checkbox; the page and browser are still
        // sent. Unticked, the checkbox drops only the page and document link.
        context: contextLabel === null || attachContext ? context : withoutPage(context),
        files: attachments.map((a) => a.file),
      });
      attachments.forEach((a) => revokePreviewUrl(a.previewUrl));
      setAttachments([]);
      setDescription("");
      setKind(DEFAULT_KIND);
      setAttachContext(true);
      setFileError(null);
      clearDraft(draftOwner);
      onSent(result.id);
    } catch (err) {
      if (err instanceof ApiError && (err.status === 413 || err.status === 415)) {
        // A refused file belongs beside the file control.
        setFileError(err.message);
      } else if (!(err instanceof ApiError) && err instanceof TypeError) {
        // fetch rejects with a TypeError when no response arrives.
        setSendError(NETWORK_ERROR);
      } else {
        setSendError(userMessage(err));
      }
    } finally {
      setSending(false);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== "Tab" || !dialogRef.current) return;
    const focusables = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && active === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  };

  const onPaste = (e: React.ClipboardEvent) => {
    const files = Array.from(e.clipboardData?.files ?? []);
    if (files.length === 0) return;
    e.preventDefault();
    addFiles(files);
  };

  return (
    <div
      style={ui.scrim}
      className="app-dialog-scrim-enter"
      onClick={(e) => {
        if (e.target === e.currentTarget && !sending) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        style={styles.dialog}
        className="app-dialog-enter"
        onKeyDown={onKeyDown}
        onPaste={onPaste}
      >
        <div style={styles.header}>
          <h2 id={titleId} style={ui.dialogTitle}>
            Report an issue
          </h2>
          <button
            type="button"
            aria-label="Close"
            title="Close"
            style={ui.iconButton}
            className={uiClass.btnQuiet}
            onClick={onClose}
            disabled={sending}
          >
            <Icon glyph={Close} size={20} />
          </button>
        </div>

        <div style={styles.field}>
          <label htmlFor={kindId} style={styles.label}>
            What kind
          </label>
          <select
            id={kindId}
            style={ui.select}
            value={kind}
            disabled={sending}
            onChange={(e) => setKind(e.target.value as IssueKind)}
          >
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {ISSUE_KIND_LABELS[k]}
              </option>
            ))}
          </select>
        </div>

        <div style={styles.field}>
          <label htmlFor={descriptionId} style={styles.label}>
            What happened
          </label>
          <textarea
            id={descriptionId}
            ref={textareaRef}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={4}
            style={{ ...ui.textarea, width: "100%", boxSizing: "border-box" }}
          />
        </div>

        <div style={styles.field}>
          <span id={`${titleId}-files`} style={styles.label}>
            Screenshots and files
          </span>
          <div
            role="group"
            aria-labelledby={`${titleId}-files`}
            aria-describedby={fileError ? fileErrorId : undefined}
            className={`file-drop-zone${dragging ? " is-dragging" : ""}`}
            style={styles.dropArea}
            onDragOver={(e) => {
              e.preventDefault();
              if (!dragging) setDragging(true);
            }}
            onDragLeave={(e) => {
              if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
              setDragging(false);
            }}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              addFiles(Array.from(e.dataTransfer?.files ?? []));
            }}
          >
            <span>Paste, drop or</span>
            <button
              ref={chooseFilesRef}
              type="button"
              style={styles.linkButton}
              onClick={() => fileInputRef.current?.click()}
            >
              Choose files
            </button>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept={ALLOWED_EXTENSIONS.join(",")}
              aria-label="Files to attach"
              style={{ display: "none" }}
              onChange={(e) => {
                addFiles(Array.from(e.target.files ?? []));
                e.target.value = "";
              }}
            />
          </div>
          <span style={ui.metadata}>Up to 5 files, 10 MB each</span>
          {fileError && (
            <span id={fileErrorId} role="alert" style={styles.error}>
              {fileError}
            </span>
          )}
          {attachments.length > 0 && (
            <ul ref={attachmentListRef} style={styles.attachmentList}>
              {attachments.map((a) =>
                a.previewUrl ? (
                  <li key={a.key} style={styles.thumb}>
                    <img src={a.previewUrl} alt={a.file.name} style={styles.thumbImg} />
                    <button
                      type="button"
                      data-remove-file=""
                      aria-label={`Remove ${a.file.name}`}
                      title={`Remove ${a.file.name}`}
                      style={styles.thumbRemove}
                      onClick={() => removeAttachment(a.key)}
                    >
                      <Icon glyph={Close} size={16} />
                    </button>
                  </li>
                ) : (
                  <li key={a.key} style={styles.chip}>
                    <span style={styles.chipName} title={a.file.name}>
                      {a.file.name}
                    </span>
                    <button
                      type="button"
                      data-remove-file=""
                      aria-label={`Remove ${a.file.name}`}
                      title={`Remove ${a.file.name}`}
                      style={styles.chipRemove}
                      className={uiClass.btnQuiet}
                      onClick={() => removeAttachment(a.key)}
                    >
                      <Icon glyph={Close} size={16} />
                    </button>
                  </li>
                ),
              )}
            </ul>
          )}
        </div>

        {contextLabel !== null && (
          <label style={styles.checkboxRow}>
            <input
              type="checkbox"
              checked={attachContext}
              onChange={(e) => setAttachContext(e.target.checked)}
              style={ui.checkbox}
            />
            <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>Attach this page: {contextLabel}</span>
          </label>
        )}

        {sendError && (
          <p role="alert" style={{ ...styles.error, margin: `${ds.space.lg}px 0 0` }}>
            {sendError}
          </p>
        )}

        <div style={{ ...ui.dialogActionBar, marginTop: sendError ? ds.space.md : ds.space.xl }}>
          <button
            type="button"
            className={uiClass.btnPrimary}
            style={ui.buttonPrimary}
            onClick={send}
            disabled={!canSend}
            aria-busy={sending || undefined}
          >
            {sending ? "Sending issue…" : "Send issue"}
          </button>
        </div>
      </div>
    </div>
  );
}

const styles: Record<string, CSSProperties> = {
  dialog: {
    ...ui.dialog,
    maxWidth: 520,
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: ds.space.md,
    marginBottom: ds.space.lg,
  },
  field: {
    display: "flex",
    flexDirection: "column",
    gap: ds.space.sm,
    marginBottom: ds.space.lg,
  },
  dropArea: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    flexWrap: "wrap",
    gap: 4,
    minHeight: 64,
    padding: ds.space.md,
    border: `1px dashed ${tokens.color.border.control}`,
    borderRadius: tokens.radius.control,
    background: tokens.surface.default,
    ...ui.bodyText,
    color: tokens.color.text.secondary,
  },
  linkButton: {
    padding: 0,
    border: "none",
    background: "transparent",
    cursor: "pointer",
    fontFamily: ds.fontBody,
    fontSize: 14,
    fontWeight: ds.weight.medium,
    color: tokens.color.text.primary,
    textDecoration: "underline",
    textUnderlineOffset: 3,
  },
  label: {
    ...ui.fieldLabel,
    color: tokens.color.text.primary,
  },
  error: {
    fontFamily: ds.fontBody,
    fontSize: 13,
    color: ds.errorText,
    margin: 0,
  },
  attachmentList: {
    listStyle: "none",
    margin: 0,
    padding: 0,
    display: "flex",
    flexWrap: "wrap",
    gap: ds.space.sm,
    alignItems: "center",
  },
  thumb: {
    position: "relative",
    width: 72,
    height: 72,
    borderRadius: tokens.radius.control,
    border: `1px solid ${tokens.color.border.subtle}`,
    overflow: "hidden",
    flexShrink: 0,
  },
  thumbImg: {
    width: "100%",
    height: "100%",
    objectFit: "cover",
    display: "block",
  },
  thumbRemove: {
    position: "absolute",
    top: 2,
    right: 2,
    width: 24,
    height: 24,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    padding: 0,
    border: "none",
    borderRadius: "50%",
    background: ds.white,
    color: tokens.color.text.primary,
    cursor: "pointer",
  },
  chip: {
    display: "inline-flex",
    alignItems: "center",
    gap: 4,
    maxWidth: "100%",
    minHeight: 32,
    paddingLeft: ds.space.md,
    border: `1px solid ${tokens.color.border.subtle}`,
    borderRadius: tokens.radius.control,
    ...ui.metadata,
    color: tokens.color.text.primary,
  },
  chipName: {
    minWidth: 0,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    maxWidth: 240,
  },
  chipRemove: {
    ...ui.iconButton,
    minWidth: 28,
    minHeight: 28,
  },
  checkboxRow: {
    display: "flex",
    alignItems: "center",
    gap: ds.space.sm,
    ...ui.bodyText,
    cursor: "pointer",
  },
};
