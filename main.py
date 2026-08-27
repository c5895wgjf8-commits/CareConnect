
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from passlib.context import CryptContext
from itsdangerous import URLSafeSerializer, BadSignature
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from pathlib import Path
from datetime import datetime, timezone
import os
import time
import stripe
import httpx

BASE = Path(__file__).resolve().parent
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE/'careconnect.db'}")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
APP_ENV = os.getenv("APP_ENV", "development")
COOKIE_SECURE = APP_ENV == "production"
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
DAILY_API_KEY = os.getenv("DAILY_API_KEY", "")
DAILY_API_BASE = "https://api.daily.co/v1"
stripe.api_key = STRIPE_SECRET_KEY or None

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=connect_args)

app = FastAPI(title="CareConnect Telemedicine MVP")
app.mount("/static", StaticFiles(directory=BASE/"static"), name="static")
templates = Jinja2Templates(directory=BASE/"templates")

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
serializer = URLSafeSerializer(SECRET_KEY, salt="careconnect-session")

def now():
    return datetime.now(timezone.utc).isoformat()

def init_db():
    schema = """
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      email TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL,
      role TEXT NOT NULL,
      specialty TEXT,
      languages TEXT,
      visit_fee_cents INTEGER NOT NULL DEFAULT 4500,
      verified INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS appointments(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      patient_id INTEGER NOT NULL,
      doctor_id INTEGER NOT NULL,
      appt_date TEXT NOT NULL,
      appt_time TEXT NOT NULL,
      reason TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'Scheduled',
      paid INTEGER NOT NULL DEFAULT 0,
      consent INTEGER NOT NULL DEFAULT 0,
      stripe_checkout_session_id TEXT,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS visit_notes(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      appointment_id INTEGER UNIQUE NOT NULL,
      doctor_id INTEGER NOT NULL,
      note_text TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    """
    # SQLite supports executescript; PostgreSQL requires individual statements.
    with engine.begin() as conn:
        for stmt in [x.strip() for x in schema.split(";") if x.strip()]:
            # Translate the SQLite autoincrement syntax for PostgreSQL.
            if not DATABASE_URL.startswith("sqlite"):
                stmt = stmt.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            conn.execute(text(stmt))

        demo_docs = [
          ("Dr. Marie Joseph","marie@demo.local","Internal Medicine","English,French,Creole",4500),
          ("Dr. Daniel Pierre","daniel@demo.local","Family Medicine","English,Creole",4000),
          ("Dr. Sofia Martinez","sofia@demo.local","Primary Care","English,Spanish",4500),
        ]
        for name,email,specialty,languages,visit_fee_cents in demo_docs:
            exists = conn.execute(text("SELECT id FROM users WHERE email=:email"), {"email": email}).mappings().first()
            if not exists:
                conn.execute(text("""INSERT INTO users(name,email,password_hash,role,specialty,languages,visit_fee_cents,verified,created_at)
                                    VALUES(:name,:email,:password_hash,'doctor',:specialty,:languages,:visit_fee_cents,1,:created_at)"""),
                             dict(name=name,email=email,password_hash=pwd.hash("demo1234"),
                                  specialty=specialty,languages=languages,visit_fee_cents=visit_fee_cents,created_at=now()))
init_db()

def fetch_one(query, params=None):
    with engine.connect() as conn:
        return conn.execute(text(query), params or {}).mappings().first()

def fetch_all(query, params=None):
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(query), params or {}).mappings().all()]

def current_user(request: Request):
    token = request.cookies.get("cc_session")
    if not token:
        return None
    try:
        data = serializer.loads(token)
    except BadSignature:
        return None
    row = fetch_one("SELECT * FROM users WHERE id=:id", {"id": data.get("uid")})
    return dict(row) if row else None

def require_user(request: Request, role=None):
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Login required")
    if role and user["role"] != role:
        raise HTTPException(403, f"{role.title()} access required")
    return user

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request":request, "user":current_user(request)})

@app.get("/api/me")
def me(request: Request):
    return {"user": current_user(request)}

