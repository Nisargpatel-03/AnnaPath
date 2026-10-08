import re
import os
import sqlite3
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import time
import uuid
import json
import urllib.request
import urllib.parse
import secrets
import threading
from datetime import timedelta, datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session
import config

import firebase_admin
from firebase_admin import credentials, firestore, auth

# Initialize Firebase Admin
cred = credentials.Certificate(
    os.path.join(os.path.dirname(__file__), "firebase-adminsdk.json")
)
firebase_admin.initialize_app(cred)
db = firestore.client()


def verify_firebase_credentials(email, password):
    """Verify credentials against Firebase Authentication REST API."""
    api_key = getattr(
        config,
        "FIREBASE_API_KEY",
        "AIzaSyC5qZAO5HX2ddE0mKFzVtIAxEEmvJh-jR8")
    if not api_key or not email or not password:
        return False
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}"
    payload = json.dumps({
        "email": email,
        "password": password,
        "returnSecureToken": True
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


app = Flask(__name__)
app.config.from_object(config)
app.secret_key = getattr(config, "SECRET_KEY", "annapath_secret_key")

# --- Session / "Remember Me" Configuration ---
app.config["SESSION_PERMANENT"] = True
app.permanent_session_lifetime = timedelta(days=30)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

UPLOAD_FOLDER = os.path.join(app.root_path, "static", "images")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def allowed_file(filename):
    return "." in filename and filename.rsplit(
        ".", 1)[1].lower() in ALLOWED_EXTENSIONS


def parse_quantity(qty_str):
    """Extract numeric quantity and unit, e.g. '30 roti' -> (30, 'roti')"""
    if not qty_str:
        return 1, "items"
    match = re.search(r"(\d+)\s*(.*)", str(qty_str).strip())
    if match:
        num = int(match.group(1))
        unit = match.group(2).strip() or "items"
        return max(1, num), unit
    return 1, "items"


def get_db():
    conn = sqlite3.connect(config.DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db_schema():
    """Ensure database tables and columns exist to prevent runtime SQL errors."""
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("PRAGMA table_info(users)")
        user_cols = [r[1] for r in cur.fetchall()]
        if user_cols and "phone" not in user_cols:
            cur.execute("ALTER TABLE users ADD COLUMN phone TEXT")

        cur.execute("PRAGMA table_info(volunteers)")
        vol_cols = [r[1] for r in cur.fetchall()]
        if vol_cols:
            if "created_at" not in vol_cols:
                cur.execute(
                    "ALTER TABLE volunteers ADD COLUMN created_at TIMESTAMP")
            if "admin_notes" not in vol_cols:
                cur.execute(
                    "ALTER TABLE volunteers ADD COLUMN admin_notes TEXT")
            if "password" not in vol_cols:
                cur.execute("ALTER TABLE volunteers ADD COLUMN password TEXT")
            if "email" not in vol_cols:
                cur.execute("ALTER TABLE volunteers ADD COLUMN email TEXT")
            if "time" not in vol_cols:
                cur.execute("ALTER TABLE volunteers ADD COLUMN time TEXT")

        cur.execute("PRAGMA table_info(food)")
        food_cols = [r[1] for r in cur.fetchall()]
        if food_cols:
            if "latitude" not in food_cols:
                cur.execute("ALTER TABLE food ADD COLUMN latitude REAL")
            if "longitude" not in food_cols:
                cur.execute("ALTER TABLE food ADD COLUMN longitude REAL")
            if "picked_up_by" not in food_cols:
                cur.execute("ALTER TABLE food ADD COLUMN picked_up_by TEXT")
            if "picked_up_at" not in food_cols:
                cur.execute(
                    "ALTER TABLE food ADD COLUMN picked_up_at TIMESTAMP")
            if "food_code" not in food_cols:
                cur.execute("ALTER TABLE food ADD COLUMN food_code TEXT")
            if "initial_quantity" not in food_cols:
                cur.execute(
                    "ALTER TABLE food ADD COLUMN initial_quantity INTEGER DEFAULT 1"
                )
            if "available_quantity" not in food_cols:
                cur.execute(
                    "ALTER TABLE food ADD COLUMN available_quantity INTEGER DEFAULT 1"
                )
            if "claimed_quantity" not in food_cols:
                cur.execute(
                    "ALTER TABLE food ADD COLUMN claimed_quantity INTEGER DEFAULT 0"
                )
            if "unit" not in food_cols:
                cur.execute(
                    "ALTER TABLE food ADD COLUMN unit TEXT DEFAULT 'items'")
            if "phone" not in food_cols:
                cur.execute("ALTER TABLE food ADD COLUMN phone TEXT")

        cur.execute("PRAGMA table_info(requests)")
        req_cols = [r[1] for r in cur.fetchall()]
        if req_cols:
            if "latitude" not in req_cols:
                cur.execute("ALTER TABLE requests ADD COLUMN latitude REAL")
            if "longitude" not in req_cols:
                cur.execute("ALTER TABLE requests ADD COLUMN longitude REAL")
            if "food_code" not in req_cols:
                cur.execute("ALTER TABLE requests ADD COLUMN food_code TEXT")
            if "name" not in req_cols:
                cur.execute("ALTER TABLE requests ADD COLUMN name TEXT")
            if "phone" not in req_cols:
                cur.execute("ALTER TABLE requests ADD COLUMN phone TEXT")
            if "food_name" not in req_cols:
                cur.execute("ALTER TABLE requests ADD COLUMN food_name TEXT")
            if "quantity" not in req_cols:
                cur.execute("ALTER TABLE requests ADD COLUMN quantity TEXT")
            if "address" not in req_cols:
                cur.execute("ALTER TABLE requests ADD COLUMN address TEXT")
            if "user_email" not in req_cols:
                cur.execute("ALTER TABLE requests ADD COLUMN user_email TEXT")
            if "volunteer_name" not in req_cols:
                cur.execute("ALTER TABLE requests ADD COLUMN volunteer_name TEXT")

        # Backfill user_email for existing requests from users table
        try:
            cur.execute("""
                UPDATE requests
                SET user_email = (
                    SELECT email FROM users 
                    WHERE (LOWER(users.name) = LOWER(requests.name) OR LOWER(users.name) = LOWER(requests.user_name) OR (users.phone = requests.phone AND users.phone != ''))
                    ORDER BY CASE WHEN users.role = 'Receiver' THEN 0 ELSE 1 END, users.id DESC LIMIT 1
                )
                WHERE user_email IS NULL OR user_email = ''
            """)
        except Exception:
            pass

        cur.execute("""
            CREATE TABLE IF NOT EXISTS pending_registrations (
                email TEXT PRIMARY KEY,
                name TEXT,
                phone TEXT,
                password TEXT,
                role TEXT,
                otp TEXT,
                expires_at TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS password_resets (
                email TEXT PRIMARY KEY,
                otp TEXT,
                expires_at TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        app.logger.warning(f"Database schema self-check notice: {e}")


# Run schema safety check on module load
init_db_schema()


def send_email_notification(to_email, subject, body_html, body_text=""):
    """Send real email to inbox via Google SMTP and archive in Firebase Firestore 'mail' collection."""
    if not to_email:
        return "No recipient email provided"

    conn = get_db()
    status = "Pending"

    # 1. Deliver to real inbox via Google SMTP
    smtp_user = getattr(config, "SMTP_USERNAME", "")
    smtp_pass = getattr(config, "SMTP_PASSWORD", "")
    smtp_server = getattr(config, "SMTP_SERVER", "smtp.gmail.com")
    smtp_port = getattr(config, "SMTP_PORT", 587)
    sender = getattr(config, "SENDER_EMAIL", "") or smtp_user or "noreply@annapath.org"

    smtp_success = False
    if smtp_user and smtp_pass:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"AnnaPath <{sender}>"
            msg["To"] = to_email
            if body_text:
                msg.attach(MIMEText(body_text, "plain"))
            if body_html:
                msg.attach(MIMEText(body_html, "html"))

            with smtplib.SMTP(smtp_server, smtp_port, timeout=12) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.sendmail(sender, to_email, msg.as_string())
            smtp_success = True
        except Exception as e:
            app.logger.error(f"SMTP delivery failed to {to_email}: {e}")

    # 2. Also record in Firebase Firestore 'mail' collection
    firebase_id = None
    try:
        if db:
            collection_name = getattr(config, "FIREBASE_MAIL_COLLECTION", "mail")
            doc_ref = db.collection(collection_name).add({
                "to": [to_email],
                "message": {
                    "subject": subject,
                    "text": body_text or subject,
                    "html": body_html or body_text,
                },
                "from": sender,
                "createdAt": firestore.SERVER_TIMESTAMP,
                "status": "delivered" if smtp_success else "pending",
            })
            firebase_id = doc_ref[1].id
    except Exception as fb_err:
        app.logger.warning(f"Firebase Firestore archiving notice: {fb_err}")

    if smtp_success:
        status = f"Delivered to Inbox & Firebase ({firebase_id})" if firebase_id else "Delivered to Inbox"
    elif firebase_id:
        status = f"Archived in Firebase ({firebase_id})"
    else:
        status = "Delivery Failed"

    try:
        conn.execute(
            "INSERT INTO email_logs (recipient, subject, body, status) VALUES (?, ?, ?, ?)",
            (to_email, subject, body_html or body_text, status),
        )
        conn.commit()
    except Exception as e:
        app.logger.error(f"Failed to record email log: {e}")
    finally:
        conn.close()

    return status


def notify_role(role, title, message, html_message=None):
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO notifications (user_role, title, message) VALUES (?, ?, ?)",
            (role, title, message),
        )
        conn.commit()

        if role == "Volunteer":
            users = conn.execute(
                "SELECT email FROM volunteers WHERE status = 'Approved'"
            ).fetchall()
        elif role == "Admin":
            users = conn.execute(
                "SELECT email FROM users WHERE role = 'Admin'"
            ).fetchall()
        elif role == "Donor":
            users = conn.execute(
                "SELECT email FROM users WHERE role = 'Donor'"
            ).fetchall()
        elif role == "Receiver":
            users = conn.execute(
                "SELECT email FROM users WHERE role = 'Receiver'"
            ).fetchall()
        else:
            users = []

        emails = [u["email"] for u in users if u["email"]]
        conn.close()

        if emails:
            def send_batch():
                for email in emails:
                    send_email_notification(
                        email, title, html_message or message, message
                    )

            threading.Thread(target=send_batch, daemon=True).start()
    except Exception as e:
        app.logger.error(f"Error notifying role {role}: {e}")


def notify_user(email, title, message, html_message=None):
    try:
        if not email:
            return
        conn = get_db()
        conn.execute(
            "INSERT INTO notifications (user_email, title, message) VALUES (?, ?, ?)",
            (email, title, message),
        )
        conn.commit()
        conn.close()

        def send_single():
            try:
                send_email_notification(
                    email, title, html_message or message, message)
            except Exception as e:
                app.logger.error(f"Error in async email to {email}: {e}")

        threading.Thread(target=send_single, daemon=False).start()
    except Exception as e:
        app.logger.error(f"Error notifying user {email}: {e}")


def find_donor_email(seller_name, phone=None, conn=None):
    """Find donor email with robust fallbacks: exact name, phone, case-insensitive, or any role."""
    close_conn = False
    if conn is None:
        conn = get_db()
        close_conn = True
    try:
        if not seller_name:
            return None
        # 1. Exact or case-insensitive name with role='Donor'
        u = conn.execute(
            "SELECT email FROM users WHERE LOWER(name) = LOWER(?) AND role = 'Donor' ORDER BY id DESC LIMIT 1",
            (seller_name.strip(),),
        ).fetchone()
        if u and u["email"]:
            return u["email"].strip()

        # 2. By phone if provided
        if phone:
            u = conn.execute(
                "SELECT email FROM users WHERE phone = ? ORDER BY id DESC LIMIT 1",
                (phone.strip(),),
            ).fetchone()
            if u and u["email"]:
                return u["email"].strip()

        # 3. Any role with matching name
        u = conn.execute(
            "SELECT email FROM users WHERE LOWER(name) = LOWER(?) ORDER BY id DESC LIMIT 1",
            (seller_name.strip(),),
        ).fetchone()
        if u and u["email"]:
            return u["email"].strip()

        # 4. Check if seller_name itself is an email
        if "@" in str(seller_name):
            return seller_name.strip()

        return None
    except Exception as e:
        app.logger.error(f"Error finding donor email: {e}")
        return None
    finally:
        if close_conn:
            conn.close()


def find_requester_email(req, conn=None):
    """Find receiver/requester email with robust multi-tiered fallbacks."""
    close_conn = False
    if conn is None:
        conn = get_db()
        close_conn = True
    try:
        if not req:
            return None

        # 1. Check if user_email is stored on the request row
        if "user_email" in req.keys() and req["user_email"] and "@" in str(req["user_email"]):
            return req["user_email"].strip()

        req_name = (req["name"] or req["user_name"] or "").strip()
        req_phone = (req["phone"] or "").strip()

        # 2. By name with role='Receiver'
        if req_name:
            u = conn.execute(
                "SELECT email FROM users WHERE (LOWER(name) = LOWER(?) OR LOWER(name) = LOWER(?)) AND role = 'Receiver' ORDER BY id DESC LIMIT 1",
                (req_name, (req["user_name"] or req_name).strip()),
            ).fetchone()
            if u and u["email"]:
                return u["email"].strip()

        # 3. By phone
        if req_phone:
            u = conn.execute(
                "SELECT email FROM users WHERE phone = ? ORDER BY id DESC LIMIT 1",
                (req_phone,),
            ).fetchone()
            if u and u["email"]:
                return u["email"].strip()

        # 4. By name across all roles
        if req_name:
            u = conn.execute(
                "SELECT email FROM users WHERE LOWER(name) = LOWER(?) OR LOWER(name) = LOWER(?) ORDER BY id DESC LIMIT 1",
                (req_name, (req["user_name"] or req_name).strip()),
            ).fetchone()
            if u and u["email"]:
                return u["email"].strip()

        # 5. Check if user_name is formatted as email
        if req["user_name"] and "@" in str(req["user_name"]):
            return req["user_name"].strip()

        return None
    except Exception as e:
        app.logger.error(f"Error finding requester email: {e}")
        return None
    finally:
        if close_conn:
            conn.close()


def notify_donor_on_pickup_action(food, action, vol_name=None, conn=None):
    """Send beautiful email notification to the donor whenever a volunteer or admin takes an action."""
    if not food:
        return

    phone = food["phone"] if ("phone" in food.keys() and food["phone"]) else None
    donor_email = find_donor_email(food["seller_name"], phone, conn)
    if not donor_email:
        app.logger.warning(f"Could not find email for donor: {food['seller_name']}")
        return

    donor_name = food["seller_name"] or "Generous Donor"
    food_name = food["food_name"]
    qty_str = food["quantity"]
    food_code = food["food_code"] or f"FOOD-{1000 + food['id']}"
    location = food["location"] or "Your specified pickup address"
    vol_display = vol_name or "An approved AnnaPath volunteer"

    if action == "start":
        subject = f"AnnaPath: Volunteer Assigned for Your Food Donation (#{food_code})"
        heading = "Pickup Accepted & Volunteer On The Way!"
        message_body = f"""
        <p>Dear <b>{donor_name}</b>,</p>
        <p>Great news! Volunteer <b>{vol_display}</b> has accepted to collect your food donation and is heading towards your pickup location.</p>
        <div style="background: #E8F5E9; border-left: 5px solid #2E7D32; padding: 15px 20px; border-radius: 8px; margin: 20px 0;">
            <p style="margin: 5px 0;"><b>Donation ID:</b> #{food_code}</p>
            <p style="margin: 5px 0;"><b>Donated Food:</b> {food_name} ({qty_str})</p>
            <p style="margin: 5px 0;"><b>Pickup Address:</b> {location}</p>
            <p style="margin: 5px 0;"><b>Assigned Volunteer:</b> {vol_display}</p>
            <p style="margin: 5px 0;"><b>Status:</b> <span style="color: #2E7D32; font-weight: bold;">Active Pickup / En Route</span></p>
        </div>
        <p>Please keep the food packed safely and ready for handover. Thank you for your wonderful kindness!</p>
        """
        text_body = f"Hello {donor_name}, volunteer {vol_display} has accepted to pick up your donation of {food_name} ({qty_str}) at {location}."

    elif action == "complete":
        subject = f"AnnaPath: Food Donation Successfully Collected (#{food_code})"
        heading = "Thank You! Your Donation Has Been Collected"
        message_body = f"""
        <p>Dear <b>{donor_name}</b>,</p>
        <p>Volunteer <b>{vol_display}</b> has successfully picked up and completed your food donation of <b>{food_name} ({qty_str})</b>.</p>
        <div style="background: #E8F5E9; border-left: 5px solid #2E7D32; padding: 15px 20px; border-radius: 8px; margin: 20px 0;">
            <p style="margin: 5px 0;"><b>Donation ID:</b> #{food_code}</p>
            <p style="margin: 5px 0;"><b>Food Collected:</b> {food_name} ({qty_str})</p>
            <p style="margin: 5px 0;"><b>Collected By:</b> {vol_display}</p>
            <p style="margin: 5px 0;"><b>Status:</b> <span style="color: #2E7D32; font-weight: bold;">Complete Pickup (Added to Live Inventory)</span></p>
        </div>
        <p>Your contribution directly supports hungry individuals and families in our community. Every meal shared brings hope!</p>
        """
        text_body = f"Hello {donor_name}, your food donation #{food_code} ({food_name} - {qty_str}) has been successfully collected by volunteer {vol_display}. Thank you for your generosity!"

    elif action == "cancel":
        subject = f"AnnaPath: Update on Food Donation Pickup (#{food_code})"
        heading = "Donation Pickup Status Update"
        message_body = f"""
        <p>Dear <b>{donor_name}</b>,</p>
        <p>The volunteer previously assigned to your food donation of <b>{food_name} ({qty_str})</b> had to release their pickup task.</p>
        <div style="background: #FFFDE7; border-left: 5px solid #F57F17; padding: 15px 20px; border-radius: 8px; margin: 20px 0;">
            <p style="margin: 5px 0;"><b>Donation ID:</b> #{food_code}</p>
            <p style="margin: 5px 0;"><b>Status:</b> <span style="color: #F57F17; font-weight: bold;">Re-listed as Pending Pickup</span></p>
        </div>
        <p>Your donation is immediately available for other volunteers in your area to claim. You do not need to take any action; we will notify you when a new volunteer accepts!</p>
        """
        text_body = f"Hello {donor_name}, the pickup for your donation #{food_code} ({food_name}) was reset to Pending Pickup and is available for other volunteers."

    else:
        subject = f"AnnaPath: Food Donation Update (#{food_code})"
        heading = "Food Donation Status Update"
        message_body = f"""
        <p>Dear <b>{donor_name}</b>,</p>
        <p>Your food donation #{food_code} ({food_name}) status has been updated to <b>{action}</b>.</p>
        <div style="background: #F5F5F5; border-left: 5px solid #666; padding: 15px 20px; border-radius: 8px; margin: 20px 0;">
            <p style="margin: 5px 0;"><b>Donation ID:</b> #{food_code}</p>
            <p style="margin: 5px 0;"><b>Current Status:</b> {action}</p>
        </div>
        """
        text_body = f"Hello {donor_name}, your food donation #{food_code} status has been updated to {action}."

    html_email = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 25px; border: 2px solid #2E7D32; border-radius: 12px; background: #ffffff;">
        <div style="text-align: center; margin-bottom: 20px; border-bottom: 2px solid #E8F5E9; padding-bottom: 15px;">
            <h1 style="color: #2E7D32; margin: 0; font-size: 26px;">AnnaPath</h1>
            <p style="color: #666; font-size: 13px; margin: 4px 0 0 0;">Food Sharing Network & Hunger Relief</p>
        </div>

        <h2 style="color: #1B5E20; margin-top: 0; font-size: 20px;">{heading}</h2>
        {message_body}

        <div style="text-align: center; margin: 25px 0;">
            <a href="http://127.0.0.1:5000/donor" style="background: #2E7D32; color: #ffffff; padding: 12px 26px; text-decoration: none; font-weight: bold; border-radius: 8px; font-size: 15px; display: inline-block;">
                View Donor Portal &rarr;
            </a>
        </div>

        <p style="color: #888; font-size: 12px; border-top: 1px solid #eee; padding-top: 15px; margin-top: 25px;">
            Thank you for fighting hunger with AnnaPath.<br>
            If you need assistance, please contact <a href="mailto:support@annapath.org" style="color: #2E7D32;">support@annapath.org</a>.
        </p>
    </div>
    """

    notify_user(donor_email, subject, text_body, html_email)


def notify_requester_on_status_change(req, new_status, vol_name=None, conn=None):
    """Send beautiful email notification to the person who requested the food."""
    if not req:
        return

    requester_email = find_requester_email(req, conn)
    if not requester_email:
        app.logger.warning(f"Could not find email for request #{req['id']} (Name: {req['name'] or req['user_name']})")
        return

    requester_name = req["name"] or req["user_name"] or "Friend"
    food_name = req["food_name"] or "Food Package"
    qty_str = req["quantity"] or "1 order"
    address = req["address"] or "Your specified address"
    vol_display = vol_name or "A dedicated AnnaPath volunteer"
    req_code = f"#REQ-{1000 + req['id']}"

    if new_status == "Accepted":
        subject = f"AnnaPath: Volunteer Accepted Your Food Request ({req_code})"
        heading = "Great News! Your Food Request Has Been Accepted"
        message_body = f"""
        <p>Dear <b>{requester_name}</b>,</p>
        <p>Volunteer <b>{vol_display}</b> has accepted your food delivery request and is now coordinating to pick up the meal!</p>
        <div style="background: #E1F5FE; border-left: 5px solid #0288D1; padding: 15px 20px; border-radius: 8px; margin: 20px 0;">
            <p style="margin: 5px 0;"><b>Request ID:</b> {req_code}</p>
            <p style="margin: 5px 0;"><b>Food Item:</b> {food_name} ({qty_str})</p>
            <p style="margin: 5px 0;"><b>Delivery Destination:</b> {address}</p>
            <p style="margin: 5px 0;"><b>Assigned Volunteer:</b> {vol_display}</p>
            <p style="margin: 5px 0;"><b>Status:</b> <span style="color: #0288D1; font-weight: bold;">Volunteer Accepted</span></p>
        </div>
        <p>You will receive another update as soon as the volunteer picks up the food and is en route to your address.</p>
        """
        text_body = f"Hello {requester_name}, volunteer {vol_display} has accepted your food request for {food_name} ({qty_str}) for delivery to {address}."

    elif new_status in ["Food Picked Up", "Picked Up"]:
        subject = f"AnnaPath: Your Food is On the Way! ({req_code})"
        heading = "Food Picked Up — On Delivery Route!"
        message_body = f"""
        <p>Dear <b>{requester_name}</b>,</p>
        <p>Volunteer <b>{vol_display}</b> has picked up your food and is now on the delivery route to your address!</p>
        <div style="background: #E3F2FD; border-left: 5px solid #1976D2; padding: 15px 20px; border-radius: 8px; margin: 20px 0;">
            <p style="margin: 5px 0;"><b>Request ID:</b> {req_code}</p>
            <p style="margin: 5px 0;"><b>Food Item:</b> {food_name} ({qty_str})</p>
            <p style="margin: 5px 0;"><b>Destination:</b> {address}</p>
            <p style="margin: 5px 0;"><b>Delivering Volunteer:</b> {vol_display}</p>
            <p style="margin: 5px 0;"><b>Status:</b> <span style="color: #1976D2; font-weight: bold;">On Delivery Route</span></p>
        </div>
        <p>Please make sure someone is available at the address or reachable by phone to receive your meal safely.</p>
        """
        text_body = f"Hello {requester_name}, volunteer {vol_display} has picked up your food ({food_name}) and is on the way to {address}."

    elif new_status == "Delivered":
        subject = f"AnnaPath: Food Delivery Completed ({req_code})"
        heading = "Your Food Has Been Delivered! Enjoy Your Meal"
        message_body = f"""
        <p>Dear <b>{requester_name}</b>,</p>
        <p>Your food delivery for <b>{food_name} ({qty_str})</b> has been successfully completed by volunteer <b>{vol_display}</b>!</p>
        <div style="background: #E8F5E9; border-left: 5px solid #2E7D32; padding: 15px 20px; border-radius: 8px; margin: 20px 0;">
            <p style="margin: 5px 0;"><b>Request ID:</b> {req_code}</p>
            <p style="margin: 5px 0;"><b>Food Item:</b> {food_name} ({qty_str})</p>
            <p style="margin: 5px 0;"><b>Delivered To:</b> {address}</p>
            <p style="margin: 5px 0;"><b>Delivered By:</b> {vol_display}</p>
            <p style="margin: 5px 0;"><b>Status:</b> <span style="color: #2E7D32; font-weight: bold;">Delivered Successfully</span></p>
        </div>
        <p>We are delighted to have served you today. Thank you for being a part of AnnaPath's mission to eliminate hunger and food waste!</p>
        """
        text_body = f"Hello {requester_name}, your food delivery for {food_name} ({qty_str}) has been marked as Delivered by volunteer {vol_display}. Enjoy your meal!"

    elif new_status == "Pending":
        subject = f"AnnaPath: Food Request Status Update ({req_code})"
        heading = "Delivery Assignment Update"
        message_body = f"""
        <p>Dear <b>{requester_name}</b>,</p>
        <p>The volunteer previously assigned to your food request had to release the delivery task. Your request has been automatically returned to the active queue for other volunteers to claim.</p>
        <div style="background: #FFFDE7; border-left: 5px solid #F57F17; padding: 15px 20px; border-radius: 8px; margin: 20px 0;">
            <p style="margin: 5px 0;"><b>Request ID:</b> {req_code}</p>
            <p style="margin: 5px 0;"><b>Food Item:</b> {food_name} ({qty_str})</p>
            <p style="margin: 5px 0;"><b>Status:</b> <span style="color: #F57F17; font-weight: bold;">Pending Volunteer Claim</span></p>
        </div>
        <p>We are actively alerting volunteers in your area and will notify you as soon as someone accepts!</p>
        """
        text_body = f"Hello {requester_name}, your request for {food_name} has been reset to Pending Volunteer Claim. We will notify you when a volunteer accepts."

    elif new_status in ["Rejected", "Cancelled"]:
        subject = f"AnnaPath: Food Request {new_status} ({req_code})"
        heading = f"Food Request {new_status}"
        message_body = f"""
        <p>Dear <b>{requester_name}</b>,</p>
        <p>Your food request <b>{req_code}</b> for <b>{food_name} ({qty_str})</b> has been marked as <b>{new_status}</b>.</p>
        <div style="background: #FFEBEE; border-left: 5px solid #D32F2F; padding: 15px 20px; border-radius: 8px; margin: 20px 0;">
            <p style="margin: 5px 0;"><b>Request ID:</b> {req_code}</p>
            <p style="margin: 5px 0;"><b>Food Item:</b> {food_name} ({qty_str})</p>
            <p style="margin: 5px 0;"><b>Current Status:</b> <span style="color: #D32F2F; font-weight: bold;">{new_status}</span></p>
        </div>
        <p>If you have any questions or would like to submit a new request, please visit the AnnaPath Receiver Portal.</p>
        """
        text_body = f"Hello {requester_name}, your food request {req_code} for {food_name} has been marked as {new_status}."

    else:
        subject = f"AnnaPath: Food Request Update ({req_code})"
        heading = "Update on Your Food Request"
        message_body = f"""
        <p>Dear <b>{requester_name}</b>,</p>
        <p>Your food request <b>{req_code}</b> for <b>{food_name} ({qty_str})</b> has been updated to <b>{new_status}</b>.</p>
        <div style="background: #F5F5F5; border-left: 5px solid #757575; padding: 15px 20px; border-radius: 8px; margin: 20px 0;">
            <p style="margin: 5px 0;"><b>Request ID:</b> {req_code}</p>
            <p style="margin: 5px 0;"><b>Current Status:</b> {new_status}</p>
        </div>
        """
        text_body = f"Hello {requester_name}, your food request {req_code} for {food_name} has been updated to {new_status}."

    html_email = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 25px; border: 2px solid #2E7D32; border-radius: 12px; background: #ffffff;">
        <div style="text-align: center; margin-bottom: 20px; border-bottom: 2px solid #E8F5E9; padding-bottom: 15px;">
            <h1 style="color: #2E7D32; margin: 0; font-size: 26px;">AnnaPath</h1>
            <p style="color: #666; font-size: 13px; margin: 4px 0 0 0;">Food Sharing Network & Hunger Relief</p>
        </div>

        <h2 style="color: #1B5E20; margin-top: 0; font-size: 20px;">{heading}</h2>
        {message_body}

        <div style="text-align: center; margin: 25px 0;">
            <a href="http://127.0.0.1:5000/tracking" style="background: #2E7D32; color: #ffffff; padding: 12px 26px; text-decoration: none; font-weight: bold; border-radius: 8px; font-size: 15px; display: inline-block;">
                Track Live Order Status &rarr;
            </a>
        </div>

        <p style="color: #888; font-size: 12px; border-top: 1px solid #eee; padding-top: 15px; margin-top: 25px;">
            Thank you for being part of the AnnaPath community.<br>
            If you need assistance, please contact <a href="mailto:support@annapath.org" style="color: #2E7D32;">support@annapath.org</a>.
        </p>
    </div>
    """

    notify_user(requester_email, subject, text_body, html_email)


# --- Session Validation: verify session user still exists in DB ---


@app.before_request
def validate_session():
    """On every request, verify the logged-in user still exists in the database.
    If the user was deleted or the session is stale, clear it immediately."""
    user_id = session.get("user_id")
    if user_id:
        try:
            conn = get_db()
            user = conn.execute(
                "SELECT id, role FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            conn.close()
            if not user:
                session.clear()
        except Exception:
            session.clear()


# --- Authentication & Role Access Decorators ---


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("home"))
        return f(*args, **kwargs)

    return decorated_function


def role_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user_role = session.get("role")
            if not user_role or (
                user_role not in allowed_roles and user_role != "Admin"
            ):
                target_role = allowed_roles[0]
                if not session.get("user_id"):
                    flash(
                        f"Please sign in as {target_role} to access this portal.",
                        "warning",
                    )
                else:
                    flash(
                        f"Access restricted. Please sign in with a {target_role} account.",
                        "warning",
                    )
                return redirect(url_for("login", role=target_role))
            return f(*args, **kwargs)

        return decorated_function

    return decorator


# --- Public / Role Selection Screen ---


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/switch_role/<role>")
def switch_role(role):
    """Switch active session: requires logging in with that role's credentials."""
    if session.get("role") == role and session.get("user_id"):
        if role == "Donor":
            return redirect(url_for("donor_portal"))
        elif role == "Receiver":
            return redirect(url_for("receiver_portal"))
        elif role == "Volunteer":
            return redirect(url_for("volunteer"))
        elif role == "Admin":
            return redirect(url_for("admin"))
        return redirect(url_for("home"))

    # Need login for the requested role
    if session.get("user_id") and session.get("role") != role:
        flash(
            f"Please sign in with your {role} credentials to switch roles.",
            "info")
    else:
        flash(f"Please sign in as {role} to continue.", "info")

    return redirect(url_for("login", role=role))


# --- Authentication Routes ---


@app.route("/api/send_otp", methods=["POST"])
def api_send_otp():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()
    password = request.form.get("password", "").strip()
    role = request.form.get("role", "Donor").strip()

    if role == "Admin":
        return {
            "success": False,
            "message": "Admin accounts cannot be registered here.",
        }

    conn = get_db()
    try:
        existing_user = conn.execute(
            "SELECT role FROM users WHERE email = ?", (email,)
        ).fetchone()
        if existing_user:
            return {
                "success": False,
                "message": "This email is already registered. Please sign in.",
            }

        otp = str(secrets.randbelow(900000) + 100000)

        conn.execute(
            """
            REPLACE INTO pending_registrations (email, name, phone, password, role, otp, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now', '+10 minutes'))
        """,
            (email, name, phone, password, role, otp),
        )
        conn.commit()

        subject = "AnnaPath: Verify Your Registration"
        html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 25px; border: 1px solid #4CAF50; border-radius: 10px;">
            <h2 style="color: #2E7D32; margin-top: 0;">AnnaPath Food Sharing Network</h2>
            <p>Dear <b>{name}</b>,</p>
            <p>Your One-Time Password (OTP) for completing your {role} registration is:</p>
            <div style="font-size: 24px; font-weight: bold; letter-spacing: 5px; color: #1B5E20; text-align: center; margin: 20px 0; background: #E8F5E9; padding: 15px; border-radius: 8px;">
                {otp}
            </div>
            <p>If you did not request this, please ignore this email.</p>
            <p>Best regards,<br><b>The AnnaPath Team</b></p>
        </div>
        """

        def send_async_email(app_context, *args):
            with app_context:
                send_email_notification(*args)

        threading.Thread(
            target=send_async_email,
            args=(
                app.app_context(),
                email,
                subject,
                html,
                f"Your OTP is: {otp}"),
        ).start()

        return {"success": True, "message": f"OTP sent to {email}"}
    except Exception as e:
        return {"success": False, "message": str(e)}
    finally:
        conn.close()


@app.route("/api/forgot_password/firebase_send_reset", methods=["POST"])
def api_forgot_password_firebase_send_reset():
    email = request.form.get("email", "").strip()
    if not email:
        return {"success": False, "message": "Email is required."}

    try:
        api_key = getattr(
            config,
            "FIREBASE_API_KEY",
            "AIzaSyC5qZAO5HX2ddE0mKFzVtIAxEEmvJh-jR8",
        )
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={api_key}"
        payload = json.dumps({
            "requestType": "PASSWORD_RESET",
            "email": email,
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                return {
                    "success": True,
                    "message": f"Password reset link sent to {email}! Please check your Inbox and Spam folder.",
                }
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        try:
            err_json = json.loads(err_body)
            err_code = err_json.get("error", {}).get("message", "")
            if err_code == "EMAIL_NOT_FOUND":
                msg = "No account found in Firebase with this email address. Make sure you registered with this email."
            elif err_code == "INVALID_EMAIL":
                msg = "The email address format is invalid."
            else:
                msg = f"Firebase error: {err_code}"
            return {"success": False, "message": msg}
        except Exception:
            return {"success": False, "message": f"Firebase request failed ({e.code})"}
    except Exception as e:
        return {"success": False, "message": str(e)}

    return {"success": False, "message": "Failed to send reset email."}


@app.route("/api/forgot_password/send_otp", methods=["POST"])
def api_forgot_password_send_otp():
    email = request.form.get("email", "").strip()

    if not email:
        return {"success": False, "message": "Email is required."}

    conn = get_db()
    try:
        # Check if user exists in SQLite
        existing_user = conn.execute(
            "SELECT role FROM users WHERE email = ?", (email,)
        ).fetchone()

        # If not in SQLite, check Firebase (as admins might only be in Firebase
        # or we might be migrating)
        if not existing_user:
            try:
                user = auth.get_user_by_email(email)
                if not user:
                    return {
                        "success": False,
                        "message": "No account found with this email.",
                    }
            except Exception:
                return {
                    "success": False,
                    "message": "No account found with this email.",
                }

        otp = str(secrets.randbelow(900000) + 100000)

        conn.execute(
            """
            REPLACE INTO password_resets (email, otp, expires_at)
            VALUES (?, ?, datetime('now', '+10 minutes'))
        """,
            (email, otp),
        )
        conn.commit()

        subject = "AnnaPath: Password Reset OTP"
        html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 25px; border: 1px solid #4CAF50; border-radius: 10px;">
            <h2 style="color: #2E7D32; margin-top: 0;">AnnaPath Security</h2>
            <p>We received a request to reset your password.</p>
            <p>Your One-Time Password (OTP) is:</p>
            <div style="font-size: 24px; font-weight: bold; letter-spacing: 5px; color: #1B5E20; text-align: center; margin: 20px 0; background: #E8F5E9; padding: 15px; border-radius: 8px;">
                {otp}
            </div>
            <p>If you did not request this, please ignore this email and your password will remain unchanged.</p>
            <p>Best regards,<br><b>The AnnaPath Team</b></p>
        </div>
        """

        def send_async_email(app_context, *args):
            with app_context:
                send_email_notification(*args)

        threading.Thread(
            target=send_async_email,
            args=(
                app.app_context(),
                email,
                subject,
                html,
                f"Your OTP is: {otp}"),
        ).start()

        return {"success": True, "message": "OTP sent to your email address."}
    except Exception as e:
        return {"success": False, "message": f"Server error: {str(e)}"}
    finally:
        conn.close()


@app.route("/api/forgot_password/verify_and_reset", methods=["POST"])
def api_forgot_password_verify_and_reset():
    email = request.form.get("email", "").strip()
    otp = request.form.get("otp", "").strip()
    new_password = request.form.get("new_password", "").strip()

    if not email or not otp or not new_password:
        return {"success": False, "message": "Missing required fields."}

    conn = get_db()
    try:
        record = conn.execute(
            "SELECT otp, expires_at FROM password_resets WHERE email = ?", (email,)
        ).fetchone()

        if not record:
            return {
                "success": False,
                "message": "No OTP request found for this email."}

        stored_otp, expires_at = record

        if datetime.strptime(expires_at,
                             "%Y-%m-%d %H:%M:%S") < datetime.utcnow():
            return {
                "success": False,
                "message": "OTP has expired. Please request a new one.",
            }

        if stored_otp != otp:
            return {
                "success": False,
                "message": "Invalid OTP. Please try again."}

        # Update password in SQLite
        conn.execute(
            "UPDATE users SET password = ? WHERE email = ?",
            (new_password,
             email))
        conn.commit()

        # Update password in Firebase Auth (if exists)
        try:
            user = auth.get_user_by_email(email)
            if user:
                auth.update_user(user.uid, password=new_password)
        except Exception as e:
            pass

        # Update password in Firestore
        try:
            # We need to find their role to update the right collection
            user_row = conn.execute(
                "SELECT role FROM users WHERE email = ?", (email,)
            ).fetchone()
            if user_row:
                role = user_row["role"]
                if role == "Admin":
                    db.collection("users").document("roles").collection(
                        "admins"
                    ).document(email).update({"password": new_password})
                else:
                    collection_name = role.lower() + "s"
                    db.collection("users").document("roles").collection(
                        collection_name
                    ).document(email).update({"password": new_password})
        except Exception as e:
            pass

        # Clear the reset request
        conn.execute("DELETE FROM password_resets WHERE email = ?", (email,))
        conn.commit()

        return {
            "success": True,
            "message": "Password reset successful! You can now log in.",
        }
    except Exception as e:
        return {"success": False, "message": f"Server error: {str(e)}"}
    finally:
        conn.close()


@app.route("/register", methods=["GET", "POST"])
def register():
    selected_role = request.args.get("role", "Donor")
    if selected_role == "Admin":
        flash(
            "Admin registration is not available. Please sign in with your admin credentials.",
            "info",
        )
        return redirect(url_for("login", role="Admin"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        entered_otp = request.form.get("otp", "").strip()
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "").strip()
        role = request.form.get("role", selected_role).strip()

        conn = get_db()
        try:
            if entered_otp:
                pending = conn.execute(
                    "SELECT * FROM pending_registrations WHERE email = ?", (email,)
                ).fetchone()
                if not pending:
                    flash(
                        "No pending registration found for this email.",
                        "warning",
                    )
                    return redirect(url_for("register", role=selected_role))

                if entered_otp != pending["otp"]:
                    flash("Invalid OTP. Please try again.", "danger")
                    return redirect(url_for("register", role=selected_role))

                name = pending["name"]
                phone = pending["phone"]
                password = pending["password"]
                role = pending["role"]
            else:
                if not email or not password or not name:
                    flash("Please fill in all required fields.", "warning")
                    return redirect(url_for("register", role=selected_role))

            existing_user = conn.execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            ).fetchone()
            if existing_user:
                flash("An account with this email already exists. Please sign in.", "warning")
                conn.close()
                return redirect(url_for("login", role=role))

            try:
                conn.execute(
                    "INSERT INTO users (name, email, phone, password, role) VALUES (?, ?, ?, ?, ?)",
                    (name, email, phone, password, role),
                )

                # Push to Firebase Firestore Table (Choice B)
                try:
                    collection_name = (
                        role.lower() + "s"
                    )  # e.g., 'donors', 'receivers', 'volunteers'
                    db.collection("users").document("roles").collection(
                        collection_name
                    ).document(email).set(
                        {
                            "name": name,
                            "email": email,
                            "phone": phone,
                            "role": role,
                            "created_at": firestore.SERVER_TIMESTAMP,
                        }
                    )
                except Exception as e:
                    print("Firebase Firestore Sync Error:", e)

                if role == "Volunteer":
                    existing_vol = conn.execute(
                        "SELECT id FROM volunteers WHERE email = ? OR (phone = ? AND phone != '')",
                        (email, phone),
                    ).fetchone()
                    if existing_vol:
                        conn.execute(
                            "UPDATE volunteers SET name = ?, phone = ?, password = ? WHERE id = ?",
                            (name, phone, password, existing_vol["id"]),
                        )
                    else:
                        conn.execute(
                            """INSERT INTO volunteers (name, email, phone, area, time, password, status)
                               VALUES (?, ?, ?, 'All Areas', 'Flexible', ?, 'Pending Approval')""",
                            (name, email, phone, password),
                        )

                conn.execute(
                    "DELETE FROM pending_registrations WHERE email = ?", (email,))
                conn.commit()

                # Auto-login after registration
                user = conn.execute(
                    "SELECT * FROM users WHERE email = ?", (email,)
                ).fetchone()
                if user:
                    session.clear()
                    session.permanent = True
                    session["user_id"] = user["id"]
                    session["user_name"] = user["name"] or "User"
                    session["role"] = user["role"]
                    session["email"] = user["email"]
                    session["phone"] = (
                        user["phone"]
                        if ("phone" in user.keys() and user["phone"])
                        else ""
                    )

                flash(
                    f"Account created successfully as {role}! Welcome, {name}.",
                    "success",
                )

                if role == "Donor":
                    return redirect(url_for("donor_portal"))
                elif role == "Receiver":
                    return redirect(url_for("receiver_portal"))
                elif role == "Volunteer":
                    return redirect(url_for("volunteer"))
                elif role == "Admin":
                    return redirect(url_for("admin"))
                return redirect(url_for("home"))

            except sqlite3.IntegrityError:
                flash(
                    "This email is already registered. Please sign in with your existing account.",
                    "danger",
                )
                return redirect(url_for("login", role=role))
        finally:
            conn.close()

    return render_template("register.html", selected_role=selected_role)


@app.route("/login", methods=["GET", "POST"])
def login():
    selected_role = request.args.get("role", "Donor")

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "").strip()
        role = request.form.get("role", "").strip()

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE email = ? AND password = ? AND role = ?",
            (email, password, role),
        ).fetchone()

        if not user:
            # Check if user exists but reset their password in Firebase Auth
            existing_user = conn.execute(
                "SELECT * FROM users WHERE email = ? AND role = ?",
                (email, role),
            ).fetchone()
            if existing_user and verify_firebase_credentials(email, password):
                conn.execute(
                    "UPDATE users SET password = ? WHERE email = ?",
                    (password, email),
                )
                conn.commit()
                try:
                    col_name = "admins" if role == "Admin" else f"{role.lower()}s"
                    db.collection("users").document("roles").collection(
                        col_name
                    ).document(email).update({"password": password})
                except Exception:
                    pass
                user = conn.execute(
                    "SELECT * FROM users WHERE email = ? AND password = ? AND role = ?",
                    (email, password, role),
                ).fetchone()

        conn.close()

        if user:
            # Valid credentials: clear previous session and authenticate
            session.clear()
            session.permanent = True
            session["user_id"] = user["id"]
            session["user_name"] = user["name"] or "User"
            session["role"] = user["role"]
            session["email"] = user["email"]
            session["phone"] = (
                user["phone"] if (
                    "phone" in user.keys() and user["phone"]) else "")

            # Check if user selected a different role in the dropdown (Admin is
            # always unrestricted)
            if role and user["role"] != "Admin" and user["role"] != role:
                flash(
                    f"Welcome back, {user['name']}! Note: Signed in to your registered {user['role']} account.",
                    "info",
                )
            else:
                flash(
                    f"Welcome back, {user['name']}! Signed in as {user['role']}.",
                    "success",
                )

            if user["role"] == "Donor":
                return redirect(url_for("donor_portal"))
            elif user["role"] == "Receiver":
                return redirect(url_for("receiver_portal"))
            elif user["role"] == "Volunteer":
                return redirect(url_for("volunteer"))
            elif user["role"] == "Admin":
                return redirect(url_for("admin"))
            return redirect(url_for("home"))
        else:
            flash("Invalid credentials. Please try again.", "danger")
            return render_template(
                "login.html", selected_role=role or selected_role, email=email
            )

    return render_template("login.html", selected_role=selected_role, email="")


@app.route("/demo_login/<role>")
def demo_login(role):
    flash("Please sign in with your credentials.", "info")
    return redirect(url_for("login", role=role))


@app.route("/logout")
def logout():
    session.clear()
    session.modified = True
    flash("You have been logged out successfully.", "info")
    response = redirect(url_for("home"))
    response.delete_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
    return response


@app.route("/dashboard")
@login_required
def dashboard():
    user_role = session.get("role")
    if user_role == "Donor":
        return redirect(url_for("donor_portal"))
    elif user_role == "Receiver":
        return redirect(url_for("receiver_portal"))
    elif user_role == "Volunteer":
        return redirect(url_for("volunteer"))
    elif user_role == "Admin":
        return redirect(url_for("admin"))
    return render_template("dashboard.html")


# --- Role 1: Food Donor Portal & Actions ---


@app.route("/donor")
@role_required("Donor")
def donor_portal():
    conn = get_db()
    foods = conn.execute("SELECT * FROM food ORDER BY id DESC").fetchall()
    conn.close()

    available_count = sum(
        1
        for f in foods
        if f["status"] in ("Available", "Complete Pickup")
        and (f["available_quantity"] or 0) > 0
    )
    total_donations = len(foods)
    pending_pickups = sum(
        1 for f in foods if f["status"] in (
            "Pending Pickup",
            "Awaiting Pickup"))
    active_pickups = sum(1 for f in foods if f["status"] == "Active Pickup")

    return render_template(
        "donor_portal.html",
        foods=foods,
        available_count=available_count,
        total_donations=total_donations,
        pending_pickups=pending_pickups,
        active_pickups=active_pickups,
    )


@app.route("/add_food", methods=["GET", "POST"])
@role_required("Donor")
def add_food():
    conn = get_db()
    try:
        donor_name = session.get("user_name", "")
        donor_phone = session.get("phone", "")

        if session.get("user_id"):
            user = conn.execute(
                "SELECT * FROM users WHERE id = ?", (session.get("user_id"),)
            ).fetchone()
            if user:
                donor_name = user["name"] or donor_name
                donor_phone = user["phone"] or donor_phone
                session["user_name"] = donor_name
                session["phone"] = donor_phone or ""

        if request.method == "POST":
            seller_name = (
                donor_name
                or request.form.get("seller_name", "").strip()
                or "Anonymous Donor"
            )
            phone = donor_phone or request.form.get("phone", "").strip() or ""
            food_name = request.form.get("food_name", "").strip()
            quantity_str = request.form.get("quantity", "").strip()
            food_type = request.form.get("food_type", "Donate").strip()
            price_val = request.form.get("price", "0").strip()
            price = int(price_val) if price_val.isdigit() else 0
            location = request.form.get("location", "").strip()

            qty_num, unit_str = parse_quantity(quantity_str)

            latitude_str = request.form.get("latitude", "").strip()
            longitude_str = request.form.get("longitude", "").strip()
            try:
                latitude = float(latitude_str) if latitude_str else None
                longitude = float(longitude_str) if longitude_str else None
            except ValueError:
                latitude, longitude = None, None

            image_filename = ""
            if "image" in request.files:
                file = request.files["image"]
                if file and file.filename != "" and allowed_file(
                        file.filename):
                    try:
                        ext = file.filename.rsplit(".", 1)[1].lower()
                        unique_name = (
                            f"food_{int(time.time())}_{uuid.uuid4().hex[:8]}.{ext}"
                        )
                        save_path = os.path.join(UPLOAD_FOLDER, unique_name)
                        file.save(save_path)
                        image_filename = unique_name
                    except Exception as e:
                        app.logger.error(f"Error saving image: {e}")
                        image_filename = ""

            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO food (seller_name, phone, food_name, quantity, price, food_type, location, image,
                                     status, initial_quantity, available_quantity, claimed_quantity, unit, latitude, longitude)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending Pickup', ?, ?, 0, ?, ?, ?)""",
                (seller_name,
                 phone,
                 food_name,
                 f"{qty_num} {unit_str}",
                    price,
                    food_type,
                    location,
                    image_filename,
                    qty_num,
                    qty_num,
                    unit_str,
                    latitude,
                    longitude,
                 ),
            )
            new_id = cursor.lastrowid
            food_code = f"FOOD-{1000 + new_id}"
            cursor.execute(
                "UPDATE food SET food_code = ? WHERE id = ?", (food_code, new_id))
            conn.commit()

            # Push to Firebase Firestore Table (foods)
            try:
                db.collection("foods").document(food_code).set(
                    {
                        "id": new_id,
                        "food_code": food_code,
                        "seller_name": seller_name,
                        "phone": phone,
                        "food_name": food_name,
                        "quantity_display": f"{qty_num} {unit_str}",
                        "initial_quantity": qty_num,
                        "available_quantity": qty_num,
                        "claimed_quantity": 0,
                        "unit": unit_str,
                        "price": price,
                        "food_type": food_type,
                        "location": location,
                        "latitude": latitude,
                        "longitude": longitude,
                        "image": image_filename,
                        "status": "Pending Pickup",
                        "created_at": firestore.SERVER_TIMESTAMP,
                    }
                )
            except Exception as e:
                print("Firebase Firestore Sync Error (Food):", e)

            # --- NOTIFY VOLUNTEERS ---
            msg = f"{seller_name} just donated {qty_num} {unit_str} of {food_name} at {location}."
            html_msg = f"<h3>New Donation Request</h3><p><b>{seller_name}</b> has donated <b>{qty_num} {unit_str}</b> of <b>{food_name}</b>.</p><p>Location: {location}</p><p>Please log in to your dashboard to review.</p>"
            notify_role("Volunteer", "New Food Donation", msg, html_msg)

            flash(
                f"Food donation #{food_code} ({qty_num} {unit_str} {food_name}) registered! Status: Pending Pickup by volunteer.",
                "success",
            )
            return redirect(url_for("donor_portal"))

        return render_template(
            "add_food.html", donor_name=donor_name, donor_phone=donor_phone
        )
    finally:
        conn.close()


# --- Role 2: Food Receiver Portal & Actions ---


@app.route("/receiver")
@role_required("Receiver")
def receiver_portal():
    conn = get_db()
    # Available live inventory items
    foods = conn.execute(
        "SELECT * FROM food WHERE status IN ('Complete Pickup', 'Available') AND available_quantity > 0 ORDER BY id DESC"
    ).fetchall()
    requests = conn.execute(
        "SELECT * FROM requests ORDER BY id DESC").fetchall()
    conn.close()
    return render_template(
        "receiver_portal.html",
        foods=foods,
        requests=requests)


@app.route("/request_food", methods=["GET", "POST"])
@role_required("Receiver")
def request_food():
    conn = get_db()
    try:
        if request.method == "POST":
            name = request.form.get("name", "").strip() or session.get(
                "user_name", "Anonymous"
            )
            phone = request.form.get(
                "phone", "").strip() or session.get(
                "phone", "")
            address = request.form.get(
                "address", "").strip() or "Address on file"
            reason = request.form.get("reason", "").strip()
            food_id = request.form.get("food_id")
            quantity_str = request.form.get("quantity", "1").strip()
            req_qty, _ = parse_quantity(quantity_str)

            food = None
            if food_id:
                food = conn.execute(
                    "SELECT * FROM food WHERE id = ?", (food_id,)
                ).fetchone()

            if not food:
                food_item_name = request.form.get("food", "").strip()
                if food_item_name:
                    clean_name = re.sub(r"^\d+\s*", "", food_item_name).strip()
                    food = conn.execute(
                        """SELECT * FROM food
                           WHERE (LOWER(food_name) = LOWER(?) OR LOWER(food_name) = LOWER(?) OR LOWER(food_name) LIKE '%' || LOWER(?) || '%')
                             AND available_quantity > 0
                           ORDER BY CASE WHEN status IN ('Complete Pickup', 'Available') THEN 0 ELSE 1 END, id DESC LIMIT 1""",
                        (food_item_name, clean_name, clean_name),
                    ).fetchone()

            if food:
                avail = (
                    food["available_quantity"]
                    if food["available_quantity"] is not None
                    else 0
                )
                unit = food["unit"] or "items"
                if req_qty > avail:
                    flash(
                        f"Only {avail} {unit} of '{food['food_name']}' available in live stock! Please enter a quantity up to {avail}.",
                        "warning",
                    )
                    return redirect(
                        url_for(
                            "request_food",
                            food_id=food["id"]))

                new_avail = max(0, avail - req_qty)
                new_claimed = (food["claimed_quantity"] or 0) + req_qty
                new_status = (
                    "Out of Stock" if new_avail == 0 else (
                        food["status"] if food["status"] in [
                            "Pending Pickup",
                            "Active Pickup"] else "Complete Pickup"))
                new_qty_str = f"{new_avail} {unit}"

                conn.execute(
                    """UPDATE food
                       SET available_quantity = ?, claimed_quantity = ?, status = ?, quantity = ?
                       WHERE id = ?""",
                    (new_avail, new_claimed, new_status, new_qty_str, food["id"]),
                )

                req_food_name = food["food_name"]
                req_food_code = food["food_code"]
                req_qty_str = f"{req_qty} {unit}"
                food_item_id = food["id"]
            else:
                req_food_name = request.form.get(
                    "food", "").strip() or "Assorted Food"
                req_food_code = None
                req_qty_str = f"{req_qty} items"
                food_item_id = None
                new_avail = None

            latitude_str = request.form.get("latitude", "").strip()
            longitude_str = request.form.get("longitude", "").strip()
            try:
                latitude = float(latitude_str) if latitude_str else None
                longitude = float(longitude_str) if longitude_str else None
            except ValueError:
                latitude, longitude = None, None

            requester_email = session.get("email", "").strip()
            conn.execute(
                """INSERT INTO requests (food_id, food_code, user_name, name, phone, food_name, quantity, address, status, latitude, longitude, user_email)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?, ?, ?)""",
                (food_item_id,
                 req_food_code,
                 name,
                 name,
                 phone,
                 req_food_name,
                 req_qty_str,
                 address,
                 latitude,
                 longitude,
                 requester_email,
                 ),
            )
            conn.commit()

            # --- NOTIFY VOLUNTEERS & ADMIN VIA EMAIL ---
            req_notify_msg = f"New food delivery requested: {name} requested {req_qty_str} of {req_food_name} to {address}."
            req_notify_html = f"<h3>New Delivery Request</h3><p><b>{name}</b> has requested <b>{req_qty_str}</b> of <b>{req_food_name}</b>.</p><p>Delivery Address: {address}</p><p>Contact: {phone}</p>"
            notify_role("Volunteer", "New Food Delivery Request", req_notify_msg, req_notify_html)
            notify_role("Admin", "New Food Delivery Request", req_notify_msg, req_notify_html)

            if food and new_avail is not None:
                flash(
                    f"Food request placed for {req_qty_str} of {req_food_name}! Live inventory updated: {new_avail} {food['unit']} remaining in stock.",
                    "success",
                )
            else:
                flash(
                    "Food request submitted successfully! You can track your request below.",
                    "success",
                )
            return redirect(url_for("tracking"))

        food_id_arg = request.args.get("food_id")
        selected_food = None
        if food_id_arg:
            selected_food = conn.execute(
                "SELECT * FROM food WHERE id = ?", (food_id_arg,)
            ).fetchone()

        available_foods = conn.execute(
            "SELECT * FROM food WHERE status IN ('Complete Pickup', 'Available') AND available_quantity > 0 ORDER BY id DESC"
        ).fetchall()
        return render_template(
            "request_food.html",
            selected_food=selected_food,
            available_foods=available_foods,
        )
    finally:
        conn.close()


# --- Role 3: Volunteer Partner Portal & Actions ---


@app.route("/volunteer", methods=["GET", "POST"])
@role_required("Volunteer")
def volunteer():
    conn = get_db()
    user_email = session.get("email", "")

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()
        area = request.form.get("area", "").strip()
        time_slot = request.form.get("time", "").strip()
        password = request.form.get("password", "").strip() or "volunteer123"

        # Check / Create user account in users table
        user = conn.execute(
            "SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if not user:
            conn.execute(
                "INSERT INTO users (name, email, phone, password, role) VALUES (?, ?, ?, ?, 'Volunteer')",
                (name, email, phone, password),
            )
            conn.commit()
            user = conn.execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            ).fetchone()

        # Check / Insert into volunteers table
        existing_vol = conn.execute(
            "SELECT id FROM volunteers WHERE email = ? OR (phone = ? AND phone != '')",
            (email, phone),
        ).fetchone()

        if existing_vol:
            conn.execute(
                "UPDATE volunteers SET name = ?, phone = ?, area = ?, time = ?, password = ?, status = 'Pending Approval' WHERE id = ?",
                (name,
                 phone,
                 area,
                 time_slot,
                 password,
                 existing_vol["id"]),
            )
        else:
            conn.execute(
                """INSERT INTO volunteers (name, email, phone, area, time, password, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'Pending Approval')""",
                (name, email, phone, area, time_slot, password),
            )
        conn.commit()

        # Log volunteer in to their new profile
        session["user_id"] = user["id"] if user else 999
        session["user_name"] = name
        session["role"] = "Volunteer"
        session["email"] = email
        session["phone"] = phone

        # Send application confirmation email
        subject = "AnnaPath: Volunteer Registration Received (Under Admin Review)"
        html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 25px; border: 1px solid #4CAF50; border-radius: 10px;">
            <h2 style="color: #2E7D32; margin-top: 0;">AnnaPath Food Sharing Network</h2>
            <p>Dear <b>{name}</b>,</p>
            <p>Thank you for offering your time to help fight hunger and reduce food waste!</p>
            <p>Your volunteer application for <b>{area}</b> has been received and forwarded to our <b>Administrator</b> for verification.</p>
            <div style="background: #FFF9C4; border-left: 4px solid #FBC02D; padding: 12px; margin: 15px 0;">
                <b>Application Status:</b> Pending Admin Approval<br>
                Once the administrator approves your request, you will receive an acceptance email and can begin claiming delivery orders!
            </div>
            <p>Best regards,<br><b>The AnnaPath Team</b></p>
        </div>
        """
        send_email_notification(
            email,
            subject,
            html,
            f"Dear {name}, your volunteer application is under review.",
        )

        flash(
            "Volunteer application submitted! Your request is now pending Admin approval.",
            "success",
        )
        return redirect(url_for("volunteer"))

    # Fetch current user's volunteer profile if logged in
    my_vol = None
    if session.get("role") == "Volunteer":
        if user_email:
            my_vol = conn.execute(
                "SELECT * FROM volunteers WHERE email = ? ORDER BY id DESC",
                (user_email,),
            ).fetchone()
        if not my_vol and session.get("user_name"):
            my_vol = conn.execute(
                "SELECT * FROM volunteers WHERE name = ? ORDER BY id DESC",
                (session.get("user_name"),),
            ).fetchone()

    requests_list = []
    pickups = []
    completed_pickups = []
    if (my_vol and my_vol["status"] in ["Approved", "Available",
                                        "Active On Route"]) or session.get("role") == "Admin":
        requests_list = conn.execute(
            "SELECT * FROM requests ORDER BY id DESC"
        ).fetchall()
        # Pending and Active Pickups for volunteers to action
        pickups = conn.execute("""SELECT * FROM food
               WHERE status IN ('Pending Pickup', 'Active Pickup', 'Awaiting Pickup')
               ORDER BY CASE status
                   WHEN 'Active Pickup' THEN 1
                   WHEN 'Pending Pickup' THEN 2
                   ELSE 3 END, id DESC""").fetchall()
        # Completed pickups history
        completed_pickups = conn.execute(
            "SELECT * FROM food WHERE status IN ('Complete Pickup', 'Available') ORDER BY id DESC LIMIT 15"
        ).fetchall()

    pending_count = sum(
        1 for p in pickups if p["status"] in [
            "Pending Pickup",
            "Awaiting Pickup"])
    active_count = sum(1 for p in pickups if p["status"] == "Active Pickup")
    completed_count = len(completed_pickups)

    pending_delivery_count = sum(
        1 for r in requests_list if r["status"] == "Pending")
    active_delivery_count = sum(
        1
        for r in requests_list
        if r["status"] in ["Accepted", "Food Picked Up", "Picked Up"]
    )
    delivered_count = sum(
        1 for r in requests_list if r["status"] == "Delivered")

    active_tab = request.args.get("tab", "donor").strip().lower()

    conn.close()
    return render_template(
        "volunteer.html",
        my_vol=my_vol,
        requests=requests_list,
        pickups=pickups,
        completed_pickups=completed_pickups,
        pending_count=pending_count,
        active_count=active_count,
        completed_count=completed_count,
        pending_delivery_count=pending_delivery_count,
        active_delivery_count=active_delivery_count,
        delivered_count=delivered_count,
        active_tab=active_tab,
    )


@app.route("/volunteer/start_pickup/<int:food_id>")
def volunteer_start_pickup(food_id):
    if session.get("role") not in ["Volunteer", "Admin"]:
        flash(
            "Only approved volunteers or administrators can accept pickups.",
            "warning")
        return redirect(url_for("volunteer"))

    vol_name = session.get("user_name", "Volunteer")
    conn = get_db()
    food = conn.execute("SELECT * FROM food WHERE id = ?",
                        (food_id,)).fetchone()
    if not food:
        conn.close()
        flash("Food listing not found.", "warning")
        return redirect(url_for("volunteer"))

    conn.execute(
        "UPDATE food SET status = 'Active Pickup', picked_up_by = ? WHERE id = ?",
        (vol_name, food_id),
    )
    conn.commit()

    # Notify Donor
    notify_donor_on_pickup_action(food, "start", vol_name, conn)

    conn.close()

    flash(
        f"Pickup #{food['food_code']} is now ACTIVE! You are assigned to collect {food['food_name']} ({food['quantity']}) from {food['seller_name']} at {food['location']}.",
        "primary",
    )
    return redirect(request.referrer or url_for("volunteer"))


@app.route("/volunteer/complete_pickup/<int:food_id>")
def volunteer_complete_pickup(food_id):
    if session.get("role") not in ["Volunteer", "Admin"]:
        flash(
            "Only approved volunteers or administrators can complete pickups.",
            "warning",
        )
        return redirect(url_for("volunteer"))

    vol_name = session.get("user_name", "Volunteer")
    conn = get_db()
    food = conn.execute("SELECT * FROM food WHERE id = ?",
                        (food_id,)).fetchone()
    if not food:
        conn.close()
        flash("Food listing not found.", "warning")
        return redirect(url_for("volunteer"))

    assigned_vol = food["picked_up_by"] or vol_name
    conn.execute(
        """UPDATE food
           SET status = 'Complete Pickup', picked_up_by = ?, picked_up_at = datetime('now')
           WHERE id = ?""",
        (assigned_vol, food_id),
    )
    conn.commit()

    # Notify Donor
    notify_donor_on_pickup_action(food, "complete", assigned_vol, conn)

    conn.close()

    flash(
        f"Success! #{food['food_code']} ({food['food_name']} - {food['quantity']}) marked as COMPLETE PICKUP and added to Live Inventory!",
        "success",
    )
    return redirect(request.referrer or url_for("volunteer"))


@app.route("/volunteer/cancel_pickup/<int:food_id>")
def volunteer_cancel_pickup(food_id):
    if session.get("role") not in ["Volunteer", "Admin"]:
        flash("Unauthorized action.", "warning")
        return redirect(url_for("volunteer"))

    vol_name = session.get("user_name", "Volunteer")
    conn = get_db()
    food = conn.execute("SELECT * FROM food WHERE id = ?",
                        (food_id,)).fetchone()
    if not food:
        conn.close()
        flash("Food listing not found.", "warning")
        return redirect(url_for("volunteer"))

    conn.execute(
        "UPDATE food SET status = 'Pending Pickup', picked_up_by = NULL WHERE id = ?",
        (food_id,),
    )
    conn.commit()

    # Notify Donor
    notify_donor_on_pickup_action(food, "cancel", vol_name, conn)

    conn.close()

    flash(f"Pickup for #{food['food_code']} reset to Pending Pickup.", "warning")
    return redirect(request.referrer or url_for("volunteer"))


@app.route("/volunteer/pickup_food/<int:food_id>")
def volunteer_pickup_food(food_id):
    return volunteer_complete_pickup(food_id)


@app.route("/volunteer_login", methods=["POST"])
def volunteer_login():
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()

    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE email = ? AND password = ? AND role = 'Volunteer'",
        (email, password),
    ).fetchone()

    if user:
        session.clear()
        session.permanent = True
        session["user_id"] = user["id"]
        session["user_name"] = user["name"] or "User"
        session["role"] = user["role"]
        session["email"] = user["email"]
        session["phone"] = (
            user["phone"] if ("phone" in user.keys() and user["phone"]) else ""
        )
        flash(f"Welcome back, {user['name']}! Signed in as Volunteer.", "success")
    else:
        flash("Invalid credentials. Please try again.", "danger")

    conn.close()
    return redirect(url_for("volunteer"))


@app.route("/volunteer_logout")
def volunteer_logout():
    session.clear()
    session.modified = True
    flash("Signed out of Volunteer Portal.", "info")
    response = redirect(url_for("home"))
    response.delete_cookie(app.config.get("SESSION_COOKIE_NAME", "session"))
    return response


@app.route("/admin/volunteer/<int:vol_id>/approve")
@role_required("Admin")
def approve_volunteer(vol_id):
    conn = get_db()
    vol = conn.execute(
        "SELECT * FROM volunteers WHERE id = ?", (vol_id,)).fetchone()
    if vol:
        conn.execute(
            "UPDATE volunteers SET status = 'Approved' WHERE id = ?", (vol_id,)
        )
        conn.commit()

        # Send Approval Email to the Volunteer
        subject = (
            "Official Confirmation: Your AnnaPath Volunteer Application is Approved"
        )
        html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 25px; border: 2px solid #2E7D32; border-radius: 12px; background: #ffffff;">
            <div style="text-align: center; margin-bottom: 20px;">
                <h1 style="color: #2E7D32; margin: 0; font-size: 28px;">AnnaPath</h1>
                <p style="color: #666; font-size: 14px; margin-top: 4px;">A Path to Good Food for Everyone</p>
            </div>

            <h2 style="color: #1B5E20; margin-top: 0;">Welcome Aboard, {vol['name']}!</h2>
            <p style="font-size: 16px; color: #333;">
                We are delighted to inform you that your volunteer application has been <b>APPROVED</b> by our administrator!
            </p>

            <div style="background: #E8F5E9; border-left: 5px solid #2E7D32; padding: 15px 20px; border-radius: 6px; margin: 20px 0;">
                <h4 style="margin: 0 0 10px 0; color: #2E7D32;">Your Verified Details:</h4>
                <p style="margin: 4px 0;"><b>Assigned Region:</b> {vol['area']}</p>
                <p style="margin: 4px 0;"><b>Available Hours:</b> {vol['time'] or 'Flexible'}</p>
                <p style="margin: 4px 0;"><b>Status:</b> <span style="color: #2E7D32; font-weight: bold;">Approved & Active</span></p>
            </div>

            <p style="font-size: 15px; color: #333;">
                You can now log in to the <b>Volunteer Portal</b> to browse live delivery requests, pick up fresh food from donors, and deliver it safely to receivers in need.
            </p>

            <div style="text-align: center; margin: 30px 0;">
                <a href="http://127.0.0.1:5000/volunteer" style="background: #2E7D32; color: #ffffff; padding: 14px 30px; text-decoration: none; font-weight: bold; border-radius: 8px; font-size: 16px; display: inline-block;">
                    Open Volunteer Portal &rarr;
                </a>
            </div>

            <p style="color: #888; font-size: 13px; border-top: 1px solid #eee; padding-top: 15px;">
                Thank you for joining our mission to bridge the hunger gap and end food waste.<br>
                For questions, contact us at <a href="mailto:support@annapath.org" style="color: #2E7D32;">support@annapath.org</a>.
            </p>
        </div>
        """
        text = f"Congratulations {vol['name']}! Your volunteer application has been approved by the Admin. Open the Volunteer Portal to start delivering food."
        mail_status = send_email_notification(
            vol["email"], subject, html, text)

        flash(
            f"Volunteer '{vol['name']}' has been APPROVED! Notification email sent to {vol['email']} ({mail_status}).",
            "success",
        )
    conn.close()
    return redirect(url_for("admin"))


@app.route("/admin/volunteer/<int:vol_id>/reject")
@role_required("Admin")
def reject_volunteer(vol_id):
    conn = get_db()
    vol = conn.execute(
        "SELECT * FROM volunteers WHERE id = ?", (vol_id,)).fetchone()
    if vol:
        conn.execute(
            "UPDATE volunteers SET status = 'Rejected' WHERE id = ?", (vol_id,)
        )
        conn.commit()

        # Send courtesy email
        subject = "AnnaPath Volunteer Application Update"
        html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #e0e0e0; border-radius: 8px;">
            <h2 style="color: #2E7D32;">AnnaPath Food Sharing Network</h2>
            <p>Dear {vol['name']},</p>
            <p>Thank you for expressing interest in volunteering with AnnaPath in <b>{vol['area']}</b>.</p>
            <p>At this moment, our volunteer roster in your selected area is fully staffed. We have kept your details on file and will reach out if volunteer openings expand in your area.</p>
            <p>Thank you again for your kindness!</p>
            <p>Warm regards,<br><b>The AnnaPath Administrative Team</b></p>
        </div>
        """
        send_email_notification(
            vol["email"],
            subject,
            html,
            f"Dear {vol['name']}, update on your volunteer application.",
        )
        flash(f"Volunteer '{vol['name']}' application marked as Rejected.", "warning")
    conn.close()
    return redirect(url_for("admin"))


@app.route("/admin/volunteer/<int:vol_id>/status/<string:new_status>")
@role_required("Admin")
def update_volunteer_status(vol_id, new_status):
    conn = get_db()
    conn.execute("UPDATE volunteers SET status = ? WHERE id = ?",
                 (new_status, vol_id))
    conn.commit()
    conn.close()
    flash(f"Volunteer status updated to '{new_status}'!", "success")
    return redirect(url_for("admin"))


@app.route("/admin/volunteer/<int:vol_id>/edit", methods=["POST"])
@role_required("Admin")
def edit_volunteer(vol_id):
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()
    area = request.form.get("area", "").strip()
    time_slot = request.form.get("time", "").strip()
    status = request.form.get("status", "Available").strip()
    admin_notes = request.form.get("admin_notes", "").strip()

    password = request.form.get("password", "").strip()
    conn = get_db()
    old_vol = conn.execute(
        "SELECT * FROM volunteers WHERE id = ?", (vol_id,)
    ).fetchone()
    if old_vol:
        if password:
            conn.execute(
                """UPDATE volunteers
                   SET name = ?, email = ?, phone = ?, area = ?, time = ?, password = ?, status = ?, admin_notes = ?
                   WHERE id = ?""",
                (
                    name,
                    email,
                    phone,
                    area,
                    time_slot,
                    password,
                    status,
                    admin_notes,
                    vol_id,
                ),
            )
            if old_vol["email"]:
                conn.execute(
                    "UPDATE users SET name = ?, email = ?, phone = ?, password = ? WHERE email = ? AND role = 'Volunteer'",
                    (name, email, phone, password, old_vol["email"]),
                )
        else:
            conn.execute(
                """UPDATE volunteers
                   SET name = ?, email = ?, phone = ?, area = ?, time = ?, status = ?, admin_notes = ?
                   WHERE id = ?""",
                (name, email, phone, area, time_slot, status, admin_notes, vol_id),
            )
            if old_vol["email"]:
                conn.execute(
                    "UPDATE users SET name = ?, email = ?, phone = ? WHERE email = ? AND role = 'Volunteer'",
                    (name, email, phone, old_vol["email"]),
                )
        conn.commit()
        flash(f"Volunteer '{name}' details updated successfully.", "success")
    else:
        flash("Volunteer record not found.", "danger")

    conn.close()
    return redirect(url_for("admin"))


@app.route("/admin/volunteer/<int:vol_id>/delete")
@role_required("Admin")
def delete_volunteer(vol_id):
    conn = get_db()
    vol = conn.execute(
        "SELECT * FROM volunteers WHERE id = ?", (vol_id,)).fetchone()
    if vol:
        conn.execute("DELETE FROM volunteers WHERE id = ?", (vol_id,))
        if vol["email"]:
            conn.execute(
                "DELETE FROM users WHERE email = ? AND role = 'Volunteer'",
                (vol["email"],),
            )
        conn.commit()
        flash(f"Volunteer '{vol['name']}' has been permanently removed.", "info")
    else:
        flash("Volunteer record not found.", "danger")

    conn.close()
    return redirect(url_for("admin"))


@app.route("/admin/volunteer/add", methods=["POST"])
@role_required("Admin")
def admin_add_volunteer():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()
    area = request.form.get("area", "").strip()
    time_slot = request.form.get("time", "").strip() or "Flexible"
    password = request.form.get("password", "").strip() or "volunteer123"
    status = request.form.get("status", "Approved").strip()
    admin_notes = request.form.get("admin_notes", "").strip()

    if not name or not area:
        flash("Volunteer name and area are required.", "warning")
        return redirect(url_for("admin"))

    conn = get_db()
    # Create user login account if email provided and not existing
    if email:
        user = conn.execute(
            "SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if not user:
            conn.execute(
                "INSERT INTO users (name, email, phone, password, role) VALUES (?, ?, ?, ?, 'Volunteer')",
                (name, email, phone, password),
            )
            conn.commit()

    conn.execute(
        """INSERT INTO volunteers (name, email, phone, area, time, password, status, admin_notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, email, phone, area, time_slot, password, status, admin_notes),
    )
    conn.commit()
    conn.close()

    flash(
        f"Volunteer '{name}' created successfully (Status: {status})!",
        "success")
    return redirect(url_for("admin"))


