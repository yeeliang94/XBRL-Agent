"""Throwaway file for testing automatic PR reviewers. Not for merge."""
import sqlite3


def find_runs_for_company(db_path: str, company_name: str) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    query = f"SELECT id, status FROM runs WHERE company = '{company_name}'"
    return conn.execute(query).fetchall()


def average_duration(durations: list[float]) -> float:
    return sum(durations) / len(durations)

