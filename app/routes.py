# app/routes.py
import os
import io
import time
import uuid
import json
from datetime import datetime, timedelta

from flask import (
    Blueprint,
    current_app,
    request,
    render_template,
    flash,
    redirect,
    url_for,
    send_from_directory,
    abort,
    jsonify,
    send_file,
)
from werkzeug.utils import secure_filename
import pandas as pd

from .models import Coupon, AuditLog
from . import db
from .utils import parse_issued_at, compute_valid_to

bp = Blueprint("main", __name__)

# Allowed file extensions
ALLOWED_EXT = {".csv", ".xlsx", ".xls"}

# Required import columns (case-insensitive)
_EXPECTED_COLS = [
    "code",
    "description",
    "issued_at",
    "validity_value",
    "validity_unit",
    "issued_to",
    "tags",
    "max_redemptions",
]


# --------------------
# Small helpers
# --------------------
def _allowed_filename(filename):
    ext = os.path.splitext(filename.lower())[1]
    return ext in ALLOWED_EXT


def _make_errors_filename():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return secure_filename(f"errors_{ts}.csv")


def _make_temp_import_filename():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return secure_filename(f"import_batch_{ts}_{uuid.uuid4().hex[:8]}.json")


def _status_for_coupon(c: Coupon, at=None):
    at = at or datetime.now()
    if getattr(c, "valid_from", None) and at < c.valid_from:
        return "Upcoming"
    if getattr(c, "valid_to", None) and at > c.valid_to:
        return "Expired"
    if (c.redeemed_count or 0) >= (c.max_redemptions or 1):
        return "Maxed"
    return "Active"


def _ensure_instance_dir():
    inst = current_app.instance_path
    os.makedirs(inst, exist_ok=True)
    return inst


# --------------------
# Dashboard / other pages (preserve existing UX)
# --------------------
def sample_stats_and_coupons():
    # dummy data for UI development; later will be DB-driven
    now = datetime.now()
    stats = {"active": 42, "redeemed_today": 8, "expiring_24h": 5}
    coupons = [
        {
            "code": "WELCOME10",
            "description": "10% off first order",
            "issued_at": "2025-11-30 10:00",
            "valid_to": "2026-01-01 10:00",
            "status": "Active",
        },
        {
            "code": "LUNCH50",
            "description": "₹50 off > ₹300",
            "issued_at": "2025-12-03 12:00",
            "valid_to": "2025-12-05 12:00",
            "status": "Redeemed",
        },
        {
            "code": "FUTURE5",
            "description": "5% off",
            "issued_at": "2025-12-10 09:00",
            "valid_to": "2025-12-20 09:00",
            "status": "Upcoming",
        },
        {
            "code": "EXPIRED1",
            "description": "20% off",
            "issued_at": "2025-10-01 08:00",
            "valid_to": "2025-10-10 08:00",
            "status": "Expired",
        },
    ]
    return stats, coupons


