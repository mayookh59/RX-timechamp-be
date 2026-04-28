import sqlite3

conn = sqlite3.connect(r'C:\Users\Hrishi\Downloads\TRACKER.AI\backend\trackme_test.db')
c = conn.cursor()

# Find test employee
c.execute("SELECT id, email, full_name FROM users WHERE email = 'testemployee@trackme.com'")
user = c.fetchone()
if not user:
    print("Test employee not found")
    conn.close()
    exit()

uid = user[0]
print(f"Removing: {user[2]} ({user[1]})")

# Delete related data
for table in ['devices', 'activity_sessions', 'app_usage', 'url_visits', 'screenshots', 'daily_user_summaries', 'audit_logs']:
    cols = [col[1] for col in c.execute(f"PRAGMA table_info({table})").fetchall()]
    if 'user_id' in cols:
        c.execute(f"DELETE FROM {table} WHERE user_id = ?", (uid,))
        print(f"  {table}: {c.rowcount} rows deleted")
    elif 'device_id' in cols:
        c.execute(f"DELETE FROM {table} WHERE device_id IN (SELECT id FROM devices WHERE user_id = ?)", (uid,))
        print(f"  {table}: {c.rowcount} rows deleted")

c.execute("DELETE FROM users WHERE id = ?", (uid,))
print(f"  users: {c.rowcount} deleted")
conn.commit()

c.execute("SELECT full_name, email FROM users")
print("\nRemaining users:")
for r in c.fetchall():
    print(f"  {r[0]} ({r[1]})")
conn.close()
