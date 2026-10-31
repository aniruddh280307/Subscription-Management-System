# SubscriptHQ — Antigravity Instructions & Development Log

> **Purpose:** This file is the persistent source of truth for AI-assisted development. Keep it updated after every task. Do not rely on chat history as the only project documentation.

## 1. Project overview

SubscriptHQ is a subscription and billing management platform.

**Existing stack**
- Frontend: React + Vite
- Backend: Python + FastAPI
- Database: Supabase PostgreSQL
- ORM/migrations: SQLAlchemy + Alembic
- Frontend tools already planned/used: Axios, React Router, TanStack Query, Recharts, Lucide React

**Known setup status (2026-10-31)**
- Supabase PostgreSQL connection tested successfully with `python -m app.test_db` (`Database connected: 1`).
- SQLAlchemy database connection exists at `backend/app/database.py`.
- Alembic initialized in `backend/alembic/` with `backend/alembic.ini`.
- Root `.gitignore` exists and ignores `.env`, Python virtual environments, Node modules and build output.
- `backend/.env` is local only and must never be committed.
- Git repository: https://github.com/aniruddh280307/Subscription-Management-System
- Local Git branch: `main`.
- Initial commit was created. Push status should be verified with `git status` and `git log`; do not assume a push succeeded unless verified.
- Important: database connectivity is verified, but application tables/migrations and full application workflows are NOT thereby verified.

## 2. Product modules

Build in this order:
1. Database foundation and migrations
2. Authentication and role-based access
3. Products and subscription plans
4. Customer management
5. Subscription lifecycle
6. Invoices and billing
7. Payments and payment tracking
8. Dashboard and reporting
9. Revenue Guardian
10. HAR Diagnostics
11. End-to-end frontend/backend integration
12. Security, tests and deployment preparation

### Roles
- Admin: full access.
- Staff: operational access to customers, subscriptions, invoices and payments.
- Customer: access only to their own records.

### Revenue Guardian
An explainable, database-backed monitoring module. Initial rules should detect overdue invoices, expected but missing invoices, duplicate invoices/payment attempts, failed payments, incorrect totals, expired discounts still applied (if discounts exist), upcoming renewals and invoice/payment mismatches. Each finding needs evidence, severity, affected records, recommended action, timestamp and status. Do not label suspected issues as confirmed.

### HAR Diagnostics
Authorized staff upload browser-exported HAR files. Validate uploads; parse request metadata; show method, URL/host, status, duration, timestamp and content type; identify failed/slow requests. Redact cookies, authorization headers, tokens, API keys and sensitive query parameters. Do not store raw request/response bodies by default. Store sanitized reports in private storage. Do not claim automatic capture of all browser traffic.

## 3. Non-negotiable engineering rules

### Inspect before changing
- Inspect the current repository and relevant files before proposing or editing.
- Never assume a file, dependency, table, column, API route or function exists; verify it.
- Reuse existing working code and the existing SQLAlchemy connection.
- Do not recreate the project from scratch.
- If requirements or architecture are ambiguous, ask before guessing.

### No hardcoding or fake functionality
- Never hardcode database credentials, JWT secrets, API keys, service-role keys, passwords or environment-specific URLs.
- Use environment variables and validated settings.
- Keep actual `.env` files out of Git. `.env.example` may contain names and blank/example placeholders only.
- Never expose Supabase service-role keys in frontend code.
- No fake API success responses, mock production data, random dashboard figures or pretend integrations.
- Do not claim a feature works unless it has been tested. Clearly label untested work.

### Database and financial safety
- PostgreSQL/Supabase is the database source of truth.
- Use SQLAlchemy models and Alembic migrations.
- Never drop, truncate, reset or destructively alter database data without explicit approval.
- Show and inspect migrations before applying them.
- Coordinate migrations because the database is shared.
- Use UUIDs where appropriate, foreign keys, constraints, indexes and relationships.
- Use `Decimal`/`Numeric` for money, never binary floating point.
- Use transactions for financial operations.
- Preserve invoice/payment history and use idempotency to prevent duplicate payment processing.
- Do not claim real payment processing until a real provider is integrated and tested.

### Security and quality
- Enforce authorization in backend endpoints, not only in the UI.
- Customers must never access another customer's records.
- Hash passwords securely; never store plaintext passwords.
- Validate inputs, handle errors, protect sensitive logs and use pagination where appropriate.
- Keep routes, schemas, services, models and UI responsibilities separated.
- Avoid unnecessary dependencies and duplicate implementations.

### Agent and Git permissions
- Do not delete working files, overwrite migrations, rewrite Git history, force-push, commit or push without asking.
- Do not print or expose secret values.
- Do not run destructive commands without explicit approval.
- Implement only the approved milestone.

## 4. Required workflow for every milestone

