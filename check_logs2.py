import sqlite3
c = sqlite3.connect('data/trades.db')
c.row_factory = sqlite3.Row
rows = c.execute("SELECT * FROM action_logs WHERE source='TradeExecutor' ORDER BY id DESC LIMIT 20").fetchall()
for r in reversed(rows):
    print(f"[{r['id']}] {r['action']} - {r['detail']}")
