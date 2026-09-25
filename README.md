# AnnaPath &bull; Food Sharing & Rescue Network

AnnaPath is a comprehensive web platform designed to combat hunger, eliminate food waste, and connect surplus meals from donors to individuals, families, and community shelters through a volunteer-driven delivery network.

---

## 🌟 Core Features

### 1. 🍲 Food Donor Portal
- List surplus food from households, restaurants, caterers, and events.
- Specify quantity, units, pickup location, food type (free donation or discounted), and photos.
- One-click device GPS location detection with interactive Google Maps navigation.
- Real-time status tracking (*Pending Pickup* &rarr; *Active Pickup* &rarr; *Complete Pickup*).

### 2. 👥 Food Receiver Portal
- Live inventory catalog of available meals verified and collected by volunteers.
- Real-time stock claiming: inventory automatically updates and prevents over-claiming.
- Delivery request tracking with an interactive 4-stage visual progress tracker and live polling.

### 3. 🚴 Volunteer Partner Portal
- Volunteer onboarding with administrator verification.
- **Donor Pickup Fleet**: Accept pickup assignments, navigate to donor locations, and complete collections.
- **Receiver Delivery Fleet**: Claim requests, collect food, and mark orders as delivered.

### 4. 🛠️ Platform Admin Panel
- Real-time analytics: total users, live in-stock units, claimed portions, and pending requests.
- Volunteer approval workflow with automatic confirmation email dispatch via SMTP.
- Centralized inventory management: adjust live stock, edit food details, update pickup statuses.
- Built-in SQLite database inspector to query and view all database tables directly.

---

## 🚀 Tech Stack

- **Backend**: Python 3, Flask, Werkzeug, Jinja2
- **Database**: SQLite3 (`food.db`) with automatic self-healing schema migration
- **Frontend**: HTML5, CSS3 (Modern SaaS Design System), Bootstrap 5, Bootstrap Icons
- **Mapping & Location**: HTML5 Geolocation API, OpenStreetMap Nominatim reverse geocoding, Google Maps Navigation
- **Email Service**: Python `smtplib` with HTML template rendering and database delivery logging

---

## 📦 Getting Started

### 1. Clone the Repository
```bash
git clone https://github.com/Nisargpatel-03/AnnaPath.git
cd AnnaPath
```

### 2. Create and Activate Virtual Environment
```bash
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Linux / macOS
source .venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Initialize Database
```bash
python database.py
```

### 5. Run the Application
```bash
python app.py
```
Open your browser and navigate to `http://127.0.0.1:5000`.

---

## ⚙️ Configuration

Application settings can be configured in `config.py` or via environment variables:

| Variable | Description | Default |
|---|---|---|
| `SECRET_KEY` | Flask session secret key | `annapath_secret_key` |
| `DATABASE` | SQLite database file path | `food.db` |
| `SMTP_SERVER` | SMTP host for email notifications | `smtp.gmail.com` |
| `SMTP_PORT` | SMTP port | `587` |
| `SMTP_USERNAME` | SMTP account email | Configured in `config.py` |
| `SMTP_PASSWORD` | App-specific password | Configured in `config.py` |
| `SENDER_EMAIL` | Sender address shown on emails | Configured in `config.py` |

---

## 📄 License
This project is licensed under the MIT License.
