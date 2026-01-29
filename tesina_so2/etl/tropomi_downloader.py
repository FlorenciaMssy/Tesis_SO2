"""
Módulo ETL para descarga de imágenes TROPOMI de SO2
Utiliza la API de Copernicus Data Space
"""
import os
import requests
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Tuple
import logging

from config.settings import (
    COPERNICUS_USERNAME, COPERNICUS_PASSWORD,
    COPERNICUS_API_URL, COPERNICUS_TOKEN_URL,
    IMAGES_DIR, TROPOMI_PRODUCT, BBOX_MARGIN_KM
)
from database import get_session, ImagenTROPOMI, Volcan, LogProcesamiento

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TROPOMIDownloader:
    """
    Clase para descargar datos de SO2 de TROPOMI desde Copernicus Data Space
    """
    
    def __init__(self):
        self.token = None
        self.token_expiry = None
        self.session = requests.Session()
    
    def _obtener_token(self) -> str:
        """Obtiene o renueva el token de acceso de Copernicus"""
        if self.token and self.token_expiry and datetime.utcnow() < self.token_expiry:
            return self.token
        
        data = {
            "grant_type": "password",
            "username": COPERNICUS_USERNAME,
            "password": COPERNICUS_PASSWORD,
            "client_id": "cdse-public"
        }
        
        try:
            response = requests.post(
                COPERNICUS_TOKEN_URL, 
                data=data,
                timeout=30
            )
            
            if response.status_code == 500:
                logger.error("Error 500 del servidor de Copernicus. El servicio puede estar temporalmente no disponible.")
                logger.error("Intentá de nuevo en unos minutos o verificá el estado en: https://status.dataspace.copernicus.eu/")
                raise Exception("Servidor de Copernicus no disponible (500)")
            
            response.raise_for_status()
            token_data = response.json()
            
            self.token = token_data["access_token"]
            # El token expira en 10 minutos, renovamos a los 9
            self.token_expiry = datetime.utcnow() + timedelta(minutes=9)
            
            logger.info("Token de Copernicus obtenido correctamente")
            return self.token
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error de conexión obteniendo token: {e}")
            raise
        except Exception as e:
            logger.error(f"Error obteniendo token: {e}")
            raise
    
    def _calcular_bbox(self, lat: float, lon: float, margen_km: float = BBOX_MARGIN_KM) -> Tuple[float, float, float, float]:
        """
        Calcula el bounding box alrededor de una coordenada
        
        Args:
            lat: Latitud del centro
            lon: Longitud del centro
            margen_km: Margen en kilómetros
            
        Returns:
            Tuple (oeste, sur, este, norte)
        """
        # Aproximación: 1 grado ≈ 111 km
        margen_grados = margen_km / 111.0
        
        oeste = lon - margen_grados
        este = lon + margen_grados
        sur = lat - margen_grados
        norte = lat + margen_grados
        
        return (oeste, sur, este, norte)
    
    def buscar_productos(
        self,
        lat: float,
        lon: float,
        fecha_inicio: datetime,
        fecha_fin: datetime,
        max_resultados: int = 100
    ) -> List[Dict]:
        """
        Busca productos TROPOMI SO2 disponibles para una ubicación y rango de fechas
        
        Args:
            lat: Latitud del volcán
            lon: Longitud del volcán
            fecha_inicio: Fecha inicial de búsqueda
            fecha_fin: Fecha final de búsqueda
            max_resultados: Número máximo de resultados
            
        Returns:
            Lista de productos encontrados
        """
        oeste, sur, este, norte = self._calcular_bbox(lat, lon)
        
        # Formato de fecha para la API
        fecha_inicio_str = fecha_inicio.strftime("%Y-%m-%dT00:00:00.000Z")
        fecha_fin_str = fecha_fin.strftime("%Y-%m-%dT23:59:59.999Z")
        
        # Construir query OData
        filter_query = (
            f"Collection/Name eq 'SENTINEL-5P' and "
            f"Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'productType' and att/OData.CSC.StringAttribute/Value eq '{TROPOMI_PRODUCT}') and "
            f"ContentDate/Start ge {fecha_inicio_str} and "
            f"ContentDate/Start le {fecha_fin_str} and "
            f"OData.CSC.Intersects(area=geography'SRID=4326;POLYGON(({oeste} {sur},{este} {sur},{este} {norte},{oeste} {norte},{oeste} {sur}))')"
        )
        
        params = {
            "$filter": filter_query,
            "$orderby": "ContentDate/Start desc",
            "$top": max_resultados,
            "$expand": "Attributes"
        }
        
        url = f"{COPERNICUS_API_URL}/Products"
        
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            productos = data.get("value", [])
            logger.info(f"Encontrados {len(productos)} productos TROPOMI")
            
            return productos
            
        except Exception as e:
            logger.error(f"Error buscando productos: {e}")
            raise
    
    def descargar_producto(self, producto_id: str, nombre_archivo: str) -> Optional[str]:
        """
        Descarga un producto TROPOMI
        
        Args:
            producto_id: ID del producto en Copernicus
            nombre_archivo: Nombre del archivo a guardar
            
        Returns:
            Ruta al archivo descargado o None si falla
        """
        token = self._obtener_token()
        
        # URL actualizada para descarga - usar zipper.dataspace para productos grandes
        # Para Sentinel-5P los archivos son .nc directamente
        url = f"https://zipper.dataspace.copernicus.eu/odata/v1/Products({producto_id})/$value"
        headers = {"Authorization": f"Bearer {token}"}
        
        ruta_destino = IMAGES_DIR / nombre_archivo
        
        try:
            logger.info(f"Descargando producto {producto_id}...")
            
            with self.session.get(url, headers=headers, stream=True) as response:
                response.raise_for_status()
                
                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0
                
                with open(ruta_destino, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        if total_size > 0:
                            progress = (downloaded / total_size) * 100
                            if downloaded % (total_size // 10) < 8192:
                                logger.info(f"Progreso: {progress:.1f}%")
            
            logger.info(f"Producto descargado: {ruta_destino}")
            return str(ruta_destino)
            
        except Exception as e:
            logger.error(f"Error descargando producto: {e}")
            if ruta_destino.exists():
                ruta_destino.unlink()
            return None
    
    def procesar_y_guardar_metadatos(
        self,
        volcan_id: int,
        productos: List[Dict]
    ) -> List[int]:
        """
        Procesa productos y guarda metadatos en la base de datos
        
        Args:
            volcan_id: ID del volcán en la base de datos
            productos: Lista de productos de la búsqueda
            
        Returns:
            Lista de IDs de imágenes creadas
        """
        session = get_session()
        imagenes_ids = []
        
        for producto in productos:
            try:
                producto_id = producto.get("Id")
                
                # Verificar si ya existe
                existente = session.query(ImagenTROPOMI).filter_by(
                    producto_id=producto_id
                ).first()
                
                if existente:
                    logger.info(f"Producto {producto_id} ya existe en la base de datos")
                    continue
                
                # Extraer información del producto
                nombre = producto.get("Name", "")
                fecha_str = producto.get("ContentDate", {}).get("Start")
                
                if fecha_str:
                    fecha_adquisicion = datetime.fromisoformat(
                        fecha_str.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                else:
                    fecha_adquisicion = datetime.utcnow()
                
                # Extraer bounding box
                footprint = producto.get("GeoFootprint", {})
                coordinates = footprint.get("coordinates", [[]])
                
                if coordinates and coordinates[0]:
                    lons = [c[0] for c in coordinates[0]]
                    lats = [c[1] for c in coordinates[0]]
                    bbox_oeste = min(lons)
                    bbox_este = max(lons)
                    bbox_sur = min(lats)
                    bbox_norte = max(lats)
                else:
                    bbox_oeste = bbox_este = bbox_sur = bbox_norte = None
                
                # Determinar modo de operación (NRTI u OFFL)
                modo = "OFFL" if "OFFL" in nombre else "NRTI"
                
                # Crear registro
                imagen = ImagenTROPOMI(
                    volcan_id=volcan_id,
                    producto_id=producto_id,
                    nombre_archivo=nombre,
                    fecha_adquisicion=fecha_adquisicion,
                    bbox_norte=bbox_norte,
                    bbox_sur=bbox_sur,
                    bbox_este=bbox_este,
                    bbox_oeste=bbox_oeste,
                    modo_operacion=modo,
                    tamano_bytes=producto.get("ContentLength"),
                    descargado=False,
                    procesado=False
                )
                
                session.add(imagen)
                session.commit()
                imagenes_ids.append(imagen.id)
                
                logger.info(f"Metadatos guardados para imagen {imagen.id}")
                
            except Exception as e:
                logger.error(f"Error procesando producto: {e}")
                session.rollback()
                continue
        
        session.close()
        return imagenes_ids
    
    def descargar_pendientes(self, volcan_id: int, limite: int = 10) -> List[str]:
        """
        Descarga imágenes pendientes para un volcán
        
        Args:
            volcan_id: ID del volcán
            limite: Número máximo de descargas
            
        Returns:
            Lista de rutas de archivos descargados
        """
        session = get_session()
        
        # Obtener imágenes no descargadas
        imagenes = session.query(ImagenTROPOMI).filter_by(
            volcan_id=volcan_id,
            descargado=False
        ).limit(limite).all()
        
        rutas_descargadas = []
        
        for imagen in imagenes:
            try:
                # Generar nombre de archivo
                nombre_archivo = f"{imagen.producto_id}.nc"
                
                # Descargar
                ruta = self.descargar_producto(imagen.producto_id, nombre_archivo)
                
                if ruta:
                    # Actualizar registro
                    imagen.ruta_archivo = ruta
                    imagen.descargado = True
                    imagen.fecha_descarga = datetime.utcnow()
                    imagen.tamano_bytes = os.path.getsize(ruta)
                    
                    session.commit()
                    rutas_descargadas.append(ruta)
                    
                    logger.info(f"Imagen {imagen.id} descargada correctamente")
                    
            except Exception as e:
                logger.error(f"Error descargando imagen {imagen.id}: {e}")
                session.rollback()
                continue
        
        session.close()
        return rutas_descargadas


def buscar_y_descargar_tropomi(
    volcan_nombre: str,
    lat: float,
    lon: float,
    fecha_inicio: datetime,
    fecha_fin: datetime,
    descargar: bool = True
) -> Dict:
    """
    Función principal para buscar y descargar datos TROPOMI
    
    Args:
        volcan_nombre: Nombre del volcán
        lat: Latitud
        lon: Longitud
        fecha_inicio: Fecha inicial
        fecha_fin: Fecha final
        descargar: Si True, descarga los archivos
        
    Returns:
        Diccionario con resultados
    """
    session = get_session()
    downloader = TROPOMIDownloader()
    
    # Buscar o crear volcán
    volcan = session.query(Volcan).filter_by(nombre=volcan_nombre).first()
    
    if not volcan:
        volcan = Volcan(
            nombre=volcan_nombre,
            latitud=lat,
            longitud=lon
        )
        session.add(volcan)
        session.commit()
        logger.info(f"Volcán '{volcan_nombre}' creado con ID {volcan.id}")
    
    # Buscar productos
    productos = downloader.buscar_productos(lat, lon, fecha_inicio, fecha_fin)
    
    # Guardar metadatos
    imagenes_ids = downloader.procesar_y_guardar_metadatos(volcan.id, productos)
    
    resultado = {
        "volcan_id": volcan.id,
        "productos_encontrados": len(productos),
        "imagenes_registradas": len(imagenes_ids),
        "imagenes_ids": imagenes_ids
    }
    
    # Descargar si se solicita
    if descargar and imagenes_ids:
        rutas = downloader.descargar_pendientes(volcan.id)
        resultado["archivos_descargados"] = rutas
    
    # Registrar log
    log = LogProcesamiento(
        nivel="INFO",
        componente="ETL_TROPOMI",
        mensaje=f"Búsqueda completada para {volcan_nombre}",
        detalles_json=resultado
    )
    session.add(log)
    session.commit()
    session.close()
    
    return resultado


if __name__ == "__main__":
    # Ejemplo de uso
    from datetime import datetime, timedelta
    
    fecha_fin = datetime.utcnow()
    fecha_inicio = fecha_fin - timedelta(days=7)
    
    resultado = buscar_y_descargar_tropomi(
        volcan_nombre="Monte Etna",
        lat=37.751,
        lon=14.993,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        descargar=False  # Solo buscar, no descargar en este ejemplo
    )
    
    print(json.dumps(resultado, indent=2, default=str))
