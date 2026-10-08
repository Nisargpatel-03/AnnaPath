import os

SECRET_KEY = os.environ.get("SECRET_KEY", "annapath_secret_key")
DATABASE = "food.db"

# Email Configuration (Google SMTP & Firebase Dual Engine)
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "patelnisarg558@gmail.com")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "odjhlfyufspieaep")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "") or SMTP_USERNAME or "noreply@annapath.org"

# Firebase Cloud Configuration
FIREBASE_MAIL_COLLECTION = os.environ.get("FIREBASE_MAIL_COLLECTION", "mail")
FIREBASE_API_KEY = os.environ.get("FIREBASE_API_KEY", "AIzaSyC5qZAO5HX2ddE0mKFzVtIAxEEmvJh-jR8")