1. Restate the task and inspect relevant files.
2. Report current state and verified facts.
3. Propose a short plan, files to create/change, dependencies, database impact and security considerations.
4. Wait for approval before modifying files.
5. Implement only the approved scope.
6. Run relevant tests and report exact commands/results.
7. Fix failures; do not hide or skip them.
8. Report changed files, completed work, limitations and next step.
9. Update this Markdown file as described below.

## 5. Mandatory Markdown update policy

**After every approved task, update this file (`PROJECT_CONTEXT.md`) before declaring the task complete.**

Maintain these sections at the bottom of this file:
- **Current status:** what is implemented and verified.
- **Completed milestones:** date, scope, changed files and test results.
- **Pending work:** what remains.
- **Known issues/limitations:** exact errors or unverified areas.
- **Architecture decisions:** decisions made and reasons.
- **Database/migration history:** migration revision, purpose, whether applied; never include credentials.
- **Next action:** the single next recommended task.

Rules:
- Record facts only. Distinguish `Implemented`, `Tested`, `Verified`, `Pending` and `Blocked`.
- Include actual test commands and outcomes. Never invent passing tests.
- Keep entries concise but sufficiently detailed for a new AI session/developer to resume.
- Preserve previous history; append entries instead of erasing useful records.
- Never put passwords, connection strings, tokens, private keys or other secrets in this file.
- If unable to update this file, stop and tell the user.

## 6. Milestone prompts

Use one prompt at a time. Do not ask the agent to build the entire application in one run.

### Prompt 0 — Read project instructions
Read this entire `PROJECT_CONTEXT.md` first. Treat it as the project source of truth. Inspect the repository and compare actual code with the status recorded here. Do not modify files yet. Report discrepancies and wait for instructions.

### Prompt 1 — Audit database foundation
Audit `backend/app/database.py`, `backend/alembic.ini`, `backend/alembic/env.py`, `backend/app/`, `backend/requirements.txt`, root `.gitignore` and environment template files. The database connection was previously tested successfully, but do not assume current configuration is still correct. Do not print secrets, change credentials, create tables, apply migrations or modify files. Report verified configuration, problems, proposed corrections and exact files affected. Wait for approval.

### Prompt 2 — SQLAlchemy models and first migration
After approval, design and implement the initial models for User, Customer, Product, SubscriptionPlan, Subscription, Invoice, InvoiceItem, Payment and AuditLog. Inspect existing models first and avoid duplicates. Use UUID identifiers where appropriate, proper relationships/foreign keys, constraints/indexes, timestamps and Numeric monetary fields. Configure Alembic metadata imports. Generate a migration and show/review its operations. Do not apply it until the user explicitly approves. Test model imports and migration generation. Update this file with exact status and migration revision.

### Prompt 3 — Authentication and authorization
Implement secure password hashing, login, JWT authentication, current-user dependency and Admin/Staff/Customer authorization using existing models and configuration. Enforce customer data isolation on backend routes. Add tests for valid/invalid credentials, token validation/expiry and role permissions. No hardcoded secrets. Update this file with files changed and actual test results.

### Prompt 4 — Products, plans and customers
Implement CRUD APIs, schemas, services and frontend screens for products, subscription plans and customers. Include validation, filtering and pagination. Connect to real PostgreSQL data; no fake production data. Apply role permissions. Test CRUD and access control. Update this file.

### Prompt 5 — Subscription lifecycle
Implement subscription creation, trial, renewal dates, upgrades/downgrades, cancellation and valid status transitions. Define billing-cycle/date/time-zone rules explicitly. Persist history. Do not charge money automatically. Test lifecycle edge cases. Update this file.

### Prompt 6 — Invoices and payments
Implement invoice numbering, line items, taxes/discounts if supported, due dates, invoice statuses, partial/full payment recording, payment statuses, refunds and idempotency. Use Decimal/Numeric and database transactions. Preserve financial history. Do not integrate or simulate a real payment gateway unless explicitly requested. Test totals, partial payments, duplicate requests, failures and refunds. Update this file.

### Prompt 7 — Dashboard and reports
Build dashboard APIs using real database aggregations and frontend charts. Include customer/subscription counts, MRR/ARR with documented assumptions, invoice balances, overdue invoices, payment outcomes, revenue trends and upcoming renewals. No hardcoded or random metrics. Test calculations and avoid double-counting. Update this file.

### Prompt 8 — Revenue Guardian
Implement explainable database-backed detection rules for overdue invoices, missing expected invoices, duplicate billing records/attempts, failed payments, incorrect totals, renewal alerts and payment/invoice mismatches. Add evidence, severity, affected records, recommendation, timestamp and workflow status. Clearly distinguish suspected from confirmed issues. Test each rule. Update this file.

