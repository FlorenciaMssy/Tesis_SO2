"""
Módulo para procesar imágenes TROPOMI de SO2
Extrae datos de columna vertical y los prepara para el cálculo de flujo
"""
import numpy as np
import xarray as xr
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Tuple
import logging
from scipy import ndimage
from scipy.interpolate import griddata

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TROPOMIProcessor:
    """
    Procesador de imágenes TROPOMI de SO2
    """
    
    # Constantes físicas
    AVOGADRO = 6.02214076e23  # mol^-1
    MASA_MOLAR_SO2 = 64.066  # g/mol
    
    def __init__(self, ruta_archivo: str):
        """
        Inicializa el procesador con un archivo NetCDF de TROPOMI
        
        Args:
            ruta_archivo: Ruta al archivo NetCDF (.nc)
        """
        self.ruta_archivo = Path(ruta_archivo)
        self.dataset = None
        self.so2_data = None
        self.qa_value = None
        self.lat = None
        self.lon = None
        self.tiempo = None
        
        self._cargar_datos()
    
    def _cargar_datos(self):
        """Carga los datos del archivo NetCDF"""
        try:
            # Abrir archivo con xarray
            self.dataset = xr.open_dataset(self.ruta_archivo, group='PRODUCT')
            
            # Extraer variables principales
            # SO2 columna vertical (mol/m²)
            if 'sulfurdioxide_total_vertical_column' in self.dataset:
                self.so2_data = self.dataset['sulfurdioxide_total_vertical_column'].values
            elif 'SO2_column_number_density' in self.dataset:
                self.so2_data = self.dataset['SO2_column_number_density'].values
            
            # Valor de calidad (0-1, donde 1 es mejor)
            if 'qa_value' in self.dataset:
                self.qa_value = self.dataset['qa_value'].values
            
            # Coordenadas
            if 'latitude' in self.dataset:
                self.lat = self.dataset['latitude'].values
            if 'longitude' in self.dataset:
                self.lon = self.dataset['longitude'].values
            
            # Tiempo
            if 'time' in self.dataset or 'time_utc' in self.dataset:
                tiempo_var = 'time' if 'time' in self.dataset else 'time_utc'
                self.tiempo = self.dataset[tiempo_var].values
            
            logger.info(f"Datos cargados: shape SO2 = {self.so2_data.shape if self.so2_data is not None else 'N/A'}")
            
        except Exception as e:
            logger.error(f"Error cargando archivo TROPOMI: {e}")
            raise
    
    def filtrar_por_calidad(self, qa_threshold: float = 0.5) -> np.ndarray:
        """
        Filtra los datos de SO2 por umbral de calidad
        
        Args:
            qa_threshold: Umbral mínimo de calidad (0-1)
            
        Returns:
            Array de SO2 con valores de baja calidad enmascarados
        """
        if self.qa_value is None:
            logger.warning("No hay datos de calidad disponibles")
            return self.so2_data
        
        # Crear máscara
        mascara = self.qa_value >= qa_threshold
        
        # Aplicar máscara
        so2_filtrado = np.where(mascara, self.so2_data, np.nan)
        
        # Estadísticas
        total_pixels = np.prod(self.so2_data.shape)
        pixels_validos = np.sum(mascara)
        logger.info(f"Píxeles válidos: {pixels_validos}/{total_pixels} ({100*pixels_validos/total_pixels:.1f}%)")
        
        return so2_filtrado
    
    def extraer_region(
        self,
        lat_centro: float,
        lon_centro: float,
        radio_km: float = 50
    ) -> Dict:
        """
        Extrae datos de SO2 para una región circular alrededor de un punto
        
        Args:
            lat_centro: Latitud del centro (volcán)
            lon_centro: Longitud del centro
            radio_km: Radio de extracción en km
            
        Returns:
            Diccionario con datos de la región
        """
        if self.lat is None or self.lon is None:
            logger.error("No hay coordenadas disponibles")
            return {}
        
        # Calcular distancia de cada píxel al centro
        # Aproximación plana (válida para distancias pequeñas)
        lat_rad = np.radians(self.lat)
        lon_rad = np.radians(self.lon)
        lat_centro_rad = np.radians(lat_centro)
        lon_centro_rad = np.radians(lon_centro)
        
        # Radio de la Tierra en km
        R = 6371.0
        
        # Distancia usando fórmula de Haversine simplificada
        dlat = lat_rad - lat_centro_rad
        dlon = lon_rad - lon_centro_rad
        
        a = np.sin(dlat/2)**2 + np.cos(lat_centro_rad) * np.cos(lat_rad) * np.sin(dlon/2)**2
        distancia_km = 2 * R * np.arcsin(np.sqrt(a))
        
        # Crear máscara de región
        mascara_region = distancia_km <= radio_km
        
        # Extraer datos
        so2_region = np.where(mascara_region, self.so2_data, np.nan)
        
        # Calcular estadísticas
        so2_validos = so2_region[~np.isnan(so2_region)]
        
        if len(so2_validos) == 0:
            logger.warning("No hay datos válidos en la región")
            return {}
        
        return {
            'so2_region': so2_region,
            'lat_region': self.lat,
            'lon_region': self.lon,
            'mascara_region': mascara_region,
            'distancia_km': distancia_km,
            'so2_max': float(np.nanmax(so2_validos)),
            'so2_min': float(np.nanmin(so2_validos)),
            'so2_mean': float(np.nanmean(so2_validos)),
            'so2_std': float(np.nanstd(so2_validos)),
            'so2_total': float(np.nansum(so2_validos)),
            'n_pixeles': len(so2_validos)
        }
    
    def detectar_pluma(
        self,
        lat_volcan: float,
        lon_volcan: float,
        umbral_so2: float = 0.001,  # mol/m² (1 DU ≈ 0.00285 mol/m²)
        radio_busqueda_km: float = 100
    ) -> Optional[Dict]:
        """
        Detecta y caracteriza la pluma de SO2 desde un volcán
        
        Args:
            lat_volcan: Latitud del volcán
            lon_volcan: Longitud del volcán
            umbral_so2: Umbral mínimo de SO2 para considerar pluma
            radio_busqueda_km: Radio de búsqueda
            
        Returns:
            Diccionario con características de la pluma
        """
        # Extraer región
        region = self.extraer_region(lat_volcan, lon_volcan, radio_busqueda_km)
        
        if not region:
            return None
        
        so2 = region['so2_region']
        
        # Filtrar por calidad
        if self.qa_value is not None:
            so2 = np.where(self.qa_value >= 0.5, so2, np.nan)
        
        # Crear máscara de pluma
        pluma_mascara = so2 >= umbral_so2
        
        if not np.any(pluma_mascara):
            logger.info("No se detectó pluma sobre el umbral")
            return None
        
        # Etiquetar regiones conectadas
        pluma_etiquetada, n_regiones = ndimage.label(pluma_mascara)
        
        # Encontrar la región más grande (presumiblemente la pluma principal)
        tamanos = ndimage.sum(pluma_mascara, pluma_etiquetada, range(1, n_regiones + 1))
        region_principal = np.argmax(tamanos) + 1 if len(tamanos) > 0 else 0
        
        # Máscara de la pluma principal
        pluma_principal = pluma_etiquetada == region_principal
        
        # Calcular propiedades de la pluma
        so2_pluma = np.where(pluma_principal, so2, np.nan)
        
        # Centro de masa de la pluma
        indices_pluma = np.where(pluma_principal)
        if len(indices_pluma[0]) > 0:
            # Ponderado por concentración de SO2
            pesos = so2[pluma_principal]
            lat_cm = np.average(self.lat[pluma_principal], weights=pesos)
            lon_cm = np.average(self.lon[pluma_principal], weights=pesos)
        else:
            lat_cm = lat_volcan
            lon_cm = lon_volcan
        
        # Calcular dirección de la pluma (desde volcán hacia centro de masa)
        dx = lon_cm - lon_volcan
        dy = lat_cm - lat_volcan
        azimut = np.degrees(np.arctan2(dx, dy))
        if azimut < 0:
            azimut += 360
        
        # Distancia máxima de la pluma
        distancias = region['distancia_km']
        distancia_max = np.nanmax(distancias[pluma_principal]) if np.any(pluma_principal) else 0
        
        return {
            'detectada': True,
            'n_regiones': n_regiones,
            'lat_centro_masa': float(lat_cm),
            'lon_centro_masa': float(lon_cm),
            'azimut_grados': float(azimut),
            'distancia_max_km': float(distancia_max),
            'so2_max': float(np.nanmax(so2_pluma)),
            'so2_mean': float(np.nanmean(so2_pluma)),
            'so2_total': float(np.nansum(so2_pluma)),
            'area_pixeles': int(np.sum(pluma_principal)),
            'pluma_mascara': pluma_principal,
            'so2_pluma': so2_pluma
        }
    
    def calcular_seccion_transversal(
        self,
        lat_volcan: float,
        lon_volcan: float,
        direccion_viento_grados: float,
        distancia_km: float = 20,
        ancho_seccion_km: float = 50,
        n_puntos: int = 100
    ) -> Dict:
        """
        Calcula la sección transversal de SO2 perpendicular a la dirección del viento
        
        Args:
            lat_volcan: Latitud del volcán
            lon_volcan: Longitud del volcán
            direccion_viento_grados: Dirección del viento (de donde viene, 0-360)
            distancia_km: Distancia del volcán donde tomar la sección
            ancho_seccion_km: Ancho total de la sección transversal
            n_puntos: Número de puntos en la sección
            
        Returns:
            Diccionario con datos de la sección transversal
        """
        # Dirección hacia donde va el viento (opuesta a de donde viene)
        direccion_pluma = (direccion_viento_grados + 180) % 360
        direccion_pluma_rad = np.radians(direccion_pluma)
        
        # Punto central de la sección (downwind del volcán)
        R = 6371.0  # Radio de la Tierra en km
        lat_centro = lat_volcan + (distancia_km / R) * np.cos(direccion_pluma_rad) * (180/np.pi)
        lon_centro = lon_volcan + (distancia_km / (R * np.cos(np.radians(lat_volcan)))) * np.sin(direccion_pluma_rad) * (180/np.pi)
        
        # Dirección perpendicular (para la sección transversal)
        perpendicular_rad = direccion_pluma_rad + np.pi/2
        
        # Crear puntos de la sección
        distancias_seccion = np.linspace(-ancho_seccion_km/2, ancho_seccion_km/2, n_puntos)
        
        lats_seccion = []
        lons_seccion = []
        
        for d in distancias_seccion:
            lat_p = lat_centro + (d / R) * np.cos(perpendicular_rad) * (180/np.pi)
            lon_p = lon_centro + (d / (R * np.cos(np.radians(lat_centro)))) * np.sin(perpendicular_rad) * (180/np.pi)
            lats_seccion.append(lat_p)
            lons_seccion.append(lon_p)
        
        lats_seccion = np.array(lats_seccion)
        lons_seccion = np.array(lons_seccion)
        
        # Interpolar valores de SO2 en los puntos de la sección
        # Aplanar arrays para interpolación
        lat_flat = self.lat.flatten()
        lon_flat = self.lon.flatten()
        so2_flat = self.so2_data.flatten()
        
        # Filtrar NaN
        mascara_validos = ~np.isnan(so2_flat)
        puntos = np.column_stack([lat_flat[mascara_validos], lon_flat[mascara_validos]])
        valores = so2_flat[mascara_validos]
        
        if len(valores) == 0:
            logger.warning("No hay datos válidos para interpolar")
            return {}
        
        # Interpolar
        puntos_seccion = np.column_stack([lats_seccion, lons_seccion])
        so2_seccion = griddata(puntos, valores, puntos_seccion, method='linear')
        
        # Calcular integral de la sección (para flujo)
        # Convertir distancia a metros
        dx_m = (ancho_seccion_km * 1000) / n_puntos
        
        # Integral usando regla del trapecio
        so2_validos = so2_seccion[~np.isnan(so2_seccion)]
        if len(so2_validos) > 1:
            integral_so2 = np.trapz(so2_validos) * dx_m  # mol/m² * m = mol/m
        else:
            integral_so2 = 0
        
        return {
            'lats_seccion': lats_seccion,
            'lons_seccion': lons_seccion,
            'distancias_km': distancias_seccion,
            'so2_seccion': so2_seccion,
            'lat_centro': lat_centro,
            'lon_centro': lon_centro,
            'ancho_km': ancho_seccion_km,
            'distancia_volcan_km': distancia_km,
            'integral_so2_mol_m': integral_so2,
            'so2_max': float(np.nanmax(so2_seccion)) if len(so2_validos) > 0 else 0,
            'so2_mean': float(np.nanmean(so2_seccion)) if len(so2_validos) > 0 else 0,
            'n_puntos_validos': len(so2_validos)
        }
    
    def mol_m2_a_DU(self, so2_mol_m2: float) -> float:
        """
        Convierte concentración de SO2 de mol/m² a Unidades Dobson (DU)
        
        1 DU = 2.6867e16 moléculas/cm² = 2.6867e20 moléculas/m²
        1 DU ≈ 4.46e-4 mol/m² (para SO2)
        
        Args:
            so2_mol_m2: Concentración en mol/m²
            
        Returns:
            Concentración en DU
        """
        # 1 DU = 2.6867e20 moléculas/m²
        # mol/m² * Avogadro = moléculas/m²
        moleculas_m2 = so2_mol_m2 * self.AVOGADRO
        du = moleculas_m2 / 2.6867e20
        return du
    
    def cerrar(self):
        """Cierra el dataset"""
        if self.dataset is not None:
            self.dataset.close()