@app.route("/admin/food/add", methods=["POST"])
@role_required("Admin")
def admin_add_food():
    food_name = request.form.get("food_name", "").strip()
    quantity_str = request.form.get("quantity", "1").strip()
    seller_name = request.form.get(
        "seller_name", "").strip() or "Admin Inventory"
    phone = request.form.get("phone", "").strip()
    location = request.form.get("location", "").strip() or "Central Kitchen"
    food_type = request.form.get("food_type", "Donate").strip()
    price_val = request.form.get("price", "0").strip()
    price = int(price_val) if price_val.isdigit() else 0
    status = request.form.get("status", "Complete Pickup").strip()

    qty_num, unit_str = parse_quantity(quantity_str)

    image_filename = ""
    if "image" in request.files:
        file = request.files["image"]
        if file and file.filename != "" and allowed_file(file.filename):
            try:
                ext = file.filename.rsplit(".", 1)[1].lower()
                unique_name = f"food_{int(time.time())}_{uuid.uuid4().hex[:8]}.{ext}"
                save_path = os.path.join(UPLOAD_FOLDER, unique_name)
                file.save(save_path)
                image_filename = unique_name
            except Exception as e:
                app.logger.error(f"Error saving image: {e}")
                image_filename = ""

    conn = get_db()
    cursor = conn.cursor()
    if status in ("Complete Pickup", "Available"):
        cursor.execute(
            """INSERT INTO food (seller_name, phone, food_name, quantity, price, food_type, location, image,
                                 status, initial_quantity, available_quantity, claimed_quantity, unit, picked_up_by, picked_up_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'Admin', datetime('now'))""",
            (seller_name,
             phone,
             food_name,
             f"{qty_num} {unit_str}",
                price,
                food_type,
                location,
                image_filename,
                status,
                qty_num,
                qty_num,
                unit_str,
             ),
        )
    else:
        cursor.execute(
            """INSERT INTO food (seller_name, phone, food_name, quantity, price, food_type, location, image,
                                 status, initial_quantity, available_quantity, claimed_quantity, unit, picked_up_by, picked_up_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, NULL, NULL)""",
            (seller_name,
             phone,
             food_name,
             f"{qty_num} {unit_str}",
                price,
                food_type,
                location,
                image_filename,
                status,
                qty_num,
                qty_num,
                unit_str,
             ),
        )
    new_id = cursor.lastrowid
    food_code = f"FOOD-{1000 + new_id}"
    cursor.execute("UPDATE food SET food_code = ? WHERE id = ?",
                   (food_code, new_id))
    conn.commit()
    conn.close()

    flash(
        f"Food #{food_code} ({food_name} - {qty_num} {unit_str}) added to inventory (Status: {status})!",
        "success",
    )
    return redirect(url_for("admin") + "#inventorySection")


