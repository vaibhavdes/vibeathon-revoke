"""PeopleHub: Tamarind Global Logistics' HR system of record.

A small FastAPI service that has run the company's employee records since
2018. It knows who works here, in which job, who their manager is, and who
has left. It knows nothing about application accounts.

Run:  uvicorn main:app --port 8201
"""

import json
import threading
from datetime import date
from pathlib import Path
from typing import Optional, Union

from fastapi import FastAPI
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
SEED_FILE = HERE / "data.json"
STATE_FILE = HERE / ".state.json"

app = FastAPI(title="PeopleHub", version="3.4.1",
              description="HR system of record for Tamarind Global Logistics.")
_lock = threading.Lock()


def _load() -> dict:
    source = STATE_FILE if STATE_FILE.exists() else SEED_FILE
    return json.loads(source.read_text())


def _save(db: dict) -> None:
    STATE_FILE.write_text(json.dumps(db, indent=1))


def _employees() -> list[dict]:
    return _load()["employees"]


def _by_id(employee_id: str) -> Optional[dict]:
    employee_id = (employee_id or "").strip().upper()
    return next((e for e in _employees() if e["employee_id"] == employee_id), None)


def _name(emp: dict) -> str:
    return f"{emp['first_name']} {emp['last_name']}"


def _manager_ref(emp: dict) -> Optional[dict]:
    mgr = _by_id(emp["manager_id"]) if emp.get("manager_id") else None
    if not mgr:
        return None
    return {"employee_id": mgr["employee_id"], "name": _name(mgr), "email": mgr["work_email"]}


def _compact(record: dict) -> dict:
    """Drop empty fields so responses stay small."""
    return {k: v for k, v in record.items() if v not in (None, [], "")}


def _public(emp: dict) -> dict:
    return _compact({
        "employee_id": emp["employee_id"],
        "name": _name(emp),
        "work_email": emp["work_email"],
        "past_emails": emp["past_emails"],
        "department": emp["department"],
        "title": emp["title"],
        "status": emp["status"],
        "hire_date": emp["hire_date"],
        "termination_date": emp["termination_date"],
        "rehire_date": emp["rehire_date"],
        "leave_until": emp["leave_until"],
        "manager": _manager_ref(emp),
    })


def _parse_date(value: str) -> Optional[date]:
    try:
        return date.fromisoformat((value or "").strip()[:10])
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Lookups
# --------------------------------------------------------------------------
class EmailLookup(BaseModel):
    emails: Optional[Union[list[str], str]] = None


@app.post("/employees/lookup")
def lookup_employees_by_email(body: EmailLookup):
    """Match emails (list) to employees, case-insensitive, including past emails such as a maiden name. Returns status (active, on_leave, terminated), termination/rehire/leave dates, title, department, manager. Non-matches go to unknown_emails. The bridge from an app account to HR."""
    raw = body.emails or []
    if isinstance(raw, str):
        raw = raw.split(",")
    wanted = [e.strip() for e in raw if e and e.strip()]
    if not wanted:
        return {"ok": False, "error": "Provide at least one email in 'emails'."}
    index: dict[str, tuple[dict, str]] = {}
    for emp in _employees():
        index[emp["work_email"].lower()] = (emp, "work_email")
        for old in emp["past_emails"]:
            index.setdefault(old.lower(), (emp, "past_email"))
    matches, unknown = [], []
    for e in wanted:
        hit = index.get(e.lower())
        if not hit:
            unknown.append(e)
            continue
        emp, how = hit
        mgr = _manager_ref(emp)
        matches.append(_compact({
            "email": e,
            "matched_on": "past_email" if how == "past_email" else None,
            "current_email": emp["work_email"] if how == "past_email" else None,
            "employee_id": emp["employee_id"],
            "name": _name(emp),
            "status": emp["status"],
            "title": emp["title"],
            "department": emp["department"],
            "termination_date": emp["termination_date"],
            "rehire_date": emp["rehire_date"],
            "leave_until": emp["leave_until"],
            "manager": f"{mgr['name']} ({mgr['employee_id']})" if mgr else None,
        }))
    return {"ok": True, "matches": matches, "unknown_emails": unknown}


