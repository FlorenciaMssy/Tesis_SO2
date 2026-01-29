"""
Modelos de base de datos para el sistema de monitoreo de SO2
Utiliza SQLAlchemy como ORM
"""
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, Float, String, DateTime, 
    Text, ForeignKey, Boolean, LargeBinary, JSON
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from config.settings import DATABASE_CONFIG

Base = declarative_base()


class Volcan(Base):
    """Modelo para almacenar información de volcanes monitoreados"""
    __tablename__ = "volcanes"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    nombre = Column(String(100), nullable=False)
    latitud = Column(Float, nullable=False)
    longitud = Column(Float, nullable=False)
    pais = Column(String(100))
    altitud_m = Column(Float)  # Altitud del cráter en metros
    descripcion = Column(Text)
    activo = Column(Boolean, default=True)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)
    
    # Relaciones
    imagenes = relationship("ImagenTROPOMI", back_populates="volcan")
    datos_viento = relationship("DatosViento", back_populates="volcan")
    resultados_flujo = relationship("ResultadoFlujoSO2", back_populates="volcan")
    
    def __repr__(self):
        return f"<Volcan(nombre='{self.nombre}', lat={self.latitud}, lon={self.longitud})>"


class ImagenTROPOMI(Base):
    """Modelo para almacenar metadatos de imágenes TROPOMI de SO2"""
    __tablename__ = "imagenes_tropomi"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    volcan_id = Column(Integer, ForeignKey("volcanes.id"), nullable=False)
    
    # Identificadores del producto
    producto_id = Column(String(255), unique=True)  # ID único del producto Copernicus
    nombre_archivo = Column(String(255))
    
    # Información temporal
    fecha_adquisicion = Column(DateTime, nullable=False)
    fecha_procesamiento = Column(DateTime)
    
    # Cobertura espacial
    bbox_norte = Column(Float)
    bbox_sur = Column(Float)
    bbox_este = Column(Float)
    bbox_oeste = Column(Float)
    
    # Información del producto
    version_procesamiento = Column(String(50))
    modo_operacion = Column(String(50))  # NRTI (Near Real Time) o OFFL (Offline)
    
    # Calidad
    qa_value_promedio = Column(Float)  # Valor promedio de calidad
    cobertura_nubes_pct = Column(Float)
    
    # Almacenamiento
    ruta_archivo = Column(String(500))  # Ruta al archivo NetCDF local
    tamano_bytes = Column(Integer)
    
    # Estado
    descargado = Column(Boolean, default=False)
    procesado = Column(Boolean, default=False)
    fecha_descarga = Column(DateTime)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)
    
    # Relaciones
    volcan = relationship("Volcan", back_populates="imagenes")
    resultados = relationship("ResultadoFlujoSO2", back_populates="imagen")
    
    def __repr__(self):
        return f"<ImagenTROPOMI(id={self.id}, fecha={self.fecha_adquisicion})>"


class DatosViento(Base):
    """Modelo para almacenar datos de viento de ERA5"""
    __tablename__ = "datos_viento"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    volcan_id = Column(Integer, ForeignKey("volcanes.id"), nullable=False)
    
    # Información temporal y espacial
    fecha_hora = Column(DateTime, nullable=False)
    latitud = Column(Float, nullable=False)
    longitud = Column(Float, nullable=False)
    nivel_presion_hpa = Column(Float)  # Nivel de presión en hPa
    altitud_m = Column(Float)  # Altitud aproximada en metros
    
    # Componentes del viento
    u_component = Column(Float)  # Componente U (este-oeste) en m/s
    v_component = Column(Float)  # Componente V (norte-sur) en m/s
    velocidad_ms = Column(Float)  # Velocidad del viento en m/s
    direccion_grados = Column(Float)  # Dirección del viento en grados (0-360)
    
    # Fuente de datos
    fuente = Column(String(50), default="ERA5")  # ERA5 o GDAS
    ruta_archivo_origen = Column(String(500))
    
    fecha_creacion = Column(DateTime, default=datetime.utcnow)
    
    # Relación
    volcan = relationship("Volcan", back_populates="datos_viento")
    
    def __repr__(self):
        return f"<DatosViento(fecha={self.fecha_hora}, vel={self.velocidad_ms} m/s)>"


