"""
API REST para el sistema de monitoreo de SO2
Implementada con FastAPI
"""
from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from datetime import datetime, timedelta
from pathlib import Path
import logging
import io

from database import (
    get_session, Volcan, ImagenTROPOMI, DatosViento,
    ResultadoFlujoSO2, LogProcesamiento, init_database
)
from config.settings import VOLCANES_PREDEFINIDOS, API_HOST, API_PORT

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
    allow_origins=["*"],
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


class FlujoResponse(BaseModel):
    id: int
    volcan_id: int
    imagen_id: Optional[int]
    fecha_hora: datetime
    flujo_so2_kg_s: Optional[float]
    flujo_so2_ton_dia: Optional[float]
    velocidad_viento_ms: Optional[float]
    direccion_viento_grados: Optional[float]
    altitud_viento_m: Optional[float]
    azimut_pluma_grados: Optional[float]
    incertidumbre_pct: Optional[float]
    qa_flag: Optional[int]
    metadatos_json: Optional[Dict] = None
    
    class Config:
        from_attributes = True


class GEEDescargaRequest(BaseModel):
    volcan_id: int = Field(..., description="ID del volcán")
    fecha_inicio: datetime = Field(..., description="Fecha inicial")
    fecha_fin: datetime = Field(..., description="Fecha final")
    radio_km: float = Field(100, description="Radio del AOI en km")
    una_por_dia: bool = Field(True, description="Descargar solo una imagen por día")


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


# (Endpoints de extracción y cálculos Copernicus eliminados - solo se usa GEE)


# ============== Endpoints de Imágenes ==============

@app.get("/api/imagenes", tags=["Imágenes"])
def listar_imagenes(
    volcan_id: Optional[int] = Query(None),
    descargado: Optional[bool] = Query(None),
    procesado: Optional[bool] = Query(None),
    limite: int = Query(100, le=500),
    session = Depends(get_db)
):
    """Lista las imágenes disponibles"""
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
            "producto_id": img.producto_id,
            "fecha_adquisicion": img.fecha_adquisicion,
            "descargado": img.descargado,
            "procesado": img.procesado,
            "ruta_archivo": img.ruta_archivo
        }
        for img in imagenes
    ]


@app.get("/api/imagenes/{imagen_id}", tags=["Imágenes"])
def obtener_imagen(imagen_id: int, session = Depends(get_db)):
    """Obtiene información de una imagen específica"""
    imagen = session.query(ImagenTROPOMI).get(imagen_id)
    
    if not imagen:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")
    
    return {
        "id": imagen.id,
        "volcan_id": imagen.volcan_id,
        "producto_id": imagen.producto_id,
        "fecha_adquisicion": imagen.fecha_adquisicion,
        "descargado": imagen.descargado,
        "procesado": imagen.procesado,
        "ruta_archivo": imagen.ruta_archivo,
        "metadatos": imagen.metadatos_json
    }


# ============== Endpoints de Resultados ==============

