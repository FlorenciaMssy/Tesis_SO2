"""
Módulo ETL para descarga de imágenes TROPOMI SO2 via Google Earth Engine
Replica el método del profesor Carbajal.

Colección: COPERNICUS/S5P/NRTI/L3_SO2
Variable: SO2_column_number_density (VCD)
Resolución: 1000m
"""
import ee
import os
import time
import requests
import struct
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict
import logging
import numpy as np

try:
    from config.settings import IMAGES_DIR
except ImportError:
    IMAGES_DIR = Path('./data/tropomi')

try:
    from database import get_session, ImagenTROPOMI, Volcan
except ImportError:
    get_session = None
    ImagenTROPOMI = None
    Volcan = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class GEETROPOMIDownloader:
    """Descargador de imágenes TROPOMI SO2 usando Google Earth Engine."""
    
    COLLECTION_ID = 'COPERNICUS/S5P/NRTI/L3_SO2'
    BAND_VCD = 'SO2_column_number_density'
    SCALE = 1000
    CRS = 'EPSG:4326'
    GEE_PROJECT = 'turing-terminus-398901'
    
    def __init__(self):
        self.initialized = False
        self._inicializar_ee()
    
    def _inicializar_ee(self):
        try:
            ee.Initialize(project=self.GEE_PROJECT)
            self.initialized = True
            logger.info(f"Google Earth Engine inicializado con proyecto: {self.GEE_PROJECT}")
        except Exception as e:
            logger.warning(f"Error inicializando GEE: {e}")
            try:
                ee.Authenticate()
                ee.Initialize(project=self.GEE_PROJECT)
                self.initialized = True
            except Exception as e2:
                logger.error(f"No se pudo inicializar GEE: {e2}")
                self.initialized = False
    
    def _crear_aoi(self, lat: float, lon: float, radio_km: float = 100) -> ee.Geometry:
        delta = radio_km / 111.0
        return ee.Geometry.Rectangle([
            lon - delta, lat - delta,
            lon + delta, lat + delta
        ])
    
    def buscar_imagenes(
        self,
        lat: float,
        lon: float,
        fecha_inicio: datetime,
        fecha_fin: datetime,
        radio_km: float = 100
    ) -> List[Dict]:
        """Busca imágenes TROPOMI disponibles"""
        if not self.initialized:
            return []
        
        try:
            aoi = self._crear_aoi(lat, lon, radio_km)
            
            collection = ee.ImageCollection(self.COLLECTION_ID) \
                .filterBounds(aoi) \
                .filterDate(
                    fecha_inicio.strftime('%Y-%m-%d'),
                    (fecha_fin + timedelta(days=1)).strftime('%Y-%m-%d')
                )
            
            n_images = collection.size().getInfo()
            logger.info(f"Encontradas {n_images} imágenes TROPOMI en GEE")
            
            if n_images == 0:
                return []
            
            image_list = collection.toList(min(n_images, 50))
            imagenes = []
            
            for i in range(min(n_images, 50)):
                img = ee.Image(image_list.get(i))
                props = img.getInfo()['properties']
                time_start = props.get('system:time_start', 0)
                fecha = datetime.fromtimestamp(time_start / 1000)
                
                imagenes.append({
                    'index': i,
                    'fecha': fecha,
                    'fecha_str': fecha.strftime('%Y%m%d-%H%M')
                })
            
            imagenes.sort(key=lambda x: x['fecha'])
            return imagenes
            
        except Exception as e:
            logger.error(f"Error buscando imágenes: {e}")
            return []
    
    def descargar_imagen(
        self,
        lat: float,
        lon: float,
        nombre_volcan: str,
        fecha: datetime,
        radio_km: float = 100,
        directorio: Path = None
    ) -> Optional[str]:
        """Descarga imagen usando getPixels con formato GeoTIFF"""
        if not self.initialized:
            return None
        
        if directorio is None:
            directorio = Path(IMAGES_DIR) / nombre_volcan.lower()
        directorio.mkdir(parents=True, exist_ok=True)
        
        fecha_str = fecha.strftime('%Y%m%d-%H%M')
        nombre_archivo = f"{nombre_volcan}_{fecha_str}_VCDofSO2_TROPOMI.tif"
        ruta_destino = directorio / nombre_archivo
        
        if ruta_destino.exists():
            logger.info(f"Imagen ya existe: {ruta_destino}")
            return str(ruta_destino)
        
        try:
            aoi = self._crear_aoi(lat, lon, radio_km)
            
            # Filtrar por día específico
            fecha_inicio = fecha.replace(hour=0, minute=0, second=0)
            fecha_fin_dia = fecha_inicio + timedelta(days=1)
            
            collection = ee.ImageCollection(self.COLLECTION_ID) \
                .filterBounds(aoi) \
                .filterDate(
                    fecha_inicio.strftime('%Y-%m-%d'),
                    fecha_fin_dia.strftime('%Y-%m-%d')
                ) \
                .select(self.BAND_VCD)
            
            # Tomar la imagen INDIVIDUAL más cercana a la hora (como hace el profesor)
            # El profesor exporta cada imagen por separado, NO promedia
            n_imgs = collection.size().getInfo()
            
            if n_imgs == 0:
                logger.warning(f"No hay imágenes para {fecha.strftime('%Y-%m-%d')}")
                return None
            
            if n_imgs == 1:
                img = collection.first().clip(aoi)
            else:
                # Buscar la imagen más cercana a la hora de la pasada
                img_list = collection.toList(n_imgs)
                mejor_idx = 0
                mejor_dif = float('inf')
                
                for idx in range(n_imgs):
                    img_i = ee.Image(img_list.get(idx))
                    t_start = img_i.get('system:time_start').getInfo()
                    fecha_img = datetime.fromtimestamp(t_start / 1000)
                    dif = abs((fecha_img - fecha).total_seconds())
                    if dif < mejor_dif:
                        mejor_dif = dif
                        mejor_idx = idx
                
                img = ee.Image(img_list.get(mejor_idx)).clip(aoi)
            
            logger.info(f"Descargando {nombre_archivo} (imagen individual, no promedio)...")
            
            # Obtener bounds
            bounds = aoi.bounds().getInfo()['coordinates'][0]
            min_lon = min(p[0] for p in bounds)
            max_lon = max(p[0] for p in bounds)
            min_lat = min(p[1] for p in bounds)
            max_lat = max(p[1] for p in bounds)
            
            # Calcular dimensiones
            pixel_size = self.SCALE / 111000  # metros a grados
            width = int((max_lon - min_lon) / pixel_size)
            height = int((max_lat - min_lat) / pixel_size)
            
            # Limitar tamaño
            max_dim = 256
            if width > max_dim:
                width = max_dim
            if height > max_dim:
                height = max_dim
            
            logger.info(f"Dimensiones: {width}x{height} pixels")
            
            # Método: usar getDownloadURL con imagen compuesta
            # La clave es usar .unmask() para evitar problemas con valores nulos
            img_masked = img.unmask(-9999)
            
            url = img_masked.getDownloadURL({
                'scale': self.SCALE,
                'crs': self.CRS,
                'region': aoi.getInfo()['coordinates'],
                'format': 'GEO_TIFF',
                'filePerBand': False
            })
            
            # Descargar
            response = requests.get(url, timeout=300)
            response.raise_for_status()
            
            # Verificar que es un GeoTIFF válido
            if response.content[:4] in [b'II*\x00', b'MM\x00*']:
                # Es un TIFF válido
                with open(ruta_destino, 'wb') as f:
                    f.write(response.content)
                logger.info(f"Imagen descargada: {ruta_destino}")
                return str(ruta_destino)
            else:
                # Puede ser un error de GEE
                logger.error(f"Respuesta no es GeoTIFF: {response.content[:100]}")
                
                # Intentar método alternativo con reduceRegion
                return self._descargar_con_reduce_region(
                    img, aoi, nombre_volcan, fecha, 
                    min_lon, max_lon, min_lat, max_lat,
                    width, height, ruta_destino
                )
            
        except Exception as e:
            logger.error(f"Error descargando imagen: {e}")
            import traceback
            traceback.print_exc()
            
            # Intentar método alternativo
            try:
                return self._descargar_con_reduce_region(
                    img, aoi, nombre_volcan, fecha,
                    min_lon, max_lon, min_lat, max_lat,
                    width, height, ruta_destino
                )
            except Exception as e2:
                logger.error(f"Error en método alternativo: {e2}")
                return None
    
    def _descargar_con_reduce_region(
        self, img, aoi, nombre_volcan, fecha,
        min_lon, max_lon, min_lat, max_lat,
        width, height, ruta_destino
    ) -> Optional[str]:
        """Método alternativo usando sampleRectangle"""
        try:
            import rasterio
            from rasterio.transform import from_bounds
            
            logger.info("Intentando método alternativo con sampleRectangle...")
            
            # Crear región más pequeña para evitar límites
            region = ee.Geometry.Rectangle([min_lon, min_lat, max_lon, max_lat])
            
            # Usar sampleRectangle
            sample = img.sampleRectangle(region=region, defaultValue=-9999)
            
            # Obtener datos
            band_data = sample.get(self.BAND_VCD).getInfo()
            
            if band_data is None:
                logger.error("No se obtuvieron datos con sampleRectangle")
                return None
            
            # Convertir a numpy
            so2_data = np.array(band_data, dtype=np.float32)
            
            # Reemplazar valores inválidos
            so2_data[so2_data == -9999] = np.nan
            so2_data[so2_data < -1e30] = np.nan
            
            # Guardar como GeoTIFF
            transform = from_bounds(min_lon, min_lat, max_lon, max_lat, 
                                   so2_data.shape[1], so2_data.shape[0])
            
            with rasterio.open(
                ruta_destino, 'w',
                driver='GTiff',
                height=so2_data.shape[0],
                width=so2_data.shape[1],
                count=1,
                dtype=np.float32,
                crs=self.CRS,
                transform=transform,
                nodata=np.nan
            ) as dst:
                dst.write(so2_data, 1)
            
            logger.info(f"Imagen descargada (sampleRectangle): {ruta_destino}")
            return str(ruta_destino)
            
        except Exception as e:
            logger.error(f"Error en sampleRectangle: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def descargar_rango_fechas(
        self,
        volcan_nombre: str,
        lat: float,
        lon: float,
        fecha_inicio: datetime,
        fecha_fin: datetime,
        radio_km: float = 100,
        una_por_dia: bool = True
    ) -> List[str]:
        """Descarga imágenes para un rango de fechas"""
        
        imagenes = self.buscar_imagenes(lat, lon, fecha_inicio, fecha_fin, radio_km)
        
        if not imagenes:
            logger.warning("No se encontraron imágenes")
            return []
        
        # Filtrar una por día
        if una_por_dia:
            por_dia = {}
            for img in imagenes:
                dia = img['fecha'].strftime('%Y%m%d')
                if dia not in por_dia:
                    por_dia[dia] = img
            imagenes = list(por_dia.values())
            logger.info(f"Filtradas a {len(imagenes)} imágenes (una por día)")
        
        # Descargar
        archivos = []
        for img_info in imagenes:
            try:
                archivo = self.descargar_imagen(
                    lat=lat,
                    lon=lon,
                    nombre_volcan=volcan_nombre,
                    fecha=img_info['fecha'],
                    radio_km=radio_km
                )
                if archivo:
                    archivos.append(archivo)
            except Exception as e:
                logger.error(f"Error: {e}")
                continue
        
        logger.info(f"Descargadas {len(archivos)} imágenes")
        return archivos


