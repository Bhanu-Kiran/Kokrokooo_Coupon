from .models import Coupon, AuditLog
from . import db
from datetime import datetime, timedelta
from dateutil import parser as dateparser
import os
import io
import time
import uuid
from flask import (
    Blueprint, current_app, request, render_template, flash, redirect,
    url_for, send_from_directory, abort, jsonify
)
import pandas as pd
from werkzeug.utils import secure_filename
from .utils import parse_issued_at, compute_valid_to

bp = Blueprint("main", __name__)
# Allowed file extensions
ALLOWED_EXT = {'.csv', '.xlsx'}
def _allowed_filename(filename):
    ext = os.path.splitext(filename.lower())[1]
    return ext in ALLOWED_EXT
# Expected columns mapping (case-insensitive)
_EXPECTED_COLS = [
    "code", "description", "issued_at", "validity_value", "validity_unit",
    "issued_to", "tags", "max_redemptions"
]


# ---------- Helpers (small, local utils) ----------
def sample_stats_and_coupons():
    # dummy data for UI development; later will be DB-driven
    now = datetime.now()
    stats = {"active": 42, "redeemed_today": 8, "expiring_24h": 5}
    coupons = [
        {"code": "WELCOME10", "description": "10% off first order", "valid_from": "2025-11-30 10:00", "valid_to": "2026-01-01 10:00", "status": "Active"},
        {"code": "LUNCH50", "description": "₹50 off > ₹300", "valid_from": "2025-12-03 12:00", "valid_to": "2025-12-05 12:00", "status": "Redeemed"},
        {"code": "FUTURE5", "description": "5% off", "valid_from": "2025-12-10 09:00", "valid_to": "2025-12-20 09:00", "status": "Upcoming"},
        {"code": "EXPIRED1", "description": "20% off", "valid_from": "2025-10-01 08:00", "valid_to": "2025-10-10 08:00", "status": "Expired"},
    ]
    return stats, coupons

