"""Generate the seed data for both legacy systems.

The company is fictional: Tamarind Global Logistics. Every person, email and
payment below is invented.

The data is deterministic (fixed random seed) so the ground truth in
eval/expected.py never drifts. Most records are ordinary, well-behaved
employees and accounts. A small set of planted cases make an access review
worth doing; they are marked "Planted" below and described in docs/DATA.md.

Run:  python scripts/seed.py
"""
from __future__ import annotations

import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOMAIN = "tamarind.example"
REVIEW_DATE = "2026-11-24"

rng = random.Random(20261124)

FIRST = [
    "Aarav", "Aditi", "Akash", "Amrita", "Ankit", "Bhavna", "Chetan", "Divya", "Gaurav", "Harini",
    "Ishaan", "Jaya", "Karan", "Lavanya", "Manish", "Neha", "Omkar", "Pooja", "Pranav", "Ritika",
    "Sahil", "Shreya", "Tanvi", "Uday", "Varun", "Yamini", "Zoya", "Abhishek", "Bharat", "Charu",
    "Devika", "Esha", "Farhan", "Geeta", "Hemant", "Irfan", "Jatin", "Kiran", "Lokesh", "Mansi",
]
LAST = [
    "Agarwal", "Banerjee", "Chatterjee", "Dutta", "Ghosh", "Hegde", "Jain", "Kapoor", "Khanna", "Malhotra",
    "Mishra", "Naidu", "Pandey", "Pillai", "Prasad", "Saxena", "Sethi", "Sinha", "Thakur", "Venkatesh",
]


def email(first: str, last: str) -> str:
    return f"{first.lower()}.{last.lower()}@{DOMAIN}"


# --------------------------------------------------------------------------
# PeopleHub: employees
# --------------------------------------------------------------------------
employees: list[dict] = []
_next_id = 1001


def add_employee(first, last, department, title, manager_id=None, *, status="active",
                 hire_date="2021-04-01", termination_date=None, rehire_date=None,
                 leave_until=None, past_emails=None, job_history=None, salary=None,
                 work_email=None):
    global _next_id
    emp = {
        "employee_id": f"E{_next_id}",
        "first_name": first,
        "last_name": last,
        "work_email": work_email or email(first, last),
        "past_emails": past_emails or [],
        "department": department,
        "title": title,
        "manager_id": manager_id,
        "employment_type": "employee",
        "status": status,
        "hire_date": hire_date,
        "termination_date": termination_date,
        "rehire_date": rehire_date,
        "leave_until": leave_until,
        "job_history": job_history or [],
        "annual_salary_usd": salary or rng.randrange(28_000, 140_000, 500),
    }
    _next_id += 1
    employees.append(emp)
    return emp


# Leadership and finance management (the certifiers)
cfo = add_employee("Lakshmi", "Krishnan", "Finance", "CFO", hire_date="2016-06-01", salary=310_000)
controller = add_employee("Sanjay", "Mehta", "Finance", "Financial Controller", cfo["employee_id"], hire_date="2018-01-15")
fin_mgr = add_employee("Ritu", "Bhatt", "Finance", "Finance Manager", cfo["employee_id"], hire_date="2019-03-04")
treasury_mgr = add_employee("Arvind", "Kulkarni", "Finance", "Treasury Manager", cfo["employee_id"], hire_date="2017-08-21")
proc_head = add_employee("Suresh", "Menon", "Procurement", "Head of Procurement", hire_date="2018-05-07")
coo = add_employee("Nandini", "Rao", "Operations", "COO", hire_date="2015-02-02")
sales_head = add_employee("Rakesh", "Iyer", "Sales", "Head of Sales", hire_date="2019-09-09")
hr_head = add_employee("Farah", "Siddiqui", "HR", "HR Director", hire_date="2017-11-13")
it_head = add_employee("Joseph", "Mathew", "IT", "IT Manager", hire_date="2018-07-30")
audit_mgr = add_employee("Harsha", "Vardhan", "Internal Audit", "Audit Manager", cfo["employee_id"], hire_date="2020-02-17")
ap_sup = add_employee("Ananya", "Bose", "Finance", "AP Supervisor", fin_mgr["employee_id"], hire_date="2020-06-01")

# ---- Planted cases --------------------------------------------------------
# 1. Leaver who approved a payment after leaving (shown by the live update)
rpatel = add_employee("Rahul", "Patel", "Finance", "AP Supervisor", fin_mgr["employee_id"],
                      status="terminated", hire_date="2019-10-01", termination_date="2026-11-03")
