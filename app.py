import re
import os
import sqlite3
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import time
import uuid
import urllib.parse
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session
import config

app = Flask(__name__)
app.config.from_object(config)
app.secret_key = getattr(config, "SECRET_KEY", "annapath_secret_key")

UPLOAD_FOLDER = os.path.join(app.root_path, "static", "images")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


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
                cur.execute("ALTER TABLE volunteers ADD COLUMN created_at TIMESTAMP")
            if "admin_notes" not in vol_cols:
                cur.execute("ALTER TABLE volunteers ADD COLUMN admin_notes TEXT")
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
                cur.execute("ALTER TABLE food ADD COLUMN picked_up_at TIMESTAMP")
            if "food_code" not in food_cols:
                cur.execute("ALTER TABLE food ADD COLUMN food_code TEXT")
            if "initial_quantity" not in food_cols:
                cur.execute("ALTER TABLE food ADD COLUMN initial_quantity INTEGER DEFAULT 1")
            if "available_quantity" not in food_cols:
                cur.execute("ALTER TABLE food ADD COLUMN available_quantity INTEGER DEFAULT 1")
            if "claimed_quantity" not in food_cols:
                cur.execute("ALTER TABLE food ADD COLUMN claimed_quantity INTEGER DEFAULT 0")
            if "unit" not in food_cols:
                cur.execute("ALTER TABLE food ADD COLUMN unit TEXT DEFAULT 'items'")
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

        conn.commit()
        conn.close()
    except Exception as e:
        app.logger.warning(f"Database schema self-check notice: {e}")

# Run schema safety check on module load
init_db_schema()


def send_email_notification(to_email, subject, body_html, body_text=""):
    """Send email via SMTP if configured, and always log in email_logs table."""
    conn = get_db()
    status = "Simulated / Logged"

    smtp_user = getattr(config, "SMTP_USERNAME", "")
    smtp_pass = getattr(config, "SMTP_PASSWORD", "")
    smtp_server = getattr(config, "SMTP_SERVER", "smtp.gmail.com")
    smtp_port = getattr(config, "SMTP_PORT", 587)
    sender = getattr(config, "SENDER_EMAIL", "") or smtp_user or "noreply@annapath.org"

    if smtp_user and smtp_pass and to_email:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"AnnaPath <{sender}>"
            msg["To"] = to_email
            if body_text:
                msg.attach(MIMEText(body_text, "plain"))
            if body_html:
                msg.attach(MIMEText(body_html, "html"))

            with smtplib.SMTP(smtp_server, smtp_port, timeout=8) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.sendmail(sender, to_email, msg.as_string())
            status = "Sent via SMTP"
        except Exception as e:
            app.logger.error(f"SMTP send failed: {e}")
            status = f"SMTP Error ({e})"

    try:
        conn.execute(
            "INSERT INTO email_logs (recipient, subject, body, status) VALUES (?, ?, ?, ?)",
            (to_email or "No Email", subject, body_html or body_text, status),
        )
        conn.commit()
    except Exception as e:
        app.logger.error(f"Failed to record email log: {e}")
    finally:
        conn.close()

    return status


# --- Authentication & Role Access Decorators ---

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("home"))
        return f(*args, **kwargs)
    return decorated_function


def role_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user_role = session.get("role")
            if not user_role or (user_role not in allowed_roles and user_role != "Admin"):
                target_role = allowed_roles[0]
                if not session.get("user_id"):
                    flash(f"Please sign in as {target_role} to access this portal.", "warning")
                else:
                    flash(f"Access restricted. Please sign in with a {target_role} account.", "warning")
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
        flash(f"Please sign in with your {role} credentials to switch roles.", "info")
    else:
        flash(f"Please sign in as {role} to continue.", "info")

    return redirect(url_for("login", role=role))


# --- Authentication Routes ---

