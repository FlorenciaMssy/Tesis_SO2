"""
Módulo ETL para descarga de datos de viento desde ERA5
Utiliza la API de CDS (Climate Data Store) de Copernicus
"""
import os
import cdsapi
import numpy as np
import xarray as xr
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Tuple
import logging
import math

from config.settings import (
    CDS_API_KEY, WIND_DIR, PRESSURE_LEVELS, DEFAULT_ALTITUDE_M
)
from database import get_session, DatosViento, Volcan, LogProcesamiento

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ERA5Downloader:
    """
    Clase para descargar datos de viento de ERA5
    """
    
    def __init__(self):
        """Inicializa el cliente de CDS API"""
        # El archivo .cdsapirc debe estar configurado o usar la variable de entorno
        try:
            self.client = cdsapi.Client()
        except Exception as e:
            logger.warning(f"Error inicializando CDS client: {e}")
            logger.info("Asegúrese de configurar el archivo ~/.cdsapirc")
            self.client = None
    
    def _presion_a_altitud(self, presion_hpa: float) -> float:
        """
        Convierte presión atmosférica a altitud aproximada
        Usa la fórmula barométrica estándar
        
        Args:
            presion_hpa: Presión en hectopascales
            
        Returns:
            Altitud aproximada en metros
        """
        # Fórmula barométrica internacional
        # h = 44330 * (1 - (P/P0)^(1/5.255))
        P0 = 1013.25  # Presión a nivel del mar en hPa
        return 44330 * (1 - (presion_hpa / P0) ** (1/5.255))
    
    def _altitud_a_presion(self, altitud_m: float) -> float:
        """
        Convierte altitud a nivel de presión aproximado
        
        Args:
            altitud_m: Altitud en metros
            
        Returns:
            Presión aproximada en hPa
        """
        P0 = 1013.25
        return P0 * (1 - altitud_m / 44330) ** 5.255
    
    def _seleccionar_nivel_presion(self, altitud_m: float) -> int:
        """
        Selecciona el nivel de presión más cercano a una altitud dada
        
        Args:
            altitud_m: Altitud objetivo en metros
            
        Returns:
            Nivel de presión más cercano en hPa
        """
        presion_objetivo = self._altitud_a_presion(altitud_m)
        
        nivel_cercano = min(PRESSURE_LEVELS, 
                          key=lambda x: abs(x - presion_objetivo))
        
        return nivel_cercano
    
    def _calcular_velocidad_direccion(
        self, 
        u: float, 
        v: float
    ) -> Tuple[float, float]:
        """
        Calcula velocidad y dirección del viento a partir de componentes U y V
        
        Args:
            u: Componente este-oeste (m/s)
            v: Componente norte-sur (m/s)
            
        Returns:
            Tuple (velocidad en m/s, dirección en grados)
        """
        # Velocidad
        velocidad = math.sqrt(u**2 + v**2)
        
        # Dirección (convención meteorológica: de donde viene el viento)
        # atan2 da el ángulo hacia donde va el viento, invertimos
        direccion_rad = math.atan2(-u, -v)
        direccion_grados = math.degrees(direccion_rad)
        
        # Normalizar a 0-360
        if direccion_grados < 0:
            direccion_grados += 360
        
        return velocidad, direccion_grados
    
    def descargar_datos_viento(
        self,
        lat: float,
        lon: float,
        fecha: datetime,
        altitud_m: float = DEFAULT_ALTITUDE_M,
        area_grados: float = 1.0
    ) -> Optional[str]:
        """
        Descarga datos de viento de ERA5 para una ubicación y fecha
        
        Args:
            lat: Latitud del centro
            lon: Longitud del centro
            fecha: Fecha y hora de los datos
            altitud_m: Altitud objetivo en metros
            area_grados: Tamaño del área a descargar en grados
            
        Returns:
            Ruta al archivo descargado o None si falla
        """
        if not self.client:
            logger.error("Cliente CDS no inicializado")
            return None
        
        # Calcular nivel de presión
        nivel_presion = self._seleccionar_nivel_presion(altitud_m)
        
        # Definir área (norte, oeste, sur, este)
        area = [
            lat + area_grados/2,  # Norte
            lon - area_grados/2,  # Oeste
            lat - area_grados/2,  # Sur
            lon + area_grados/2   # Este
        ]
        
        # Definir horas (cada 3 horas disponibles en ERA5)
        hora = fecha.hour
        horas_disponibles = [0, 3, 6, 9, 12, 15, 18, 21]
        hora_cercana = min(horas_disponibles, key=lambda x: abs(x - hora))
        
        # Nombre del archivo
        fecha_str = fecha.strftime("%Y%m%d")
        nombre_archivo = f"era5_wind_{fecha_str}_{hora_cercana:02d}_{nivel_presion}hPa.nc"
        ruta_destino = WIND_DIR / nombre_archivo
        
        # Verificar si ya existe
        if ruta_destino.exists():
            logger.info(f"Archivo ya existe: {ruta_destino}")
            return str(ruta_destino)
        
        try:
            logger.info(f"Descargando datos ERA5 para {fecha_str} {hora_cercana}:00 UTC, {nivel_presion} hPa")
            
            self.client.retrieve(
                'reanalysis-era5-pressure-levels',
                {
                    'product_type': 'reanalysis',
                    'format': 'netcdf',
                    'variable': [
                        'u_component_of_wind',
                        'v_component_of_wind',
                    ],
                    'pressure_level': str(nivel_presion),
                    'year': fecha.strftime('%Y'),
                    'month': fecha.strftime('%m'),
                    'day': fecha.strftime('%d'),
                    'time': f'{hora_cercana:02d}:00',
                    'area': area,
                },
                str(ruta_destino)
            )
            
            logger.info(f"Datos ERA5 descargados: {ruta_destino}")
            return str(ruta_destino)
            
        except Exception as e:
            logger.error(f"Error descargando datos ERA5: {e}")
            if ruta_destino.exists():
                ruta_destino.unlink()
            return None
    
    def extraer_viento_punto(
        self,
        ruta_archivo: str,
        lat: float,
        lon: float
    ) -> Optional[Dict]:
        """
        Extrae datos de viento para un punto específico de un archivo NetCDF
        
        Args:
            ruta_archivo: Ruta al archivo NetCDF de ERA5
            lon: Longitud del punto
            lat: Latitud del punto
            
        Returns:
            Diccionario con datos de viento o None si falla
        """
        try:
            ds = xr.open_dataset(ruta_archivo)
            
            # Seleccionar el punto más cercano
            # ERA5 usa 'latitude' y 'longitude'
            punto = ds.sel(latitude=lat, longitude=lon, method='nearest')
            
            # Extraer componentes
            u = float(punto['u'].values)
            v = float(punto['v'].values)
            
            # Obtener nivel de presión y tiempo
            if 'level' in punto.dims or 'level' in punto.coords:
                nivel_presion = float(punto['level'].values)
            elif 'pressure_level' in punto.coords:
                nivel_presion = float(punto['pressure_level'].values)
            else:
                nivel_presion = None
            
            if 'time' in punto.coords:
                tiempo = punto['time'].values
                if hasattr(tiempo, 'astype'):
                    tiempo = tiempo.astype('datetime64[s]').astype(datetime)
            else:
                tiempo = None
            
            # Calcular velocidad y dirección
            velocidad, direccion = self._calcular_velocidad_direccion(u, v)
            
            # Calcular altitud aproximada
            altitud = self._presion_a_altitud(nivel_presion) if nivel_presion else None
            
            ds.close()
            
            return {
                'u_component': u,
                'v_component': v,
                'velocidad_ms': velocidad,
                'direccion_grados': direccion,
                'nivel_presion_hpa': nivel_presion,
                'altitud_m': altitud,
                'tiempo': tiempo,
                'latitud': lat,
                'longitud': lon
            }
            
        except Exception as e:
            logger.error(f"Error extrayendo datos de viento: {e}")
            return None
    
    def guardar_datos_viento(
        self,
        volcan_id: int,
        datos_viento: Dict,
        fuente: str = "ERA5"
    ) -> Optional[int]:
        """
        Guarda datos de viento en la base de datos
        
        Args:
            volcan_id: ID del volcán
            datos_viento: Diccionario con datos de viento
            fuente: Fuente de los datos
            
        Returns:
            ID del registro creado o None si falla
        """
        session = get_session()
        
        try:
            # Verificar si ya existe un registro similar
            existente = session.query(DatosViento).filter(
                DatosViento.volcan_id == volcan_id,
                DatosViento.fecha_hora == datos_viento.get('tiempo'),
                DatosViento.nivel_presion_hpa == datos_viento.get('nivel_presion_hpa')
            ).first()
            
            if existente:
                logger.info(f"Datos de viento ya existen para este tiempo y nivel")
                return existente.id
            
            # Crear nuevo registro
            registro = DatosViento(
                volcan_id=volcan_id,
                fecha_hora=datos_viento.get('tiempo', datetime.utcnow()),
                latitud=datos_viento['latitud'],
                longitud=datos_viento['longitud'],
                nivel_presion_hpa=datos_viento.get('nivel_presion_hpa'),
                altitud_m=datos_viento.get('altitud_m'),
                u_component=datos_viento['u_component'],
                v_component=datos_viento['v_component'],
                velocidad_ms=datos_viento['velocidad_ms'],
                direccion_grados=datos_viento['direccion_grados'],
                fuente=fuente
            )
            
            session.add(registro)
            session.commit()
            
            registro_id = registro.id
            logger.info(f"Datos de viento guardados con ID {registro_id}")
            
            return registro_id
            
        except Exception as e:
            logger.error(f"Error guardando datos de viento: {e}")
            session.rollback()
            return None
            
        finally:
            session.close()