@app.get("/employees/leavers/since/{since_date}")
def list_leavers_since(since_date: str):
    """People whose employment ended on or after since_date (YYYY-MM-DD), with current status. Rehired people appear with rehire_date and status active. A future termination_date means notice period, not left yet."""
    since = _parse_date(since_date)
    if not since:
        return {"ok": False, "error": "since_date must be YYYY-MM-DD."}
    rows = []
    for emp in _employees():
        ended = _parse_date(emp["termination_date"] or "")
        if ended and ended >= since:
            row = _public(emp)
            row.pop("hire_date", None)
            rows.append(row)
    rows.sort(key=lambda r: r["termination_date"])
    return {"ok": True, "since": since.isoformat(), "count": len(rows), "leavers": rows}


@app.get("/employees/job-changes/since/{since_date}")
def list_job_changes_since(since_date: str):
    """Internal transfers and promotions effective on or after since_date (YYYY-MM-DD): old and new title and department, and manager. Finds people whose access may still reflect their old job."""
    since = _parse_date(since_date)
    if not since:
        return {"ok": False, "error": "since_date must be YYYY-MM-DD."}
    rows = []
    for emp in _employees():
        for move in emp["job_history"]:
            when = _parse_date(move["effective_date"])
            if when and when >= since:
                rows.append(_compact({
                    "employee_id": emp["employee_id"],
                    "name": _name(emp),
                    "work_email": emp["work_email"],
                    "status": emp["status"],
                    "effective_date": move["effective_date"],
                    "from_title": move["from_title"],
                    "from_department": move["from_department"],
                    "to_title": move["to_title"],
                    "to_department": move["to_department"],
                    "manager": _manager_ref(emp),
                }))
    rows.sort(key=lambda r: r["effective_date"])
    return {"ok": True, "since": since.isoformat(), "count": len(rows), "job_changes": rows}


@app.get("/employees/{employee_id}")
def get_employee(employee_id: str):
    """One employee by id (e.g. E1012): emails, job, status, dates, manager. Resolves service-account owners."""
    emp = _by_id(employee_id)
    if not emp:
        return {"ok": False, "error": f"No employee with id {employee_id}."}
    return {"ok": True, "employee": _public(emp)}


@app.get("/managers/{employee_id}/reports")
def list_direct_reports(employee_id: str):
    """Direct reports of a manager (by employee_id), with job and status."""
    mgr = _by_id(employee_id)
    if not mgr:
        return {"ok": False, "error": f"No employee with id {employee_id}."}
    reports = [_compact({"employee_id": e["employee_id"], "name": _name(e), "title": e["title"],
                         "status": e["status"], "work_email": e["work_email"]})
               for e in _employees() if e["manager_id"] == mgr["employee_id"]]
    return {"ok": True, "manager": {"employee_id": mgr["employee_id"], "name": _name(mgr)},
            "count": len(reports), "reports": reports}


# --------------------------------------------------------------------------
# HR-only operations (used by the HR team's own screens)
# --------------------------------------------------------------------------
@app.get("/employees/{employee_id}/compensation")
def get_compensation(employee_id: str):
    """Salary details for one employee. HR payroll staff only."""
    emp = _by_id(employee_id)
    if not emp:
        return {"ok": False, "error": f"No employee with id {employee_id}."}
    return {"ok": True, "employee_id": emp["employee_id"], "annual_salary_usd": emp["annual_salary_usd"]}


class NewEmployee(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    department: Optional[str] = None
    title: Optional[str] = None
    manager_id: Optional[str] = None
    hire_date: Optional[str] = None


@app.post("/employees")
def create_employee(body: NewEmployee):
    """Create a new employee record (HR onboarding)."""
    if not (body.first_name and body.last_name and body.department and body.title):
        return {"ok": False, "error": "first_name, last_name, department and title are required."}
    with _lock:
        db = _load()
        next_num = max(int(e["employee_id"][1:]) for e in db["employees"]) + 1
        emp = {
            "employee_id": f"E{next_num}", "first_name": body.first_name, "last_name": body.last_name,
            "work_email": f"{body.first_name.lower()}.{body.last_name.lower()}@tamarind.example",
            "past_emails": [], "department": body.department, "title": body.title,
            "manager_id": body.manager_id, "employment_type": "employee", "status": "active",
            "hire_date": body.hire_date or date.today().isoformat(), "termination_date": None,
            "rehire_date": None, "leave_until": None, "job_history": [], "annual_salary_usd": None,
        }
        db["employees"].append(emp)
        _save(db)
    return {"ok": True, "employee": _public(emp)}


@app.post("/admin/reseed")
def reseed():
    """Discard all changes and restore the seed data."""
    with _lock:
        STATE_FILE.unlink(missing_ok=True)
    return {"ok": True}


@app.get("/health")
def health():
    """Liveness probe."""
    return {"ok": True, "service": "peoplehub"}
