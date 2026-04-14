"""Diagnostic: dump all Oracle audit logs with full detail."""
import sqlite3

conn = sqlite3.connect("data/trades.db")
conn.row_factory = sqlite3.Row

rows = conn.execute(
    "SELECT id, timestamp, source, action, level, detail "
    "FROM action_logs ORDER BY id ASC"
).fetchall()

print(f"Total entries: {len(rows)}\n")
for r in rows:
    ts = str(r["timestamp"])[11:19]
    src = str(r["source"])
    act = str(r["action"])
    lvl = str(r["level"])
    det = str(r["detail"])
    print(f"  [{r['id']:3}] {ts} | {src:15} | {act:35} | {lvl:8}")
    print(f"        Detail: {det[:200]}")
    print()

conn.close()