# 2. Leaver whose Ledgerly account still uses her maiden-name email
priya = add_employee("Priya", "Menon", "Procurement", "Vendor Master Analyst", proc_head["employee_id"],
                     status="terminated", hire_date="2020-01-06", termination_date="2026-10-17",
                     past_emails=[f"priya.nair@{DOMAIN}"])
# 3. Leaver whose Ledgerly email differs only in case; also owns a service account
arjun = add_employee("Arjun", "Shah", "Finance", "GL Accountant", controller["employee_id"],
                     status="terminated", hire_date="2018-03-12", termination_date="2026-09-30")
# 4. Leaver, low-risk access
meera = add_employee("Meera", "Iyer", "Finance", "Accounts Receivable Clerk", fin_mgr["employee_id"],
                     status="terminated", hire_date="2022-07-18", termination_date="2026-08-14")
# 5. Leaver with payment and treasury release rights
vikram = add_employee("Vikram", "Rao", "Finance", "Treasury Analyst", treasury_mgr["employee_id"],
                      status="terminated", hire_date="2021-02-01", termination_date="2026-11-14")
# 6. Rehire: left in 2025, came back in 2026. Must NOT be flagged.
sneha = add_employee("Sneha", "Kulkarni", "Finance", "Accounts Payable Clerk", ap_sup["employee_id"],
                     status="active", hire_date="2019-05-06", termination_date="2025-03-31",
                     rehire_date="2026-02-02")
# 7. On parental leave. Must NOT be flagged.
anil = add_employee("Anil", "Deshmukh", "Finance", "Senior Accountant", controller["employee_id"],
                    status="on_leave", hire_date="2018-09-17", leave_until="2027-01-15")
# 8. Resigned, serving notice: last day in the future. Upcoming, not a leaver yet.
kavita = add_employee("Kavita", "Joshi", "Finance", "Accounts Payable Specialist", ap_sup["employee_id"],
                      status="active", hire_date="2021-11-22", termination_date="2026-12-15")
# 9-11. Movers (job changes since July) who kept finance privileges
rohan = add_employee("Rohan", "Gupta", "Sales", "Sales Executive", sales_head["employee_id"],
                     hire_date="2020-04-13", job_history=[{
                         "effective_date": "2026-08-01", "from_department": "Finance",
                         "from_title": "AP Supervisor", "to_department": "Sales", "to_title": "Sales Executive"}])
fatima = add_employee("Fatima", "Sheikh", "HR", "HR Business Partner", hr_head["employee_id"],
                      hire_date="2021-06-07", job_history=[{
                          "effective_date": "2026-09-15", "from_department": "Finance",
                          "from_title": "Treasury Analyst", "to_department": "HR", "to_title": "HR Business Partner"}])
nikhil = add_employee("Nikhil", "Verma", "Procurement", "Procurement Specialist", proc_head["employee_id"],
                      hire_date="2019-12-02", job_history=[{
                          "effective_date": "2026-07-20", "from_department": "Finance",
                          "from_title": "GL Accountant", "to_department": "Procurement", "to_title": "Procurement Specialist"}])
# 12. Mover handled correctly (promotion, old role already removed). Must NOT be flagged.
swati = add_employee("Swati", "Reddy", "Finance", "AP Supervisor", fin_mgr["employee_id"],
                     hire_date="2022-03-14", job_history=[{
                         "effective_date": "2026-08-10", "from_department": "Finance",
                         "from_title": "Accounts Payable Clerk", "to_department": "Finance", "to_title": "AP Supervisor"}])
# 13. Active employee with a segregation-of-duties conflict
deepak = add_employee("Deepak", "Nair", "Procurement", "Procurement Manager", proc_head["employee_id"],
                      hire_date="2017-04-24")
# 14. IT owner of a healthy service account
it_admin = add_employee("Tarun", "Bhatia", "IT", "Systems Administrator", it_head["employee_id"],
                        hire_date="2020-10-05")