@app.route("/register", methods=["GET", "POST"])
def register():
    selected_role = request.args.get("role", "Donor")
    if selected_role == "Admin":
        flash("Admin registration is not available. Please sign in with your admin credentials.", "info")
        return redirect(url_for("login", role="Admin"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "").strip()
        role = request.form.get("role", "Donor").strip()

        if role == "Admin":
            flash("Admin accounts cannot be created via public registration. Please sign in.", "warning")
            return redirect(url_for("login", role="Admin"))

        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO users (name, email, phone, password, role) VALUES (?, ?, ?, ?, ?)",
                (name, email, phone, password, role),
            )

            # If registering as Volunteer, also initialize record in volunteers table
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

            conn.commit()

            # Auto-login after registration
            user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if user:
                session.clear()
                session["user_id"] = user["id"]
                session["user_name"] = user["name"] or "User"
                session["role"] = user["role"]
                session["email"] = user["email"]
                session["phone"] = user["phone"] if ("phone" in user.keys() and user["phone"]) else ""

            flash(f"Account created successfully as {role}! Welcome, {name}.", "success")

            # Redirect directly to the appropriate role portal
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
            flash("Email is already registered. Please sign in.", "warning")
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
            "SELECT * FROM users WHERE email = ? AND password = ?",
            (email, password),
        ).fetchone()
        conn.close()

        if user:
            # Valid credentials: clear previous session and authenticate
            session.clear()
            session["user_id"] = user["id"]
            session["user_name"] = user["name"] or "User"
            session["role"] = user["role"]
            session["email"] = user["email"]
            session["phone"] = user["phone"] if ("phone" in user.keys() and user["phone"]) else ""

            # Check if user selected a different role in the dropdown (Admin is always unrestricted)
            if role and user["role"] != "Admin" and user["role"] != role:
                flash(f"Welcome back, {user['name']}! Note: Signed in to your registered {user['role']} account.", "info")
            else:
                flash(f"Welcome back, {user['name']}! Signed in as {user['role']}.", "success")

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
            flash("Invalid email or password. Please try again.", "danger")
            return render_template("login.html", selected_role=role or selected_role, email=email)

    return render_template("login.html", selected_role=selected_role, email="")


@app.route("/demo_login/<role>")
def demo_login(role):
    flash("Please sign in with your credentials.", "info")
    return redirect(url_for("login", role=role))


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out successfully.", "info")
    return redirect(url_for("home"))


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
    
    available_count = sum(1 for f in foods if f["status"] in ("Available", "Complete Pickup") and (f["available_quantity"] or 0) > 0)
    total_donations = len(foods)
    pending_pickups = sum(1 for f in foods if f["status"] in ("Pending Pickup", "Awaiting Pickup"))
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
            user = conn.execute("SELECT * FROM users WHERE id = ?", (session.get("user_id"),)).fetchone()
            if user:
                donor_name = user["name"] or donor_name
                donor_phone = user["phone"] or donor_phone
                session["user_name"] = donor_name
                session["phone"] = donor_phone or ""

        if request.method == "POST":
            seller_name = donor_name or request.form.get("seller_name", "").strip() or "Anonymous Donor"
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

            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO food (seller_name, phone, food_name, quantity, price, food_type, location, image, 
                                     status, initial_quantity, available_quantity, claimed_quantity, unit, latitude, longitude)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending Pickup', ?, ?, 0, ?, ?, ?)""",
                (seller_name, phone, food_name, f"{qty_num} {unit_str}", price, food_type, location, image_filename,
                 qty_num, qty_num, unit_str, latitude, longitude),
            )
            new_id = cursor.lastrowid
            food_code = f"FOOD-{1000 + new_id}"
            cursor.execute("UPDATE food SET food_code = ? WHERE id = ?", (food_code, new_id))
            conn.commit()

            flash(f"Food donation #{food_code} ({qty_num} {unit_str} {food_name}) registered! Status: Pending Pickup by volunteer.", "success")
            return redirect(url_for("donor_portal"))

        return render_template("add_food.html", donor_name=donor_name, donor_phone=donor_phone)
    finally:
        conn.close()


