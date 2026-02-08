from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from database import Base
import datetime

class Veiculo(Base):
    __tablename__ = "veiculos"
    chassi = Column(String, primary_key=True, index=True)
    modelo = Column(String)

class Apontamento(Base):
    __tablename__ = "apontamentos"
    id = Column(Integer, primary_key=True, index=True)
    chassi = Column(String, ForeignKey("veiculos.chassi"))
    etapa = Column(String)
    status = Column(String)

class Historico(Base):
    __tablename__ = "historico"
    id = Column(Integer, primary_key=True, index=True)
    chassi = Column(String)
    modelo = Column(String)
    etapa = Column(String)
    status = Column(String)
    data_apontamento = Column(DateTime, default=datetime.datetime.now)