### Prompt 9 — HAR Diagnostics
Implement secure staff-only HAR upload and parsing. Validate file size/type and untrusted content. Redact cookies, authorization headers, tokens, API keys and sensitive query parameters on server side; do not store raw request/response bodies by default. Store sanitized reports privately. Display request metadata, failures and slow requests. Do not claim automatic browser-wide capture. Test parsing, redaction and access controls. Update this file.

### Prompt 10 — Integration and final audit
Audit all frontend/backend routes and workflows. Test authentication, role isolation, database persistence, subscription-to-invoice-to-payment flow, dashboard calculations, Revenue Guardian and HAR Diagnostics. Fix verified defects. Report exact test commands/results, known limitations, local run instructions and deployment prerequisites. Update this file. Do not claim full completion if any major workflow remains untested.

## 7. Current status — update after each task

### Current status
- Database foundation & migrations: Supabase PostgreSQL connected; initial schema migration `807fd70445a0` successfully applied via `alembic upgrade head`.
- All 9 core application tables live in Supabase: `users`, `customers`, `products`, `subscription_plans`, `subscriptions`, `invoices`, `invoice_items`, `payments`, `audit_logs`.
- Backend authentication & RBAC operational: user registration, bcrypt password hashing, login, JWT token issuance/verification, active-status checks, and role dependencies (`admin`, `staff`, `customer`).
- Customer management operational: full CRUD with search, pagination, active-status filtering, strict customer-level isolation, and safe archiving to preserve financial history.
- Product management operational: full CRUD with code uniqueness, search, pagination, admin/staff write permissions, customer visibility of active products, and safe archiving.
- Subscription plan management operational: full CRUD linked to products, support for weekly/monthly/quarterly/yearly intervals, interval counts, trial periods, Decimal pricing, validation, and safe archiving.
- Subscription lifecycle operational: full CRUD + cancel/pause/resume/change-plan with calendar-safe billing period calculation, trial support, strict state-transition graph, audit logging, and customer isolation.
- Invoices and Payments operational: DRAFT→OPEN→PAID lifecycle, line-item price snapshots, sequential invoice/payment numbering, partial payments, payment idempotency, full/partial refunds with invoice reconciliation, voiding, uncollectible marking, overdue lazy detection, customer isolation.
- API documentation: Interactive Swagger docs active at `/docs`, OpenAPI spec at `/openapi.json`, and database-backed health check at `/health` and `/api/health`.
- Frontend prototype foundation: classy responsive authentication UI, Customer registration, login, forgot/reset-password screens, protected workspace shell, session-scoped token handling, environment-configured API URL, and restricted CORS configuration are implemented and browser-verified.
- Product/plan foundation: generic product pricing and variants, daily billing, quantity/availability/payment-term rules, plan lifecycle permissions, corrected multi-interval MRR normalization, and availability-aware subscription operations are implemented. Migration `b31f6c2a9d10` is applied and verified in Supabase. The classy role-aware catalog UI is implemented.
- Test record: the earlier full backend run recorded **111 passed**. On 2026-10-31, 9 new isolated authentication-security unit tests passed; the database-writing integration suite was not rerun against the shared Supabase database.

### Completed milestones
- **2026-10-31 — Prompt 0 & Prompt 1: Database Foundation Audit & Corrections**
  - **Scope:** Repository inspection, database foundation audit, path resolution fix in `backend/app/database.py`, metadata and URL connection in `backend/alembic/env.py`, UTF-8 re-encoding of `backend/requirements.txt`, creation of `backend/.env.example`.
  - **Changed files:** `backend/app/database.py`, `backend/alembic/env.py`, `backend/requirements.txt`, `backend/.env.example`.
  - **Test results:** Verified database connection, zero BOM in requirements.txt, alembic online connection.

- **2026-10-31 — Prompt 2: SQLAlchemy Models Architecture & Initial Migration Generation**
  - **Scope:** Designed and implemented all 9 models with UUID primary keys, Decimal monetary fields, customer/staff/admin role segregation, customer-payment consistency foreign key constraints, invoice status lifecycle, line-item pricing snapshots, payment idempotency, and historical record preservation (`is_archived` on Products/Plans). Configured Alembic model imports and generated initial migration `807fd70445a0`.
  - **Changed files:** `backend/app/models.py`, `backend/alembic/env.py`, `backend/alembic/versions/807fd70445a0_initial_schema.py`.

