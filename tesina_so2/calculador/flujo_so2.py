"""
Calculador de Flujo SO2

CORRECCIÓN IMPORTANTE:
El flujo se calcula iterando por alturas de viento disponibles y
seleccionando aquella donde se detecta una pluma coherente.

El flujo se calcula como:
    Flujo (kg/s) = SO2_max (kg/m²) × distancia (m) × velocidad_viento (m/s)

Para 6 franjas horarias (0.5h, 1h, 1.5h, 2h, 2.5h, 3h) desde el punto de emisión.
El flujo diario es el promedio de las franjas válidas, convertido a ton/día.
"""
import numpy as np
from datetime import datetime, date
from typing import Optional, Dict, List
import logging

from database import get_session, ResultadoFlujoSO2
from config.settings import SO2FC_FRANJAS_HORAS, KGS_TO_TD_FACTOR

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def to_float(value) -> Optional[float]:
    """Convierte a float nativo de Python"""
    if value is None:
        return None
    try:
        result = float(value)
        if np.isnan(result) or np.isinf(result):
            return None
        return result
    except (TypeError, ValueError):
        return None


def to_int(value) -> Optional[int]:
    """Convierte a int nativo de Python"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def convertir_a_serializable(obj):
    """Convierte recursivamente a tipos serializables JSON"""
    if obj is None:
        return None
    elif isinstance(obj, (np.integer,)):
        return int(obj)
    elif isinstance(obj, (np.floating,)):
        val = float(obj)
        return None if (np.isnan(val) or np.isinf(val)) else val
    elif isinstance(obj, np.ndarray):
        return [convertir_a_serializable(x) for x in obj.tolist()]
    elif isinstance(obj, dict):
        return {k: convertir_a_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [convertir_a_serializable(item) for item in obj]
    elif isinstance(obj, (str, int, bool)):
        return obj
    elif isinstance(obj, float):
        return None if (np.isnan(obj) or np.isinf(obj)) else obj
    elif isinstance(obj, (datetime, date)):
        return obj.isoformat()
    else:
        try:
            return str(obj)
        except:
            return None


class CalculadorFlujoSO2:
    """
    Calculador de flujo SO2 usando método SO2FC

    Franjas horarias: 0.5, 1.0, 1.5, 2.0, 2.5, 3.0 horas desde el volcán
    """

    def __init__(self):
        self.session = get_session()

    def guardar_resultado(
        self,
        volcan_id: int,
        imagen_id: int,
        fecha_hora: datetime,
        resultado: Dict
    ) -> Optional[int]:
        """
        Guarda el resultado del cálculo en la base de datos
        """
        if not resultado.get('exito'):
            return None

        try:
            # Determinar calidad
            n_franjas = resultado.get('n_franjas_validas', 0)
            if n_franjas >= 5:
                qa_flag = 0  # Bueno
            elif n_franjas >= 3:
                qa_flag = 1  # Aceptable
            else:
                qa_flag = 2  # Malo

            # Calcular incertidumbre aproximada
            flujos = [f['flujo_kgs'] for f in resultado.get('franjas', [])
                     if f.get('flujo_kgs') is not None]
            if len(flujos) > 1:
                incertidumbre_pct = (np.std(flujos) / np.mean(flujos)) * 100
            else:
                incertidumbre_pct = 50  # Default

            registro = ResultadoFlujoSO2(
                volcan_id=to_int(volcan_id),
                imagen_id=to_int(imagen_id),
                fecha_hora=fecha_hora,
                flujo_so2_kg_s=to_float(resultado.get('flujo_promedio_kgs')),
                flujo_so2_ton_dia=to_float(resultado.get('flujo_promedio_td')),
                columna_so2_max=to_float(resultado.get('so2_total_molm2')),
                columna_so2_total=to_float(resultado.get('so2_total_molm2')),
                velocidad_viento_ms=to_float(resultado.get('velocidad_viento_ms')),
                direccion_viento_grados=to_float(resultado.get('direccion_viento_grados')),
                altitud_viento_m=to_float(resultado.get('altura_viento_m')),
                azimut_pluma_grados=to_float(resultado.get('azimut_pluma')),
                distancia_volcan_km=None,
                ancho_pluma_km=None,
                incertidumbre_pct=to_float(incertidumbre_pct),
                qa_flag=to_int(qa_flag),
                metadatos_json=convertir_a_serializable(resultado)
            )

            self.session.add(registro)
            self.session.commit()

            logger.info(f"Resultado guardado con ID {registro.id}")
            return registro.id

        except Exception as e:
            logger.error(f"Error guardando resultado: {e}")
            import traceback
            traceback.print_exc()
            self.session.rollback()
            return None

    def obtener_resultados_diarios(
        self,
        volcan_id: int,
        fecha_inicio: datetime,
        fecha_fin: datetime
    ) -> List[Dict]:
        """
        Obtiene resultados agrupados por día (como en MATLAB)
        """
        resultados = self.session.query(ResultadoFlujoSO2).filter(
            ResultadoFlujoSO2.volcan_id == volcan_id,
            ResultadoFlujoSO2.fecha_hora >= fecha_inicio,
            ResultadoFlujoSO2.fecha_hora <= fecha_fin
        ).order_by(ResultadoFlujoSO2.fecha_hora).all()

        # Agrupar por día
        por_dia = {}
        for r in resultados:
            fecha = r.fecha_hora.date()
            if fecha not in por_dia:
                por_dia[fecha] = []
            por_dia[fecha].append(r)

        # Calcular promedio por día
        resultados_diarios = []
        for fecha, registros in sorted(por_dia.items()):
            flujos_td = [r.flujo_so2_ton_dia for r in registros
                        if r.flujo_so2_ton_dia is not None]

            if flujos_td:
                flujo_promedio = np.mean(flujos_td)
                flujo_std = np.std(flujos_td) if len(flujos_td) > 1 else 0

                resultados_diarios.append({
                    'fecha': fecha.isoformat(),
                    'flujo_td': to_float(flujo_promedio),
                    'flujo_std': to_float(flujo_std),
                    'n_mediciones': len(flujos_td),
                    'mediciones': [
                        {
                            'hora': r.fecha_hora.strftime('%H:%M'),
                            'flujo_td': to_float(r.flujo_so2_ton_dia),
                            'qa_flag': r.qa_flag
                        }
                        for r in registros
                    ]
                })

        return resultados_diarios

    def obtener_serie_temporal(
        self,
        volcan_id: int,
        fecha_inicio: datetime,
        fecha_fin: datetime
    ) -> List[Dict]:
        """
        Obtiene la serie temporal de flujos de SO2 para un volcán
        """
        resultados = self.session.query(ResultadoFlujoSO2).filter(
            ResultadoFlujoSO2.volcan_id == volcan_id,
            ResultadoFlujoSO2.fecha_hora >= fecha_inicio,
            ResultadoFlujoSO2.fecha_hora <= fecha_fin
        ).order_by(ResultadoFlujoSO2.fecha_hora).all()

        serie = []
        for r in resultados:
            serie.append({
                'id': r.id,
                'fecha_hora': r.fecha_hora.isoformat(),
                'flujo_kg_s': to_float(r.flujo_so2_kg_s),
                'flujo_ton_dia': to_float(r.flujo_so2_ton_dia),
                'so2_max': to_float(r.columna_so2_max),
                'velocidad_viento_ms': to_float(r.velocidad_viento_ms),
                'direccion_viento_grados': to_float(r.direccion_viento_grados),
                'incertidumbre_pct': to_float(r.incertidumbre_pct),
                'qa_flag': to_int(r.qa_flag)
            })

        return serie

    def cerrar(self):
        """Cierra la sesión"""
        self.session.close()


if __name__ == "__main__":
    print("Calculador de Flujo SO2 - Método SO2FC")
    print("=" * 50)
    print("\nMétodo basado en Carbajal et al. (SEGEMAR/OAVV)")
    print(f"\nFranjas horarias: {SO2FC_FRANJAS_HORAS}")
    print(f"\nFórmula: Flujo (kg/s) = SO2_max × distancia × velocidad_viento")
    print(f"Conversión: ton/día = kg/s × {KGS_TO_TD_FACTOR}")
