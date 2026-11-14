from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any

from flask import Flask, Response, current_app, g, jsonify, render_template, request
from werkzeug.exceptions import BadRequest

ROOT = Path(__file__).resolve().parent.parent
OPT_OUT_WORDS = {"STOP", "STOP ALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "REMOVE"}
WORKERS_LOCK = threading.Lock()
ACTIVE_WORKERS: set[int] = set()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_phone(value: Any) -> str:
    """Normalize an explicitly international number to E.164; do not guess country codes."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Phone number is required")
    cleaned = re.sub(r"[\s().-]", "", raw)
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if not re.fullmatch(r"\+[1-9]\d{7,14}", cleaned):
        raise ValueError("Use international E.164 format, including + and country code (for example +919876543210)")
    return cleaned


def truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "consented", "opted_in"}


def json_object(value: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        db_path = current_app.config["DATABASE_PATH"]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        g.db = conn
    return g.db


def close_db(_error: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(app: Flask) -> None:
    with app.app_context():
        db = get_db()
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL UNIQUE,
                opted_in INTEGER NOT NULL DEFAULT 0,
                consent_source TEXT NOT NULL DEFAULT '',
                consent_at TEXT,
                unsubscribed_at TEXT,
                custom_fields_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS contact_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS group_members (
                group_id INTEGER NOT NULL REFERENCES contact_groups(id) ON DELETE CASCADE,
                contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                PRIMARY KEY (group_id, contact_id)
            );
            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                group_id INTEGER NOT NULL REFERENCES contact_groups(id),
                template_name TEXT NOT NULL,
                language TEXT NOT NULL,
                body_fields_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'draft',
                total INTEGER NOT NULL DEFAULT 0,
                sent INTEGER NOT NULL DEFAULT 0,
                delivered INTEGER NOT NULL DEFAULT 0,
                read_count INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                dry_run INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            );
            CREATE TABLE IF NOT EXISTS campaign_recipients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
                contact_id INTEGER NOT NULL REFERENCES contacts(id),
                status TEXT NOT NULL DEFAULT 'pending',
                wamid TEXT UNIQUE,
                error TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(campaign_id, contact_id)
            );
            CREATE INDEX IF NOT EXISTS idx_campaign_recipients_wamid ON campaign_recipients(wamid);
            CREATE INDEX IF NOT EXISTS idx_group_members_contact ON group_members(contact_id);
            """
        )
        db.commit()


