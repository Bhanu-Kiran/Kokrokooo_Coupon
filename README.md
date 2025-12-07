# Kokrokooo — Coupon Management (work in progress)

This is a small Flask app to manage coupon issuance and redemption. The current work-in-progress implements core routes and APIs for UI development and local testing.

## What is implemented so far
- Basic dashboard (dummy data) and admin/log views.
- Coupon registration page (form skeleton).
- Redemption page with AJAX endpoints:
  - POST /api/redeem_validate — validate a coupon before redeeming.
  - POST /api/redeem_mark — mark a coupon as redeemed (increments redeemed_count).
- Import endpoints to upload .csv/.xlsx and produce import results/errors.
- Export endpoints to download coupons as .xlsx.
- Simple audit logging (AuditLog model used).
- Routes and helpers live in `app/routes.py`.

## Quick setup (Windows PowerShell)
1. Open PowerShell and go to the project folder:
   cd 'C:\Kokrokooo'

2. Create a virtual environment and activate:
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1

3. Install dependencies:
   pip install -r requirements.txt

4. Configure the app (example):
   - Create instance config or set environment variables for DB connection and SECRET_KEY.
   - Example (PowerShell):
     $Env:FLASK_APP = "app"
     $Env:FLASK_ENV = "development"

5. Run:
   flask run

Notes:
- The app uses Flask-SQLAlchemy; initialize/configure your database before using import/export/redeem features.
- The repository .gitignore currently excludes `.venv`, `Data/`, image files, `Code.zip`, and Python bytecode caches (`__pycache__`, `*.pyc`).

## Next steps / TODO
- Wire up registration form to persist Coupon records.
- Implement proper import validation & transactional DB commits.
- Add unit tests and CI.
- Harden redemption logic (concurrent-safe increments).
- Add UI polish and real dashboard metrics.

Files to check first:
- `app/routes.py` — current route implementations and TODOs.
- `app/models.py` — data models (Coupon, AuditLog).
- `app/utils.py` — parsing/validity helpers.