- **2026-10-31 — Prompt 6: Invoices and Payments Module**
  - **Scope:** DRAFT invoice creation with line-item price snapshots and sequential numbering (`INV-YYYYMM-NNNNNN`), totals computation (subtotal + tax − discount), DRAFT→OPEN finalization, DRAFT-only edits, void and uncollectible marking, payment recording (`PAY-YYYYMM-NNNNNN`) with idempotency, partial and full payments updating `amount_paid`/`amount_due`/`status`, failed payments (no financial effect), partial and full refunds reconciling invoice totals, lazy overdue detection, customer isolation on all routes, admin/staff write restrictions.
  - **Created files:** `backend/app/schemas/invoice.py`, `backend/app/services/invoice_service.py`, `backend/app/routers/invoices.py`, `backend/tests/test_invoices.py`.
  - **Modified files:** `backend/app/main.py` (registered `invoices.router`).
  - **Endpoints implemented:** `POST /api/invoices`, `GET /api/invoices`, `GET /api/invoices/{id}`, `PATCH /api/invoices/{id}`, `POST /api/invoices/{id}/finalize`, `POST /api/invoices/{id}/void`, `POST /api/invoices/{id}/mark-uncollectible`, `POST /api/invoices/{id}/payments`, `GET /api/invoices/{id}/payments`, `GET /api/payments`, `GET /api/payments/{id}`, `POST /api/payments/{id}/refund`.
  - **Test results:** `28 passed in 199.10s` — all 28 invoice and payment integration tests.

- **2026-10-31 — Prompt 5: Subscription Lifecycle Module**
  - **Scope:** Subscription creation (with/without trial), calendar-safe billing period calculation (monthly/quarterly/yearly/weekly, leap-year and month-end anchoring), state-transition graph (`TRIALING`→`ACTIVE`/`CANCELED`/`PAUSED`, etc.), immediate and period-end cancellation, pause/resume, immediate plan change with period recalculation, customer isolation, audit logging.
  - **Created files:** `backend/app/schemas/subscription.py`, `backend/app/services/subscription_service.py`, `backend/app/routers/subscriptions.py`, `backend/tests/test_subscriptions.py`.
  - **Modified files:** `backend/app/main.py` (registered `subscriptions.router`).
  - **Endpoints implemented:** `POST /api/subscriptions`, `GET /api/subscriptions`, `GET /api/subscriptions/{id}`, `PATCH /api/subscriptions/{id}`, `POST /api/subscriptions/{id}/cancel`, `POST /api/subscriptions/{id}/pause`, `POST /api/subscriptions/{id}/resume`, `POST /api/subscriptions/{id}/change-plan`.
  - **Test results:** `34 passed in 186.92s` — 10 pure-function unit tests (calendar math) + 24 API integration tests.
    - `TestCalendarMath` (10 tests): `add_calendar_months` and `calculate_period_end` for all intervals, month-end anchoring, leap years, year rollover.
    - `TestSubscriptionCreation` (6 tests): no-trial active, trial TRIALING status, Jan-31 monthly capping, archived plan rejection, nonexistent plan 404, nonexistent customer 404.
    - `TestCustomerIsolation` (3 tests): cross-customer create forbidden, cross-customer view forbidden, list scoped to own customer.
    - `TestCancellation` (3 tests): immediate cancel, period-end flag, double-cancel rejection.
    - `TestPauseResume` (5 tests): pause active, double-pause rejection, resume paused, resume-non-paused rejection, pause-canceled rejection.
    - `TestPlanChange` (4 tests): immediate change recalculates period end, same-plan rejection, archived plan rejection, change on canceled rejected.
    - `TestSubscriptionListing` (3 tests): status filter, 404 on unknown id, unauthenticated 401.