# ---------- Routes ----------
@bp.route("/")
def index():
    stats, coupons = sample_stats_and_coupons()
    return render_template("dashboard.html", stats=stats, coupons=coupons, generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

@bp.route("/register", methods=["GET", "POST"])
def register():
    """
    Register new coupon:
    - GET: render the registration form (empty or prefilled if form posted with errors)
    - POST: validate inputs, compute valid_from/valid_to, persist to DB, log action, redirect to dashboard
    """
    if request.method == "POST":
        # Required field: code
        code = (request.form.get("code") or "").strip()
        description = (request.form.get("description") or "").strip()
        issued_at_raw = (request.form.get("issued_at") or "").strip()
        validity_value = request.form.get("validity_value") or "0"
        validity_unit = request.form.get("validity_unit") or "days"
        issued_to = (request.form.get("issued_to") or "").strip()
        tags = (request.form.get("tags") or "").strip()
        max_redemptions = request.form.get("max_redemptions") or 1

        # Basic validation
        if not code:
            flash("Code is required.", "error")
            return render_template("register.html")

        # Parse numeric fields safely
        try:
            validity_value = int(validity_value)
        except ValueError:
            validity_value = 0

        try:
            max_redemptions = int(max_redemptions)
            if max_redemptions < 1:
                max_redemptions = 1
        except ValueError:
            max_redemptions = 1

        # Parse issued_at if provided, else use now
        now = datetime.now()
        if issued_at_raw:
            try:
                # dateutil handles both "YYYY-MM-DDTHH:MM" and other formats
                valid_from = dateparser.parse(issued_at_raw)
            except Exception:
                valid_from = now
        else:
            valid_from = now

        # Compute valid_to from validity_value & validity_unit
        if validity_unit == "hours":
            valid_to = valid_from + timedelta(hours=validity_value)
        else:
            valid_to = valid_from + timedelta(days=validity_value)

        # Persist to DB
        try:
            # Avoid duplicates: simple unique check
            existing = Coupon.query.filter_by(code=code).first()
            if existing:
                flash(f"Coupon code '{code}' already exists.", "error")
                return render_template("register.html")

            coupon = Coupon(
                code=code,
                description=description,
                issued_at=now,
                valid_from=valid_from,
                valid_to=valid_to,
                validity_value=validity_value,
                validity_unit=validity_unit,
                issued_to=issued_to,
                tags=tags,
                max_redemptions=max_redemptions,
                redeemed_count=0,
            )
            db.session.add(coupon)
            # Log the create action
            log = AuditLog(user="admin", action="create", coupon_code=code, details="created via register form")
            db.session.add(log)
            db.session.commit()
            flash(f"Coupon '{code}' created.", "success")
            return redirect(url_for("main.index"))
        except Exception as e:
            db.session.rollback()
            flash(f"Failed to create coupon: {e}", "error")
            return render_template("register.html")

    # GET request
    return render_template("register.html")


@bp.route("/redeem", methods=["GET", "POST"])
def redeem():
    """
    Render redeem page. If 'code' passed via query or POST, prefill input.
    UI logic handled in template + AJAX.
    """
    code = request.args.get("code") or request.form.get("code") or ""
    return render_template("redeem.html", code=code)


# --------------------------------------------------------------
# API: Validate coupon before redemption (AJAX)
# --------------------------------------------------------------
@bp.route("/api/redeem_validate", methods=["POST"])
def api_redeem_validate():
    """
    Validate coupon for redemption (AJAX).
    Returns JSON including:
      - ok (bool)
      - status ("Active"|"Expired"|"Upcoming"|"Invalid"|"Maxed")
      - message (str)
      - valid_from (str, human-readable)
      - valid_to (str, human-readable)
      - redeemed_count (int)
      - max_redemptions (int)
    """
    data = request.get_json() or {}
    code = (data.get("code") or "").strip()

    if not code:
        return jsonify({"ok": False, "message": "Missing coupon code"}), 400

    coupon = Coupon.query.filter_by(code=code).first()
    if not coupon:
        return jsonify({
            "ok": False,
            "status": "Invalid",
            "message": "Coupon does not exist",
            "valid_from": None,
            "valid_to": None
        }), 404

    now = datetime.now()

    # formatted date strings
    vf = coupon.valid_from.isoformat(sep=" ", timespec="minutes") if coupon.valid_from else None
    vt = coupon.valid_to.isoformat(sep=" ", timespec="minutes") if coupon.valid_to else None

    # Expired
    if coupon.valid_to and now > coupon.valid_to:
        return jsonify({
            "ok": False,
            "status": "Expired",
            "message": "This coupon is expired",
            "valid_from": vf,
            "valid_to": vt
        })

    # Upcoming
    if coupon.valid_from and now < coupon.valid_from:
        return jsonify({
            "ok": False,
            "status": "Upcoming",
            "message": "This coupon is not active yet",
            "valid_from": vf,
            "valid_to": vt
        })

    # MAX-REDEMPTIONS check (blocking)
    if coupon.max_redemptions is not None and coupon.redeemed_count >= coupon.max_redemptions:
        return jsonify({
            "ok": False,
            "status": "Maxed",
            "message": "Maximum redemptions reached for this coupon",
            "valid_from": vf,
            "valid_to": vt,
            "redeemed_count": coupon.redeemed_count,
            "max_redemptions": coupon.max_redemptions
        })

    # If all checks pass → coupon is redeemable
    return jsonify({
        "ok": True,
        "status": "Active",
        "message": "Coupon is valid for redemption",
        "valid_from": vf,
        "valid_to": vt,
        "redeemed_count": coupon.redeemed_count,
        "max_redemptions": coupon.max_redemptions
    })



# --------------------------------------------------------------
# API: Mark coupon as redeemed
# --------------------------------------------------------------
@bp.route("/api/redeem_mark", methods=["POST"])
def api_redeem_mark():
    data = request.get_json() or {}
    code = (data.get("code") or "").strip()

    if not code:
        return jsonify({"ok": False, "message": "Missing coupon code"}), 400

    coupon = Coupon.query.filter_by(code=code).first()
    if not coupon:
        return jsonify({"ok": False, "message": "Coupon not found"}), 404

    # Re-check expiry / upcoming status before marking
    now = datetime.now()
    if coupon.valid_to and now > coupon.valid_to:
        return jsonify({"ok": False, "message": "Coupon is expired"}), 400
    if coupon.valid_from and now < coupon.valid_from:
        return jsonify({"ok": False, "message": "Coupon is not active yet"}), 400

    # Re-check max-redemptions before incrementing (blocking)
    if coupon.max_redemptions is not None and coupon.redeemed_count >= coupon.max_redemptions:
        return jsonify({"ok": False, "message": "Maximum redemptions reached for this coupon"}), 400

    # increment redemption
    coupon.redeemed_count += 1

    # log
    log = AuditLog(
        user="admin",
        action="redeem",
        coupon_code=code,
        details=f"Redeemed #{coupon.redeemed_count}"
    )
    db.session.add(log)
    db.session.commit()

    return jsonify({
        "ok": True,
        "message": "Coupon redeemed successfully",
        "redeemed_count": coupon.redeemed_count,
        "max_redemptions": coupon.max_redemptions
    })



@bp.route("/import", methods=["GET", "POST"])
def import_excel():
    """
    Upload page for .csv or .xlsx files.
    GET -> render upload form.
    POST -> process file and render results.
    """
    if request.method == "GET":
        return render_template("import.html")

    # POST handling
    f = request.files.get("file")
    if not f or f.filename == "":
        flash("No file selected", "error")
        return redirect(url_for("main.import_excel"))

    filename = secure_filename(f.filename)
    if not _allowed_filename(filename):
        flash("Unsupported file type. Upload .csv or .xlsx", "error")
        return redirect(url_for("main.import_excel"))

    # Read into pandas
    ext = os.path.splitext(filename.lower())[1]
    try:
        if ext == ".csv":
            df = pd.read_csv(f)
        else:
            df = pd.read_excel(f)  # engine openpyxl used by pandas
    except Exception as e:
        flash(f"Failed to read file: {e}", "error")
        return redirect(url_for("main.import_excel"))

    # Normalize column names (lowercase, strip)
    df.columns = [str(c).strip().lower() for c in df.columns]

    # Quick check: ensure 'code' exists
    if 'code' not in df.columns:
        flash("Uploaded file must contain a 'code' column.", "error")
        return redirect(url_for("main.import_excel"))

    # Prepare results
    imported = []
    skipped = []
    errors = []  # list of dicts: {row_index, row_data, error}

    # Load existing codes in DB to decide skip behaviour
    existing_codes = set([r.code for r in Coupon.query.with_entities(Coupon.code).all()])

    # Process rows (iterate via DataFrame.itertuples for speed)
    for idx, row in df.iterrows():
        rownum = idx + 2  # +2 to approximate Excel row (1-based + header)
        try:
            code = str(row.get('code', '')).strip()
            if not code or code.lower() in ('nan', ''):
                errors.append({"row": rownum, "error": "Missing code", "data": row.to_dict()})
                continue

            # skip duplicates (B = Skip)
            if code in existing_codes:
                skipped.append({"row": rownum, "code": code, "reason": "Duplicate code"})
                continue

            # parse other fields
            description = row.get('description') if 'description' in df.columns else None
            issued_at_raw = row.get('issued_at') if 'issued_at' in df.columns else None
            validity_value = row.get('validity_value', 0) if 'validity_value' in df.columns else 0
            validity_unit = row.get('validity_unit', 'days') if 'validity_unit' in df.columns else 'days'
            issued_to = row.get('issued_to') if 'issued_to' in df.columns else None
            tags = row.get('tags') if 'tags' in df.columns else None
            max_redemptions = row.get('max_redemptions', 1) if 'max_redemptions' in df.columns else 1

            # parse issued_at
            issued_at_dt = parse_issued_at(issued_at_raw) or datetime.now()

            # validate numeric fields
            try:
                validity_value = int(validity_value)
                max_redemptions = int(max_redemptions)
            except Exception:
                errors.append({"row": rownum, "error": "validity_value or max_redemptions not an integer", "data": row.to_dict()})
                continue

            if validity_value < 0 or max_redemptions < 1:
                errors.append({"row": rownum, "error": "invalid numeric values", "data": row.to_dict()})
                continue

            # compute valid_to
            valid_to = compute_valid_to(issued_at_dt, validity_value, validity_unit)

            # create model instance
            coupon = Coupon(
                code=code,
                description=str(description) if description is not None else None,
                issued_at=issued_at_dt,
                validity_value=validity_value,
                validity_unit=validity_unit,
                issued_to=str(issued_to) if issued_to is not None else None,
                tags=str(tags) if tags is not None else None,
                max_redemptions=max_redemptions,
                redeemed_count=0,
                valid_to=valid_to
            )
            db.session.add(coupon)
            imported.append({"row": rownum, "code": code})
            # keep existing_codes updated to avoid duplicates within same file
            existing_codes.add(code)

        except Exception as e:
            errors.append({"row": rownum, "error": f"Exception: {e}", "data": row.to_dict()})

    # commit all imported
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f"DB error while saving imported rows: {e}", "error")
        return redirect(url_for("main.import_excel"))

    # Save errors to a CSV file in instance folder so user can download
    error_file = None
    if errors:
        inst = current_app.instance_path
        os.makedirs(inst, exist_ok=True)
        fname = f"import_errors_{int(time.time())}_{uuid.uuid4().hex[:8]}.csv"
        error_path = os.path.join(inst, fname)
        # convert errors into dataframe
        err_rows = []
        for e in errors:
            data = e.get("data", {})
            data_out = {k: (v if v is not None else '') for k, v in data.items()}
            data_out['_row'] = e.get("row")
            data_out['_error'] = e.get("error")
            err_rows.append(data_out)
        try:
            pd.DataFrame(err_rows).to_csv(error_path, index=False)
            error_file = fname
        except Exception:
            # fallback: write minimal csv
            with open(error_path, 'w', encoding='utf8') as fh:
                fh.write("row,error,code\n")
                for e in errors:
                    code = (e.get('data') or {}).get('code','')
                    fh.write(f"{e.get('row')},{e.get('error')},{code}\n")
            error_file = fname

    # Prepare preview slices
    preview_ok = imported[:10]
    preview_skipped = skipped[:10]
    preview_errors = errors[:10]

    summary = {
        "imported_count": len(imported),
        "skipped_count": len(skipped),
        "errors_count": len(errors),
        "error_file": error_file
    }

    return render_template("import_result.html",
                           summary=summary,
                           preview_ok=preview_ok,
                           preview_skipped=preview_skipped,
                           preview_errors=preview_errors,
                           error_file=error_file)