class ResultadoFlujoSO2(Base):
    """Modelo para almacenar resultados de cálculos de flujo de SO2"""
    __tablename__ = "resultados_flujo_so2"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    volcan_id = Column(Integer, ForeignKey("volcanes.id"), nullable=False)
    imagen_id = Column(Integer, ForeignKey("imagenes_tropomi.id"), nullable=False)
    
    # Información temporal
    fecha_hora = Column(DateTime, nullable=False)
    
    # Resultados del cálculo de flujo
    flujo_so2_kg_s = Column(Float)  # Flujo de SO2 en kg/s
    flujo_so2_ton_dia = Column(Float)  # Flujo de SO2 en toneladas/día
    
    # Columna vertical de SO2
    columna_so2_max = Column(Float)  # Máximo en mol/cm²
    columna_so2_promedio = Column(Float)  # Promedio en mol/cm²
    columna_so2_total = Column(Float)  # Total integrado
    
    # Datos de viento utilizados
    velocidad_viento_ms = Column(Float)
    direccion_viento_grados = Column(Float)
    altitud_viento_m = Column(Float)
    
    # Parámetros del cálculo
    ancho_pluma_km = Column(Float)  # Ancho de la sección transversal
    distancia_volcan_km = Column(Float)  # Distancia desde el cráter
    azimut_pluma_grados = Column(Float)  # Dirección de la pluma
    
    # Calidad y metadatos
    incertidumbre_pct = Column(Float)
    qa_flag = Column(Integer)  # Flag de calidad (0=bueno, 1=aceptable, 2=malo)
    notas = Column(Text)
    metadatos_json = Column(JSON)  # Metadatos adicionales en formato JSON
    
    fecha_calculo = Column(DateTime, default=datetime.utcnow)
    
    # Relaciones
    volcan = relationship("Volcan", back_populates="resultados_flujo")
    imagen = relationship("ImagenTROPOMI", back_populates="resultados")
    
    def __repr__(self):
        return f"<ResultadoFlujoSO2(fecha={self.fecha_hora}, flujo={self.flujo_so2_kg_s} kg/s)>"


class LogProcesamiento(Base):
    """Modelo para registrar logs del sistema"""
    __tablename__ = "logs_procesamiento"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    nivel = Column(String(20))  # INFO, WARNING, ERROR
    componente = Column(String(50))  # ETL, Calculador, API, etc.
    mensaje = Column(Text)
    detalles_json = Column(JSON)
    
    def __repr__(self):
        return f"<Log({self.timestamp}, {self.nivel}: {self.mensaje[:50]})>"


def get_database_url():
    """Genera la URL de conexión a PostgreSQL"""
    return (
        f"postgresql://{DATABASE_CONFIG['user']}:{DATABASE_CONFIG['password']}"
        f"@{DATABASE_CONFIG['host']}:{DATABASE_CONFIG['port']}/{DATABASE_CONFIG['database']}"
    )


def get_engine():
    """Crea y retorna el engine de SQLAlchemy"""
    return create_engine(get_database_url(), echo=False)


def get_session():
    """Crea y retorna una sesión de base de datos"""
    engine = get_engine()
    Session = sessionmaker(bind=engine)
    return Session()


def init_database():
    """Inicializa la base de datos creando todas las tablas"""
    engine = get_engine()
    Base.metadata.create_all(engine)
    print("Base de datos inicializada correctamente")
    return engine


if __name__ == "__main__":
    # Inicializar la base de datos si se ejecuta directamente
    init_database()
