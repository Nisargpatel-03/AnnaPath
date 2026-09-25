import os

SECRET_KEY = os.environ.get("SECRET_KEY", "annapath_secret_key")
DATABASE = "food.db"

# Email Configuration for Gmail SMTP
# To send real emails via Gmail, enter your Gmail and a 16-character Google App Password:
# (Generate App Password at: https://myaccount.google.com/apppasswords)
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "patelnisarg558@gmail.com")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "wbgjgdpbnsiozlne")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "") or SMTP_USERNAME or "noreply@annapath.org"