- **2026-10-31 — Master Backend Implementation: Stages 1 to 5**
  - **Stage 1 (Migration Application):** Applied migration `807fd70445a0` to Supabase PostgreSQL using `alembic upgrade head`. Verified all 9 tables exist in database. Current Alembic revision: `807fd70445a0 (head)`.
  - **Stage 2 (Authentication & Roles):** Implemented bcrypt password hashing, JWT creation/decoding, user registration with duplicate prevention, login, `/api/auth/me`, and RBAC dependencies (`require_admin`, `require_staff_or_admin`, customer isolation).
  - **Stage 3 (Customer Management):** Implemented customer CRUD (`/api/customers`), pagination, name/email search, customer isolation (customers only see own records), staff/admin management, and historical archiving on delete.
  - **Stage 4 (Product Management):** Implemented product CRUD (`/api/products`), unique code validation, pagination, search, admin/staff write permissions, customer active visibility, and catalog archiving.
  - **Stage 5 (Subscription Plan Management):** Implemented plan CRUD (`/api/plans`) with parent product validation, billing intervals (`weekly`, `monthly`, `quarterly`, `yearly`), interval count >= 1, Decimal pricing, trial periods >= 0, code uniqueness, customer visibility, and plan archiving.
  - **Endpoints Implemented:**
    - `GET /` & `GET /health` & `GET /api/health`
    - `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me`
    - `POST /api/customers`, `GET /api/customers`, `GET /api/customers/{customer_id}`, `PATCH /api/customers/{customer_id}`, `DELETE /api/customers/{customer_id}`
    - `POST /api/products`, `GET /api/products`, `GET /api/products/{product_id}`, `PATCH /api/products/{product_id}`, `DELETE /api/products/{product_id}`
    - `POST /api/plans`, `GET /api/plans`, `GET /api/plans/{plan_id}`, `PATCH /api/plans/{plan_id}`, `DELETE /api/plans/{plan_id}`
  - **Changed / Created files:**
    - `backend/app/security.py` (created)
    - `backend/app/dependencies.py` (created)
    - `backend/app/schemas/common.py` (created)
    - `backend/app/schemas/auth.py` (created)
    - `backend/app/schemas/customer.py` (created)
    - `backend/app/schemas/product.py` (created)
    - `backend/app/schemas/plan.py` (created)
    - `backend/app/routers/auth.py` (created)
    - `backend/app/routers/customers.py` (created)
    - `backend/app/routers/products.py` (created)
    - `backend/app/routers/plans.py` (created)
    - `backend/app/main.py` (modified: included all routers and enhanced health check)
    - `backend/app/models.py` (modified: added `overlaps` attributes to silence relationship mapping warnings)
    - `backend/tests/conftest.py` (created)
    - `backend/tests/test_database.py` (created)
    - `backend/tests/test_auth_roles.py` (created)
    - `backend/tests/test_customers.py` (created)
    - `backend/tests/test_products.py` (created)
    - `backend/tests/test_plans.py` (created)
  - **Test commands & results:**
    - Command: `venv\Scripts\pytest -v tests` (in `backend/`)
    - Outcome: **13 passed in 66.92s**
      1. `tests/test_database.py::test_database_connection` -> PASSED
      2. `tests/test_database.py::test_all_nine_tables_exist` -> PASSED
      3. `tests/test_database.py::test_alembic_current_revision` -> PASSED
      4. `tests/test_auth_roles.py::test_auth_registration_success` -> PASSED
      5. `tests/test_auth_roles.py::test_auth_duplicate_registration_rejection` -> PASSED
      6. `tests/test_auth_roles.py::test_auth_login_success` -> PASSED
      7. `tests/test_auth_roles.py::test_auth_invalid_password_rejection` -> PASSED
      8. `tests/test_auth_roles.py::test_auth_me_jwt_verification` -> PASSED
      9. `tests/test_auth_roles.py::test_auth_inactive_user_rejection` -> PASSED
      10. `tests/test_auth_roles.py::test_role_restrictions` -> PASSED
      11. `tests/test_customers.py::test_customer_crud_and_isolation` -> PASSED
      12. `tests/test_products.py::test_product_crud_and_archiving` -> PASSED
      13. `tests/test_plans.py::test_subscription_plan_lifecycle` -> PASSED

- **2026-10-31 — Prompt 7: Dashboard and Reports Module**
  - **Scope:** Real database aggregations for MRR/ARR with documented formulas (normalizing weekly, monthly, quarterly, yearly), customer/subscription counts by status, total invoiced/collected/outstanding, overdue metrics, monthly revenue trends (gross, refunded, net), upcoming renewals within customizable window, and invoice status breakdown.
  - **Created files:** `backend/app/services/dashboard_service.py`, `backend/app/routers/dashboard.py`, `backend/tests/test_dashboard.py`.
  - **Modified files:** `backend/app/main.py`.
  - **Test results:** 13 passed in 64.69s.

- **2026-10-31 — Prompt 8: Revenue Guardian Module**
  - **Scope:** Explainable, database-backed automated monitoring service with 8 detection rules: overdue invoices, failed payments, duplicate charges (same invoice/date), invoice total calculation discrepancies, payment-to-due balance mismatches, upcoming renewals, missing cycle invoices, and cross-customer payment mismatches. Each finding includes severity, certainty, evidence, and actionable recommendations.
  - **Created files:** `backend/app/services/guardian_service.py`, `backend/app/routers/guardian.py`, `backend/tests/test_guardian.py`.
  - **Modified files:** `backend/app/main.py`.
  - **Test results:** 11 passed in 51.08s.

- **2026-10-31 — Prompt 9: HAR Diagnostics Module**
  - **Scope:** Secure staff-only browser HAR upload, schema validation, file size limits (20MB), automated server-side redaction (Authorization, Cookies, API keys, sensitive URL query tokens), zero raw request/response body storage, diagnostic metrics (failed/slow requests, status distributions), and private storage management.
  - **Created files:** `backend/app/schemas/har.py`, `backend/app/services/har_service.py`, `backend/app/routers/har.py`, `backend/tests/test_har.py`.
  - **Modified files:** `backend/app/main.py`, `.gitignore`.
  - **Test results:** 11 passed in 29.05s.