@app.get("/api/resultados", tags=["Resultados"])
def listar_resultados(
    volcan_id: Optional[int] = Query(None),
    fecha_inicio: Optional[datetime] = Query(None),
    fecha_fin: Optional[datetime] = Query(None),
    limite: int = Query(100, le=500),
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
    
    resultados = query.order_by(ResultadoFlujoSO2.fecha_hora.desc()).limit(limite).all()
    
    return [
        {
            "id": r.id,
            "volcan_id": r.volcan_id,
            "imagen_id": r.imagen_id,
            "fecha_hora": r.fecha_hora,
            "flujo_so2_kg_s": r.flujo_so2_kg_s,
            "flujo_so2_ton_dia": r.flujo_so2_ton_dia,
            "velocidad_viento_ms": r.velocidad_viento_ms,
            "direccion_viento_grados": r.direccion_viento_grados,
            "altitud_viento_m": r.altitud_viento_m,
            "azimut_pluma_grados": r.azimut_pluma_grados,
            "incertidumbre_pct": r.incertidumbre_pct,
            "qa_flag": r.qa_flag,
            "metadatos_json": r.metadatos_json
        }
        for r in resultados
    ]


@app.get("/api/resultados/{resultado_id}", tags=["Resultados"])
def obtener_resultado(resultado_id: int, session = Depends(get_db)):
    """Obtiene un resultado específico con todos sus detalles"""
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
    
    n_imagenes = session.query(func.count(ImagenTROPOMI.id)).filter(
        ImagenTROPOMI.volcan_id == volcan_id
    ).scalar()
    
    n_procesadas = session.query(func.count(ImagenTROPOMI.id)).filter(
        ImagenTROPOMI.volcan_id == volcan_id,
        ImagenTROPOMI.procesado == True
    ).scalar()
    
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
    
    try:
        session = get_session()
        session.execute("SELECT 1")
        session.close()
        estado["base_datos"] = "ok"
    except Exception as e:
        estado["base_datos"] = f"error: {str(e)}"
    
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


# ============== Endpoints de Borrado ==============

@app.delete("/api/imagenes/{imagen_id}", tags=["Imágenes"])
def borrar_imagen(imagen_id: int, session = Depends(get_db)):
    """Borra una imagen y sus resultados asociados"""
    imagen = session.query(ImagenTROPOMI).get(imagen_id)
    if not imagen:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")
    
    session.query(ResultadoFlujoSO2).filter(
        ResultadoFlujoSO2.imagen_id == imagen_id
    ).delete()
    
    session.delete(imagen)
    session.commit()
    
    return {"mensaje": f"Imagen {imagen_id} eliminada"}


@app.delete("/api/resultados/borrar", tags=["Resultados"])
def borrar_resultados(
    volcan_id: int = Query(...),
    fecha_inicio: Optional[datetime] = Query(None),
    fecha_fin: Optional[datetime] = Query(None),
    session = Depends(get_db)
):
    """Borra resultados de un volcán en un rango de fechas"""
    query = session.query(ResultadoFlujoSO2).filter(
        ResultadoFlujoSO2.volcan_id == volcan_id
    )
    
    if fecha_inicio:
        query = query.filter(ResultadoFlujoSO2.fecha_hora >= fecha_inicio)
    if fecha_fin:
        query = query.filter(ResultadoFlujoSO2.fecha_hora <= fecha_fin)
    
    count = query.delete()
    session.commit()
    
    return {"mensaje": f"{count} resultados eliminados"}


@app.delete("/api/volcanes/{volcan_id}", tags=["Volcanes"])
def eliminar_volcan(volcan_id: int, session = Depends(get_db)):
    """Elimina un volcán y todos sus datos asociados"""
    volcan = session.query(Volcan).get(volcan_id)
    if not volcan:
        raise HTTPException(status_code=404, detail="Volcán no encontrado")
    
    session.query(ResultadoFlujoSO2).filter(
        ResultadoFlujoSO2.volcan_id == volcan_id
    ).delete()
    
    session.query(DatosViento).filter(
        DatosViento.volcan_id == volcan_id
    ).delete()
    
    session.query(ImagenTROPOMI).filter(
        ImagenTROPOMI.volcan_id == volcan_id
    ).delete()
    
    session.delete(volcan)
    session.commit()
    
    return {"mensaje": f"Volcán {volcan.nombre} eliminado con todos sus datos"}



# ============== Endpoint de Visualización de Imágenes ==============

@app.get("/api/imagenes/{imagen_id}/preview", tags=["Imágenes"])
def preview_imagen(
    imagen_id: int,
    colormap: str = Query("hot", description="Mapa de colores (hot, inferno, viridis, plasma, jet)"),
    session = Depends(get_db)
):
    """
    Genera un preview PNG de una imagen GeoTIFF TROPOMI.
    
    Renderiza los datos SO2 con un colormap y devuelve la imagen PNG
    para visualización en el navegador.
    """
    imagen = session.query(ImagenTROPOMI).get(imagen_id)
    if not imagen:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")
    
    if not imagen.ruta_archivo or not Path(imagen.ruta_archivo).exists():
        raise HTTPException(status_code=404, detail="Archivo de imagen no encontrado en disco")
    
    try:
        import numpy as np
        import rasterio
        from matplotlib import cm
        from PIL import Image
        
        # Leer GeoTIFF
        with rasterio.open(imagen.ruta_archivo) as ds:
            data = ds.read(1).astype(np.float64)
        
        # Limpiar datos: valores <= 0 o muy negativos = NaN
        data[data <= 0] = np.nan
        data[data < -1e30] = np.nan
        
        # Convertir mol/m² a DU para mejor visualización (1 DU = 2.69e20 molec/m²)
        # Pero para simplificar, normalizamos directamente
        valid = data[~np.isnan(data)]
        
        if len(valid) == 0:
            raise HTTPException(status_code=404, detail="Imagen sin datos SO2 válidos")
        
        # Normalizar a 0-1 usando percentiles para mejor contraste
        vmin = np.percentile(valid, 2)
        vmax = np.percentile(valid, 98)
        
        if vmax <= vmin:
            vmax = vmin + 1e-10
        
        normalized = np.clip((data - vmin) / (vmax - vmin), 0, 1)
        
        # Aplicar colormap
        colormaps_validos = ['hot', 'inferno', 'viridis', 'plasma', 'jet', 'magma', 'coolwarm']
        if colormap not in colormaps_validos:
            colormap = 'hot'
        
        cmap = cm.get_cmap(colormap)
        rgba = cmap(normalized)
        
        # Píxeles NaN = transparente
        rgba[np.isnan(data)] = [0, 0, 0, 0]
        
        # Convertir a imagen PIL (RGBA 0-255)
        img_array = (rgba * 255).astype(np.uint8)
        img = Image.fromarray(img_array, 'RGBA')
        
        # Guardar en buffer
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        
        return StreamingResponse(
            buf,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=3600",
                "Content-Disposition": f"inline; filename=SO2_preview_{imagen_id}.png"
            }
        )
        
    except HTTPException:
        raise
    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Dependencia faltante para generar preview: {e}. Instalar: pip install matplotlib Pillow"
        )
    except Exception as e:
        logger.error(f"Error generando preview: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/imagenes/{imagen_id}/preview-con-pluma", tags=["Imágenes"])
