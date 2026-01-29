"""
API REST para el sistema de monitoreo de SO2
Implementada con FastAPI
"""
from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from datetime import datetime, timedelta
import logging

from database import (
    get_session, Volcan, ImagenTROPOMI, DatosViento,
    ResultadoFlujoSO2, LogProcesamiento, init_database
)
from config.settings import VOLCANES_PREDEFINIDOS, API_HOST, API_PORT
from message_bus import MessageBus

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Crear aplicación FastAPI
app = FastAPI(
    title="API de Monitoreo de SO2 Volcánico",
    description="Sistema para procesamiento y análisis de flujo de SO2 en volcanes usando datos TROPOMI",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En producción, especificar orígenes permitidos
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============== Modelos Pydantic ==============

class VolcanCreate(BaseModel):
    nombre: str = Field(..., description="Nombre del volcán")
    latitud: float = Field(..., ge=-90, le=90, description="Latitud en grados")
    longitud: float = Field(..., ge=-180, le=180, description="Longitud en grados")
    pais: Optional[str] = Field(None, description="País donde se ubica")
    altitud_m: Optional[float] = Field(None, description="Altitud del cráter en metros")
    descripcion: Optional[str] = Field(None, description="Descripción del volcán")


class VolcanResponse(BaseModel):
    id: int
    nombre: str
    latitud: float
    longitud: float
    pais: Optional[str]
    altitud_m: Optional[float]
    activo: bool
    
    class Config:
        from_attributes = True


class ExtraccionRequest(BaseModel):
    volcan_id: int = Field(..., description="ID del volcán")
    fecha_inicio: datetime = Field(..., description="Fecha inicial de búsqueda")
    fecha_fin: datetime = Field(..., description="Fecha final de búsqueda")
    descargar: bool = Field(True, description="Si descargar los archivos automáticamente")


class CalculoRequest(BaseModel):
    imagen_id: int = Field(..., description="ID de la imagen a procesar")
    altitud_viento_m: Optional[float] = Field(3000, description="Altitud para datos de viento")


class FlujoResponse(BaseModel):
    id: int
    fecha_hora: datetime
    flujo_so2_kg_s: Optional[float]
    flujo_so2_ton_dia: Optional[float]
    velocidad_viento_ms: Optional[float]
    direccion_viento_grados: Optional[float]
    incertidumbre_pct: Optional[float]
    qa_flag: Optional[int]
    
    class Config:
        from_attributes = True


class SerieTemporalRequest(BaseModel):
    volcan_id: int
    fecha_inicio: datetime
    fecha_fin: datetime


class EstadisticasResponse(BaseModel):
    volcan_id: int
    volcan_nombre: str
    n_imagenes_total: int
    n_imagenes_procesadas: int
    n_resultados: int
    flujo_promedio_ton_dia: Optional[float]
    flujo_max_ton_dia: Optional[float]
    fecha_primera_medicion: Optional[datetime]
    fecha_ultima_medicion: Optional[datetime]


# ============== Dependencias ==============

def get_db():
    """Dependency para obtener sesión de base de datos"""
    session = get_session()
    try:
        yield session
    finally:
        session.close()


# ============== Endpoints de Volcanes ==============

@app.get("/api/volcanes", response_model=List[VolcanResponse], tags=["Volcanes"])
def listar_volcanes(
    activo: Optional[bool] = Query(None, description="Filtrar por estado activo"),
    session = Depends(get_db)
):
    """Lista todos los volcanes registrados"""
    query = session.query(Volcan)
    
    if activo is not None:
        query = query.filter(Volcan.activo == activo)
    
    volcanes = query.all()
    return volcanes


@app.get("/api/volcanes/{volcan_id}", response_model=VolcanResponse, tags=["Volcanes"])
def obtener_volcan(volcan_id: int, session = Depends(get_db)):
    """Obtiene información de un volcán específico"""
    volcan = session.query(Volcan).get(volcan_id)
    
    if not volcan:
        raise HTTPException(status_code=404, detail="Volcán no encontrado")
    
    return volcan


@app.post("/api/volcanes", response_model=VolcanResponse, tags=["Volcanes"])
def crear_volcan(volcan: VolcanCreate, session = Depends(get_db)):
    """Crea un nuevo volcán"""
    # Verificar si ya existe
    existente = session.query(Volcan).filter_by(nombre=volcan.nombre).first()
    if existente:
        raise HTTPException(status_code=400, detail="Ya existe un volcán con ese nombre")
    
    nuevo_volcan = Volcan(**volcan.dict())
    session.add(nuevo_volcan)
    session.commit()
    session.refresh(nuevo_volcan)
    
    return nuevo_volcan


@app.get("/api/volcanes/predefinidos", tags=["Volcanes"])
def obtener_volcanes_predefinidos():
    """Retorna la lista de volcanes predefinidos disponibles"""
    return VOLCANES_PREDEFINIDOS


@app.post("/api/volcanes/predefinidos/{clave}", response_model=VolcanResponse, tags=["Volcanes"])
def agregar_volcan_predefinido(clave: str, session = Depends(get_db)):
    """Agrega un volcán de la lista predefinida a la base de datos"""
    if clave not in VOLCANES_PREDEFINIDOS:
        raise HTTPException(status_code=404, detail=f"Volcán predefinido '{clave}' no encontrado")
    
    datos = VOLCANES_PREDEFINIDOS[clave]
    
    # Verificar si ya existe
    existente = session.query(Volcan).filter_by(nombre=datos['nombre']).first()
    if existente:
        return existente
    
    nuevo_volcan = Volcan(
        nombre=datos['nombre'],
        latitud=datos['lat'],
        longitud=datos['lon'],
        pais=datos.get('pais')
    )
    session.add(nuevo_volcan)
    session.commit()
    session.refresh(nuevo_volcan)
    
    return nuevo_volcan


# ============== Endpoints de Extracción ==============

@app.post("/api/extraccion/iniciar", tags=["Extracción"])
def iniciar_extraccion(request: ExtraccionRequest, session = Depends(get_db)):
    """
    Inicia el proceso de extracción de datos TROPOMI para un volcán.
    Publica un comando en el Message Bus para procesamiento asíncrono.
    """
    # Verificar volcán
    volcan = session.query(Volcan).get(request.volcan_id)
    if not volcan:
        raise HTTPException(status_code=404, detail="Volcán no encontrado")
    
    try:
        # Publicar comando
        mb = MessageBus()
        mb.publicar_comando_extraccion(
            volcan_id=request.volcan_id,
            fecha_inicio=request.fecha_inicio.isoformat(),
            fecha_fin=request.fecha_fin.isoformat(),
            parametros={'descargar': request.descargar}
        )
        mb.cerrar()
        
        return {
            "mensaje": "Extracción iniciada",
            "volcan": volcan.nombre,
            "fecha_inicio": request.fecha_inicio,
            "fecha_fin": request.fecha_fin
        }
        
    except Exception as e:
        logger.error(f"Error iniciando extracción: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/extraccion/sincrona", tags=["Extracción"])
def ejecutar_extraccion_sincrona(request: ExtraccionRequest, session = Depends(get_db)):
    """
    Ejecuta la extracción de datos de forma síncrona (espera a completar).
    Útil para pruebas o cuando se necesita el resultado inmediatamente.
    """
    from etl.tropomi_downloader import buscar_y_descargar_tropomi
    
    volcan = session.query(Volcan).get(request.volcan_id)
    if not volcan:
        raise HTTPException(status_code=404, detail="Volcán no encontrado")
    
    try:
        resultado = buscar_y_descargar_tropomi(
            volcan_nombre=volcan.nombre,
            lat=volcan.latitud,
            lon=volcan.longitud,
            fecha_inicio=request.fecha_inicio,
            fecha_fin=request.fecha_fin,
            descargar=request.descargar
        )
        
        return resultado
        
    except Exception as e:
        logger.error(f"Error en extracción: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============== Endpoints de Imágenes ==============

@app.get("/api/imagenes", tags=["Imágenes"])
def listar_imagenes(
    volcan_id: Optional[int] = Query(None),
    descargado: Optional[bool] = Query(None),
    procesado: Optional[bool] = Query(None),
    limite: int = Query(100, le=1000),
    session = Depends(get_db)
):
    """Lista las imágenes TROPOMI disponibles"""
    query = session.query(ImagenTROPOMI)
    
    if volcan_id:
        query = query.filter(ImagenTROPOMI.volcan_id == volcan_id)
    if descargado is not None:
        query = query.filter(ImagenTROPOMI.descargado == descargado)
    if procesado is not None:
        query = query.filter(ImagenTROPOMI.procesado == procesado)
    
    imagenes = query.order_by(ImagenTROPOMI.fecha_adquisicion.desc()).limit(limite).all()
    
    return [
        {
            "id": img.id,
            "volcan_id": img.volcan_id,
            "fecha_adquisicion": img.fecha_adquisicion,
            "descargado": img.descargado,
            "procesado": img.procesado,
            "modo_operacion": img.modo_operacion
        }
        for img in imagenes
    ]


@app.get("/api/imagenes/{imagen_id}", tags=["Imágenes"])
def obtener_imagen(imagen_id: int, session = Depends(get_db)):
    """Obtiene detalles de una imagen específica"""
    imagen = session.query(ImagenTROPOMI).get(imagen_id)
    
    if not imagen:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")
    
    return {
        "id": imagen.id,
        "volcan_id": imagen.volcan_id,
        "producto_id": imagen.producto_id,
        "nombre_archivo": imagen.nombre_archivo,
        "fecha_adquisicion": imagen.fecha_adquisicion,
        "bbox": {
            "norte": imagen.bbox_norte,
            "sur": imagen.bbox_sur,
            "este": imagen.bbox_este,
            "oeste": imagen.bbox_oeste
        },
        "descargado": imagen.descargado,
        "procesado": imagen.procesado,
        "ruta_archivo": imagen.ruta_archivo,
        "tamano_bytes": imagen.tamano_bytes
    }


# ============== Endpoints de Cálculos ==============

@app.post("/api/calculos/iniciar", tags=["Cálculos"])
def iniciar_calculo(request: CalculoRequest, session = Depends(get_db)):
    """
    Inicia el cálculo de flujo para una imagen.
    Publica comando en Message Bus para procesamiento asíncrono.
    """
    imagen = session.query(ImagenTROPOMI).get(request.imagen_id)
    if not imagen:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")
    
    if not imagen.descargado:
        raise HTTPException(status_code=400, detail="La imagen no está descargada")
    
    try:
        mb = MessageBus()
        mb.publicar_comando_calculos(
            imagen_id=request.imagen_id,
            parametros={'altitud_viento_m': request.altitud_viento_m}
        )
        mb.cerrar()
        
        return {
            "mensaje": "Cálculo iniciado",
            "imagen_id": request.imagen_id
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/calculos/sincronos", tags=["Cálculos"])
def ejecutar_calculo_sincrono(request: CalculoRequest, session = Depends(get_db)):
    """
    Ejecuta el cálculo de flujo de forma síncrona.
    """
    from calculador.flujo_so2 import procesar_imagen_completa
    
    imagen = session.query(ImagenTROPOMI).get(request.imagen_id)
    if not imagen:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")
    
    if not imagen.descargado:
        raise HTTPException(status_code=400, detail="La imagen no está descargada")
    
    try:
        resultado = procesar_imagen_completa(
            imagen_id=request.imagen_id,
            altitud_viento_m=request.altitud_viento_m
        )
        
        if not resultado:
            raise HTTPException(status_code=500, detail="Error en el cálculo")
        
        return resultado
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============== Endpoints de Resultados ==============

@app.get("/api/resultados", response_model=List[FlujoResponse], tags=["Resultados"])
def listar_resultados(
    volcan_id: Optional[int] = Query(None),
    fecha_inicio: Optional[datetime] = Query(None),
    fecha_fin: Optional[datetime] = Query(None),
    qa_max: Optional[int] = Query(None, description="Filtrar por flag de calidad máximo"),
    limite: int = Query(100, le=1000),
    session = Depends(get_db)
):
    """Lista los resultados de flujo de SO2"""
    query = session.query(ResultadoFlujoSO2)
    
    if volcan_id:
        query = query.filter(ResultadoFlujoSO2.volcan_id == volcan_id)
    if fecha_inicio:
        query = query.filter(ResultadoFlujoSO2.fecha_hora >= fecha_inicio)
    if fecha_fin:
        query = query.filter(ResultadoFlujoSO2.fecha_hora <= fecha_fin)
    if qa_max is not None:
        query = query.filter(ResultadoFlujoSO2.qa_flag <= qa_max)
    
    resultados = query.order_by(ResultadoFlujoSO2.fecha_hora.desc()).limit(limite).all()
    
    return resultados


@app.get("/api/resultados/{resultado_id}", tags=["Resultados"])
def obtener_resultado(resultado_id: int, session = Depends(get_db)):
    """Obtiene detalles completos de un resultado"""
    resultado = session.query(ResultadoFlujoSO2).get(resultado_id)
    
    if not resultado:
        raise HTTPException(status_code=404, detail="Resultado no encontrado")
    
    return {
        "id": resultado.id,
        "volcan_id": resultado.volcan_id,
        "imagen_id": resultado.imagen_id,
        "fecha_hora": resultado.fecha_hora,
        "flujo": {
            "kg_s": resultado.flujo_so2_kg_s,
            "ton_dia": resultado.flujo_so2_ton_dia
        },
        "so2": {
            "max": resultado.columna_so2_max,
            "total": resultado.columna_so2_total
        },
        "viento": {
            "velocidad_ms": resultado.velocidad_viento_ms,
            "direccion_grados": resultado.direccion_viento_grados
        },
        "pluma": {
            "ancho_km": resultado.ancho_pluma_km,
            "distancia_km": resultado.distancia_volcan_km,
            "azimut_grados": resultado.azimut_pluma_grados
        },
        "calidad": {
            "incertidumbre_pct": resultado.incertidumbre_pct,
            "qa_flag": resultado.qa_flag
        },
        "metadatos": resultado.metadatos_json
    }


@app.post("/api/resultados/serie-temporal", tags=["Resultados"])
def obtener_serie_temporal(request: SerieTemporalRequest, session = Depends(get_db)):
    """
    Obtiene la serie temporal de flujo de SO2 para un volcán.
    Útil para graficar la evolución temporal.
    """
    from calculador.flujo_so2 import CalculadorFlujoSO2
    
    calculador = CalculadorFlujoSO2()
    
    serie = calculador.obtener_serie_temporal(
        volcan_id=request.volcan_id,
        fecha_inicio=request.fecha_inicio,
        fecha_fin=request.fecha_fin
    )
    
    calculador.cerrar()
    
    return {
        "volcan_id": request.volcan_id,
        "fecha_inicio": request.fecha_inicio,
        "fecha_fin": request.fecha_fin,
        "n_datos": len(serie),
        "datos": serie
    }


# ============== Endpoints de Estadísticas ==============

@app.get("/api/estadisticas/{volcan_id}", response_model=EstadisticasResponse, tags=["Estadísticas"])
def obtener_estadisticas(volcan_id: int, session = Depends(get_db)):
    """Obtiene estadísticas resumidas para un volcán"""
    from sqlalchemy import func
    
    volcan = session.query(Volcan).get(volcan_id)
    if not volcan:
        raise HTTPException(status_code=404, detail="Volcán no encontrado")
    
    # Contar imágenes
    n_imagenes = session.query(func.count(ImagenTROPOMI.id)).filter(
        ImagenTROPOMI.volcan_id == volcan_id
    ).scalar()
    
    n_procesadas = session.query(func.count(ImagenTROPOMI.id)).filter(
        ImagenTROPOMI.volcan_id == volcan_id,
        ImagenTROPOMI.procesado == True
    ).scalar()
    
    # Estadísticas de resultados
    stats = session.query(
        func.count(ResultadoFlujoSO2.id),
        func.avg(ResultadoFlujoSO2.flujo_so2_ton_dia),
        func.max(ResultadoFlujoSO2.flujo_so2_ton_dia),
        func.min(ResultadoFlujoSO2.fecha_hora),
        func.max(ResultadoFlujoSO2.fecha_hora)
    ).filter(
        ResultadoFlujoSO2.volcan_id == volcan_id
    ).first()
    
    return EstadisticasResponse(
        volcan_id=volcan_id,
        volcan_nombre=volcan.nombre,
        n_imagenes_total=n_imagenes,
        n_imagenes_procesadas=n_procesadas,
        n_resultados=stats[0] or 0,
        flujo_promedio_ton_dia=stats[1],
        flujo_max_ton_dia=stats[2],
        fecha_primera_medicion=stats[3],
        fecha_ultima_medicion=stats[4]
    )


# ============== Endpoints de Sistema ==============

@app.get("/api/sistema/salud", tags=["Sistema"])
def verificar_salud():
    """Verifica el estado del sistema"""
    estado = {
        "api": "ok",
        "timestamp": datetime.utcnow().isoformat()
    }
    
    # Verificar base de datos
    try:
        session = get_session()
        session.execute("SELECT 1")
        session.close()
        estado["base_datos"] = "ok"
    except Exception as e:
        estado["base_datos"] = f"error: {str(e)}"
    
    # Verificar RabbitMQ
    try:
        mb = MessageBus()
        mb.cerrar()
        estado["message_bus"] = "ok"
    except Exception as e:
        estado["message_bus"] = f"error: {str(e)}"
    
    return estado


@app.get("/api/sistema/logs", tags=["Sistema"])
def obtener_logs(
    nivel: Optional[str] = Query(None),
    componente: Optional[str] = Query(None),
    limite: int = Query(100, le=500),
    session = Depends(get_db)
):
    """Obtiene los logs del sistema"""
    query = session.query(LogProcesamiento)
    
    if nivel:
        query = query.filter(LogProcesamiento.nivel == nivel.upper())
    if componente:
        query = query.filter(LogProcesamiento.componente == componente)
    
    logs = query.order_by(LogProcesamiento.timestamp.desc()).limit(limite).all()
    
    return [
        {
            "id": log.id,
            "timestamp": log.timestamp,
            "nivel": log.nivel,
            "componente": log.componente,
            "mensaje": log.mensaje,
            "detalles": log.detalles_json
        }
        for log in logs
    ]


@app.on_event("startup")
async def startup_event():
    """Inicialización al arrancar la API"""
    logger.info("Iniciando API de Monitoreo SO2...")
    try:
        init_database()
        logger.info("Base de datos verificada")
    except Exception as e:
        logger.error(f"Error inicializando base de datos: {e}")


def run_api():
    """Función para ejecutar la API"""
    import uvicorn
    uvicorn.run(app, host=API_HOST, port=API_PORT)


if __name__ == "__main__":
    run_api()