# --- Role 2: Food Receiver Portal & Actions ---

@app.route("/receiver")
@role_required("Receiver")
def receiver_portal():
    conn = get_db()
    # Available live inventory items
    foods = conn.execute("SELECT * FROM food WHERE status IN ('Complete Pickup', 'Available') AND available_quantity > 0 ORDER BY id DESC").fetchall()
    requests = conn.execute("SELECT * FROM requests ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("receiver_portal.html", foods=foods, requests=requests)


@app.route("/request_food", methods=["GET", "POST"])
@role_required("Receiver")
def request_food():
    conn = get_db()
    try:
        if request.method == "POST":
            name = request.form.get("name", "").strip() or session.get("user_name", "Anonymous")
            phone = request.form.get("phone", "").strip() or session.get("phone", "")
            address = request.form.get("address", "").strip() or "Address on file"
            reason = request.form.get("reason", "").strip()
            food_id = request.form.get("food_id")
            quantity_str = request.form.get("quantity", "1").strip()
            req_qty, _ = parse_quantity(quantity_str)

            food = None
            if food_id:
                food = conn.execute("SELECT * FROM food WHERE id = ?", (food_id,)).fetchone()

            if not food:
                food_item_name = request.form.get("food", "").strip()
                if food_item_name:
                    clean_name = re.sub(r'^\d+\s*', '', food_item_name).strip()
                    food = conn.execute(
                        """SELECT * FROM food 
                           WHERE (LOWER(food_name) = LOWER(?) OR LOWER(food_name) = LOWER(?) OR LOWER(food_name) LIKE '%' || LOWER(?) || '%')
                             AND available_quantity > 0
                           ORDER BY CASE WHEN status IN ('Complete Pickup', 'Available') THEN 0 ELSE 1 END, id DESC LIMIT 1""",
                        (food_item_name, clean_name, clean_name),
                    ).fetchone()

            if food:
                avail = food["available_quantity"] if food["available_quantity"] is not None else 0
                unit = food["unit"] or "items"
                if req_qty > avail:
                    flash(f"Only {avail} {unit} of '{food['food_name']}' available in live stock! Please enter a quantity up to {avail}.", "warning")
                    return redirect(url_for("request_food", food_id=food["id"]))

                new_avail = max(0, avail - req_qty)
                new_claimed = (food["claimed_quantity"] or 0) + req_qty
                new_status = "Out of Stock" if new_avail == 0 else (
                    food["status"] if food["status"] in ["Pending Pickup", "Active Pickup"] else "Complete Pickup"
                )
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
                req_food_name = request.form.get("food", "").strip() or "Assorted Food"
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

            conn.execute(
                """INSERT INTO requests (food_id, food_code, user_name, name, phone, food_name, quantity, address, status, latitude, longitude)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?, ?)""",
                (food_item_id, req_food_code, name, name, phone, req_food_name, req_qty_str, address, latitude, longitude),
            )
            conn.commit()

            if food and new_avail is not None:
                flash(f"Food request placed for {req_qty_str} of {req_food_name}! Live inventory updated: {new_avail} {food['unit']} remaining in stock.", "success")
            else:
                flash("Food request submitted successfully! You can track your request below.", "success")
            return redirect(url_for("tracking"))

        food_id_arg = request.args.get("food_id")
        selected_food = None
        if food_id_arg:
            selected_food = conn.execute("SELECT * FROM food WHERE id = ?", (food_id_arg,)).fetchone()

        available_foods = conn.execute("SELECT * FROM food WHERE status IN ('Complete Pickup', 'Available') AND available_quantity > 0 ORDER BY id DESC").fetchall()
        return render_template("request_food.html", selected_food=selected_food, available_foods=available_foods)
    finally:
        conn.close()


# --- Role 3: Volunteer Partner Portal & Actions ---

@app.route("/volunteer", methods=["GET", "POST"])
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
        user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if not user:
            conn.execute(
                "INSERT INTO users (name, email, phone, password, role) VALUES (?, ?, ?, ?, 'Volunteer')",
                (name, email, phone, password),
            )
            conn.commit()
            user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()

        # Check / Insert into volunteers table
        existing_vol = conn.execute(
            "SELECT id FROM volunteers WHERE email = ? OR (phone = ? AND phone != '')",
            (email, phone),
        ).fetchone()

        if existing_vol:
            conn.execute(
                "UPDATE volunteers SET name = ?, phone = ?, area = ?, time = ?, password = ?, status = 'Pending Approval' WHERE id = ?",
                (name, phone, area, time_slot, password, existing_vol["id"]),
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
        send_email_notification(email, subject, html, f"Dear {name}, your volunteer application is under review.")

        flash("Volunteer application submitted! Your request is now pending Admin approval.", "success")
        return redirect(url_for("volunteer"))

    # Fetch current user's volunteer profile if logged in
    my_vol = None
    if session.get("role") == "Volunteer":
        if user_email:
            my_vol = conn.execute("SELECT * FROM volunteers WHERE email = ? ORDER BY id DESC", (user_email,)).fetchone()
        if not my_vol and session.get("user_name"):
            my_vol = conn.execute("SELECT * FROM volunteers WHERE name = ? ORDER BY id DESC", (session.get("user_name"),)).fetchone()

    requests_list = []
    pickups = []
    completed_pickups = []
    if (my_vol and my_vol["status"] in ["Approved", "Available", "Active On Route"]) or session.get("role") == "Admin":
        requests_list = conn.execute("SELECT * FROM requests ORDER BY id DESC").fetchall()
        # Pending and Active Pickups for volunteers to action
        pickups = conn.execute(
            """SELECT * FROM food 
               WHERE status IN ('Pending Pickup', 'Active Pickup', 'Awaiting Pickup') 
               ORDER BY CASE status 
                   WHEN 'Active Pickup' THEN 1 
                   WHEN 'Pending Pickup' THEN 2 
                   ELSE 3 END, id DESC"""
        ).fetchall()
        # Completed pickups history
        completed_pickups = conn.execute(
            "SELECT * FROM food WHERE status IN ('Complete Pickup', 'Available') ORDER BY id DESC LIMIT 15"
        ).fetchall()

    pending_count = sum(1 for p in pickups if p["status"] in ["Pending Pickup", "Awaiting Pickup"])
    active_count = sum(1 for p in pickups if p["status"] == "Active Pickup")
    completed_count = len(completed_pickups)

    pending_delivery_count = sum(1 for r in requests_list if r["status"] == "Pending")
    active_delivery_count = sum(1 for r in requests_list if r["status"] in ["Accepted", "Food Picked Up", "Picked Up"])
    delivered_count = sum(1 for r in requests_list if r["status"] == "Delivered")

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
        flash("Only approved volunteers or administrators can accept pickups.", "warning")
        return redirect(url_for("volunteer"))

    vol_name = session.get("user_name", "Volunteer")
    conn = get_db()
    food = conn.execute("SELECT * FROM food WHERE id = ?", (food_id,)).fetchone()
    if not food:
        conn.close()
        flash("Food listing not found.", "warning")
        return redirect(url_for("volunteer"))

    conn.execute(
        "UPDATE food SET status = 'Active Pickup', picked_up_by = ? WHERE id = ?",
        (vol_name, food_id),
    )
    conn.commit()
    conn.close()

    flash(
        f"Pickup #{food['food_code']} is now ACTIVE! You are assigned to collect {food['food_name']} ({food['quantity']}) from {food['seller_name']} at {food['location']}.",
        "primary",
    )
    return redirect(request.referrer or url_for("volunteer"))


@app.route("/volunteer/complete_pickup/<int:food_id>")
def volunteer_complete_pickup(food_id):
    if session.get("role") not in ["Volunteer", "Admin"]:
        flash("Only approved volunteers or administrators can complete pickups.", "warning")
        return redirect(url_for("volunteer"))

    vol_name = session.get("user_name", "Volunteer")
    conn = get_db()
    food = conn.execute("SELECT * FROM food WHERE id = ?", (food_id,)).fetchone()
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

    conn = get_db()
    food = conn.execute("SELECT * FROM food WHERE id = ?", (food_id,)).fetchone()
    if not food:
        conn.close()
        flash("Food listing not found.", "warning")
        return redirect(url_for("volunteer"))

    conn.execute(
        "UPDATE food SET status = 'Pending Pickup', picked_up_by = NULL WHERE id = ?",
        (food_id,),
    )
    conn.commit()
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
        "SELECT * FROM users WHERE email = ? AND password = ? AND role IN ('Volunteer', 'Admin')",
        (email, password),
    ).fetchone()

    if user:
        session.clear()
        session["user_id"] = user["id"]
        session["user_name"] = user["name"] or "User"
        session["role"] = user["role"]
        session["email"] = user["email"]
        session["phone"] = user["phone"] if ("phone" in user.keys() and user["phone"]) else ""
        flash(f"Welcome back, {user['name']}! Signed in as {user['role']} in Volunteer Portal.", "success")
    else:
        # Check if user exists with any other role
        any_user = conn.execute("SELECT * FROM users WHERE email = ? AND password = ?", (email, password)).fetchone()
        if any_user:
            flash(f"Account found, but role is '{any_user['role']}', not 'Volunteer' or 'Admin'.", "warning")
        else:
            flash("Invalid email or password.", "danger")

    conn.close()
    return redirect(url_for("volunteer"))


@app.route("/volunteer_logout")
def volunteer_logout():
    session.clear()
    flash("Signed out of Volunteer Portal.", "info")
    return redirect(url_for("volunteer"))


@app.route("/admin/volunteer/<int:vol_id>/approve")
@role_required("Admin")
def approve_volunteer(vol_id):
    conn = get_db()
    vol = conn.execute("SELECT * FROM volunteers WHERE id = ?", (vol_id,)).fetchone()
    if vol:
        conn.execute("UPDATE volunteers SET status = 'Approved' WHERE id = ?", (vol_id,))
        conn.commit()

        # Send Approval Email to the Volunteer
        subject = "Official Confirmation: Your AnnaPath Volunteer Application is Approved"
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
        mail_status = send_email_notification(vol["email"], subject, html, text)

        flash(f"Volunteer '{vol['name']}' has been APPROVED! Notification email sent to {vol['email']} ({mail_status}).", "success")
    conn.close()
    return redirect(url_for("admin"))


@app.route("/admin/volunteer/<int:vol_id>/reject")
@role_required("Admin")
def reject_volunteer(vol_id):
    conn = get_db()
    vol = conn.execute("SELECT * FROM volunteers WHERE id = ?", (vol_id,)).fetchone()
    if vol:
        conn.execute("UPDATE volunteers SET status = 'Rejected' WHERE id = ?", (vol_id,))
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
        send_email_notification(vol["email"], subject, html, f"Dear {vol['name']}, update on your volunteer application.")
        flash(f"Volunteer '{vol['name']}' application marked as Rejected.", "warning")
    conn.close()
    return redirect(url_for("admin"))


@app.route("/admin/volunteer/<int:vol_id>/status/<string:new_status>")
@role_required("Admin")
def update_volunteer_status(vol_id, new_status):
    conn = get_db()
    conn.execute("UPDATE volunteers SET status = ? WHERE id = ?", (new_status, vol_id))
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
    old_vol = conn.execute("SELECT * FROM volunteers WHERE id = ?", (vol_id,)).fetchone()
    if old_vol:
        if password:
            conn.execute(
                """UPDATE volunteers 
                   SET name = ?, email = ?, phone = ?, area = ?, time = ?, password = ?, status = ?, admin_notes = ? 
                   WHERE id = ?""",
                (name, email, phone, area, time_slot, password, status, admin_notes, vol_id),
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
    vol = conn.execute("SELECT * FROM volunteers WHERE id = ?", (vol_id,)).fetchone()
    if vol:
        conn.execute("DELETE FROM volunteers WHERE id = ?", (vol_id,))
        if vol["email"]:
            conn.execute("DELETE FROM users WHERE email = ? AND role = 'Volunteer'", (vol["email"],))
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
        user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
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

    flash(f"Volunteer '{name}' created successfully (Status: {status})!", "success")
    return redirect(url_for("admin"))


@app.route("/admin/food/add", methods=["POST"])
@role_required("Admin")
def admin_add_food():
    food_name = request.form.get("food_name", "").strip()
    quantity_str = request.form.get("quantity", "1").strip()
    seller_name = request.form.get("seller_name", "").strip() or "Admin Inventory"
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
            (seller_name, phone, food_name, f"{qty_num} {unit_str}", price, food_type, location, image_filename,
             status, qty_num, qty_num, unit_str),
        )
    else:
        cursor.execute(
            """INSERT INTO food (seller_name, phone, food_name, quantity, price, food_type, location, image, 
                                 status, initial_quantity, available_quantity, claimed_quantity, unit, picked_up_by, picked_up_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, NULL, NULL)""",
            (seller_name, phone, food_name, f"{qty_num} {unit_str}", price, food_type, location, image_filename,
             status, qty_num, qty_num, unit_str),
        )
    new_id = cursor.lastrowid
    food_code = f"FOOD-{1000 + new_id}"
    cursor.execute("UPDATE food SET food_code = ? WHERE id = ?", (food_code, new_id))
    conn.commit()
    conn.close()

    flash(f"Food #{food_code} ({food_name} - {qty_num} {unit_str}) added to inventory (Status: {status})!", "success")
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
    available_qty = int(available_qty_val) if available_qty_val.isdigit() else 0
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
        (food_name, seller_name, phone, location, food_type, price, available_qty, claimed_qty, unit, status, picked_up_by or None, f"{available_qty} {unit}", food_id),
    )
    conn.commit()
    conn.close()

    flash(f"Food inventory item #{food_id} updated successfully!", "success")
    return redirect(url_for("admin") + "#inventorySection")