- **2026-10-31 — Prompt 10: End-to-End Integration & Workspace Audit**
  - **Scope:** Full cross-module integration test auditing the entire lifecycle: multi-role registration & login -> customer creation -> catalog product & plan -> subscription creation (trial + billing math) -> invoice generation & finalization -> partial & full payments with idempotency -> dashboard metric verification -> Revenue Guardian integrity verification -> HAR Diagnostics upload & server-side redaction.
  - **Created files:** `backend/tests/test_integration_e2e.py`, `SUBSCRIPTHQ_IMPLEMENTATION_REPORT.md`.
  - **Cleaned files:** Removed temporary `backend/app/test_db.py`.
  - **Test results:** 1 passed in 33.58s (E2E lifecycle audit).

- **2026-10-31 — Authentication Security & Password Reset Milestone**
  - **Scope:** Removed caller-selected roles from public registration; public registration now creates Customer users only. Added Admin-only Staff creation, PDF-compliant password validation (more than eight characters with uppercase, lowercase, and special character), rejection of passwords exceeding bcrypt's 72-byte input boundary, SMTP password-reset email delivery, short-lived reset tokens tied to the current password hash, and automatic one-time invalidation after a password change.
  - **Created files:** `backend/app/services/email_service.py`, `backend/tests/test_auth_security_unit.py`.
  - **Modified files:** `backend/app/database.py`, `backend/app/schemas/auth.py`, `backend/app/routers/auth.py`, `backend/app/security.py`, `backend/tests/test_auth_roles.py`, `backend/.env.example`.
  - **Endpoints added:** `POST /api/auth/staff`, `POST /api/auth/forgot-password`, `POST /api/auth/reset-password`.
  - **Verification:** `python -m pytest -q tests/test_auth_security_unit.py` -> **9 passed**; backend compilation passed; OpenAPI generation passed with 49 paths. Database-writing integration tests were intentionally not run against the shared Supabase database.
  - **Configuration required:** Real SMTP values and `FRONTEND_RESET_URL` must be supplied through the uncommitted local environment before email delivery can be verified end to end.

- **2026-10-31 — Classy Frontend Authentication Foundation**
  - **Scope:** Replaced the Vite starter screen with a responsive SubscriptHQ prototype using a deep navy/ivory/gold visual system. Implemented login, Customer registration, forgot-password, reset-password, authenticated user loading, sign-out, and a role-aware workspace shell. Added session-scoped JWT storage, environment-configured API base URL, and explicit backend CORS origins.
  - **Modified files:** `frontend/src/App.jsx`, `frontend/src/App.css`, `frontend/src/index.css`, `frontend/index.html`, `backend/app/main.py`, `backend/app/database.py`, `backend/.env.example`.
  - **Created file:** `frontend/.env.example`.
  - **Verification:** `npm run build` passed; `npm run lint` passed; browser visual inspection passed for login and registration at the mobile breakpoint; no browser console warnings or errors; backend import/OpenAPI generation passed with 49 paths.

- **2026-10-31 — Product Variants & Plan Rules Foundation**
  - **Scope:** Added product type, sales price, cost price, notes, generic attribute/value variants with optional SKU and decimal price modifiers. Added daily billing, minimum quantity, plan availability dates, payment terms, and per-plan automatic closure, early closure, pause, and renewal controls. Customer plan visibility and subscription/plan-change validation now respect availability dates; pause and early cancellation respect plan policy. Corrected MRR calculations so multi-interval plans divide by the interval count rather than multiply it.
  - **Created files:** `backend/alembic/versions/b31f6c2a9d10_product_variants_and_plan_rules.py`, `backend/tests/test_catalog_rules_unit.py`.
  - **Modified files:** `backend/app/models.py`, `backend/app/schemas/product.py`, `backend/app/schemas/plan.py`, `backend/app/routers/products.py`, `backend/app/routers/plans.py`, `backend/app/services/subscription_service.py`, `backend/app/services/dashboard_service.py`.
  - **API changes:** Product create/update/read responses include pricing metadata and variants. Added `POST /api/products/{product_id}/variants` and `PATCH`/`DELETE /api/products/{product_id}/variants/{variant_id}`. Plan requests and responses include the new rules.
  - **Migration status:** `b31f6c2a9d10` is the local Alembic head. Offline SQL was generated and reviewed. It is **Pending / Not applied** to Supabase.
  - **Verification:** Python compile passed; OpenAPI generation passed with 51 paths; Alembic head/history validation passed; offline migration SQL generation passed; `python -m pytest -q tests/test_catalog_rules_unit.py tests/test_auth_security_unit.py` -> **17 passed**. Database integration tests were not run because the migration is not applied and tests still target shared Supabase.