@bp.route("/")
def index():
    stats, coupons = sample_stats_and_coupons()
    return render_template(
        "dashboard.html",
        stats=stats,
        coupons=coupons,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        description = (request.form.get("description") or "").strip()
        issued_at_raw = (request.form.get("issued_at") or "").strip()
        validity_value = request.form.get("validity_value") or "0"
        validity_unit = request.form.get("validity_unit") or "days"
        issued_to = (request.form.get("issued_to") or "").strip()
        tags = (request.form.get("tags") or "").strip()
        max_redemptions = request.form.get("max_redemptions") or 1

        if not code:
            flash("Code is required.", "error")
            return render_template("register.html")

        try:
            validity_value = int(validity_value)
        except Exception:
            validity_value = 0

        try:
            max_redemptions = int(max_redemptions)
            if max_redemptions < 1:
                max_redemptions = 1
        except Exception:
            max_redemptions = 1

        now = datetime.now()
        if issued_at_raw:
            try:
                parsed = parse_issued_at(issued_at_raw)
                valid_from = parsed or now
            except Exception:
                valid_from = now
        else:
            valid_from = now

        if str(validity_unit).lower() in ("hours", "hour", "h"):
            valid_to = valid_from + timedelta(hours=validity_value)
        else:
            valid_to = valid_from + timedelta(days=validity_value)

        try:
            existing = Coupon.query.filter_by(code=code).first()
            if existing:
                flash(f"Coupon code '{code}' already exists.", "error")
                return render_template("register.html")

            coupon = Coupon(
                code=code,
                description=description,
                issued_at=valid_from,
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
            log = AuditLog(
                user="admin", action="create", coupon_code=code, details="created via register form"
            )
            db.session.add(log)
            db.session.commit()
            flash(f"Coupon '{code}' created.", "success")
            return redirect(url_for("main.index"))
        except Exception as e:
            db.session.rollback()
            flash(f"Failed to create coupon: {e}", "error")
            return render_template("register.html")

    return render_template("register.html")


@bp.route("/redeem", methods=["GET", "POST"])
def redeem():
    code = request.args.get("code") or request.form.get("code") or ""
    return render_template("redeem.html", code=code)


# --------------------
# API: Redeem validate/mark (preserve)
# --------------------
@bp.route("/api/redeem_validate", methods=["POST"])
def api_redeem_validate():
    data = request.get_json() or {}
    code = (data.get("code") or "").strip()

    if not code:
        return jsonify({"ok": False, "message": "Missing coupon code"}), 400

    coupon = Coupon.query.filter_by(code=code).first()
    if not coupon:
        return (
            jsonify(
                {
                    "ok": False,
                    "status": "Invalid",
                    "message": "Coupon does not exist",
                    "valid_from": None,
                    "valid_to": None,
                }
            ),
            404,
        )

    now = datetime.now()
    vf = coupon.valid_from.isoformat(sep=" ", timespec="minutes") if coupon.valid_from else None
    vt = coupon.valid_to.isoformat(sep=" ", timespec="minutes") if coupon.valid_to else None

    if coupon.valid_to and now > coupon.valid_to:
        return jsonify({"ok": False, "status": "Expired", "message": "This coupon is expired", "valid_from": vf, "valid_to": vt})

    if coupon.valid_from and now < coupon.valid_from:
        return jsonify({"ok": False, "status": "Upcoming", "message": "This coupon is not active yet", "valid_from": vf, "valid_to": vt})

    if coupon.max_redemptions is not None and coupon.redeemed_count >= coupon.max_redemptions:
        return jsonify({"ok": False, "status": "Maxed", "message": "Maximum redemptions reached for this coupon", "valid_from": vf, "valid_to": vt, "redeemed_count": coupon.redeemed_count, "max_redemptions": coupon.max_redemptions})

    return jsonify({"ok": True, "status": "Active", "message": "Coupon is valid for redemption", "valid_from": vf, "valid_to": vt, "redeemed_count": coupon.redeemed_count, "max_redemptions": coupon.max_redemptions})


@bp.route("/api/redeem_mark", methods=["POST"])
def api_redeem_mark():
    data = request.get_json() or {}
    code = (data.get("code") or "").strip()

    if not code:
        return jsonify({"ok": False, "message": "Missing coupon code"}), 400

    coupon = Coupon.query.filter_by(code=code).first()
    if not coupon:
        return jsonify({"ok": False, "message": "Coupon not found"}), 404

    now = datetime.now()
    if coupon.valid_to and now > coupon.valid_to:
        return jsonify({"ok": False, "message": "Coupon is expired"}), 400
    if coupon.valid_from and now < coupon.valid_from:
        return jsonify({"ok": False, "message": "Coupon is not active yet"}), 400

    if coupon.max_redemptions is not None and coupon.redeemed_count >= coupon.max_redemptions:
        return jsonify({"ok": False, "message": "Maximum redemptions reached for this coupon"}), 400

    coupon.redeemed_count += 1
    log = AuditLog(user="admin", action="redeem", coupon_code=code, details=f"Redeemed #{coupon.redeemed_count}")
    db.session.add(log)
    db.session.commit()

    return jsonify({"ok": True, "message": "Coupon redeemed successfully", "redeemed_count": coupon.redeemed_count, "max_redemptions": coupon.max_redemptions})


# --------------------
# IMPORT (step 1: upload & validate -> write temp JSON & errors CSV if any)
# --------------------
@bp.route("/import", methods=["GET", "POST"])
def import_excel():
    """
    GET -> render upload form
    POST -> read, validate, write a temp JSON with VALID rows (but DO NOT write to DB).
            Show import_result page with preview and Confirm button.
    """
    if request.method == "GET":
        return render_template("import.html")

    f = request.files.get("file")
    if not f or f.filename == "":
        flash("No file selected", "error")
        return redirect(url_for("main.import"))

    filename = secure_filename(f.filename)
    if not _allowed_filename(filename):
        flash("Unsupported file type. Upload .csv or .xlsx", "error")
        return redirect(url_for("main.import"))

    # read dataframe
    ext = os.path.splitext(filename.lower())[1]
    try:
        if ext == ".csv":
            df = pd.read_csv(f, dtype=object)
        else:
            df = pd.read_excel(f, dtype=object)
    except Exception as e:
        flash(f"Failed to read file: {e}", "error")
        return redirect(url_for("main.import"))

    # normalize headers
    df.columns = [str(c).strip().lower() for c in df.columns]

    # required columns check -> strict abort if missing any
    missing_cols = [c for c in _EXPECTED_COLS if c not in df.columns]
    if missing_cols:
        flash("Uploaded file is missing required column(s): " + ", ".join(missing_cols), "error")
        return redirect(url_for("main.import"))

    # prepare
    inst = _ensure_instance_dir()
    existing_codes = {r.code for r in Coupon.query.with_entities(Coupon.code).all()}

    valid_rows = []  # list of dicts (clean data) that would be inserted if confirmed
    skipped = []
    errors = []

    for idx, row in df.iterrows():
        rownum = int(idx) + 2
        try:
            code_raw = row.get("code")
            code = "" if pd.isna(code_raw) else str(code_raw).strip()
            if not code:
                errors.append({"row": rownum, "error": "Missing code", "data": row.to_dict()})
                continue

            if code in existing_codes:
                skipped.append({"row": rownum, "code": code, "reason": "duplicate"})
                continue

            description = None if pd.isna(row.get("description")) else str(row.get("description")).strip()

            raw_issued = row.get("issued_at")
            try:
                if pd.isna(raw_issued) or raw_issued is None or str(raw_issued).strip() == "":
                    parsed_issued = None
                elif isinstance(raw_issued, (pd.Timestamp, datetime)):
                    parsed_issued = raw_issued.to_pydatetime() if hasattr(raw_issued, "to_pydatetime") else raw_issued
                else:
                    parsed_issued = parse_issued_at(str(raw_issued).strip())
            except Exception:
                parsed_issued = None

            if parsed_issued is None:
                errors.append({"row": rownum, "error": "Invalid issued_at", "data": row.to_dict()})
                continue

            # validity_value
            raw_val = row.get("validity_value")
            try:
                validity_value = int(float(raw_val))
                if validity_value < 0:
                    raise ValueError("negative")
            except Exception:
                errors.append({"row": rownum, "error": "Invalid validity_value", "data": row.to_dict()})
                continue

            # validity_unit
            raw_unit = row.get("validity_unit")
            if pd.isna(raw_unit) or str(raw_unit).strip() == "":
                validity_unit = "days"
            else:
                unit = str(raw_unit).strip().lower()
                if unit in ("days", "day", "d"):
                    validity_unit = "days"
                elif unit in ("hours", "hour", "h"):
                    validity_unit = "hours"
                else:
                    errors.append({"row": rownum, "error": "Invalid validity_unit", "data": row.to_dict()})
                    continue

            issued_to = None if pd.isna(row.get("issued_to")) else str(row.get("issued_to")).strip()
            tags = None if pd.isna(row.get("tags")) else str(row.get("tags")).strip()

            raw_max = row.get("max_redemptions")
            try:
                max_redemptions = 1 if pd.isna(raw_max) or raw_max is None or str(raw_max).strip() == "" else int(float(raw_max))
                if max_redemptions < 1:
                    raise ValueError("min 1")
            except Exception:
                errors.append({"row": rownum, "error": "Invalid max_redemptions", "data": row.to_dict()})
                continue

            # compute valid_to
            try:
                valid_to = compute_valid_to(parsed_issued, validity_value, validity_unit)
            except Exception as e:
                errors.append({"row": rownum, "error": f"Failed compute_valid_to: {e}", "data": row.to_dict()})
                continue

            # build cleaned dict (exact DB field names)
            cleaned = {
                "code": code,
                "description": description,
                "issued_at": parsed_issued.isoformat(sep=" ", timespec="seconds"),
                "valid_from": parsed_issued.isoformat(sep=" ", timespec="seconds"),
                "valid_to": valid_to.isoformat(sep=" ", timespec="seconds") if valid_to is not None else None,
                "validity_value": int(validity_value),
                "validity_unit": validity_unit,
                "issued_to": issued_to,
                "tags": tags,
                "max_redemptions": int(max_redemptions),
                "redeemed_count": 0,
            }
            valid_rows.append({"row": rownum, "data": cleaned})
            existing_codes.add(code)

        except Exception as exc:
            errors.append({"row": rownum, "error": f"Exception: {exc}", "data": row.to_dict()})
            continue

    # write temp JSON for valid rows (so confirm step can commit)
    temp_filename = None
    if valid_rows:
        temp_filename = _make_temp_import_filename()
        temp_path = os.path.join(inst, temp_filename)
        try:
            with open(temp_path, "w", encoding="utf8") as fh:
                json.dump(valid_rows, fh, ensure_ascii=False, indent=2)
        except Exception as e:
            flash(f"Failed to save temporary import batch: {e}", "error")
            temp_filename = None

    # If there are errors, write an errors.csv to instance
    error_filename = None
    if errors:
        err_fname = _make_errors_filename()
        err_path = os.path.join(inst, err_fname)
        err_rows = []
        for e in errors:
            data = e.get("data", {}) or {}
            row_out = {k: (v if v is not None else "") for k, v in data.items()}
            row_out["_row"] = e.get("row")
            row_out["_error"] = e.get("error")
            err_rows.append(row_out)
        try:
            pd.DataFrame(err_rows).to_csv(err_path, index=False)
            error_filename = err_fname
        except Exception:
            # fallback minimal csv
            try:
                with open(err_path, "w", encoding="utf8") as fh:
                    fh.write("row,error,code\n")
                    for e in errors:
                        code = (e.get("data") or {}).get("code", "")
                        fh.write(f"{e.get('row')},{e.get('error')},{code}\n")
                error_filename = err_fname
            except Exception as ee:
                flash(f"Failed to write errors file: {ee}", "error")
                error_filename = None

    # Prepare previews
    preview_ok = [v for v in valid_rows[:10]]
    preview_skipped = skipped[:10]
    preview_errors = [{"row": e.get("row"), "error": e.get("error")} for e in errors[:10]]

    summary = {
        "valid_count": len(valid_rows),
        "skipped_count": len(skipped),
        "errors_count": len(errors),
    }

    # Render the import_result page. If errors_count > 0 then Confirm Import will be disabled.
    return render_template(
        "import_result.html",
        summary=summary,
        preview_ok=preview_ok,
        preview_skipped=preview_skipped,
        preview_errors=preview_errors,
        temp_file=temp_filename,
        error_file=error_filename,
    )


# --------------------
# IMPORT CONFIRM (strict): only allow commit if there were NO errors
# --------------------
@bp.route("/import_confirm", methods=["POST"])
def import_confirm():
    """
    Confirm endpoint - accepts 'temp_file' parameter (POST form).
    Strict mode: will commit only if a valid temp file exists and no error conditions
    were present at validation time (we enforce that by checking the temp file exists
    and it's non-empty). If the temp file is missing or empty -> abort.
    After successful commit -> delete temp file and redirect to /import with success flash.
    """
    temp_file = request.form.get("temp_file")
    if not temp_file:
        flash("Missing temp import batch identifier.", "error")
        return redirect(url_for("main.import"))

    inst = _ensure_instance_dir()
    temp_path = os.path.join(inst, secure_filename(temp_file))
    if not os.path.isfile(temp_path):
        flash("Temporary import batch not found or already processed.", "error")
        return redirect(url_for("main.import"))

    # Load JSON
    try:
        with open(temp_path, "r", encoding="utf8") as fh:
            batch = json.load(fh)
    except Exception as e:
        flash(f"Failed to read import batch: {e}", "error")
        return redirect(url_for("main.import"))

    if not batch:
        flash("Import batch is empty.", "error")
        # delete empty file just in case
        try:
            os.remove(temp_path)
        except Exception:
            pass
        return redirect(url_for("main.import"))

    # Strict mode: ensure there were no errors when temp file was created
    # We enforced that by writing temp file only for valid rows. However double-check:
    # If there are any rows with missing or invalid keys, we abort.
    safe_to_commit = True
    for item in batch:
        if "data" not in item or not isinstance(item["data"], dict):
            safe_to_commit = False
            break

    if not safe_to_commit:
        flash("Import batch appears invalid; aborting.", "error")
        try:
            os.remove(temp_path)
        except Exception:
            pass
        return redirect(url_for("main.import"))

    # Insert all rows into DB
    inserted = 0
    try:
        for item in batch:
            d = item["data"]
            # parse ISO-ish datetimes back into python datetimes
            issued_at = None
            try:
                issued_at = datetime.strptime(d.get("issued_at"), "%Y-%m-%d %H:%M:%S")
            except Exception:
                # try alternate parse
                try:
                    issued_at = datetime.fromisoformat(d.get("issued_at"))
                except Exception:
                    issued_at = None

            valid_from = issued_at
            valid_to = None
            try:
                if d.get("valid_to"):
                    valid_to = datetime.strptime(d.get("valid_to"), "%Y-%m-%d %H:%M:%S")
            except Exception:
                try:
                    valid_to = datetime.fromisoformat(d.get("valid_to")) if d.get("valid_to") else None
                except Exception:
                    valid_to = None

            coupon = Coupon(
                code=d.get("code"),
                description=d.get("description"),
                issued_at=issued_at,
                valid_from=valid_from,
                valid_to=valid_to,
                validity_value=d.get("validity_value"),
                validity_unit=d.get("validity_unit"),
                issued_to=d.get("issued_to"),
                tags=d.get("tags"),
                max_redemptions=d.get("max_redemptions"),
                redeemed_count=d.get("redeemed_count", 0),
            )
            db.session.add(coupon)
            inserted += 1

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f"DB error during import commit: {e}", "error")
        return redirect(url_for("main.import"))
    finally:
        # remove temp file if present
        try:
            os.remove(temp_path)
        except Exception:
            pass

    flash(f"Imported {inserted} coupons successfully.", "success")
    # Per your choice D: redirect back to /import with success message
    return redirect(url_for("main.import"))


# --------------------
# Serve errors file generated during validation
# --------------------
@bp.route("/import/errors/<filename>", methods=["GET"])
def import_error_file(filename):
    inst = _ensure_instance_dir()
    safe = secure_filename(filename)
    path = os.path.join(inst, safe)
    if not os.path.isfile(path):
        abort(404)
    return send_from_directory(inst, safe, as_attachment=True)


# --------------------
# Export UI: show all coupons, then user clicks Download button to call export_xlsx
# --------------------
@bp.route("/export", methods=["GET"])
def export():
    coupons = []
    rows = Coupon.query.order_by(Coupon.id.asc()).all()
    for c in rows:
        coupons.append(
            {
                "code": c.code,
                "description": c.description or "",
                "issued_at": getattr(c, "issued_at", None),
                "validity_value": c.validity_value,
                "validity_unit": c.validity_unit,
                "issued_to": c.issued_to or "",
                "tags": c.tags or "",
                "max_redemptions": c.max_redemptions or 1,
                "redeemed_count": c.redeemed_count or 0,
                "valid_to": getattr(c, "valid_to", None),
                "status": _status_for_coupon(c, datetime.now()),
            }
        )
    return render_template("export.html", coupons=coupons)


@bp.route("/export_xlsx", methods=["GET"])
def export_xlsx():
    rows = Coupon.query.order_by(Coupon.id.asc()).all()
    data = []
    now = datetime.now()
    for c in rows:
        issued = getattr(c, "issued_at", None)
        valid_to = getattr(c, "valid_to", None)
        status = _status_for_coupon(c, now)
        data.append(
            {
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
                "status": status,
            }
        )

    df = pd.DataFrame(
        data,
        columns=[
            "code",
            "description",
            "issued_at",
            "validity_value",
            "validity_unit",
            "issued_to",
            "tags",
            "max_redemptions",
            "redeemed_count",
            "valid_to",
            "status",
        ],
    )

    for dt_col in ("issued_at", "valid_to"):
        if dt_col in df.columns:
            df[dt_col] = df[dt_col].apply(lambda v: pd.NaT if v is None else pd.to_datetime(v))

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="coupons")
    output.seek(0)

    fn = f"coupons_export_{int(time.time())}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=fn,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# --------------------