@app.route("/admin/food/<int:food_id>/delete")
@role_required("Admin")
def admin_delete_food(food_id):
    conn = get_db()
    food = conn.execute("SELECT * FROM food WHERE id = ?", (food_id,)).fetchone()
    if food:
        conn.execute("DELETE FROM food WHERE id = ?", (food_id,))
        conn.commit()
        flash(f"Food item #{food['food_code']} ({food['food_name']}) removed from inventory.", "info")
    else:
        flash("Food item not found.", "warning")
    conn.close()
    return redirect(url_for("admin") + "#inventorySection")


@app.route("/admin/food/<int:food_id>/status/<string:new_status>")
@role_required("Admin")
def admin_update_food_status(food_id, new_status):
    new_status = urllib.parse.unquote(new_status)
    conn = get_db()
    food = conn.execute("SELECT * FROM food WHERE id = ?", (food_id,)).fetchone()
    if food:
        if new_status in ["Complete Pickup", "Available"]:
            picked_up_by = food["picked_up_by"] or "Admin"
            conn.execute(
                "UPDATE food SET status = ?, picked_up_by = ?, picked_up_at = datetime('now') WHERE id = ?",
                (new_status, picked_up_by, food_id)
            )
        elif new_status in ["Pending Pickup", "Awaiting Pickup"]:
            conn.execute(
                "UPDATE food SET status = ?, picked_up_by = NULL, picked_up_at = NULL WHERE id = ?",
                (new_status, food_id)
            )
        else:
            conn.execute(
                "UPDATE food SET status = ? WHERE id = ?",
                (new_status, food_id)
            )
        conn.commit()
        flash(f"Food #{food['food_code']} status updated to '{new_status}'!", "success")
    else:
        flash("Food item not found.", "warning")
    conn.close()
    return redirect(url_for("admin") + "#inventorySection")


