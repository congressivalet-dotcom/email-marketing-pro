import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

# Usa Path per risolvere correttamente i percorsi su Windows
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.getenv("DATABASE_URL") or f"sqlite:///{BASE_DIR / 'data' / 'emails.db'}"

# Assicurati che la cartella esista
os.makedirs(BASE_DIR / "data", exist_ok=True)

engine = create_engine(DB_PATH, connect_args={"check_same_thread": False} if "sqlite" in DB_PATH else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def init_db(): Base.metadata.create_all(bind=engine)