@app.route("/admin/food/<int:food_id>/edit", methods=["POST"])
@role_required("Admin")
def admin_edit_food(food_id):
    food_name = request.form.get("food_name", "").strip()
    seller_name = request.form.get("seller_name", "").strip()
    phone = request.form.get("phone", "").strip()
    location = request.form.get("location", "").strip()
    food_type = request.form.get("food_type", "Donate").strip()
    price_val = request.form.get("price", "0").strip()
    price = int(price_val) if price_val.isdigit() else 0
    available_qty_val = request.form.get("available_quantity", "0").strip()
    available_qty = int(
        available_qty_val) if available_qty_val.isdigit() else 0
    claimed_qty_val = request.form.get("claimed_quantity", "0").strip()
    claimed_qty = int(claimed_qty_val) if claimed_qty_val.isdigit() else 0
    unit = request.form.get("unit", "items").strip()
    status = request.form.get("status", "Complete Pickup").strip()
    picked_up_by = request.form.get("picked_up_by", "").strip()

    conn = get_db()
    conn.execute(
        """UPDATE food
           SET food_name = ?, seller_name = ?, phone = ?, location = ?, food_type = ?, price = ?,
               available_quantity = ?, claimed_quantity = ?, unit = ?, status = ?, picked_up_by = ?,
               quantity = ?
           WHERE id = ?""",
        (
            food_name,
            seller_name,
            phone,
            location,
            food_type,
            price,
            available_qty,
            claimed_qty,
            unit,
            status,
            picked_up_by or None,
            f"{available_qty} {unit}",
            food_id,
        ),
    )
    conn.commit()
    conn.close()

    flash(f"Food inventory item #{food_id} updated successfully!", "success")
    return redirect(url_for("admin") + "#inventorySection")


