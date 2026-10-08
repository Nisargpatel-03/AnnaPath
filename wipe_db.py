import os
import sqlite3
import firebase_admin
from firebase_admin import credentials, firestore

print("Wiping local SQLite database (except Admins)...")
conn = sqlite3.connect("food.db")
conn.execute("DELETE FROM users WHERE role != 'Admin'")
conn.execute("DELETE FROM volunteers")
conn.execute("DELETE FROM pending_registrations")
conn.execute("DELETE FROM food")
conn.execute("DELETE FROM requests")
conn.execute("DELETE FROM email_logs")
conn.commit()
conn.close()
print("Local SQLite database wiped successfully!")

print("\nWiping Firebase Firestore data (except Admins)...")
try:
    # Initialize Firebase if not already initialized
    if not firebase_admin._apps:
        cred = credentials.Certificate(
            os.path.join(os.path.dirname(__file__), "firebase-adminsdk.json")
        )
        firebase_admin.initialize_app(cred)

    db = firestore.client()

    def delete_collection(coll_ref):
        docs = coll_ref.list_documents()
        deleted = 0
        for doc in docs:
            doc.delete()
            deleted += 1
        return deleted

    # Delete collections
    deleted_donors = delete_collection(
        db.collection("users").document("roles").collection("donors")
    )
    deleted_receivers = delete_collection(
        db.collection("users").document("roles").collection("receivers")
    )
    deleted_volunteers = delete_collection(
        db.collection("users").document("roles").collection("volunteers")
    )
    deleted_foods = delete_collection(db.collection("foods"))

    print(f"Deleted {deleted_donors} donors from Firebase.")
    print(f"Deleted {deleted_receivers} receivers from Firebase.")
    print(f"Deleted {deleted_volunteers} volunteers from Firebase.")
    print(f"Deleted {deleted_foods} food listings from Firebase.")

except Exception as e:
    print(f"Error wiping Firebase: {e}")

print("\nAll databases are completely clean! Admins were preserved.")