@bp.route("/import/errors/<filename>", methods=["GET"])
def import_error_file(filename):
    # serve file from instance folder (we saved it there)
    inst = current_app.instance_path
    safe = secure_filename(filename)
    file_path = os.path.join(inst, safe)
    if not os.path.isfile(file_path):
        abort(404)
    return send_from_directory(inst, safe, as_attachment=True)


@bp.route("/export", methods=["GET"])
def export_excel():
    """
    Export all coupons to an xlsx file (downloaded).
    Columns in order:
    code, description, issued_at, validity_value, validity_unit,
    issued_to, tags, max_redemptions, redeemed_count, valid_to, status
    """
    # Query DB
    rows = Coupon.query.all()

    data = []
    now = datetime.now()
    for c in rows:
        status = "Active"
        if c.valid_to and now > c.valid_to:
            status = "Expired"
        elif c.valid_from and now < c.valid_from:
            status = "Upcoming"
        # ensure datetime formatting
        issued = c.issued_at.isoformat(sep=' ', timespec='minutes') if getattr(c, 'issued_at', None) else ''
        valid_to = c.valid_to.isoformat(sep=' ', timespec='minutes') if getattr(c, 'valid_to', None) else ''
        data.append({
            "code": c.code,
            "description": c.description or "",
            "issued_at": issued,
            "validity_value": c.validity_value,
            "validity_unit": c.validity_unit,
            "issued_to": c.issued_to or "",
            "tags": c.tags or "",
            "max_redemptions": c.max_redemptions or 1,
            "redeemed_count": c.redeemed_count or 0,
            "valid_to": valid_to,
            "status": status
        })

    df = pd.DataFrame(data, columns=[
        "code", "description", "issued_at", "validity_value", "validity_unit",
        "issued_to", "tags", "max_redemptions", "redeemed_count", "valid_to", "status"
    ])

    # Create Excel in-memory
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Coupons")
    output.seek(0)

    fname = f"coupons_export_{int(time.time())}.xlsx"
    return send_from_directory(
        directory=os.path.dirname(output.name) if hasattr(output, 'name') else None,
        path="",  # we won't use send_from_directory to send BytesIO; instead use send_file
        # fallback to send_file below
    )