def obtener_viento_para_imagen(
    volcan_id: int,
    lat: float,
    lon: float,
    fecha: datetime,
    altitud_m: float = DEFAULT_ALTITUDE_M
) -> Optional[Dict]:
    """
    Función principal para obtener datos de viento para una imagen TROPOMI
    
    Args:
        volcan_id: ID del volcán en la base de datos
        lat: Latitud del volcán
        lon: Longitud del volcán
        fecha: Fecha y hora de la imagen
        altitud_m: Altitud de interés en metros
        
    Returns:
        Diccionario con datos de viento
    """
    downloader = ERA5Downloader()
    
    # Descargar datos
    ruta_archivo = downloader.descargar_datos_viento(
        lat=lat,
        lon=lon,
        fecha=fecha,
        altitud_m=altitud_m
    )
    
    if not ruta_archivo:
        logger.warning("No se pudieron descargar datos de viento")
        return None
    
    # Extraer datos para el punto
    datos = downloader.extraer_viento_punto(ruta_archivo, lat, lon)
    
    if not datos:
        logger.warning("No se pudieron extraer datos de viento")
        return None
    
    # Guardar en base de datos
    datos['ruta_archivo'] = ruta_archivo
    downloader.guardar_datos_viento(volcan_id, datos)
    
    return datos


