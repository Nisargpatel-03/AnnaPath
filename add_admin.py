import os
import sqlite3
import firebase_admin
from firebase_admin import credentials, auth, firestore

# Initialize Firebase Admin
cred = credentials.Certificate(os.path.join(os.path.dirname(__file__), "firebase-adminsdk.json"))
firebase_admin.initialize_app(cred)
db = firestore.client()

admins = [
    {"name": "Nisarg Patel", "email": "patelnisarg558@gmail.com", "password": "Anna_P@th_03"},
    {"name": "Ruchika Kani", "email": "ruchikakani05@gmail.com", "password": "Anna_P@th_05"}
]

for admin in admins:
    print(f"\nProcessing admin: {admin['email']}...")
    
    # 1. Add to SQLite (Needed for app.py login right now)
    try:
        conn = sqlite3.connect('food.db')
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (name, email, password, role) VALUES (?, ?, ?, ?)",
            (admin['name'], admin['email'], admin['password'], "Admin")
        )
        conn.commit()
        print("  - Added to SQLite database.")
    except sqlite3.IntegrityError:
        print("  - User already exists in SQLite.")
    except Exception as e:
        print(f"  - Error saving to SQLite: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

    # 2. Add to Firebase Authentication
    try:
        user = auth.create_user(
            email=admin['email'],
            password=admin['password'],
            display_name=admin['name']
        )
        print(f"  - Created new user in Firebase Auth: {user.uid}")
    except auth.EmailAlreadyExistsError:
        print(f"  - User already exists in Firebase Auth. Skipping creation.")
    except Exception as e:
        print(f"  - Error creating user in Firebase Auth: {e}")

    # 3. Add to Firebase Firestore
    try:
        db.collection('users').document('roles').collection('admins').document(admin['email']).set({
            'name': admin['name'],
            'email': admin['email'],
            'password': admin['password'],
            'role': "Admin",
            'created_at': firestore.SERVER_TIMESTAMP
        })
        print(f"  - Added Admin data to Firestore 'users > roles > admins' collection.")
    except Exception as e:
        print(f"  - Error saving to Firestore: {e}")

print("\nFinished processing all admins!")
