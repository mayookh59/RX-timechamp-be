import sqlite3

conn = sqlite3.connect(r'C:\Users\Hrishi\Downloads\TRACKER.AI\backend\trackme_test.db')
c = conn.cursor()

# Remove the Thinkpad device (it was accidentally registered on admin's machine)
c.execute("SELECT id FROM users WHERE email = 'thinkpad@gmail.com'")
thinkpad_user = c.fetchone()
if thinkpad_user:
    uid = thinkpad_user[0]
    for table in ['screenshots', 'url_visits', 'app_usage', 'activity_sessions', 'devices']:
        cols = [col[1] for col in c.execute(f"PRAGMA table_info({table})").fetchall()]
        if 'user_id' in cols:
            c.execute(f"DELETE FROM {table} WHERE user_id = ?", (uid,))
            print(f"  {table}: {c.rowcount} deleted")
        elif 'device_id' in cols:
            c.execute(f"DELETE FROM {table} WHERE device_id IN (SELECT id FROM devices WHERE user_id = ?)", (uid,))
            print(f"  {table}: {c.rowcount} deleted")
    print("Thinkpad device data cleaned")

# Also remove any stale admin devices (will re-register fresh)
c.execute("SELECT id FROM users WHERE email = 'admin@trackme.com'")
admin = c.fetchone()
if admin:
    c.execute("DELETE FROM devices WHERE user_id = ?", (admin[0],))
    print(f"Admin stale devices removed: {c.rowcount}")

conn.commit()

# Verify
c.execute("SELECT u.email, d.hostname FROM users u LEFT JOIN devices d ON d.user_id = u.id")
for r in c.fetchall():
    print(f"  {r[0]} -> device: {r[1] or 'none'}")
conn.close()
print("Done")