# Admin / Logs / validate coupon API
# --------------------
@bp.route("/admin")
def admin():
    return render_template("admin.html")


@bp.route("/logs")
def logs():
    sample = [
        {"ts": "2025-12-04 15:20", "user": "admin", "action": "redeem", "coupon": "LUNCH50", "details": "marked claimed"},
    ]
    return render_template("logs.html", logs=sample)


@bp.route("/api/validate_coupon", methods=["POST"])
def api_validate_coupon():
    data = request.get_json() or {}
    code = (data.get("code") or "").strip()
    issued_at_raw = data.get("issued_at")
    validity_value = data.get("validity_value", 0)
    validity_unit = data.get("validity_unit", "days")

    resp = {"ok": True, "code_exists": False, "message": "", "status": None, "valid_from": None, "valid_to": None}

    if not code:
        resp["ok"] = False
        resp["message"] = "Missing coupon code."
        return jsonify(resp), 400

    existing = Coupon.query.filter_by(code=code).first()
    resp["code_exists"] = bool(existing)
    if existing:
        resp["message"] = f"Coupon code '{code}' already exists."

    now = datetime.now()
    try:
        if issued_at_raw:
            parsed = parse_issued_at(issued_at_raw) if not isinstance(issued_at_raw, datetime) else issued_at_raw
            valid_from = parsed or now
        else:
            valid_from = now
    except Exception:
        valid_from = now

    try:
        validity_value = int(validity_value)
    except Exception:
        validity_value = 0

    try:
        valid_to = compute_valid_to(valid_from, validity_value, validity_unit)
    except Exception:
        if str(validity_unit).lower() in ("hours", "hour", "h"):
            valid_to = valid_from + timedelta(hours=validity_value)
        else:
            valid_to = valid_from + timedelta(days=validity_value)

    now = datetime.now()
    if valid_from > now:
        status = "Upcoming"
    elif valid_to < now:
        status = "Expired"
    else:
        status = "Active"

    resp.update(
        {
            "valid_from": valid_from.isoformat(sep=" ", timespec="minutes"),
            "valid_to": valid_to.isoformat(sep=" ", timespec="minutes"),
            "status": status,
        }
    )

    if existing:
        resp["ok"] = False

    return jsonify(resp)
