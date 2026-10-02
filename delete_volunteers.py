import sqlite3

def delete_all_volunteers():
    try:
        conn = sqlite3.connect('food.db')
        cur = conn.cursor()
        
        # Delete from users table where role is Volunteer
        cur.execute("DELETE FROM users WHERE role = 'Volunteer'")
        users_deleted = cur.rowcount
        
        # Delete from volunteers table
        cur.execute("DELETE FROM volunteers")
        volunteers_deleted = cur.rowcount
        
        conn.commit()
        print(f"Successfully deleted {users_deleted} users with Volunteer role.")
        print(f"Successfully deleted {volunteers_deleted} records from volunteers table.")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == '__main__':
    delete_all_volunteers()
