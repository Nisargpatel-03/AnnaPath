import sqlite3

conn = sqlite3.connect("food.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    role TEXT NOT NULL,
    phone TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS food (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_name TEXT NOT NULL,
    food_name TEXT NOT NULL,
    quantity TEXT NOT NULL,
    price INTEGER,
    food_type TEXT NOT NULL,
    location TEXT NOT NULL,
    image TEXT,
    status TEXT DEFAULT 'Available',
    food_code TEXT,
    initial_quantity INTEGER DEFAULT 1,
    available_quantity INTEGER DEFAULT 1,
    claimed_quantity INTEGER DEFAULT 0,
    unit TEXT DEFAULT 'items',
    picked_up_by TEXT,
    picked_up_at TIMESTAMP,
    phone TEXT,
    latitude REAL,
    longitude REAL
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    food_id INTEGER,
    user_name TEXT,
    status TEXT DEFAULT 'Pending',
    name TEXT,
    phone TEXT,
    food_name TEXT,
    quantity TEXT,
    address TEXT,
    food_code TEXT,
    latitude REAL,
    longitude REAL
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS volunteers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    email TEXT,
    phone TEXT,
    area TEXT,
    time TEXT,
    status TEXT DEFAULT 'Pending Approval',
    password TEXT,
    admin_notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS email_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT DEFAULT 'Sent',
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")


cursor.execute('''
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_email TEXT,
    user_role TEXT,
    title TEXT,
    message TEXT,
    is_read INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

conn.commit()
conn.close()

print("food.db initialized successfully.")