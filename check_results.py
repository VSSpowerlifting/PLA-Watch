import sqlite3
conn = sqlite3.connect('pla_watch.db')
conn.row_factory = sqlite3.Row
total = conn.execute('SELECT COUNT(*) FROM articles WHERE passed_relevance=1').fetchone()[0]
flagged = conn.execute('SELECT COUNT(*) FROM articles WHERE is_significant=1').fetchone()[0]
print(f'Analyzed: {total} | Flagged: {flagged} | Rate: {flagged/total*100:.1f}%')
print()
for r in conn.execute('SELECT title_english, significance_reasoning FROM articles WHERE is_significant=1'):
    print(f'  * {r["title_english"][:70]}')
    print(f'    {r["significance_reasoning"]}')
    print()