@app.route("/admin/food/<int:food_id>/delete")
@role_required("Admin")
def admin_delete_food(food_id):
    conn = get_db()
    food = conn.execute("SELECT * FROM food WHERE id = ?",
                        (food_id,)).fetchone()
    if food:
        conn.execute("DELETE FROM food WHERE id = ?", (food_id,))
        conn.commit()
        flash(
            f"Food item #{food['food_code']} ({food['food_name']}) removed from inventory.",
            "info",
        )
    else:
        flash("Food item not found.", "warning")
    conn.close()
    return redirect(url_for("admin") + "#inventorySection")


@app.route("/admin/food/<int:food_id>/status/<string:new_status>")
@role_required("Admin")
def admin_update_food_status(food_id, new_status):
    new_status = urllib.parse.unquote(new_status)
    conn = get_db()
    food = conn.execute("SELECT * FROM food WHERE id = ?",
                        (food_id,)).fetchone()
    if food:
        if new_status in ["Complete Pickup", "Available"]:
            picked_up_by = food["picked_up_by"] or "Admin"
            conn.execute(
                "UPDATE food SET status = ?, picked_up_by = ?, picked_up_at = datetime('now') WHERE id = ?",
                (new_status, picked_up_by, food_id),
            )
            action = "complete"
        elif new_status in ["Pending Pickup", "Awaiting Pickup"]:
            conn.execute(
                "UPDATE food SET status = ?, picked_up_by = NULL, picked_up_at = NULL WHERE id = ?",
                (new_status, food_id),
            )
            action = "cancel"
        else:
            conn.execute(
                "UPDATE food SET status = ? WHERE id = ?", (new_status, food_id))
            action = new_status
        conn.commit()

        # Notify donor of status change
        notify_donor_on_pickup_action(food, action, "Admin", conn)

        flash(
            f"Food #{food['food_code']} status updated to '{new_status}'!",
            "success")
    else:
        flash("Food item not found.", "warning")
    conn.close()
    return redirect(url_for("admin") + "#inventorySection")


