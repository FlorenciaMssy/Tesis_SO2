"""
Procesador de imágenes TROPOMI de SO2
El método calcula el flujo en 6 franjas horarias (0.5h, 1h, 1.5h, 2h, 2.5h, 3h)
desde el punto de emisión, usando la fórmula:

    Flujo (kg/s) = SO2_max (kg/m²) × distancia (m) × velocidad_viento (m/s)
"""
import numpy as np
import xarray as xr
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Tuple
import logging
import math
import warnings

from config.settings import (
    SO2FC_FRANJAS_HORAS, SO2FC_TOLERANCIA_HORA, SO2FC_TOLERANCIA_AZIMUT,
    SO2FC_DISTANCIA_REFERENCIA, MOLM2_TO_GM2_FACTOR, GM2_TO_KGM2_FACTOR,
    KGS_TO_TD_FACTOR
)

warnings.filterwarnings('ignore')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TROPOMIProcessor:
    """
    Procesador de imágenes TROPOMI de SO2
    """
    
    # Radio de la Tierra en metros
    EARTH_RADIUS_M = 6371000
    
    def __init__(self, ruta_archivo: str):
        """
        Inicializa el procesador con un archivo NetCDF de TROPOMI
        
        Args:
            ruta_archivo: Ruta al archivo NetCDF (.nc)
        """
        self.ruta_archivo = Path(ruta_archivo)
        self.dataset = None
        self.so2_molm2 = None  # Datos originales en mol/m²
        self.so2_kgm2 = None   # Datos convertidos a kg/m²
        self.lat = None
        self.lon = None
        self.qa_value = None
        self.fecha = None
        
        self._cargar_datos()
    
    def _cargar_datos(self):
        """Carga los datos del archivo NetCDF"""
        try:
            # Intentar abrir con grupo PRODUCT (formato estándar TROPOMI)
            try:
                self.dataset = xr.open_dataset(self.ruta_archivo, group='PRODUCT')
                logger.info("Archivo abierto con grupo PRODUCT")
            except:
                self.dataset = xr.open_dataset(self.ruta_archivo)
                logger.info("Archivo abierto sin grupo")
            
            # Buscar variable de SO2
            so2_var_names = [
                'SO2_column_number_density',
                'sulfurdioxide_total_vertical_column',
                'so2_column_number_density',
                'SO2_column_amount_7km',
                'SO2'
            ]
            
            so2_var = None
            for var_name in so2_var_names:
                if var_name in self.dataset:
                    so2_var = var_name
                    break
            
            if so2_var:
                self.so2_molm2 = np.squeeze(self.dataset[so2_var].values)
                logger.info(f"Variable SO2: {so2_var}, shape: {self.so2_molm2.shape}")
                
                # Convertir a kg/m² (método MATLAB)
                # SO2_gm2 = SO2_Molm2 / 0.0156
                # SO2_kgm2 = SO2_gm2 * 0.001
                so2_gm2 = self.so2_molm2 * MOLM2_TO_GM2_FACTOR
                self.so2_kgm2 = so2_gm2 * GM2_TO_KGM2_FACTOR
                
                # Valores negativos = NaN (como en MATLAB: SO2_Selec(SO2_Molm2<=0)=NaN)
                self.so2_molm2 = np.where(self.so2_molm2 > 0, self.so2_molm2, np.nan)
                self.so2_kgm2 = np.where(self.so2_kgm2 > 0, self.so2_kgm2, np.nan)
                
            else:
                logger.error(f"No se encontró variable SO2. Disponibles: {list(self.dataset.variables)}")
            
            # QA value
            qa_names = ['qa_value', 'QA_value', 'quality_flag']
            for name in qa_names:
                if name in self.dataset:
                    self.qa_value = np.squeeze(self.dataset[name].values)
                    break
            
            # Coordenadas
            for name in ['latitude', 'lat']:
                if name in self.dataset:
                    self.lat = np.squeeze(self.dataset[name].values)
                    break
            
            for name in ['longitude', 'lon']:
                if name in self.dataset:
                    self.lon = np.squeeze(self.dataset[name].values)
                    break
            
            # Fecha
            for name in ['time', 'time_utc', 'datetime']:
                if name in self.dataset:
                    self.fecha = self.dataset[name].values
                    break
            
            # Log de estadísticas
            if self.so2_kgm2 is not None:
                valid = self.so2_kgm2[~np.isnan(self.so2_kgm2)]
                if len(valid) > 0:
                    logger.info(f"SO2 kg/m²: min={np.min(valid):.2e}, max={np.max(valid):.2e}, mean={np.mean(valid):.2e}")
                    
        except Exception as e:
            logger.error(f"Error cargando archivo: {e}")
            import traceback
            traceback.print_exc()
            raise
    
    def filtrar_por_calidad(self, qa_threshold: float = 0.5) -> Tuple[np.ndarray, np.ndarray]:
        """
        Filtra datos por calidad
        
        Returns:
            Tuple (so2_molm2_filtrado, so2_kgm2_filtrado)
        """
        if self.qa_value is None:
            logger.warning("Sin datos de calidad, usando todos los datos")
            return self.so2_molm2.copy(), self.so2_kgm2.copy()
        
        mascara = self.qa_value >= qa_threshold
        
        so2_molm2_filt = np.where(mascara, self.so2_molm2, np.nan)
        so2_kgm2_filt = np.where(mascara, self.so2_kgm2, np.nan)
        
        # Estadísticas
        total = np.prod(self.so2_molm2.shape)
        validos = np.sum(~np.isnan(so2_molm2_filt))
        logger.info(f"Píxeles válidos: {validos}/{total} ({100*validos/total:.1f}%)")
        
        return so2_molm2_filt, so2_kgm2_filt
    
    def calcular_distancia_metros(
        self,
        lat_centro: float,
        lon_centro: float
    ) -> np.ndarray:
        """
        Calcula la distancia en metros de cada píxel al punto de emisión
        usando fórmula de Haversine
        """
        if self.lat is None or self.lon is None:
            logger.error("No hay coordenadas disponibles")
            return None
        
        lat_rad = np.radians(self.lat)
        lon_rad = np.radians(self.lon)
        lat_centro_rad = np.radians(lat_centro)
        lon_centro_rad = np.radians(lon_centro)
        
        dlat = lat_rad - lat_centro_rad
        dlon = lon_rad - lon_centro_rad
        
        a = np.sin(dlat/2)**2 + np.cos(lat_centro_rad) * np.cos(lat_rad) * np.sin(dlon/2)**2
        c = 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
        
        distancia_m = self.EARTH_RADIUS_M * c
        
        logger.info(f"Distancias: min={np.nanmin(distancia_m)/1000:.1f}km, max={np.nanmax(distancia_m)/1000:.1f}km")
        
        return distancia_m
    
    def calcular_azimut(
        self,
        lat_centro: float,
        lon_centro: float
    ) -> np.ndarray:
        """
        Calcula el azimut desde el punto de emisión a cada píxel
        
        Azimut: 0° = Norte, 90° = Este, 180° = Sur, 270° = Oeste
        """
        if self.lat is None or self.lon is None:
            return None
        
        # Diferencias
        dlat = self.lat - lat_centro
        dlon = self.lon - lon_centro
        
        # Azimut geográfico
        azimut = np.degrees(np.arctan2(dlon, dlat))
        
        # Normalizar a 0-360
        azimut = np.where(azimut < 0, azimut + 360, azimut)
        
        return azimut
    
    def detectar_azimut_pluma(
        self,
        lat_volcan: float,
        lon_volcan: float,
        distancia_referencia_m: float = None
    ) -> Optional[float]:
        """
        Detecta automáticamente el azimut de la pluma
        
        Método del código MATLAB:
        1. Buscar píxeles a ~60km del volcán
        2. Encontrar el máximo de SO2 en esa franja
        3. El azimut de ese píxel es el azimut de la pluma
        """
        if distancia_referencia_m is None:
            distancia_referencia_m = SO2FC_DISTANCIA_REFERENCIA
        
        # Calcular distancias y azimuts
        distancias = self.calcular_distancia_metros(lat_volcan, lon_volcan)
        azimuts = self.calcular_azimut(lat_volcan, lon_volcan)
        
        if distancias is None or azimuts is None:
            return None
        
        # Filtrar por calidad
        so2_molm2, _ = self.filtrar_por_calidad()
        
        # Buscar píxeles a ~60km (±2.5km como en MATLAB)
        tolerancia = 2500  # metros
        
        for dist_ref in [distancia_referencia_m, 40000, 30000, 20000, 10000]:
            mascara_distancia = (np.abs(distancias - dist_ref) < tolerancia)
            so2_selec = np.where(mascara_distancia, so2_molm2, 0)
            max_so2 = np.nanmax(so2_selec)
            
            if max_so2 > 0 and not np.isnan(max_so2):
                logger.info(f"Pluma detectada a {dist_ref/1000:.0f}km")
                break
        else:
            logger.warning("No se detectó pluma en ninguna distancia")
            return None
        
        # Encontrar ubicación del máximo
        idx_max = np.unravel_index(np.nanargmax(so2_selec), so2_selec.shape)
        azimut_pluma = azimuts[idx_max]
        
        logger.info(f"Azimut de pluma detectado: {azimut_pluma:.1f}°")
        
        return float(azimut_pluma)
    
    def calcular_distancia_temporal(
        self,
        distancia_m: np.ndarray,
        velocidad_viento_ms: float
    ) -> np.ndarray:
        """
        Calcula la distancia en horas desde el volcán
        
        Fórmula del MATLAB: disthSO2 = distmSO2 / (ws * 3600)
        """
        # Velocidad en m/h
        velocidad_mh = velocidad_viento_ms * 3600
        
        # Distancia en horas
        distancia_h = distancia_m / velocidad_mh
        
        return distancia_h
    
    def seleccionar_pixeles_por_azimut(
        self,
        azimuts: np.ndarray,
        azimut_pluma: float,
        tolerancia_grados: float = None
    ) -> np.ndarray:
        """
        Selecciona píxeles dentro del rango de azimut de la pluma
        """
        if tolerancia_grados is None:
            tolerancia_grados = SO2FC_TOLERANCIA_AZIMUT
        
        az_min = azimut_pluma - tolerancia_grados
        az_max = azimut_pluma + tolerancia_grados
        
        # Manejar wrap-around
        if az_min < 0:
            mascara = (azimuts >= az_min + 360) | (azimuts <= az_max)
        elif az_max > 360:
            mascara = (azimuts >= az_min) | (azimuts <= az_max - 360)
        else:
            mascara = (azimuts >= az_min) & (azimuts <= az_max)
        
        return mascara
    
    def calcular_so2_por_franja_horaria(
        self,
        lat_volcan: float,
        lon_volcan: float,
        velocidad_viento_ms: float,
        azimut_pluma: float,
        horas: List[float] = None,
        tolerancia_azimut: float = None
    ) -> Dict:
        """
        Calcula el SO2 para cada franja horaria
        
        Franjas por defecto: 0.5h, 1h, 1.5h, 2h, 2.5h, 3h
        
        Fórmula: Flujo (kg/s) = SO2_max (kg/m²) × distancia (m) × velocidad (m/s)
        """
        if horas is None:
            horas = SO2FC_FRANJAS_HORAS
        if tolerancia_azimut is None:
            tolerancia_azimut = SO2FC_TOLERANCIA_AZIMUT
        
        # Calcular distancias y azimuts
        distancia_m = self.calcular_distancia_metros(lat_volcan, lon_volcan)
        azimuts = self.calcular_azimut(lat_volcan, lon_volcan)
        
        if distancia_m is None or azimuts is None:
            return {'exito': False, 'mensaje': 'No se pudieron calcular distancias'}
        
        # Filtrar por calidad
        so2_molm2, so2_kgm2 = self.filtrar_por_calidad()
        
        # Calcular distancia temporal
        distancia_h = self.calcular_distancia_temporal(distancia_m, velocidad_viento_ms)
        
        # Máscara de píxeles en la dirección de la pluma
        mascara_pluma = self.seleccionar_pixeles_por_azimut(
            azimuts, azimut_pluma, tolerancia_azimut
        )
        
        # Aplicar máscara de pluma
        distancia_h_pluma = np.where(mascara_pluma, distancia_h, np.nan)
        
        resultados_franjas = []
        
        for hora in horas:
            # Buscar píxeles en esta franja horaria (±0.1h como en MATLAB)
            tolerancia_h = SO2FC_TOLERANCIA_HORA
            mascara_hora = (distancia_h_pluma >= hora - tolerancia_h) & \
                          (distancia_h_pluma <= hora + tolerancia_h)
            
            # Seleccionar SO2 en esta franja
            so2_molm2_franja = np.where(mascara_hora, so2_molm2, 0)
            so2_kgm2_franja = np.where(mascara_hora, so2_kgm2, 0)
            
            # Buscar máximo
            max_so2_molm2 = np.nanmax(so2_molm2_franja)
            max_so2_kgm2 = np.nanmax(so2_kgm2_franja)
            
            if max_so2_kgm2 > 0 and not np.isnan(max_so2_kgm2):
                # Encontrar ubicación del máximo
                idx_max = np.unravel_index(np.nanargmax(so2_kgm2_franja), so2_kgm2_franja.shape)
                dist_m_max = distancia_m[idx_max]
                
                # Calcular flujo: SO2_kg/m² × distancia_m × velocidad_m/s
                # (Fórmula del código MATLAB línea 652)
                flujo_kgs = max_so2_kgm2 * dist_m_max * velocidad_viento_ms
                
                resultados_franjas.append({
                    'hora': hora,
                    'so2_molm2': float(max_so2_molm2),
                    'so2_kgm2': float(max_so2_kgm2),
                    'distancia_m': float(dist_m_max),
                    'flujo_kgs': float(flujo_kgs),
                    'pixeles_encontrados': int(np.sum(mascara_hora))
                })
                
                logger.info(f"Franja {hora}h: SO2={max_so2_kgm2:.2e} kg/m², "
                           f"dist={dist_m_max/1000:.1f}km, flujo={flujo_kgs:.4f} kg/s")
            else:
                resultados_franjas.append({
                    'hora': hora,
                    'so2_molm2': None,
                    'so2_kgm2': None,
                    'distancia_m': None,
                    'flujo_kgs': None,
                    'pixeles_encontrados': 0
                })
                logger.info(f"Franja {hora}h: Sin datos")
        
        # Calcular promedios (ignorando None)
        flujos_validos = [r['flujo_kgs'] for r in resultados_franjas if r['flujo_kgs'] is not None]
        so2_validos = [r['so2_molm2'] for r in resultados_franjas if r['so2_molm2'] is not None]
        
        if flujos_validos:
            flujo_promedio_kgs = np.mean(flujos_validos)
            # Convertir a ton/día: kg/s × 86.4 (como en MATLAB línea 761)
            flujo_promedio_td = flujo_promedio_kgs * KGS_TO_TD_FACTOR
            so2_total = np.sum(so2_validos)
            
            return {
                'exito': True,
                'franjas': resultados_franjas,
                'flujo_promedio_kgs': float(flujo_promedio_kgs),
                'flujo_promedio_td': float(flujo_promedio_td),
                'so2_total_molm2': float(so2_total),
                'azimut_pluma': azimut_pluma,
                'velocidad_viento_ms': velocidad_viento_ms,
                'n_franjas_validas': len(flujos_validos)
            }
        else:
            return {
                'exito': False,
                'mensaje': 'No se encontró SO2 en ninguna franja horaria',
                'franjas': resultados_franjas
            }
    
    def detectar_pluma(
        self,
        lat_volcan: float,
        lon_volcan: float,
        umbral_so2: float = 0.001,
        radio_busqueda_km: float = 100
    ) -> Optional[Dict]:
        """
        Función de compatibilidad: detecta pluma
        """
        azimut = self.detectar_azimut_pluma(lat_volcan, lon_volcan)
        
        if azimut is None:
            return None
        
        so2_molm2, _ = self.filtrar_por_calidad()
        valid = so2_molm2[~np.isnan(so2_molm2)]
        
        return {
            'detectada': True,
            'azimut': azimut,
            'azimut_grados': azimut,
            'so2_max': float(np.nanmax(valid)) if len(valid) > 0 else 0,
            'so2_mean': float(np.nanmean(valid)) if len(valid) > 0 else 0,
            'so2_total': float(np.nansum(valid)) if len(valid) > 0 else 0
        }
    
    def cerrar(self):
        """Cierra el dataset"""
        if self.dataset is not None:
            self.dataset.close()