- **2026-10-31 — Product/Plan Migration, Live Verification & Catalog UI**
  - **Scope:** Applied migration `b31f6c2a9d10` to Supabase after explicit approval. Verified the live revision, `product_variants` table, four product columns, and eight plan-rule columns. Strengthened integration tests to cover nested variants, variant create/update/deactivate operations, and daily plan rules. Added a role-aware Products & Plans frontend with real API loading, product and variant creation, plan creation, availability/payment-term fields, lifecycle controls, empty states, responsive tables/cards, and Admin/Staff-only management controls.
  - **Modified files:** `backend/tests/test_products.py`, `backend/tests/test_plans.py`, `frontend/src/App.jsx`, `frontend/src/App.css`.
  - **Database:** Supabase Alembic revision is now `b31f6c2a9d10 (head)`.
  - **Verification:** Live schema inspection passed. `python -m pytest -q tests/test_products.py tests/test_plans.py tests/test_catalog_rules_unit.py` -> **10 passed**. Frontend `npm run lint` and `npm run build` passed. Automated browser visual control was unavailable during the final catalog check; no product/plan records were created, and the temporary Staff preview account was removed successfully.

- **2026-10-31 — Commercial Lifecycle, Billing UI & Revenue Guardian Completion (through Milestone 9)**
  - **Scope:** Added quotation templates and Draft -> Quotation -> Confirmed -> Active -> Closed transitions; quotation activation creates subscriptions. Added database-backed tax/discount rules, recurring invoice generation with billing-key idempotency, tax/discount application, invoice email/print output, manual payment dates/notes, refunds, and persistent Revenue Guardian findings with Open/Acknowledged/Resolved workflow.
  - **Revenue Guardian:** Expanded monitoring to 10 checks: overdue invoices, failed payments, duplicate payments, duplicate invoices, invoice total discrepancies, paid/due balance mismatches, upcoming renewals, missing cycle invoices, cross-customer payment mismatches, and active expired/exhausted discounts. Findings retain severity, certainty, evidence, affected records, recommendation, timestamps, and workflow status.
  - **Frontend:** Connected role-aware Customers, Quotations, Subscriptions, Invoices & Payments, Taxes & Discounts, and Revenue Guardian workspaces. Staff/Admin can run billing, record manual payments/refunds, progress quotations, print/email invoices, and manage Guardian findings. Customer users receive scoped quotation, subscription, and invoice views.
  - **Database:** Reviewed and applied Alembic revision `1b0a8fa0a82d` to Supabase. It adds quotation, template, tax/discount, and Guardian tables plus commercial lifecycle fields without deleting historical records.
  - **Verification:** Frontend lint and production build passed. Backend compilation passed and OpenAPI generation exposes 69 paths. Targeted commercial/Guardian tests passed (**12 passed**). Complete-suite result is recorded below after the final run.
  - **OpenAI usage:** The application does not call OpenAI and requires no OpenAI API key.

- **2026-10-31 — Prototype Completion: Dashboard, HAR Diagnostics & Automation**
  - **Dashboard:** Added a role-protected reporting workspace for MRR, ARR, collections, outstanding balances, overdue exposure, active customers, twelve-month net revenue, upcoming renewals, invoice health, and payment outcomes using the existing real-data APIs.
  - **HAR diagnostics:** Added Staff/Admin `.har` upload, adjustable slow-request threshold, server-side sanitization feedback, report history, failed/slow request evidence, report deletion, and downloadable developer-friendly text reports. Raw bodies and secrets remain excluded from stored reports.
  - **Automation:** Added configurable in-app recurring billing and Guardian loops (defaults: 60 and 30 minutes) plus a readiness API showing intervals, last run results/errors, and SMTP configuration status without returning secrets.
  - **UI review:** Completed a signed-in Administrator walkthrough of Overview, Dashboard, and HAR Diagnostics. Corrected the demo email format and verified the live dashboard metrics/readiness display and responsive diagnostics upload layout.
  - **Verification:** Frontend lint and production build passed; backend imports and OpenAPI generation passed with 70 paths. Production preparation was explicitly excluded by the user.

- **2026-10-31 — Role Portal Login & HAR Confirmation**
  - **Login:** Added explicit Administrator, Staff, and Customer portal options. The authenticated database role must match the selected portal. Administrator and Staff accounts route to Dashboard & Reports; Customer accounts route directly to their scoped Subscriptions page. Public registration remains Customer-only.
  - **HAR confirmation:** Focused HAR diagnostics suite passed **11/11**, covering staff authorization, file validation, sensitive-data redaction, failed/slow request analysis, sanitized storage, report listing/retrieval, entry filtering, and deletion.
  - **Frontend verification:** `npm run lint` and `npm run build` passed after the role portal change.