@app.route("/update_request/<int:req_id>/<string:new_status>",
           methods=["GET", "POST"])
def update_request(req_id, new_status):
    new_status = urllib.parse.unquote(new_status)
    status_map = {
        "Pending": "Pending",
        "Accepted": "Accepted",
        "Food Picked Up": "Food Picked Up",
        "Picked Up": "Food Picked Up",
        "Delivered": "Delivered",
        "Cancelled": "Cancelled",
        "Rejected": "Rejected",
    }
    status_clean = status_map.get(new_status, new_status)
    conn = get_db()
    req = conn.execute("SELECT * FROM requests WHERE id = ?", (req_id,)).fetchone()
    if not req:
        conn.close()
        flash("Request not found.", "warning")
        return redirect(request.referrer or url_for("volunteer"))

    vol_name = session.get("user_name", "Volunteer")
    if status_clean in ["Accepted", "Food Picked Up"]:
        conn.execute(
            "UPDATE requests SET status = ?, volunteer_name = ? WHERE id = ?",
            (status_clean, vol_name, req_id),
        )
    elif status_clean == "Delivered":
        conn.execute(
            "UPDATE requests SET status = ?, volunteer_name = COALESCE(volunteer_name, ?) WHERE id = ?",
            (status_clean, vol_name, req_id),
        )
    elif status_clean in ["Pending", "Cancelled", "Rejected"]:
        conn.execute(
            "UPDATE requests SET status = ?, volunteer_name = NULL WHERE id = ?",
            (status_clean, req_id),
        )
    else:
        conn.execute(
            "UPDATE requests SET status = ? WHERE id = ?",
            (status_clean, req_id),
        )
    conn.commit()

    # Notify Requester via email
    notify_requester_on_status_change(req, status_clean, vol_name, conn)

    conn.close()

    if (
        request.is_json
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.args.get("format") == "json"
    ):
        return {"success": True, "status": status_clean, "req_id": req_id}

    flash(f"Request #{req_id} marked as '{status_clean}'!", "success")

    tab = request.args.get("tab")
    ref = request.referrer or ""
    if tab == "receiver" or "volunteer" in ref:
        return redirect(
            url_for(
                "volunteer",
                tab="receiver") +
            "#receiverSection")

    return redirect(request.referrer or url_for("tracking"))