@app.route("/update_request/<int:req_id>/<string:new_status>", methods=["GET", "POST"])
def update_request(req_id, new_status):
    new_status = urllib.parse.unquote(new_status)
    status_map = {
        "Pending": "Pending",
        "Accepted": "Accepted",
        "Food Picked Up": "Food Picked Up",
        "Picked Up": "Food Picked Up",
        "Delivered": "Delivered",
    }
    status_clean = status_map.get(new_status, new_status)
    conn = get_db()
    conn.execute("UPDATE requests SET status = ? WHERE id = ?", (status_clean, req_id))
    conn.commit()
    conn.close()

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get("format") == "json":
        return {"success": True, "status": status_clean, "req_id": req_id}

    flash(f"Request #{req_id} marked as '{status_clean}'!", "success")

    tab = request.args.get("tab")
    ref = request.referrer or ""
    if tab == "receiver" or "volunteer" in ref:
        return redirect(url_for("volunteer", tab="receiver") + "#receiverSection")

    return redirect(request.referrer or url_for("tracking"))


@app.route("/api/tracking_status")
def api_tracking_status():
    conn = get_db()
    requests = conn.execute("SELECT id, status, food_name, quantity, address, name FROM requests ORDER BY id DESC").fetchall()
    conn.close()
    return {"requests": [dict(r) for r in requests]}


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
    volunteer_count = conn.execute("SELECT COUNT(*) FROM volunteers").fetchone()[0]
    pending_volunteers_count = conn.execute("SELECT COUNT(*) FROM volunteers WHERE status = 'Pending Approval'").fetchone()[0]
    requests_count = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]

    # Live inventory analytics
    total_live_stock = conn.execute("SELECT COALESCE(SUM(available_quantity), 0) FROM food WHERE status IN ('Complete Pickup', 'Available')").fetchone()[0]
    total_claimed_stock = conn.execute("SELECT COALESCE(SUM(claimed_quantity), 0) FROM food").fetchone()[0]

    foods = conn.execute("SELECT * FROM food ORDER BY id DESC").fetchall()
    volunteers = conn.execute("""
        SELECT 
            v.*, 
            COALESCE(v.password, u.password, 'volunteer123') AS password
        FROM volunteers v
        LEFT JOIN users u ON (v.email = u.email AND u.role = 'Volunteer')
        ORDER BY v.id DESC
    """).fetchall()
    food_requests = conn.execute("SELECT * FROM requests ORDER BY id DESC").fetchall()
    email_logs = conn.execute("SELECT * FROM email_logs ORDER BY id DESC LIMIT 20").fetchall()
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
        "table", "food" if "food" in table_names else (table_names[0] if table_names else "")
    )
    columns = []
    rows = []
    if selected_table and selected_table in table_names:
        cursor = conn.execute(f"SELECT * FROM {selected_table} ORDER BY rowid DESC")
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
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
    foods = conn.execute("SELECT * FROM food WHERE status IN ('Complete Pickup', 'Available') AND available_quantity > 0 ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("food_list.html", foods=foods)


@app.route("/tracking")
def tracking():
    conn = get_db()
    requests = conn.execute("SELECT * FROM requests ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("tracking.html", requests=requests)


if __name__ == "__main__":
    app.run(debug=True)