- **2026-10-31 — Customer-to-Developer HAR Queue & Live Demo**
  - **Workflow:** Authenticated Customers can now upload their recorded HAR from **Report a problem**. The report is immediately sanitized, marked `submitted_to_developers`, and placed in the shared Staff/Admin **Developer HAR queue**. Customers can list, inspect, and delete only their own reports; Staff/Admin can review every submitted report and update its developer status through the API (`new`, `reviewing`, `resolved`).
  - **Security:** Full browser capture still requires the user to export the HAR because ordinary websites cannot silently record browser-wide network traffic. Server-side redaction, raw-body exclusion, file validation, private storage, and role isolation remain enforced.
  - **Demo assets:** Added `demo-har/subscripthq-demo.har` with synthetic successful, slow-500, and 404 requests plus fake secrets, and `HAR_LIVE_DEMO.md` with a Customer submission / Administrator review script.
  - **Verification:** Updated HAR suite passed **11/11**; frontend lint and production build passed. Invalid legacy `.local` demo records created during setup were deactivated and corrected so they cannot break the Customer list.

- **2026-10-31 — Consent-Based Semi-Automatic HAR-Compatible Recording**
  - **Workflow:** Added a consent-gated recorder alongside manual HAR upload. The user selects **Start diagnostic recording**, reproduces the issue across SubscriptHQ workspace pages, returns to Diagnostics, selects **Stop and review**, and explicitly selects **Send to developers**.
  - **Capture scope:** Records only SubscriptHQ API request URLs, HTTP methods, response status/status text, content metadata, and timings. Authorization values are redacted immediately; request/response bodies are never captured. `/api/har` submission calls are excluded from the recording.
  - **Safety:** Recording is visibly indicated in the sidebar and Diagnostics panel, is limited to five minutes, can be discarded before submission, and does not claim browser-wide capture. Manual HAR upload remains available for complete browser-level evidence.
  - **Delivery:** Generates HAR 1.2-compatible JSON and sends it through the same server-side sanitization pipeline into the Customer-owned / Staff-Admin developer queue.
  - **Verification:** Frontend lint and production build passed. Browser automation was unavailable for the final visual pass; the existing local preview remains configured for the updated frontend.

### Pending work
- Supply real SMTP host/sender credentials in the private environment to enable actual password-reset and invoice delivery. The code and readiness indicator are complete; credentials cannot be invented or committed.
- Production preparation was excluded by user request.

### Known issues / limitations
- SMTP delivery is implemented but cannot send until environment-specific SMTP credentials and sender settings are configured.
- The lightweight in-app scheduler is suitable for the requested local prototype. A distributed production scheduler is intentionally outside scope.

### Architecture decisions
- **Bcrypt Direct Hashing:** Switched from `passlib` to direct `bcrypt` (`bcrypt.hashpw` / `bcrypt.checkpw`) due to incompatibility between Passlib 1.7.4 and modern Bcrypt 5.0+, ensuring long-term stability and security.
- **Pydantic V2 ConfigDict:** Replaced legacy class-based `Config` with `ConfigDict(from_attributes=True)` across all response models.
- **Relationship Overlaps Declaration:** Specified `overlaps="customer,payments"` on composite foreign key relationships on `Invoice` and `Payment` to cleanly inform SQLAlchemy mapper of shared `customer_id` columns.
- **Customer Isolation Rule:** Implemented in `dependencies.py` and router queries: customer users are restricted to viewing and managing only their own `Customer` records (`customer.user_id == current_user.id`).
- **Catalog & Financial Archiving:** Deletion requests on Customers, Products, and Plans set `is_archived = True` and `is_active = False` rather than issuing SQL `DELETE`, guaranteeing that historical references are preserved.
- **HAR Privacy Engine:** Strips raw payload bodies and redacts cookies, tokens, and query parameters server-side, storing diagnostic reports in private storage only.

### Database / migration history
- Supabase PostgreSQL: Connected and verified.
- **Revision `807fd70445a0`**: Initial schema creating 9 tables (`users`, `customers`, `products`, `subscription_plans`, `subscriptions`, `invoices`, `invoice_items`, `payments`, `audit_logs`). Status: **Applied (`alembic upgrade head` completed, verified)**.
- **Revision `b31f6c2a9d10`**: Adds product pricing fields, `product_variants`, daily billing, quantity/availability/payment-term rules, and plan lifecycle controls. Status: **Applied and verified in Supabase**.
- **Revision `1b0a8fa0a82d`**: Adds commercial quotation/template, tax/discount, persistent Guardian, recurring billing, and lifecycle fields. Status: **Applied and verified in Supabase**.

### Next action
Add private SMTP credentials when an email provider is selected; otherwise the requested non-production prototype is complete.