def obtener_perfil_vertical_viento(
    lat: float,
    lon: float,
    fecha: datetime,
    niveles: List[int] = None
) -> List[Dict]:
    """
    Obtiene un perfil vertical de viento para múltiples niveles de presión
    
    Args:
        lat: Latitud
        lon: Longitud
        fecha: Fecha y hora
        niveles: Lista de niveles de presión (hPa)
        
    Returns:
        Lista de diccionarios con datos de viento por nivel
    """
    if niveles is None:
        niveles = PRESSURE_LEVELS
    
    downloader = ERA5Downloader()
    perfil = []
    
    for nivel in niveles:
        altitud = downloader._presion_a_altitud(nivel)
        
        ruta = downloader.descargar_datos_viento(
            lat=lat,
            lon=lon,
            fecha=fecha,
            altitud_m=altitud
        )
        
        if ruta:
            datos = downloader.extraer_viento_punto(ruta, lat, lon)
            if datos:
                perfil.append(datos)
    
    return perfil


if __name__ == "__main__":
    # Ejemplo de uso
    from datetime import datetime
    
    # Coordenadas del Monte Etna
    lat = 37.751
    lon = 14.993
    
    # Fecha de ejemplo
    fecha = datetime(2024, 1, 15, 12, 0)
    
    print("Obteniendo datos de viento ERA5 para Monte Etna...")
    
    # Crear instancia del downloader
    downloader = ERA5Downloader()
    
    # Probar cálculos de altitud/presión
    print(f"\nNivel de presión para 3000m: {downloader._seleccionar_nivel_presion(3000)} hPa")
    print(f"Altitud para 700 hPa: {downloader._presion_a_altitud(700):.0f} m")
    
    # Probar cálculo de viento
    u, v = 5.0, -3.0  # Ejemplo
    vel, dir = downloader._calcular_velocidad_direccion(u, v)
    print(f"\nU={u}, V={v} -> Velocidad={vel:.2f} m/s, Dirección={dir:.1f}°")