def procesar_imagen_tropomi(
    ruta_archivo: str,
    lat_volcan: float,
    lon_volcan: float,
    qa_threshold: float = 0.5
) -> Dict:
    """
    Función principal para procesar una imagen TROPOMI
    
    Args:
        ruta_archivo: Ruta al archivo NetCDF
        lat_volcan: Latitud del volcán
        lon_volcan: Longitud del volcán
        qa_threshold: Umbral de calidad
        
    Returns:
        Diccionario con resultados del procesamiento
    """
    processor = TROPOMIProcessor(ruta_archivo)
    
    try:
        # Filtrar por calidad
        so2_filtrado = processor.filtrar_por_calidad(qa_threshold)
        
        # Extraer región del volcán
        region = processor.extraer_region(lat_volcan, lon_volcan, radio_km=100)
        
        # Detectar pluma
        pluma = processor.detectar_pluma(lat_volcan, lon_volcan)
        
        resultado = {
            'archivo': str(ruta_archivo),
            'tiempo': processor.tiempo,
            'region': region,
            'pluma': pluma,
            'qa_threshold': qa_threshold,
            'procesado': True
        }
        
        return resultado
        
    finally:
        processor.cerrar()


if __name__ == "__main__":
    # Ejemplo de uso
    print("Módulo de procesamiento TROPOMI")
    print("Este módulo requiere archivos NetCDF de TROPOMI para funcionar")
    print("\nUso:")
    print("  processor = TROPOMIProcessor('archivo.nc')")
    print("  resultado = processor.detectar_pluma(lat_volcan, lon_volcan)")
