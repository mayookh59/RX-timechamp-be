import sqlite3

conn = sqlite3.connect(r'C:\Users\Hrishi\Downloads\TRACKER.AI\backend\trackme_test.db')
c = conn.cursor()

# Show current users
c.execute("SELECT id, email, full_name, role FROM users")
users = c.fetchall()
print("Current users:")
for u in users:
    print(f"  {u[2]} ({u[1]}) - {u[3]} - id: {u[0]}")

# Find admin user id
admin_ids = [u[0] for u in users if u[3] == 'admin']
non_admin_ids = [u[0] for u in users if u[3] != 'admin']

print(f"\nKeeping: {len(admin_ids)} admin user(s)")
print(f"Deleting: {len(non_admin_ids)} non-admin user(s)")

if non_admin_ids:
    placeholders = ','.join(['?' for _ in non_admin_ids])

    # Delete related data first
    c.execute(f"DELETE FROM devices WHERE user_id IN ({placeholders})", non_admin_ids)
    print(f"  Deleted {c.rowcount} devices")

    # Check for other related tables
    tables = [t[0] for t in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print(f"  Tables: {tables}")

    for table in tables:
        cols = [col[1] for col in c.execute(f"PRAGMA table_info({table})").fetchall()]
        if 'user_id' in cols and table != 'users':
            c.execute(f"DELETE FROM {table} WHERE user_id IN ({placeholders})", non_admin_ids)
            print(f"  Deleted {c.rowcount} rows from {table}")

    # Delete the users
    c.execute(f"DELETE FROM users WHERE id IN ({placeholders})", non_admin_ids)
    print(f"  Deleted {c.rowcount} users")

    conn.commit()

    # Verify
    c.execute("SELECT id, email, full_name, role FROM users")
    remaining = c.fetchall()
    print(f"\nRemaining users:")
    for u in remaining:
        print(f"  {u[2]} ({u[1]}) - {u[3]}")

conn.close()
print("\nDone!")
