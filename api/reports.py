"""Authenticated report review, using the application's run and canonical stores."""
from contextlib import closing
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
import server
from reports.registry import REPORTS
from reports.repository import family, snapshot, save_field, report_view, StaleReportEdit

router = APIRouter()


class ReportEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str | None = Field(default=None, max_length=500000)
    revision: int = Field(ge=0)


@router.get("/api/runs/{run_id}/reports")
def get_reports(run_id: int):
    with closing(server._open_audit_conn()) as conn:
        try:
            return snapshot(conn, run_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc


@router.patch("/api/runs/{run_id}/reports/{report}/{concept}")
def edit_report(run_id: int, report: str, concept: str, body: ReportEdit):
    if report not in REPORTS:
        raise HTTPException(404, "Report not found")
    with closing(server._open_audit_conn()) as conn, conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            _, _, run = family(conn, run_id)
            if run.status in {"running", "pending", "draft", "preparing"}:
                raise HTTPException(409, "Wait for extraction to finish before editing reports")
            previous = next((f for f in report_view(conn, run_id, report)["fields"] if f["concept"] == concept), None)
            if previous is None:
                raise ValueError("Unknown report field")
            value = body.value if body.value and body.value.strip() else None
            if previous["kind"] == "html" and value:
                from notes.html_to_text import html_to_excel_text
                if not html_to_excel_text(value).strip():
                    value = None
            save_field(conn, run_id, report, concept, value,
                                  "user_override" if value else "cleared", "User correction", previous["source_pages"],
                                  expected_revision=body.revision)
            return next(f for f in report_view(conn, run_id, report)["fields"] if f["concept"] == concept)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except StaleReportEdit as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