# 15-17. Earlier leavers whose accounts were disabled on time. Must be ignored.
old_leavers = [
    add_employee("Gopal", "Rangan", "Finance", "Accounts Payable Clerk", ap_sup["employee_id"],
                 status="terminated", hire_date="2019-01-07", termination_date="2026-06-30"),
    add_employee("Leela", "Fernandes", "Finance", "Credit Controller", fin_mgr["employee_id"],
                 status="terminated", hire_date="2020-08-03", termination_date="2026-05-15"),
    add_employee("Mohit", "Arora", "Procurement", "Procurement Specialist", proc_head["employee_id"],
                 status="terminated", hire_date="2021-01-18", termination_date="2026-07-31"),
]

# ---- Ordinary staff (no findings) ----------------------------------------
FINANCE_ROLES = [
    # (department, title, manager, roles in Ledgerly)
    ("Finance", "Accounts Payable Clerk", ap_sup, ["AP_CLERK"]),
    ("Finance", "Accounts Payable Clerk", ap_sup, ["AP_CLERK"]),
    ("Finance", "Accounts Payable Clerk", ap_sup, ["AP_CLERK", "READ_ONLY"]),
    ("Finance", "Accounts Payable Specialist", ap_sup, ["AP_CLERK"]),
    ("Finance", "Accounts Receivable Clerk", fin_mgr, ["AR_CLERK"]),
    ("Finance", "Accounts Receivable Clerk", fin_mgr, ["AR_CLERK"]),
    ("Finance", "AR Specialist", fin_mgr, ["AR_CLERK", "READ_ONLY"]),
    ("Finance", "Credit Controller", fin_mgr, ["AR_CLERK"]),
    ("Finance", "GL Accountant", controller, ["GL_ADMIN"]),
    ("Finance", "GL Accountant", controller, ["GL_ADMIN"]),
    ("Finance", "Senior Accountant", controller, ["GL_ADMIN", "READ_ONLY"]),
    ("Finance", "Treasury Analyst", treasury_mgr, ["TREASURY_RELEASE"]),
    ("Procurement", "Procurement Specialist", proc_head, ["VENDOR_ADMIN"]),
    ("Procurement", "Procurement Specialist", proc_head, ["VENDOR_ADMIN"]),
    ("Procurement", "Vendor Master Analyst", proc_head, ["VENDOR_ADMIN"]),
    ("Internal Audit", "Internal Auditor", audit_mgr, ["AUDITOR"]),
    ("Internal Audit", "Internal Auditor", audit_mgr, ["AUDITOR"]),
    ("Operations", "Operations Manager", coo, ["READ_ONLY"]),
    ("Sales", "Sales Operations Analyst", sales_head, ["READ_ONLY"]),
]
OTHER_ROLES = [
    ("Operations", "Warehouse Supervisor", coo), ("Operations", "Fleet Coordinator", coo),
    ("Operations", "Dispatch Planner", coo), ("Operations", "Customs Executive", coo),
    ("Sales", "Account Manager", sales_head), ("Sales", "Sales Executive", sales_head),
    ("HR", "HR Generalist", hr_head), ("HR", "Recruiter", hr_head),
    ("IT", "Support Engineer", it_head), ("IT", "Network Engineer", it_head),
]

used_names = {(e["first_name"], e["last_name"]) for e in employees}


def fresh_name():
    while True:
        pair = (rng.choice(FIRST), rng.choice(LAST))
        if pair not in used_names:
            used_names.add(pair)
            return pair


ordinary_finance: list[tuple[dict, list[str]]] = []
for dept, title, mgr, roles in FINANCE_ROLES:
    f, l = fresh_name()
    emp = add_employee(f, l, dept, title, mgr["employee_id"],
                       hire_date=f"20{rng.randint(17, 25)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}")
    ordinary_finance.append((emp, roles))

for i in range(38):
    dept, title, mgr = OTHER_ROLES[i % len(OTHER_ROLES)]
    f, l = fresh_name()
    add_employee(f, l, dept, title, mgr["employee_id"],
                 hire_date=f"20{rng.randint(15, 25)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}")

# A few ordinary leavers who never had Ledgerly access (noise for the leaver list)
for i in range(4):
    dept, title, mgr = OTHER_ROLES[i * 2]
    f, l = fresh_name()
    add_employee(f, l, dept, title, mgr["employee_id"], status="terminated",
                 hire_date="2022-01-10", termination_date=f"2026-{7 + i:02d}-{10 + i * 3:02d}")