@app.post("/api/register")
def register(name: str = Form(...), email: str = Form(...), password: str = Form(...), role: str = Form(...)):
    if role not in ("patient","doctor"):
        raise HTTPException(400, "Invalid role")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    email = email.strip().lower()
    try:
        with engine.begin() as conn:
            result = conn.execute(text("""INSERT INTO users(name,email,password_hash,role,verified,created_at)
                                         VALUES(:name,:email,:password_hash,:role,:verified,:created_at)"""),
                                  dict(name=name.strip(),email=email,password_hash=pwd.hash(password),
                                       role=role,verified=0 if role=="doctor" else 1,created_at=now()))
            if DATABASE_URL.startswith("sqlite"):
                uid = result.lastrowid
            else:
                uid = conn.execute(text("SELECT id FROM users WHERE email=:email"), {"email":email}).scalar_one()
    except IntegrityError:
        raise HTTPException(409, "Email already exists")

    token = serializer.dumps({"uid": uid})
    response = JSONResponse({"ok": True})
    response.set_cookie("cc_session", token, httponly=True, samesite="lax", secure=COOKIE_SECURE, max_age=60*60*24*7)
    return response

@app.post("/api/login")
def login(email: str = Form(...), password: str = Form(...)):
    user = fetch_one("SELECT * FROM users WHERE email=:email", {"email":email.strip().lower()})
    if not user or not pwd.verify(password, user["password_hash"]):
        raise HTTPException(401, "Invalid credentials")
    token = serializer.dumps({"uid": user["id"]})
    response = JSONResponse({"ok": True, "role": user["role"]})
    response.set_cookie("cc_session", token, httponly=True, samesite="lax", secure=COOKIE_SECURE, max_age=60*60*24*7)
    return response

@app.post("/api/logout")
def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie("cc_session")
    return response

@app.get("/api/doctors")
def list_doctors():
    return {"doctors": fetch_all("""SELECT id,name,specialty,languages,visit_fee_cents,verified
                                    FROM users WHERE role='doctor'
                                    ORDER BY verified DESC,name""")}

@app.post("/api/appointments")
def create_appointment(
    request: Request,
    doctor_id: int = Form(...),
    appt_date: str = Form(...),
    appt_time: str = Form(...),
    reason: str = Form(...),
    consent: str = Form(...)
):
    user = require_user(request, "patient")
    if consent != "yes":
        raise HTTPException(400, "Consent required")
    doctor = fetch_one("SELECT id FROM users WHERE id=:id AND role='doctor'", {"id":doctor_id})
    if not doctor:
        raise HTTPException(404, "Doctor not found")

    with engine.begin() as conn:
        result = conn.execute(text("""INSERT INTO appointments(patient_id,doctor_id,appt_date,appt_time,reason,status,paid,consent,created_at)
                                     VALUES(:patient_id,:doctor_id,:appt_date,:appt_time,:reason,'Scheduled',0,1,:created_at)"""),
                              dict(patient_id=user["id"],doctor_id=doctor_id,appt_date=appt_date,
                                   appt_time=appt_time,reason=reason.strip(),created_at=now()))
        appt_id = result.lastrowid if DATABASE_URL.startswith("sqlite") else conn.execute(
            text("""SELECT id FROM appointments
                    WHERE patient_id=:patient_id AND doctor_id=:doctor_id
                    ORDER BY id DESC LIMIT 1"""),
            {"patient_id":user["id"],"doctor_id":doctor_id}).scalar_one()

    return {"ok":True,"appointment_id":appt_id}

@app.get("/api/appointments")
def list_appointments(request: Request):
    user = require_user(request)
    if user["role"] == "patient":
        rows = fetch_all("""SELECT a.*, d.name AS doctor_name, d.specialty
                            FROM appointments a JOIN users d ON d.id=a.doctor_id
                            WHERE a.patient_id=:uid ORDER BY a.appt_date,a.appt_time""", {"uid":user["id"]})
    else:
        rows = fetch_all("""SELECT a.*, p.name AS patient_name, p.email AS patient_email
                            FROM appointments a JOIN users p ON p.id=a.patient_id
                            WHERE a.doctor_id=:uid ORDER BY a.appt_date,a.appt_time""", {"uid":user["id"]})
    return {"appointments": rows}