@app.route("/api/tracking_status")
def api_tracking_status():
    conn = get_db()
    requests = conn.execute(
        "SELECT id, status, food_name, quantity, address, name FROM requests ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return {"requests": [dict(r) for r in requests]}


@app.route("/api/notifications/poll")
def api_notifications_poll():
    user_email = session.get("email")
    user_role = session.get("role")
    if not user_email and not user_role:
        return {"notifications": []}

    conn = get_db()
    try:
        notes = conn.execute(
            """SELECT * FROM notifications 
               WHERE is_read = 0 AND (user_email = ? OR user_role = ?)
               ORDER BY id DESC LIMIT 5""",
            (user_email or "", user_role or ""),
        ).fetchall()

        if notes:
            note_ids = [n["id"] for n in notes]
            placeholders = ",".join("?" * len(note_ids))
            conn.execute(
                f"UPDATE notifications SET is_read = 1 WHERE id IN ({placeholders})",
                note_ids,
            )
            conn.commit()

        return {"notifications": [dict(n) for n in notes]}
    finally:
        conn.close()


# --- Removed Role: Emergency Relief (Redirect to Home) ---


@app.route("/emergency", methods=["GET", "POST"])
def emergency():
    return redirect(url_for("home"))


# --- Role 5: Admin Panel & Database Inspection ---


@app.route("/admin")
@role_required("Admin")
def admin():
    conn = get_db()
    users_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    food_count = conn.execute("SELECT COUNT(*) FROM food").fetchone()[0]
    volunteer_count = conn.execute(
        "SELECT COUNT(*) FROM volunteers").fetchone()[0]
    pending_volunteers_count = conn.execute(
        "SELECT COUNT(*) FROM volunteers WHERE status = 'Pending Approval'"
    ).fetchone()[0]
    requests_count = conn.execute(
        "SELECT COUNT(*) FROM requests").fetchone()[0]

    # Live inventory analytics
    total_live_stock = conn.execute(
        "SELECT COALESCE(SUM(available_quantity), 0) FROM food WHERE status IN ('Complete Pickup', 'Available')"
    ).fetchone()[0]
    total_claimed_stock = conn.execute(
        "SELECT COALESCE(SUM(claimed_quantity), 0) FROM food"
    ).fetchone()[0]

    foods = conn.execute("SELECT * FROM food ORDER BY id DESC").fetchall()
    volunteers = conn.execute("""
        SELECT
            v.*,
            COALESCE(v.password, u.password, 'volunteer123') AS password
        FROM volunteers v
        LEFT JOIN users u ON (v.email = u.email AND u.role = 'Volunteer')
        ORDER BY v.id DESC
    """).fetchall()
    food_requests = conn.execute(
        "SELECT * FROM requests ORDER BY id DESC").fetchall()
    email_logs = conn.execute(
        "SELECT * FROM email_logs ORDER BY id DESC LIMIT 20"
    ).fetchall()
    conn.close()
    return render_template(
        "admin.html",
        users_count=users_count,
        food_count=food_count,
        volunteer_count=volunteer_count,
        pending_volunteers_count=pending_volunteers_count,
        requests_count=requests_count,
        total_live_stock=total_live_stock,
        total_claimed_stock=total_claimed_stock,
        foods=foods,
        volunteers=volunteers,
        requests=food_requests,
        email_logs=email_logs,
    )


@app.route("/database")
@role_required("Admin")
def database_viewer():
    conn = get_db()
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    table_names = [t[0] for t in tables]

    selected_table = request.args.get(
        "table", "food" if "food" in table_names else (
            table_names[0] if table_names else ""), )
    columns = []
    rows = []
    if selected_table and selected_table in table_names:
        cursor = conn.execute(
            f"SELECT * FROM {selected_table} ORDER BY rowid DESC")
        columns = [desc[0]
                   for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchall()

    conn.close()
    return render_template(
        "database_viewer.html",
        tables=table_names,
        selected_table=selected_table,
        columns=columns,
        rows=rows,
    )


# --- General Public Pages ---


@app.route("/food_list")
def food_list():
    conn = get_db()
    # Live stock: all items in inventory
    foods = conn.execute(
        "SELECT * FROM food WHERE status IN ('Complete Pickup', 'Available') AND available_quantity > 0 ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return render_template("food_list.html", foods=foods)


@app.route("/tracking")
def tracking():
    conn = get_db()
    requests = conn.execute(
        "SELECT * FROM requests ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("tracking.html", requests=requests)


if __name__ == "__main__":
    app.run(debug=True)