# --------------------------------------------------------------------------
# Ledgerly: user accounts, roles, SoD rules, activity log
# --------------------------------------------------------------------------
ROLES = {
    "READ_ONLY": {"description": "View ledgers, vendors and payments. No changes.", "risk": "low",
                  "typical_titles": ["*"]},
    "AP_CLERK": {"description": "Enter vendor invoices for payment.", "risk": "medium",
                 "typical_titles": ["Accounts Payable Clerk", "Accounts Payable Specialist"]},
    "AR_CLERK": {"description": "Record customer receipts and credit notes.", "risk": "medium",
                 "typical_titles": ["Accounts Receivable Clerk", "AR Specialist", "Credit Controller"]},
    "VENDOR_ADMIN": {"description": "Create vendors and change vendor bank details.", "risk": "high",
                     "typical_titles": ["Procurement Specialist", "Procurement Manager", "Vendor Master Analyst"]},
    "PAYMENT_APPROVER": {"description": "Approve vendor payments up to USD 250,000.", "risk": "high",
                         "typical_titles": ["AP Supervisor", "Finance Manager", "Financial Controller", "CFO"]},
    "GL_ADMIN": {"description": "Post and reverse general-ledger journals.", "risk": "high",
                 "typical_titles": ["GL Accountant", "Senior Accountant", "Financial Controller"]},
    "TREASURY_RELEASE": {"description": "Release payment batches to the bank.", "risk": "critical",
                         "typical_titles": ["Treasury Analyst", "Treasury Manager", "CFO"]},
    "AUDITOR": {"description": "Read everything, including the audit log.", "risk": "low",
                "typical_titles": ["Internal Auditor", "Audit Manager"]},
}

SOD_RULES = [
    {"rule_id": "SOD-01", "roles": ["VENDOR_ADMIN", "PAYMENT_APPROVER"],
     "risk": "Can create a vendor and approve payments to it (fictitious-vendor fraud)."},
    {"rule_id": "SOD-02", "roles": ["AP_CLERK", "PAYMENT_APPROVER"],
     "risk": "Can enter an invoice and approve its own payment."},
    {"rule_id": "SOD-03", "roles": ["GL_ADMIN", "TREASURY_RELEASE"],
     "risk": "Can release funds and post the journals that hide them."},
]

users: list[dict] = []


def add_user(username, emp=None, roles=(), *, email_override=None, type_="human", status="active",
             created_at="2022-01-03", disabled_at=None, display_name=None, owner_employee_id=None,
             company=None):
    users.append({
        "username": username,
        "display_name": display_name or (f"{emp['first_name']} {emp['last_name']}" if emp else username),
        "email": email_override if email_override is not None else (emp["work_email"] if emp else None),
        "type": type_,
        "company": company,
        "owner_employee_id": owner_employee_id,
        "status": status,
        "roles": list(roles),
        "created_at": created_at,
        "disabled_at": disabled_at,
        "disabled_reason": None,
    })


def uname(emp):
    return (emp["first_name"][0] + emp["last_name"]).lower()


add_user(uname(cfo), cfo, ["PAYMENT_APPROVER", "READ_ONLY"], created_at="2016-06-03")
add_user(uname(controller), controller, ["PAYMENT_APPROVER", "GL_ADMIN"], created_at="2018-01-16")
add_user(uname(fin_mgr), fin_mgr, ["PAYMENT_APPROVER"], created_at="2019-03-05")
add_user(uname(treasury_mgr), treasury_mgr, ["TREASURY_RELEASE"], created_at="2017-08-22")
add_user(uname(proc_head), proc_head, ["VENDOR_ADMIN", "READ_ONLY"], created_at="2018-05-08")
add_user(uname(audit_mgr), audit_mgr, ["AUDITOR"], created_at="2020-02-18")
# Ananya holds both AP_CLERK and PAYMENT_APPROVER: SoD-02
add_user(uname(ap_sup), ap_sup, ["PAYMENT_APPROVER", "AP_CLERK"], created_at="2020-06-02")

# Planted accounts
add_user("rpatel", rpatel, ["PAYMENT_APPROVER", "AP_CLERK"], created_at="2019-10-02")
add_user("pnair", priya, ["VENDOR_ADMIN"], email_override=f"priya.nair@{DOMAIN}",
         display_name="Priya Nair", created_at="2020-01-07")
