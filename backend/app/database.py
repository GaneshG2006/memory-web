import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://memory_user:memory_dev_password@localhost:5432/memory_web"
)


def get_database_url() -> str:
    url = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    # Normalize postgresql:// to postgresql+psycopg:// for SQLAlchemy 2.0 with psycopg 3
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    elif url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    return url


database_url = get_database_url()

# Configure engine arguments based on driver (e.g. check_same_thread for SQLite)
connect_args = {}
if "sqlite" in database_url:
    connect_args["check_same_thread"] = False

engine = create_engine(
    database_url,
    echo=False,
    connect_args=connect_args,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
