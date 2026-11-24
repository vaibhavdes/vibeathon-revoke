# The demo data

Tamarind Global Logistics is fictional. Every person, email address, IP address and payment is invented.
`scripts/seed.py` generates the data with a fixed random seed, so the answers never drift.

| System | Records |
|---|---|
| PeopleHub (HR) | 89 employees: 12 have left, 1 is on parental leave, 1 is serving notice, 1 was rehired |
| Ledgerly (finance) | 47 accounts: 44 active (39 people, 3 service accounts, 1 external contractor, 1 shared team login), 3 disabled; 75 activity events. Each account has `last_login`, as most apps keep |

The review date is pinned to **2026-11-24** (`REVIEW_DATE` in `.env`).

## Planted cases

Most records are ordinary. These are the ones a review has to get right. `eval/expected.py` recomputes the
answer key from the raw files and checks it against this list.

| Account | Person | What is going on | Correct outcome |
|---|---|---|---|
| `rpatel` | Rahul Patel | Left 2026-11-03. Still holds PAYMENT_APPROVER + AP_CLERK. Signed in on 2026-11-06 and approved **PAY-88412, USD 48,200** to a vendor whose bank details changed the day before. | Leaver (critical), SoD-02, **used after leaving** (visible only after the live update) |
| `pnair` | Priya Menon | Left 2026-10-17. Her Ledgerly account still uses her **maiden-name email** `priya.nair@`. Signed in from a residential IP on 2026-10-29 and viewed the vendor master. | Leaver, found through the past email; **used after leaving** |
| `ashah` | Arjun Shah | Left 2026-09-30. Ledgerly stores his email as `Arjun.Shah@TAMARIND.example`. Also the named owner of `svc_gl_import`. | Leaver (case-insensitive match) |
| `miyer` | Meera Iyer | Left 2026-08-14, low-risk roles. | Leaver (high) |
| `vrao` | Vikram Rao | Left 2026-11-14 with TREASURY_RELEASE + PAYMENT_APPROVER. | Leaver (critical) |
| `skulkarni` | Sneha Kulkarni | Left in 2025, **rehired** 2026-02-02. Appears in the HR leavers list. | **Not** flagged |
| `adeshmukh` | Anil Deshmukh | On parental leave until 2027-01-15. Last signed in 2026-07-28, so his account also *looks* dormant. | **Not** flagged (approved leave is not dormancy) |
| `kjoshi` | Kavita Joshi | Resigned; last day **2026-12-15** (in the future). | Upcoming leaver, do not disable now |
| `rgupta` | Rohan Gupta | Moved AP Supervisor → Sales Executive on 2026-08-01. Kept AP_CLERK + PAYMENT_APPROVER. | Mover ×2, SoD-02 |
| `fsheikh` | Fatima Sheikh | Moved Treasury Analyst → HR Business Partner. Kept TREASURY_RELEASE. | Mover |
| `nverma` | Nikhil Verma | Moved GL Accountant → Procurement Specialist. Kept GL_ADMIN (VENDOR_ADMIN fits the new job). | Mover for GL_ADMIN only |
| `sreddy` | Swati Reddy | Promoted AP Clerk → AP Supervisor; old role already removed. | **Not** flagged |
| `dnair` | Deepak Nair | Procurement Manager with VENDOR_ADMIN + PAYMENT_APPROVER. | SoD-01 |
| `abose` | Ananya Bose | AP Supervisor with AP_CLERK + PAYMENT_APPROVER. | SoD-02 |
| `svc_batch_payments` | (service) | Approves the nightly payment batch. **No owner** recorded. | Needs an owner; never auto-disable |
| `svc_gl_import` | (service) | Owner is Arjun Shah, who left. | Needs a new owner |
| `svc_fx_rates` | (service) | Owner is an active IT administrator. | **Not** flagged |
| `ext_dkozlov` | Dmitri Kozlov | External contractor, no HR record at all. | Unknown identity, escalate |
| `fmishra` | Farhan Mishra | Vendor Master Analyst with VENDOR_ADMIN. Last signed in 2026-05-19. | Dormant (high: vendor master rights) |
| `fpillai` | Farhan Pillai | Credit Controller. Last signed in 2026-07-02. | Dormant |
| `aagarwal` | Amrita Agarwal | Account created 2026-05-04 and **never used**. | Dormant (never signed in) |
| `mkhanna` | Manish Khanna | Account created 2026-11-10, not used yet. | **Not** flagged (inside the 30-day grace period) |
| `finops_shared` | (shared) | `finance-ops@` team login with AP_CLERK, signed in from three places in one week. | Shared account: replace with named accounts |
| `grangan`, `lfernandes`, `marora` | | Earlier leavers, disabled on time. | Ignored (not active) |

## Policy constants

Dormant means no sign-in for **90 days**, or never signed in and older than **30 days**
(`DORMANT_DAYS`, `NEW_ACCOUNT_GRACE_DAYS` in `agent/grounding.py`). People on approved leave and leavers are not
dormant; leavers are reported as leavers. These thresholds are the usual ones in access-review policies; change them
there.

## How close is this to real data?

The records have the shape of real exports: an HR system's employee ID, manager, job history, past emails, rehire,
leave and termination dates; a finance app's accounts, account types, roles, last sign-in and segregation-of-duties
rules. Each planted case is a common access-review finding. What it is not: real people, or the scale of a large
company (89 employees). Pointing Revoke at a real app means one `synapse build`, the allowed endpoints in
`mcp/exposure.json`, and mapping field names where they differ.