add_user("ashah", arjun, ["GL_ADMIN"], email_override=f"Arjun.Shah@{DOMAIN.upper()}", created_at="2018-03-13")
add_user("miyer", meera, ["AR_CLERK", "READ_ONLY"], created_at="2022-07-19")
add_user("vrao", vikram, ["TREASURY_RELEASE", "PAYMENT_APPROVER"], created_at="2021-02-02")
add_user("skulkarni", sneha, ["AP_CLERK"], created_at="2026-02-03")
add_user("adeshmukh", anil, ["GL_ADMIN", "READ_ONLY"], created_at="2018-09-18")
add_user("kjoshi", kavita, ["AP_CLERK"], created_at="2021-11-23")
add_user("rgupta", rohan, ["AP_CLERK", "PAYMENT_APPROVER"], created_at="2020-04-14")
add_user("fsheikh", fatima, ["TREASURY_RELEASE", "READ_ONLY"], created_at="2021-06-08")
add_user("nverma", nikhil, ["GL_ADMIN", "VENDOR_ADMIN"], created_at="2019-12-03")
add_user("sreddy", swati, ["PAYMENT_APPROVER"], created_at="2022-03-15")
add_user("dnair", deepak, ["VENDOR_ADMIN", "PAYMENT_APPROVER"], created_at="2017-04-25")
# Service accounts
add_user("svc_batch_payments", None, ["PAYMENT_APPROVER"], type_="service",
         display_name="Nightly payment batch", created_at="2019-02-11", owner_employee_id=None)
add_user("svc_gl_import", None, ["GL_ADMIN"], type_="service",
         display_name="Bank statement GL import", created_at="2020-05-04", owner_employee_id=arjun["employee_id"])
add_user("svc_fx_rates", None, ["READ_ONLY"], type_="service",
         display_name="Daily FX rate loader", created_at="2021-03-09", owner_employee_id=it_admin["employee_id"])
# External contractor with no HR record
add_user("ext_dkozlov", None, ["AP_CLERK"], type_="contractor", email_override="d.kozlov@finbridge-consulting.example",
         display_name="Dmitri Kozlov", company="FinBridge Consulting", created_at="2026-04-01")
# Leavers already handled correctly
for emp in old_leavers:
    add_user(uname(emp), emp, ["AP_CLERK"] if "Payable" in emp["title"] else ["AR_CLERK"] if "Credit" in emp["title"] else ["VENDOR_ADMIN"],
             status="disabled", created_at="2021-02-01", disabled_at=emp["termination_date"] + "T18:00:00Z")
for u in users:
    if u["status"] == "disabled":
        u["disabled_reason"] = "Leaver - HR ticket"
# Ordinary accounts
for emp, roles in ordinary_finance:
    add_user(uname(emp), emp, roles, created_at=emp["hire_date"])

# Dormant accounts and a shared login: the two findings most access reviews add
# after leavers and movers. Each comes with a case that must NOT be flagged.
credit_ctrl = ordinary_finance[7][0]      # Credit Controller: last signed in early July
vendor_analyst = ordinary_finance[14][0]  # Vendor Master Analyst: VENDOR_ADMIN, last signed in May
ops_mgr = ordinary_finance[17][0]         # Operations Manager: account created in May, never used
new_ap = ordinary_finance[3][0]           # AP Specialist: account created 10 Nov, not used yet (grace period)
DORMANT_LAST_LOGIN = {uname(credit_ctrl): "2026-07-02T09:14:00Z", uname(vendor_analyst): "2026-05-19T11:30:00Z",
                      "adeshmukh": "2026-07-28T17:05:00Z"}   # Anil: on approved leave since 1 Aug, not dormant
NEVER_USED = {uname(ops_mgr): "2026-05-04", uname(new_ap): "2026-11-10"}
for u in users:
    if u["username"] in NEVER_USED:
        u["created_at"] = NEVER_USED[u["username"]]
add_user("finops_shared", None, ["AP_CLERK", "READ_ONLY"], type_="shared",
         email_override=f"finance-ops@{DOMAIN}", display_name="Finance Ops team login", created_at="2019-07-01")

# Activity log. Ledgerly always recorded this; the API only exposes it after the
# live update (GET /users/{username}/activity).
activity: list[dict] = []


def act(ts, username, action, detail, amount=None):
    activity.append({"ts": ts, "username": username, "action": action, "detail": detail,
                     "amount_usd": amount})


VENDORS = ["Zenith Office Supplies", "Coastal Freight Lines", "Sahyadri Packaging", "BluePeak Fuel Cards",
           "Orbit Customs Brokers", "Monsoon IT Services"]