# ============================================================
# FUNCIONES DE COMPATIBILIDAD CON CÓDIGO ANTERIOR
# ============================================================

def procesar_imagen_tropomi(
    ruta_archivo: str,
    lat_volcan: float,
    lon_volcan: float,
    qa_threshold: float = 0.5
) -> Dict:
    """
    Función de compatibilidad para procesar una imagen TROPOMI
    """
    processor = TROPOMIProcessor(ruta_archivo)
    
    try:
        # Filtrar por calidad
        so2_molm2, so2_kgm2 = processor.filtrar_por_calidad(qa_threshold)
        
        # Detectar pluma
        azimut = processor.detectar_azimut_pluma(lat_volcan, lon_volcan)
        
        # Estadísticas básicas
        valid = so2_molm2[~np.isnan(so2_molm2)]
        
        return {
            'archivo': str(ruta_archivo),
            'tiempo': processor.fecha,
            'pluma': {
                'detectada': azimut is not None,
                'azimut': azimut,
                'so2_max': float(np.nanmax(valid)) if len(valid) > 0 else 0,
                'so2_mean': float(np.nanmean(valid)) if len(valid) > 0 else 0,
                'so2_total': float(np.nansum(valid)) if len(valid) > 0 else 0
            },
            'region': {
                'so2_max': float(np.nanmax(valid)) if len(valid) > 0 else 0,
                'so2_mean': float(np.nanmean(valid)) if len(valid) > 0 else 0,
                'n_pixeles': len(valid)
            },
            'qa_threshold': qa_threshold,
            'procesado': True
        }
        
    finally:
        processor.cerrar()


if __name__ == "__main__":
    print("Procesador TROPOMI")
    print("=" * 50)
    print("\nEste módulo implementa el método de distancia/tiempo")
    print("para calcular flujo de SO2")
    print(f"\nFranjas horarias: {SO2FC_FRANJAS_HORAS}")
    print(f"Tolerancia hora: ±{SO2FC_TOLERANCIA_HORA}h")
    print(f"Tolerancia azimut: ±{SO2FC_TOLERANCIA_AZIMUT}°")