def require_api_header(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.headers.get("X-Requested-With") != "WhatsAppCampaignStudio":
                return jsonify(error="Request rejected: missing application request header"), 403
        return fn(*args, **kwargs)
    return wrapper


def graph_request(app: Flask, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    token = app.config.get("META_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("META_ACCESS_TOKEN is not configured")
    url = f"https://graph.facebook.com/{app.config['GRAPH_API_VERSION']}/{path.lstrip('/')}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
            message = detail.get("error", {}).get("message", str(detail))
        except Exception:
            message = f"Meta API returned HTTP {exc.code}"
        raise RuntimeError(message) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Meta Graph API: {exc.reason}") from exc


def fetch_approved_templates(app: Flask) -> list[dict[str, Any]]:
    waba_id = app.config.get("META_WABA_ID", "")
    if not waba_id:
        raise RuntimeError("META_WABA_ID is not configured")
    fields = "name,language,status,category,components"
    result = graph_request(app, "GET", f"{waba_id}/message_templates?fields={urllib.parse.quote(fields)}&limit=100")
    templates = result.get("data", [])
    return [item for item in templates if item.get("status") == "APPROVED"]


def send_template_message(app: Flask, to: str, template_name: str, language: str,
                          body_fields: list[str], contact: sqlite3.Row) -> str:
    phone_number_id = app.config.get("META_PHONE_NUMBER_ID", "")
    if not phone_number_id:
        raise RuntimeError("META_PHONE_NUMBER_ID is not configured")
    custom = json_object(contact["custom_fields_json"])
    values: list[dict[str, str]] = []
    for field in body_fields:
        if field == "name":
            raw = contact["name"]
        elif field == "phone":
            raw = contact["phone"]
        else:
            raw = custom.get(field, "")
        values.append({"type": "text", "text": str(raw)[:1024]})
    template: dict[str, Any] = {"name": template_name, "language": {"code": language}}
    if values:
        template["components"] = [{"type": "body", "parameters": values}]
    result = graph_request(app, "POST", f"{phone_number_id}/messages", {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to.lstrip("+"),
        "type": "template",
        "template": template,
    })
    messages = result.get("messages") or []
    if not messages or not messages[0].get("id"):
        raise RuntimeError("Meta accepted no message ID in its response")
    return messages[0]["id"]


def update_campaign_counts(db: sqlite3.Connection, campaign_id: int) -> None:
    counts = {row["status"]: row["n"] for row in db.execute(
        "SELECT status, COUNT(*) AS n FROM campaign_recipients WHERE campaign_id=? GROUP BY status",
        (campaign_id,),
    ).fetchall()}
    sent = sum(counts.get(k, 0) for k in ("sent", "delivered", "read"))
    db.execute(
        "UPDATE campaigns SET sent=?, delivered=?, read_count=?, failed=? WHERE id=?",
        (sent, counts.get("delivered", 0) + counts.get("read", 0), counts.get("read", 0), counts.get("failed", 0) + counts.get("skipped", 0), campaign_id),
    )
    db.commit()


def process_campaign(app: Flask, campaign_id: int) -> None:
    try:
        with app.app_context():
            db = get_db()
            campaign = db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
            if campaign is None:
                return
            body_fields = json.loads(campaign["body_fields_json"] or "[]")
            db.execute("UPDATE campaigns SET status='sending', started_at=? WHERE id=?", (utc_now(), campaign_id))
            db.commit()
            recipients = db.execute(
                "SELECT cr.id AS recipient_id, cr.contact_id, c.* FROM campaign_recipients cr "
                "JOIN contacts c ON c.id=cr.contact_id WHERE cr.campaign_id=? AND cr.status='pending' ORDER BY cr.id",
                (campaign_id,),
            ).fetchall()
            for recipient in recipients:
                # Re-check consent immediately before each send; opt-outs always win.
                current = db.execute("SELECT * FROM contacts WHERE id=?", (recipient["contact_id"],)).fetchone()
                if not current or not current["opted_in"] or current["unsubscribed_at"]:
                    db.execute("UPDATE campaign_recipients SET status='skipped', error='Contact is not currently opted in', updated_at=? WHERE id=?",
                               (utc_now(), recipient["recipient_id"]))
                    db.commit()
                    update_campaign_counts(db, campaign_id)
                    continue
                try:
                    if campaign["dry_run"]:
                        message_id = "dryrun_" + secrets.token_hex(10)
                        recipient_status = "sent"
                    else:
                        message_id = send_template_message(
                            app, current["phone"], campaign["template_name"], campaign["language"], body_fields, current
                        )
                        recipient_status = "sent"
                    db.execute(
                        "UPDATE campaign_recipients SET status=?, wamid=?, error=NULL, updated_at=? WHERE id=?",
                        (recipient_status, message_id, utc_now(), recipient["recipient_id"]),
                    )
                except Exception as exc:  # Keep processing the remaining consented contacts.
                    db.execute(
                        "UPDATE campaign_recipients SET status='failed', error=?, updated_at=? WHERE id=?",
                        (str(exc)[:1000], utc_now(), recipient["recipient_id"]),
                    )
                db.commit()
                update_campaign_counts(db, campaign_id)
                time.sleep(max(0.5, float(app.config.get("SEND_DELAY_SECONDS", 1.5))))
            remaining = db.execute(
                "SELECT COUNT(*) AS n FROM campaign_recipients WHERE campaign_id=? AND status='pending'",
                (campaign_id,),
            ).fetchone()["n"]
            failures = db.execute(
                "SELECT COUNT(*) AS n FROM campaign_recipients WHERE campaign_id=? AND status IN ('failed','skipped')",
                (campaign_id,),
            ).fetchone()["n"]
            state = "completed_with_errors" if failures else "completed"
            if remaining:
                state = "paused"
            db.execute("UPDATE campaigns SET status=?, finished_at=? WHERE id=?", (state, utc_now(), campaign_id))
            db.commit()
            update_campaign_counts(db, campaign_id)
    finally:
        with WORKERS_LOCK:
            ACTIVE_WORKERS.discard(campaign_id)


def launch_worker(app: Flask, campaign_id: int) -> None:
    with WORKERS_LOCK:
        if campaign_id in ACTIVE_WORKERS:
            return
        ACTIVE_WORKERS.add(campaign_id)
    thread = threading.Thread(target=process_campaign, args=(app, campaign_id), daemon=True)
    thread.start()


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config.update(
        DATABASE_PATH=os.getenv("DATABASE_PATH", str(ROOT / "data" / "campaign-studio.db")),
        APP_USERNAME=os.getenv("APP_USERNAME", "admin"),
        APP_PASSWORD=os.getenv("APP_PASSWORD", ""),
        WEBHOOK_VERIFY_TOKEN=os.getenv("WEBHOOK_VERIFY_TOKEN", ""),
        META_ACCESS_TOKEN=os.getenv("META_ACCESS_TOKEN", ""),
        META_PHONE_NUMBER_ID=os.getenv("META_PHONE_NUMBER_ID", ""),
        META_WABA_ID=os.getenv("META_WABA_ID", ""),
        META_APP_SECRET=os.getenv("META_APP_SECRET", ""),
        GRAPH_API_VERSION=os.getenv("GRAPH_API_VERSION", "v27.0"),
        DRY_RUN=os.getenv("DRY_RUN", "true").lower() in {"1", "true", "yes"},
        SEND_DELAY_SECONDS=float(os.getenv("SEND_DELAY_SECONDS", "1.5")),
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)

    if not app.config.get("APP_PASSWORD") or len(app.config["APP_PASSWORD"]) < 16:
        raise RuntimeError("Set APP_PASSWORD to a unique password of at least 16 characters before starting the app")
    if not app.config.get("WEBHOOK_VERIFY_TOKEN"):
        raise RuntimeError("Set WEBHOOK_VERIFY_TOKEN before starting the app")
    if not app.config["DRY_RUN"]:
        required = ["META_ACCESS_TOKEN", "META_PHONE_NUMBER_ID", "META_WABA_ID", "META_APP_SECRET"]
        missing = [key for key in required if not app.config.get(key)]
        if missing:
            raise RuntimeError("Live mode requires these environment variables: " + ", ".join(missing))

    app.teardown_appcontext(close_db)
    init_db(app)

    @app.before_request
    def authenticate() -> Response | None:
        # Meta's webhook handshake and event delivery must be reachable without dashboard Basic Auth.
        if request.path == "/webhook":
            return None
        auth = request.authorization
        username = str(auth.username if auth else "")
        password = str(auth.password if auth else "")
        valid_user = hmac.compare_digest(username, app.config["APP_USERNAME"])
        valid_pass = hmac.compare_digest(password, app.config["APP_PASSWORD"])
        if not (valid_user and valid_pass):
            return Response("Authentication required", 401, {"WWW-Authenticate": 'Basic realm="WhatsApp Campaign Studio"'})
        return None

    @app.after_request
    def security_headers(response: Response) -> Response:
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        return jsonify(ok=True, dry_run=app.config["DRY_RUN"])

    @app.get("/api/settings")
    def settings():
        return jsonify({
            "dry_run": app.config["DRY_RUN"],
            "meta_configured": bool(app.config["META_ACCESS_TOKEN"] and app.config["META_PHONE_NUMBER_ID"] and app.config["META_WABA_ID"]),
            "phone_number_configured": bool(app.config["META_PHONE_NUMBER_ID"]),
            "templates_configured": bool(app.config["META_WABA_ID"]),
            "graph_api_version": app.config["GRAPH_API_VERSION"],
            "send_delay_seconds": app.config["SEND_DELAY_SECONDS"],
        })

    @app.get("/api/contacts")
    def contacts_list():
        rows = get_db().execute(
            "SELECT c.*, GROUP_CONCAT(g.name, ', ') AS groups_text FROM contacts c "
            "LEFT JOIN group_members gm ON gm.contact_id=c.id LEFT JOIN contact_groups g ON g.id=gm.group_id "
            "GROUP BY c.id ORDER BY c.id DESC LIMIT 500"
        ).fetchall()
        return jsonify([{
            "id": r["id"], "name": r["name"], "phone": r["phone"], "created_at": r["created_at"], "opted_in": bool(r["opted_in"]),
            "consent_source": r["consent_source"], "consent_at": r["consent_at"],
            "unsubscribed_at": r["unsubscribed_at"], "groups": r["groups_text"] or "",
            "custom_fields": json_object(r["custom_fields_json"]),
        } for r in rows])

    @app.post("/api/contacts")
    @require_api_header
    def contacts_create():
        data = request.get_json(silent=True) or {}
        if not truthy(data.get("opted_in")):
            return jsonify(error="A contact must have explicit WhatsApp consent before being added as opted in"), 400
        try:
            phone = normalize_phone(data.get("phone"))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        name = str(data.get("name", "")).strip()[:160]
        source = str(data.get("consent_source", "")).strip()[:240]
        group_id = data.get("group_id")
        if not source:
            return jsonify(error="Record where and how consent was obtained"), 400
        db = get_db()
        if group_id not in (None, ""):
            if not isinstance(group_id, int) or not db.execute("SELECT id FROM contact_groups WHERE id=?", (group_id,)).fetchone():
                return jsonify(error="Selected group does not exist"), 404
        try:
            cursor = db.execute(
                "INSERT INTO contacts(name,phone,opted_in,consent_source,consent_at,created_at) VALUES(?,?,?,?,?,?)",
                (name, phone, 1, source, utc_now(), utc_now()),
            )
            if group_id:
                db.execute("INSERT INTO group_members(group_id,contact_id,created_at) VALUES(?,?,?)",
                           (group_id, cursor.lastrowid, utc_now()))
            db.commit()
        except sqlite3.IntegrityError:
            return jsonify(error="That phone number already exists"), 409
        return jsonify(id=cursor.lastrowid, message="Contact added with consent recorded"), 201

    @app.post("/api/contacts/import")
    @require_api_header
    def contacts_import():
        upload = request.files.get("file")
        if not upload or not upload.filename.lower().endswith(".csv"):
            return jsonify(error="Upload a CSV file"), 400
        try:
            content = upload.stream.read().decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(content))
            if not reader.fieldnames or "phone" not in {h.strip().lower() for h in reader.fieldnames}:
                return jsonify(error="CSV must include a phone column"), 400
            headers = {h.strip().lower(): h for h in reader.fieldnames}
            db = get_db()
            group_id = request.form.get("group_id", type=int)
            if group_id and not db.execute("SELECT id FROM contact_groups WHERE id=?", (group_id,)).fetchone():
                return jsonify(error="Selected group does not exist"), 404
            imported = skipped = duplicates = 0
            errors: list[str] = []
            for line_no, row in enumerate(reader, start=2):
                try:
                    phone = normalize_phone(row.get(headers["phone"], ""))
                    name = str(row.get(headers.get("name", ""), "") or "").strip()[:160]
                    opted = truthy(row.get(headers.get("opted_in", ""), ""))
                    source = str(row.get(headers.get("consent_source", ""), "") or "").strip()[:240]
                    if opted and not source:
                        raise ValueError("opted_in is yes but consent_source is missing")
                    excluded = {"phone", "name", "opted_in", "consent_source"}
                    custom = {key.strip(): str(row.get(original, "") or "")[:1000]
                              for key, original in headers.items() if key not in excluded and row.get(original) not in (None, "")}
                    cur = db.execute(
                        "INSERT OR IGNORE INTO contacts(name,phone,opted_in,consent_source,consent_at,custom_fields_json,created_at) VALUES(?,?,?,?,?,?,?)",
                        (name, phone, int(opted), source if opted else "", utc_now() if opted else None,
                         json.dumps(custom, ensure_ascii=False), utc_now()),
                    )
                    if cur.rowcount == 0:
                        duplicates += 1
                    else:
                        imported += 1
                        contact_id = cur.lastrowid
                        if group_id:
                            db.execute("INSERT OR IGNORE INTO group_members(group_id,contact_id,created_at) VALUES(?,?,?)",
                                       (group_id, contact_id, utc_now()))
                except ValueError as exc:
                    skipped += 1
                    if len(errors) < 10:
                        errors.append(f"Line {line_no}: {exc}")
            db.commit()
            return jsonify(imported=imported, skipped=skipped, duplicates=duplicates, errors=errors)
        except UnicodeDecodeError:
            return jsonify(error="CSV must be UTF-8 encoded"), 400
        except csv.Error as exc:
            return jsonify(error=f"Could not read CSV: {exc}"), 400

    @app.post("/api/contacts/<int:contact_id>/opt-out")
    @require_api_header
    def contacts_opt_out(contact_id: int):
        db = get_db()
        cur = db.execute("UPDATE contacts SET opted_in=0, unsubscribed_at=? WHERE id=?", (utc_now(), contact_id))
        db.commit()
        if not cur.rowcount:
            return jsonify(error="Contact not found"), 404
        return jsonify(ok=True)

    @app.post("/api/groups")
    @require_api_header
    def groups_create():
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", "")).strip()[:100]
        if not name:
            return jsonify(error="Group name is required"), 400
        db = get_db()
        try:
            cur = db.execute("INSERT INTO contact_groups(name,created_at) VALUES(?,?)", (name, utc_now()))
            db.commit()
            return jsonify(id=cur.lastrowid, name=name), 201
        except sqlite3.IntegrityError:
            return jsonify(error="A group with that name already exists"), 409

    @app.get("/api/groups")
    def groups_list():
        rows = get_db().execute(
            "SELECT g.id,g.name,COUNT(CASE WHEN c.opted_in=1 AND c.unsubscribed_at IS NULL THEN 1 END) AS eligible_count "
            "FROM contact_groups g LEFT JOIN group_members gm ON gm.group_id=g.id "
            "LEFT JOIN contacts c ON c.id=gm.contact_id GROUP BY g.id ORDER BY g.name"
        ).fetchall()
        return jsonify([dict(r) for r in rows])

    @app.post("/api/groups/<int:group_id>/members")
    @require_api_header
    def groups_add_members(group_id: int):
        data = request.get_json(silent=True) or {}
        ids = data.get("contact_ids", [])
        if not isinstance(ids, list) or len(ids) > 1000:
            return jsonify(error="contact_ids must be a list of up to 1,000 IDs"), 400
        db = get_db()
        if not db.execute("SELECT id FROM contact_groups WHERE id=?", (group_id,)).fetchone():
            return jsonify(error="Group not found"), 404
        for contact_id in ids:
            if isinstance(contact_id, int):
                db.execute("INSERT OR IGNORE INTO group_members(group_id,contact_id,created_at) VALUES(?,?,?)",
                           (group_id, contact_id, utc_now()))
        db.commit()
        return jsonify(ok=True)

    @app.get("/api/templates")
    def templates_list():
        if not (app.config["META_ACCESS_TOKEN"] and app.config["META_WABA_ID"]):
            return jsonify(error="Configure META_ACCESS_TOKEN and META_WABA_ID to retrieve approved templates"), 503
        try:
            templates = fetch_approved_templates(app)
            return jsonify(templates=templates)
        except Exception as exc:
            return jsonify(error=str(exc)), 502

    @app.get("/api/campaigns")
    def campaigns_list():
        rows = get_db().execute(
            "SELECT c.*, g.name AS group_name FROM campaigns c JOIN contact_groups g ON g.id=c.group_id ORDER BY c.id DESC LIMIT 100"
        ).fetchall()
        return jsonify([{
            "id": r["id"], "name": r["name"], "group_id": r["group_id"], "group_name": r["group_name"],
            "template_name": r["template_name"], "language": r["language"], "status": r["status"],
            "total": r["total"], "sent": r["sent"], "delivered": r["delivered"],
            "read": r["read_count"], "failed": r["failed"], "dry_run": bool(r["dry_run"]),
            "created_at": r["created_at"], "finished_at": r["finished_at"],
        } for r in rows])

    @app.post("/api/campaigns")
    @require_api_header
    def campaigns_create():
        data = request.get_json(silent=True) or {}
        name = str(data.get("name", "")).strip()[:160]
        template_name = str(data.get("template_name", "")).strip()
        language = str(data.get("language", "en_US")).strip()
        group_id = data.get("group_id")
        body_fields = data.get("body_fields", [])
        if not name or not template_name or not language:
            return jsonify(error="Campaign name, approved template name and language are required"), 400
        if not isinstance(group_id, int) or not isinstance(body_fields, list) or len(body_fields) > 10:
            return jsonify(error="Choose a contact group and provide up to 10 body parameter field names"), 400
        clean_fields = [str(item).strip()[:80] for item in body_fields if str(item).strip()]
        db = get_db()
        if not db.execute("SELECT id FROM contact_groups WHERE id=?", (group_id,)).fetchone():
            return jsonify(error="Selected contact group does not exist"), 404
        eligible = db.execute(
            "SELECT c.id FROM contacts c JOIN group_members gm ON gm.contact_id=c.id "
            "WHERE gm.group_id=? AND c.opted_in=1 AND c.unsubscribed_at IS NULL ORDER BY c.id",
            (group_id,),
        ).fetchall()
        if not eligible:
            return jsonify(error="This group has no contacts with recorded, active consent"), 400
        if not app.config["DRY_RUN"]:
            if not (app.config["META_ACCESS_TOKEN"] and app.config["META_PHONE_NUMBER_ID"] and app.config["META_WABA_ID"]):
                return jsonify(error="Live sending requires META_ACCESS_TOKEN, META_PHONE_NUMBER_ID and META_WABA_ID"), 400
            try:
                approved = fetch_approved_templates(app)
            except Exception as exc:
                return jsonify(error=f"Could not verify approved templates with Meta: {exc}"), 502
            if not any(t.get("name") == template_name and t.get("language") == language for t in approved):
                return jsonify(error="The selected template and language were not found with APPROVED status in this WhatsApp Business Account"), 400
        cur = db.execute(
            "INSERT INTO campaigns(name,group_id,template_name,language,body_fields_json,status,total,dry_run,created_at) "
            "VALUES(?,?,?,?,?,'draft',?,?,?)",
            (name, group_id, template_name, language, json.dumps(clean_fields), len(eligible), int(app.config["DRY_RUN"]), utc_now()),
        )
        campaign_id = cur.lastrowid
        db.executemany(
            "INSERT INTO campaign_recipients(campaign_id,contact_id,status,updated_at) VALUES(?,?,'pending',?)",
            [(campaign_id, row["id"], utc_now()) for row in eligible],
        )
        db.commit()
        return jsonify(id=campaign_id, total=len(eligible), status="draft", dry_run=bool(app.config["DRY_RUN"])), 201

    @app.post("/api/campaigns/<int:campaign_id>/send")
    @require_api_header
    def campaigns_send(campaign_id: int):
        data = request.get_json(silent=True) or {}
        if data.get("confirm") is not True:
            return jsonify(error="Explicit confirmation is required"), 400
        db = get_db()
        campaign = db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if not campaign:
            return jsonify(error="Campaign not found"), 404
        if campaign["status"] not in {"draft", "paused"}:
            return jsonify(error=f"Campaign cannot be started from status {campaign['status']}"), 409
        if not campaign["dry_run"]:
            # Re-verify approval immediately before the send is queued.
            try:
                approved = fetch_approved_templates(app)
            except Exception as exc:
                return jsonify(error=f"Could not verify template approval: {exc}"), 502
            if not any(t.get("name") == campaign["template_name"] and t.get("language") == campaign["language"] for t in approved):
                return jsonify(error="The campaign template is no longer approved"), 400
        db.execute("UPDATE campaigns SET status='queued', started_at=? WHERE id=?", (utc_now(), campaign_id))
        db.commit()
        launch_worker(app, campaign_id)
        return jsonify(ok=True, status="queued", dry_run=bool(campaign["dry_run"]))

    @app.get("/api/campaigns/<int:campaign_id>/recipients")
    def campaigns_recipients(campaign_id: int):
        rows = get_db().execute(
            "SELECT c.name,c.phone,cr.status,cr.error,cr.updated_at FROM campaign_recipients cr "
            "JOIN contacts c ON c.id=cr.contact_id WHERE cr.campaign_id=? ORDER BY cr.id LIMIT 1000",
            (campaign_id,),
        ).fetchall()
        return jsonify([dict(r) for r in rows])

    @app.get("/webhook")
    def webhook_verify():
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge", "")
        if mode == "subscribe" and hmac.compare_digest(str(token or ""), app.config["WEBHOOK_VERIFY_TOKEN"]):
            return Response(challenge, status=200, mimetype="text/plain")
        return Response("Forbidden", status=403)

    @app.post("/webhook")
    def webhook_receive():
        raw = request.get_data(cache=True)
        app_secret = app.config.get("META_APP_SECRET", "")
        if app_secret:
            supplied = request.headers.get("X-Hub-Signature-256", "")
            expected = "sha256=" + hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(supplied, expected):
                return jsonify(error="Invalid webhook signature"), 401
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return jsonify(error="Invalid JSON"), 400
        db = get_db()
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for status in value.get("statuses", []):
                    wamid = status.get("id")
                    state = status.get("status")
                    if not wamid or state not in {"sent", "delivered", "read", "failed"}:
                        continue
                    row = db.execute("SELECT campaign_id FROM campaign_recipients WHERE wamid=?", (wamid,)).fetchone()
                    if row:
                        errors = status.get("errors") or []
                        err_text = json.dumps(errors, ensure_ascii=False)[:1000] if errors else None
                        db.execute("UPDATE campaign_recipients SET status=?, error=?, updated_at=? WHERE wamid=?",
                                   (state, err_text, utc_now(), wamid))
                        update_campaign_counts(db, row["campaign_id"])
                for message in value.get("messages", []):
                    sender = message.get("from")
                    body = ""
                    if message.get("type") == "text":
                        body = str(message.get("text", {}).get("body", "")).strip().upper()
                    if sender and body in OPT_OUT_WORDS:
                        try:
                            phone = normalize_phone("+" + str(sender))
                            db.execute("UPDATE contacts SET opted_in=0, unsubscribed_at=? WHERE phone=?", (utc_now(), phone))
                        except ValueError:
                            pass
        db.commit()
        return jsonify(ok=True)

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify(error="Upload exceeds the 5 MB limit"), 413

    @app.errorhandler(BadRequest)
    def bad_request(_error):
        return jsonify(error="Invalid request"), 400

    return app


if __name__ == "__main__":
    application = create_app()
    application.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")), debug=False)
