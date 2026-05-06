import csv, os
from datetime import datetime
from database import init_db, SessionLocal
from models import Recipient

def migrate(path="recipients_test.csv"):
    if not os.path.exists(path): return print(f"❌ {path} non trovato")
    init_db(); db = SessionLocal()
    try:
        with open(path, encoding="utf-8") as f:
            count = 0
            for row in csv.DictReader(f):
                if db.query(Recipient).filter(Recipient.email==row["email"]).first(): continue
                try: st = datetime.strptime(row["send_time"], "%Y-%m-%d %H:%M")
                except: st = datetime.utcnow()
                db.add(Recipient(email=row["email"], name=row.get("nome",""), template=row.get("template","default.html"),
                                 subject=row.get("subject",""), status=row.get("status","pending"), send_time=st))
                count += 1
        db.commit(); print(f"✅ Migrati {count} destinatari.")
    except Exception as e: db.rollback(); print(f"❌ {e}")
    finally: db.close()

if __name__=="__main__": migrate()