# Use send_file for BytesIO (put this after the above)
from flask import send_file

@bp.route("/export_xlsx", methods=["GET"])
def export_xlsx():
    rows = Coupon.query.all()
    data = []
    now = datetime.now()
    for c in rows:
        status = "Active"
        if c.valid_to and now > c.valid_to:
            status = "Expired"
        elif c.valid_from and now < c.valid_from:
            status = "Upcoming"
        issued = c.issued_at.isoformat(sep=' ', timespec='minutes') if getattr(c, 'issued_at', None) else ''
        valid_to = c.valid_to.isoformat(sep=' ', timespec='minutes') if getattr(c, 'valid_to', None) else ''
        data.append({
            "code": c.code,
            "description": c.description or "",
            "issued_at": issued,
            "validity_value": c.validity_value,
            "validity_unit": c.validity_unit,
            "issued_to": c.issued_to or "",
            "tags": c.tags or "",
            "max_redemptions": c.max_redemptions or 1,
            "redeemed_count": c.redeemed_count or 0,
            "valid_to": valid_to,
            "status": status
        })
    df = pd.DataFrame(data, columns=[
        "code", "description", "issued_at", "validity_value", "validity_unit",
        "issued_to", "tags", "max_redemptions", "redeemed_count", "valid_to", "status"
    ])
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Coupons")
    output.seek(0)
    fname = f"coupons_export_{int(time.time())}.xlsx"
    return send_file(output,
                     as_attachment=True,
                     download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/admin")
def admin():
    return render_template("admin.html")

@bp.route("/logs")
def logs():
    # show dummy logs or DB logs later
    sample = [
        {"ts": "2025-12-04 15:20", "user": "admin", "action": "redeem", "coupon": "LUNCH50", "details": "marked claimed"},
    ]
    return render_template("logs.html", logs=sample)


@bp.route("/api/validate_coupon", methods=["POST"])
def api_validate_coupon():
    """
    Accepts JSON:
    {
      "code": "...",
      "issued_at": "...",        # optional, ISO / datetime-local string
      "validity_value": 30,
      "validity_unit": "days"
    }
    Returns:
    {
      ok: true|false,
      code_exists: true|false,
      valid_from: "YYYY-MM-DDTHH:MM:SS",
      valid_to: "YYYY-MM-DDTHH:MM:SS",
      status: "Active"|"Expired"|"Upcoming",
      message: "..."
    }
    """
    data = request.get_json() or {}
    code = (data.get("code") or "").strip()
    issued_at_raw = (data.get("issued_at") or "").strip()
    validity_value = data.get("validity_value", 0)
    validity_unit = data.get("validity_unit", "days")

    # Basic response template
    resp = {"ok": True, "code_exists": False, "message": "", "status": None,
            "valid_from": None, "valid_to": None}

    # Code presence check (not blocking but reported)
    if not code:
        resp["ok"] = False
        resp["message"] = "Missing coupon code."
        return jsonify(resp), 400

    # Check for existing code
    existing = Coupon.query.filter_by(code=code).first()
    resp["code_exists"] = bool(existing)
    if existing:
        resp["message"] = f"Coupon code '{code}' already exists."

    # Parse issued_at
    now = datetime.now()
    try:
        if issued_at_raw:
            # dateutil handles "YYYY-MM-DDTHH:MM" and other formats
            valid_from = dateparser.parse(issued_at_raw)
        else:
            valid_from = now
    except Exception:
        valid_from = now

    # numeric parse fallback
    try:
        validity_value = int(validity_value)
    except Exception:
        validity_value = 0

    # compute valid_to
    if validity_unit == "hours":
        valid_to = valid_from + timedelta(hours=validity_value)
    else:
        valid_to = valid_from + timedelta(days=validity_value)

    # Status decision
    now = datetime.now()
    if valid_from > now:
        status = "Upcoming"
    elif valid_to < now:
        status = "Expired"
    else:
        status = "Active"

    resp.update({
        "valid_from": valid_from.isoformat(sep=" ", timespec="minutes"),
        "valid_to": valid_to.isoformat(sep=" ", timespec="minutes"),
        "status": status,
    })

    # If code exists treat as blocking (you can change this rule later)
    if existing:
        resp["ok"] = False
        # message already set above

    return jsonify(resp)
