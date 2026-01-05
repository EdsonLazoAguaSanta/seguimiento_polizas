# database.py
import os
from sqlalchemy import create_engine, Column, String, DateTime, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
from uuid import uuid4

# Detectar si está en desarrollo o producción
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./siniestros.db"  # SQLite local para desarrollo
)

# Para PostgreSQL en Render, reemplazar postgresql:// por postgresql+psycopg2://
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

engine = create_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ===== MODELOS =====

class SiniestroClasificado(Base):
    """Modelo para siniestros clasificados"""
    __tablename__ = "siniestros_clasificados"

    id = Column(String, primary_key=True, default=lambda: str(uuid4()), index=True)
    mail_id = Column(String, unique=True, index=True, nullable=False)
    numero_siniestro = Column(String, index=True, nullable=True)
    remitente = Column(String, nullable=False)
    asunto = Column(String, nullable=False)
    fecha_mail = Column(DateTime, nullable=False)
    creado_en = Column(DateTime, default=datetime.utcnow, nullable=False)
    actualizado_en = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<SiniestroClasificado(mail_id={self.mail_id}, numero={self.numero_siniestro})>"

# Crear tablas
Base.metadata.create_all(bind=engine)

def get_db():
    """Dependency para obtener sesión de BD"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
