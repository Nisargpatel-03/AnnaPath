import sqlite3

try:
    conn = sqlite3.connect('food.db')
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (name, email, password, role) VALUES (?, ?, ?, ?)",
        ("Ruchika Kani", "ruchikakani05@gmail.com", "Anna_P@th_05", "Admin")
    )
    conn.commit()
    print("Second admin added successfully.")
except sqlite3.IntegrityError:
    print("User with this email already exists.")
except Exception as e:
    print(f"Error: {e}")
finally:
    if 'conn' in locals():
        conn.close()