def descargar_tropomi_gee(
    volcan_nombre: str,
    lat: float,
    lon: float,
    fecha_inicio: datetime,
    fecha_fin: datetime,
    radio_km: float = 100,
    una_por_dia: bool = True,
    registrar_db: bool = True
) -> Dict:
    """Función principal para descargar imágenes TROPOMI desde GEE"""
    
    downloader = GEETROPOMIDownloader()
    
    if not downloader.initialized:
        return {
            'exito': False,
            'mensaje': 'No se pudo inicializar Google Earth Engine',
            'imagenes': []
        }
    
    archivos = downloader.descargar_rango_fechas(
        volcan_nombre=volcan_nombre,
        lat=lat,
        lon=lon,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        radio_km=radio_km,
        una_por_dia=una_por_dia
    )
    
    # Registrar en DB
    imagenes_registradas = []
    if registrar_db and archivos and get_session is not None:
        try:
            session = get_session()
            volcan = session.query(Volcan).filter_by(nombre=volcan_nombre).first()
            if not volcan:
                volcan = session.query(Volcan).filter(
                    Volcan.nombre.ilike(f'%{volcan_nombre}%')
                ).first()
            
            if volcan:
                for archivo in archivos:
                    nombre = Path(archivo).name
                    partes = nombre.split('_')
                    try:
                        fecha = datetime.strptime(partes[1], '%Y%m%d-%H%M')
                    except:
                        fecha = datetime.now()
                    
                    existente = session.query(ImagenTROPOMI).filter_by(
                        volcan_id=volcan.id,
                        ruta_archivo=archivo
                    ).first()
                    
                    if not existente:
                        imagen = ImagenTROPOMI(
                            volcan_id=volcan.id,
                            producto_id=f"GEE_{nombre}",
                            nombre_archivo=nombre,
                            fecha_adquisicion=fecha,
                            ruta_archivo=archivo,
                            descargado=True,
                            procesado=False,
                        )
                        session.add(imagen)
                        imagenes_registradas.append(nombre)
                
                session.commit()
                logger.info(f"Registradas {len(imagenes_registradas)} imágenes en DB")
            
            session.close()
        except Exception as e:
            logger.error(f"Error registrando en DB: {e}")
    
    return {
        'exito': len(archivos) > 0,
        'mensaje': f'Descargadas {len(archivos)} imágenes',
        'imagenes': archivos,
        'registradas': imagenes_registradas
    }