def preview_imagen_con_pluma(
    imagen_id: int,
    resultado_id: Optional[int] = Query(None, description="ID del resultado para superponer info de pluma"),
    colormap: str = Query("hot", description="Mapa de colores"),
    session = Depends(get_db)
):
    """
    Genera un preview PNG con la posición del volcán y dirección de pluma superpuesta.
    Versión más completa que incluye anotaciones.
    """
    imagen = session.query(ImagenTROPOMI).get(imagen_id)
    if not imagen:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")
    
    if not imagen.ruta_archivo or not Path(imagen.ruta_archivo).exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    
    volcan = session.query(Volcan).get(imagen.volcan_id)
    
    # Buscar resultado asociado
    resultado = None
    if resultado_id:
        resultado = session.query(ResultadoFlujoSO2).get(resultado_id)
    elif imagen.procesado:
        resultado = session.query(ResultadoFlujoSO2).filter(
            ResultadoFlujoSO2.imagen_id == imagen_id
        ).first()
    
    try:
        import numpy as np
        import rasterio
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib.colors import Normalize
        import math
        
        with rasterio.open(imagen.ruta_archivo) as ds:
            data = ds.read(1).astype(np.float64)
            transform = ds.transform
            bounds = ds.bounds
        
        # Limpiar datos
        data[data <= 0] = np.nan
        data[data < -1e30] = np.nan
        
        valid = data[~np.isnan(data)]
        if len(valid) == 0:
            raise HTTPException(status_code=404, detail="Sin datos SO2 válidos")
        
        # Crear figura con matplotlib
        fig, ax = plt.subplots(1, 1, figsize=(8, 7), facecolor='#1a1a2e')
        ax.set_facecolor('#1a1a2e')
        
        # Normalizar con percentiles
        vmin = np.percentile(valid, 5)
        vmax = np.percentile(valid, 95)
        
        # Plot SO2
        extent = [bounds.left, bounds.right, bounds.bottom, bounds.top]
        im = ax.imshow(
            data, extent=extent, origin='upper',
            cmap=colormap if colormap in ['hot','inferno','viridis','plasma','jet','magma','coolwarm'] else 'hot',
            norm=Normalize(vmin=vmin, vmax=vmax),
            interpolation='nearest', alpha=0.9
        )
        
        # Colorbar
        cbar = plt.colorbar(im, ax=ax, shrink=0.8, pad=0.02)
        cbar.set_label('SO₂ (mol/m²)', color='white', fontsize=10)
        cbar.ax.yaxis.set_tick_params(color='white')
        plt.setp(plt.getp(cbar.ax.axes, 'yticklabels'), color='white', fontsize=8)
        
        # Marcar volcán
        if volcan:
            ax.plot(volcan.longitud, volcan.latitud, '^', color='#00ff88',
                    markersize=14, markeredgecolor='white', markeredgewidth=1.5, zorder=10)
            ax.annotate(volcan.nombre, (volcan.longitud, volcan.latitud),
                       textcoords="offset points", xytext=(10, 10),
                       color='#00ff88', fontsize=10, fontweight='bold',
                       bbox=dict(boxstyle='round,pad=0.3', facecolor='#1a1a2e', edgecolor='#00ff88', alpha=0.8))
        
        # Dibujar dirección de pluma si hay resultado
        if resultado and volcan and resultado.azimut_pluma_grados is not None:
            azimut_rad = math.radians(resultado.azimut_pluma_grados)
            delta = (bounds.right - bounds.left) * 0.35
            dx = delta * math.sin(azimut_rad)
            dy = delta * math.cos(azimut_rad)
            
            ax.annotate('', xy=(volcan.longitud + dx, volcan.latitud + dy),
                        xytext=(volcan.longitud, volcan.latitud),
                        arrowprops=dict(arrowstyle='->', color='#00d9ff', lw=2.5))
            
            # Info texto
            info_lines = []
            if resultado.flujo_so2_ton_dia:
                info_lines.append(f"Flujo: {resultado.flujo_so2_ton_dia:.1f} t/d")
            if resultado.velocidad_viento_ms:
                info_lines.append(f"Viento: {resultado.velocidad_viento_ms:.1f} m/s")
            if resultado.altitud_viento_m:
                info_lines.append(f"Alt: {resultado.altitud_viento_m:.0f} m")
            info_lines.append(f"Azimut: {resultado.azimut_pluma_grados:.1f}°")
            
            info_text = '\n'.join(info_lines)
            ax.text(0.02, 0.98, info_text, transform=ax.transAxes,
                   fontsize=9, color='white', verticalalignment='top',
                   bbox=dict(boxstyle='round', facecolor='#16213e', edgecolor='#00d9ff', alpha=0.9))
        
        # Título
        fecha_str = imagen.fecha_adquisicion.strftime('%Y-%m-%d %H:%M') if imagen.fecha_adquisicion else 'Fecha desconocida'
        ax.set_title(f"TROPOMI SO₂ — {fecha_str}", color='white', fontsize=12, pad=10)
        
        ax.set_xlabel('Longitud', color='white', fontsize=9)
        ax.set_ylabel('Latitud', color='white', fontsize=9)
        ax.tick_params(colors='white', labelsize=8)
        for spine in ax.spines.values():
            spine.set_edgecolor('#333')
        
        plt.tight_layout()
        
        # Guardar en buffer
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=120, bbox_inches='tight',
                   facecolor=fig.get_facecolor(), edgecolor='none')
        plt.close(fig)
        buf.seek(0)
        
        return StreamingResponse(
            buf,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=3600",
                "Content-Disposition": f"inline; filename=SO2_pluma_{imagen_id}.png"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generando preview con pluma: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ============== Endpoints de Google Earth Engine ==============

@app.post("/api/gee/descargar", tags=["Google Earth Engine"])
def descargar_desde_gee(request: GEEDescargaRequest, session = Depends(get_db)):
    """
    Descarga imágenes TROPOMI desde Google Earth Engine.
    
    Usa la misma colección que el profesor: COPERNICUS/S5P/NRTI/L3_SO2
    Guarda como GeoTIFF con resolución 1000m.
    """
    from etl.gee_tropomi_downloader import descargar_tropomi_gee
    
    volcan = session.query(Volcan).get(request.volcan_id)
    if not volcan:
        raise HTTPException(status_code=404, detail="Volcán no encontrado")
    
    try:
        resultado = descargar_tropomi_gee(
            volcan_nombre=volcan.nombre,
            lat=volcan.latitud,
            lon=volcan.longitud,
            fecha_inicio=request.fecha_inicio,
            fecha_fin=request.fecha_fin,
            radio_km=request.radio_km,
            una_por_dia=request.una_por_dia,
            registrar_db=True
        )
        
        return {
            "mensaje": "Descarga GEE completada" if resultado.get('exito') else "Error en descarga",
            "volcan": volcan.nombre,
            "resultado": resultado
        }
        
    except Exception as e:
        logger.error(f"Error descargando desde GEE: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/gee/procesar/{imagen_id}", tags=["Google Earth Engine"])
def procesar_geotiff(
    imagen_id: int,
    azimut_manual: Optional[float] = None,
    session = Depends(get_db)
):
    """
    Procesa una imagen GeoTIFF descargada de GEE.
    
    Usa el método SO2FC con 6 franjas horarias, igual que el MATLAB del profesor.
    """
    from etl.geotiff_processor import GeoTIFFProcessor
    from etl.ncep_downloader import obtener_viento_para_imagen as obtener_viento_ncep
    from config.settings import SO2FC_FRANJAS_HORAS
    
    # Obtener imagen
    imagen = session.query(ImagenTROPOMI).get(imagen_id)
    if not imagen:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")
    
    if not imagen.ruta_archivo or not Path(imagen.ruta_archivo).exists():
        raise HTTPException(status_code=400, detail="Archivo de imagen no encontrado")
    
    # Obtener volcán
    volcan = session.query(Volcan).get(imagen.volcan_id)
    if not volcan:
        raise HTTPException(status_code=404, detail="Volcán no encontrado")
    
    try:
        # 1. Cargar y procesar GeoTIFF
        processor = GeoTIFFProcessor(imagen.ruta_archivo)
        
        # 2. Determinar fecha de la imagen
        fecha = imagen.fecha_adquisicion or processor.fecha
        if not fecha:
            raise HTTPException(status_code=400, detail="No se pudo determinar la fecha de la imagen")
        
        # 3. Detectar azimut de pluma PRIMERO (antes de obtener viento)
        #    Esto es crítico: el azimut determina la altura correcta del viento
        if azimut_manual is not None:
            azimut_pluma = azimut_manual
            logger.info(f"Usando azimut manual: {azimut_pluma}°")
        else:
            azimut_pluma = processor.detectar_azimut_pluma(
                lat_volcan=volcan.latitud,
                lon_volcan=volcan.longitud
            )
            
            if azimut_pluma is None:
                logger.warning("No se detectó pluma, se obtendrá viento sin azimut (primera altura)")
        
        # 4. Obtener datos de viento PASANDO el azimut de pluma
        #    Así buscar_altura_por_azimut selecciona la altura correcta
        viento = obtener_viento_ncep(
            volcan_id=volcan.id,
            lat=volcan.latitud,
            lon=volcan.longitud,
            fecha=fecha,
            altitud_m=volcan.altitud_m or 5000,
            azimut_pluma=azimut_pluma
        )
        
        if not viento:
            raise HTTPException(
                status_code=500, 
                detail="No se pudieron obtener datos de viento"
            )

        velocidad_viento = viento['velocidad_ms']
        direccion_viento = viento['direccion_grados']
        
        # Si no se detectó pluma, usar dirección del viento como fallback
        if azimut_pluma is None:
            azimut_pluma = direccion_viento
            logger.warning(f"Usando dirección del viento como azimut de pluma: {azimut_pluma}°")
        
        logger.info(f"Viento seleccionado: {velocidad_viento:.2f} m/s a {viento.get('altura_m')}m "
                    f"(azimut_pluma={azimut_pluma:.1f}°)")
        
        # 5. Calcular flujo por franjas horarias
        resultado_calculo = processor.calcular_so2_por_franja_horaria(
            lat_volcan=volcan.latitud,
            lon_volcan=volcan.longitud,
            velocidad_viento_ms=velocidad_viento,
            azimut_pluma=azimut_pluma
        )
        
        processor.cerrar()
        
        if not resultado_calculo.get('exito'):
            return {
                "mensaje": "No se pudo calcular flujo",
                "imagen_id": imagen_id,
                "error": resultado_calculo.get('mensaje'),
                "franjas": resultado_calculo.get('franjas', [])
            }
        
        # 6. Guardar resultado en base de datos
        resultado_db = ResultadoFlujoSO2(
            volcan_id=volcan.id,
            imagen_id=imagen.id,
            fecha_hora=fecha,
            flujo_so2_kg_s=resultado_calculo['flujo_promedio_kgs'],
            flujo_so2_ton_dia=resultado_calculo['flujo_promedio_td'],
            velocidad_viento_ms=velocidad_viento,
            direccion_viento_grados=direccion_viento,
            altitud_viento_m=viento.get('altura_m'),
            azimut_pluma_grados=azimut_pluma,
            metadatos_json={
                'metodo': 'SO2FC_GeoTIFF',
                'franjas': resultado_calculo['franjas'],
                'n_franjas_validas': resultado_calculo['n_franjas_validas'],
                'fuente_datos': 'GEE_L3'
            }
        )
        
        session.add(resultado_db)
        
        # Marcar imagen como procesada
        imagen.procesado = True
        imagen.fecha_procesamiento = datetime.utcnow()
        
        session.commit()
        
        return {
            "mensaje": "Procesamiento completado",
            "imagen_id": imagen_id,
            "resultado": {
                "flujo_kg_s": resultado_calculo['flujo_promedio_kgs'],
                "flujo_ton_dia": resultado_calculo['flujo_promedio_td'],
                "velocidad_viento_ms": velocidad_viento,
                "direccion_viento_grados": direccion_viento,
                "altitud_viento_m": viento.get('altura_m'),
                "azimut_pluma_grados": azimut_pluma,
                "n_franjas_validas": resultado_calculo['n_franjas_validas'],
                "franjas": resultado_calculo['franjas']
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error procesando GeoTIFF: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/gee/procesar-lote", tags=["Google Earth Engine"])
def procesar_lote_geotiff(
    volcan_id: int = Query(...),
    fecha_inicio: Optional[datetime] = Query(None),
    fecha_fin: Optional[datetime] = Query(None),
    session = Depends(get_db)
):
    """
    Procesa múltiples imágenes GeoTIFF de un volcán.
    """
    # Buscar imágenes GeoTIFF pendientes
    query = session.query(ImagenTROPOMI).filter(
        ImagenTROPOMI.volcan_id == volcan_id,
        ImagenTROPOMI.descargado == True,
        ImagenTROPOMI.procesado == False,
        ImagenTROPOMI.nombre_archivo.like('%_VCDofSO2_TROPOMI.tif')  # Solo GeoTIFF de GEE
    )
    
    if fecha_inicio:
        query = query.filter(ImagenTROPOMI.fecha_adquisicion >= fecha_inicio)
    if fecha_fin:
        query = query.filter(ImagenTROPOMI.fecha_adquisicion <= fecha_fin)
    
    imagenes = query.all()
    
    if not imagenes:
        return {"mensaje": "No hay imágenes GeoTIFF pendientes", "n_imagenes": 0}
    
    resultados = []
    errores = []
    
    for img in imagenes:
        try:
            # Llamar al endpoint individual (reutilizar lógica)
            # En producción podrías usar un worker async
            resultado = procesar_geotiff(img.id, session=session)
            resultados.append({
                "imagen_id": img.id,
                "exito": True,
                "flujo_ton_dia": resultado.get('resultado', {}).get('flujo_ton_dia')
            })
        except Exception as e:
            errores.append({
                "imagen_id": img.id,
                "error": str(e)
            })
    
    return {
        "mensaje": f"Procesamiento completado: {len(resultados)} éxitos, {len(errores)} errores",
        "n_procesadas": len(resultados),
        "n_errores": len(errores),
        "resultados": resultados,
        "errores": errores
    }


@app.get("/api/gee/estado", tags=["Google Earth Engine"])
def verificar_estado_gee():
    """
    Verifica si Google Earth Engine está configurado y funcionando.
    """
    try:
        import ee
        ee.Initialize(project='turing-terminus-398901')
        
        # Test simple: verificar que podemos acceder a la colección
        collection = ee.ImageCollection('COPERNICUS/S5P/NRTI/L3_SO2')
        size = collection.size().getInfo()
        
        return {
            "estado": "ok",
            "mensaje": "Google Earth Engine está configurado correctamente",
            "coleccion": "COPERNICUS/S5P/NRTI/L3_SO2",
            "imagenes_disponibles": size
        }
        
    except Exception as e:
        return {
            "estado": "error",
            "mensaje": str(e),
            "solucion": "Ejecute 'python -m earthengine authenticate' en la terminal"
        }


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