@app.post("/api/appointments/{appointment_id}/cancel")
def cancel_appointment(appointment_id:int, request:Request):
    user = require_user(request)
    a = fetch_one("SELECT * FROM appointments WHERE id=:id", {"id":appointment_id})
    if not a:
        raise HTTPException(404,"Appointment not found")
    allowed = (user["role"]=="patient" and a["patient_id"]==user["id"]) or (user["role"]=="doctor" and a["doctor_id"]==user["id"])
    if not allowed:
        raise HTTPException(403,"Forbidden")
    with engine.begin() as conn:
        conn.execute(text("UPDATE appointments SET status='Cancelled' WHERE id=:id"), {"id":appointment_id})
    return {"ok":True}

@app.post("/api/appointments/{appointment_id}/checkout")
def create_checkout(appointment_id:int, request:Request):
    user = require_user(request, "patient")
    if not STRIPE_SECRET_KEY:
        raise HTTPException(503, "Stripe is not configured on this deployment.")

    a = fetch_one("""SELECT a.*, d.name AS doctor_name, d.specialty, d.visit_fee_cents
                     FROM appointments a
                     JOIN users d ON d.id=a.doctor_id
                     WHERE a.id=:id AND a.patient_id=:uid""",
                  {"id":appointment_id, "uid":user["id"]})
    if not a:
        raise HTTPException(404, "Appointment not found")
    if a["status"] == "Cancelled":
        raise HTTPException(400, "Cancelled appointments cannot be paid.")
    if a["paid"]:
        return {"ok": True, "already_paid": True, "url": None}

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            customer_email=user["email"],
            line_items=[{
                "quantity": 1,
                "price_data": {
                    "currency": "usd",
                    "unit_amount": int(a["visit_fee_cents"] or 4500),
                    "product_data": {
                        "name": f"Virtual visit with {a['doctor_name']}",
                        "description": f"{a['specialty'] or 'Medical'} appointment on {a['appt_date']} at {a['appt_time']}",
                    },
                },
            }],
            success_url=f"{APP_BASE_URL}/?payment=success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{APP_BASE_URL}/?payment=cancelled",
            metadata={
                "careconnect_appointment_id": str(appointment_id),
                "careconnect_patient_id": str(user["id"]),
            },
        )
    except Exception as exc:
        raise HTTPException(502, f"Unable to start Stripe Checkout: {exc}")

    with engine.begin() as conn:
        conn.execute(text("""UPDATE appointments
                            SET stripe_checkout_session_id=:sid
                            WHERE id=:id"""),
                     {"sid": session.id, "id": appointment_id})
    return {"ok": True, "checkout_session_id": session.id, "url": session.url}


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(503, "Stripe webhook secret is not configured.")

    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)
    except ValueError:
        raise HTTPException(400, "Invalid webhook payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(400, "Invalid webhook signature")

    event_type = event.get("type")
    obj = event["data"]["object"]

    if event_type == "checkout.session.completed":
        appointment_id = (obj.get("metadata") or {}).get("careconnect_appointment_id")
        if appointment_id:
            with engine.begin() as conn:
                conn.execute(text("""UPDATE appointments
                                    SET paid=1, stripe_checkout_session_id=:sid
                                    WHERE id=:id"""),
                             {"sid": obj.get("id"), "id": int(appointment_id)})

    elif event_type in ("checkout.session.expired", "payment_intent.payment_failed"):
        # Payment remains unpaid; no PHI is included in Stripe metadata.
        pass

    return {"received": True}


@app.get("/api/appointments/{appointment_id}/video-room")
async def video_room(appointment_id:int, request:Request):
    user = require_user(request)
    a = fetch_one("SELECT * FROM appointments WHERE id=:id", {"id":appointment_id})
    if not a:
        raise HTTPException(404, "Appointment not found")

    allowed = (user["role"]=="patient" and a["patient_id"]==user["id"]) or (user["role"]=="doctor" and a["doctor_id"]==user["id"])
    if not allowed:
        raise HTTPException(403, "Forbidden")

    if not a["paid"] and user["role"] == "patient":
        raise HTTPException(402, "Appointment payment is required before joining.")

    if not DAILY_API_KEY:
        return {
          "provider":"placeholder",
          "room_name":f"cc-demo-{appointment_id}",
          "url":None,
          "message":"Daily is not configured. Add DAILY_API_KEY to activate real video rooms."
        }

    headers = {"Authorization": f"Bearer {DAILY_API_KEY}", "Content-Type": "application/json"}
    room_name = f"careconnect-{appointment_id}"

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Create a private room; if it already exists Daily may return an error,
        # in which case we retrieve it instead.
        expiry = int(time.time()) + 60 * 60 * 6
        create_resp = await client.post(
            f"{DAILY_API_BASE}/rooms",
            headers=headers,
            json={
                "name": room_name,
                "privacy": "private",
                "properties": {
                    "exp": expiry,
                    "enable_chat": True
                }
            }
        )
        if create_resp.status_code in (200, 201):
            room = create_resp.json()
        else:
            get_resp = await client.get(f"{DAILY_API_BASE}/rooms/{room_name}", headers=headers)
            if get_resp.status_code != 200:
                raise HTTPException(502, "Unable to create or retrieve video room.")
            room = get_resp.json()

        token_resp = await client.post(
            f"{DAILY_API_BASE}/meeting-tokens",
            headers=headers,
            json={
                "properties": {
                    "room_name": room_name,
                    "is_owner": user["role"] == "doctor",
                    "user_name": user["name"],
                    "exp": expiry
                }
            }
        )
        if token_resp.status_code not in (200, 201):
            raise HTTPException(502, "Unable to create video meeting token.")
        token = token_resp.json().get("token")

    join_url = room.get("url")
    if token:
        join_url = f"{join_url}?t={token}"
    return {
      "provider":"daily",
      "room_name":room_name,
      "url":join_url
    }


@app.get("/api/appointments/{appointment_id}/notes")
def get_notes(appointment_id:int, request:Request):
    user = require_user(request)
    a = fetch_one("SELECT * FROM appointments WHERE id=:id", {"id":appointment_id})
    if not a:
        raise HTTPException(404,"Appointment not found")
    allowed = (user["role"]=="doctor" and a["doctor_id"]==user["id"]) or (user["role"]=="patient" and a["patient_id"]==user["id"])
    if not allowed:
        raise HTTPException(403,"Forbidden")
    note = fetch_one("SELECT note_text,updated_at FROM visit_notes WHERE appointment_id=:id", {"id":appointment_id})
    return {"note": dict(note) if note else None}

@app.post("/api/appointments/{appointment_id}/notes")
def save_note(appointment_id:int, request:Request, note_text:str=Form(...)):
    user = require_user(request,"doctor")
    a = fetch_one("SELECT * FROM appointments WHERE id=:id AND doctor_id=:uid", {"id":appointment_id,"uid":user["id"]})
    if not a:
        raise HTTPException(404,"Appointment not found")
    with engine.begin() as conn:
        existing = conn.execute(text("SELECT id FROM visit_notes WHERE appointment_id=:id"), {"id":appointment_id}).first()
        if existing:
            conn.execute(text("""UPDATE visit_notes SET note_text=:note_text,updated_at=:updated_at
                                WHERE appointment_id=:id"""),
                         {"note_text":note_text.strip(),"updated_at":now(),"id":appointment_id})
        else:
            conn.execute(text("""INSERT INTO visit_notes(appointment_id,doctor_id,note_text,updated_at)
                                VALUES(:appointment_id,:doctor_id,:note_text,:updated_at)"""),
                         {"appointment_id":appointment_id,"doctor_id":user["id"],"note_text":note_text.strip(),"updated_at":now()})
    return {"ok":True}

@app.get("/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status":"ok","database":"connected","environment":APP_ENV}
    except Exception:
        raise HTTPException(503,"Database unavailable")
