"""Quick DB inspection — shows recent discovery cards and swipe history."""
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "discovery.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

print("=" * 70)
print("DISCOVERY CARDS - most recent 15")
print("=" * 70)
for r in conn.execute("SELECT id, source, type, title, keywords, preference_score, is_exploration, status, discovered_at FROM discovery_cards ORDER BY discovered_at DESC LIMIT 15"):
    flag = "[EXP]" if r["is_exploration"] else "     "
    print(f"{flag} {r['status']:<8} score={r['preference_score']:6.2f}  [{r['type'][:4]}]  {r['source'][:18]:<18}")
    print(f"       title: {r['title'][:90]}")
    print(f"       kw: {r['keywords'][:120] if r['keywords'] else 'none'}")

print("\n" + "=" * 70)
print("SWIPE EVENTS - most recent 25")
print("=" * 70)
for r in conn.execute("SELECT card_id, keyword, action, swiped_at FROM swipe_events ORDER BY swiped_at DESC LIMIT 25"):
    print(f"  {r['action']:<6} kw={r['keyword'][:30]:<30}  at={r['swiped_at'][:19]}")

print("\n" + "=" * 70)
print("KEYWORD WEIGHTS - top 20 positive, top 10 negative")
print("=" * 70)
rows = list(conn.execute("SELECT keyword, action FROM swipe_events"))
weights = {}
for r in rows:
    sign = 1 if r["action"] == "liked" else -1
    weights[r["keyword"]] = weights.get(r["keyword"], 0) + sign
sorted_w = sorted(weights.items(), key=lambda x: x[1], reverse=True)
print("\nTop positive:")
for kw, w in sorted_w[:20]:
    print(f"  {kw[:30]:<30}  +{w}")
print("\nTop negative:")
for kw, w in sorted_w[-10:]:
    print(f"  {kw[:30]:<30}  {w:+}")

print("\n" + "=" * 70)
print("CORPUS STATS - top 15 most common keywords (lowest IDF)")
print("=" * 70)
for r in conn.execute("SELECT keyword, doc_count FROM corpus_stats ORDER BY doc_count DESC LIMIT 15"):
    print(f"  {r['keyword'][:30]:<30}  in {r['doc_count']} docs")

total = conn.execute('SELECT COUNT(*) FROM discovery_cards').fetchone()[0]
swipes = conn.execute('SELECT COUNT(*) FROM swipe_events').fetchone()[0]
likes = conn.execute("SELECT COUNT(*) FROM discovery_cards WHERE status='liked'").fetchone()[0]
disliked = conn.execute("SELECT COUNT(*) FROM discovery_cards WHERE status='disliked'").fetchone()[0]
pending = conn.execute("SELECT COUNT(*) FROM discovery_cards WHERE status='pending'").fetchone()[0]
print(f"\n  Cards: {total}, Swipes: {swipes}, Likes: {likes}, Disliked: {disliked}, Pending: {pending}")
