from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import Base, Location

settings = get_settings()
engine = create_engine(f"sqlite:///{settings.db_path}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        existing = {loc.name: loc for loc in session.query(Location).all()}
        for loc in settings.weather_locations:
            if loc.name not in existing:
                session.add(Location(name=loc.name, lat=loc.lat, lon=loc.lon, use_llm=loc.use_llm))
            else:
                existing[loc.name].use_llm = loc.use_llm
        session.commit()


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()