active_humans = [u for u in users if u["status"] == "active" and u["type"] == "human"]
for u in active_humans:
    if u["username"] in {"rpatel", "pnair", "ashah", "miyer", "vrao"}:
        continue
    for k in range(rng.randint(1, 3)):
        day = rng.randint(3, 21)
        act(f"2026-11-{day:02d}T{rng.randint(8, 18):02d}:{rng.randint(0, 59):02d}:00Z", u["username"], "login",
            "Signed in from office network")
    if "PAYMENT_APPROVER" in u["roles"]:
        act(f"2026-11-{rng.randint(4, 20):02d}T11:{rng.randint(10, 59):02d}:00Z", u["username"], "approve_payment",
            f"PAY-{rng.randint(87000, 88300)} to {rng.choice(VENDORS)}", rng.randrange(1_200, 60_000, 50))

# Leavers' activity: normal before their last day...
act("2026-10-30T10:02:00Z", "rpatel", "login", "Signed in from office network")
act("2026-10-30T10:15:00Z", "rpatel", "approve_payment", "PAY-87911 to Coastal Freight Lines", 12_450)
act("2026-09-25T09:40:00Z", "ashah", "login", "Signed in from office network")
act("2026-08-11T15:05:00Z", "miyer", "login", "Signed in from office network")
act("2026-11-12T16:20:00Z", "vrao", "login", "Signed in from office network")
act("2026-10-14T12:00:00Z", "pnair", "login", "Signed in from office network")
# ...and AFTER their last day (the reason the live update matters)
act("2026-10-29T22:47:00Z", "pnair", "login", "Signed in from 103.48.17.209 (residential ISP, Pune)")
act("2026-10-29T22:51:00Z", "pnair", "view_vendor_list", "Viewed vendor master (412 records)")
act("2026-11-06T21:12:00Z", "rpatel", "login", "Signed in from 49.36.88.14 (mobile network)")
act("2026-11-06T21:18:00Z", "rpatel", "approve_payment",
    "PAY-88412 to Zenith Office Supplies (bank details changed 2026-11-05)", 48_200)
act("2026-11-04T02:00:00Z", "svc_batch_payments", "approve_payment", "Nightly batch BATCH-1104 (31 payments)", 214_880)
# Dormant and never-used accounts have no recent sign-ins; drop the generated ones.
activity = [a for a in activity if a["username"] not in DORMANT_LAST_LOGIN and a["username"] not in NEVER_USED]
for username, ts in DORMANT_LAST_LOGIN.items():
    act(ts, username, "login", "Signed in from office network")
# The shared login is used by several people from several places.
act("2026-11-17T09:02:00Z", "finops_shared", "login", "Signed in from office network (Mumbai)")
act("2026-11-18T13:40:00Z", "finops_shared", "login", "Signed in from office network (Chennai)")
act("2026-11-19T20:11:00Z", "finops_shared", "login", "Signed in from 182.70.4.12 (home broadband)")
act("2026-11-19T20:16:00Z", "finops_shared", "enter_invoice", "INV-55102 for Monsoon IT Services", 7_400)
activity.sort(key=lambda a: a["ts"])

# Ledgerly keeps the time of each account's latest sign-in on the account itself.
for u in users:
    logins = [a["ts"] for a in activity if a["username"] == u["username"] and a["action"] == "login"]
    u["last_login"] = max(logins) if logins else None


def main():
    hub_dir = ROOT / "systems" / "peoplehub"
    led_dir = ROOT / "systems" / "ledgerly"
    hub_dir.mkdir(parents=True, exist_ok=True)
    led_dir.mkdir(parents=True, exist_ok=True)
    (hub_dir / "data.json").write_text(json.dumps({
        "company": "Tamarind Global Logistics",
        "employees": employees,
    }, indent=1) + "\n")
    (led_dir / "data.json").write_text(json.dumps({
        "company": "Tamarind Global Logistics",
        "roles": ROLES,
        "sod_rules": SOD_RULES,
        "users": users,
        "activity": activity,
    }, indent=1) + "\n")
    print(f"PeopleHub: {len(employees)} employees "
          f"({sum(e['status'] == 'terminated' for e in employees)} terminated)")
    print(f"Ledgerly:  {len(users)} accounts ({sum(u['status'] == 'active' for u in users)} active), "
          f"{len(activity)} activity events")


if __name__ == "__main__":
    main()
