#!/usr/bin/env python3
"""
Script principal para ejecutar el sistema de monitoreo de SO2

Uso:
    python main.py init          # Inicializar base de datos
    python main.py api           # Ejecutar API REST
    python main.py worker-calc   # Ejecutar worker de cálculos
    python main.py worker-etl    # Ejecutar worker de extracción
    python main.py demo          # Ejecutar demostración
"""
import sys
import argparse


def cmd_init():
    """Inicializar la base de datos"""
    from init_db import main
    return main()


def cmd_api():
    """Ejecutar la API REST"""
    from api import run_api
    run_api()


def cmd_worker_calc():
    """Ejecutar worker de cálculos"""
    from message_bus import iniciar_worker_calculos
    iniciar_worker_calculos()


def cmd_worker_etl():
    """Ejecutar worker de extracción"""
    from message_bus import iniciar_worker_extraccion
    iniciar_worker_extraccion()


def cmd_demo():
    """Ejecutar demostración del sistema"""
    from datetime import datetime, timedelta
    from database import get_session, Volcan, init_database
    from calculador import CalculadorFlujoSO2
    
    print("=" * 60)
    print("DEMOSTRACIÓN DEL SISTEMA DE MONITOREO SO2")
    print("=" * 60)
    
    # Inicializar
    print("\n[1] Inicializando base de datos...")
    init_database()
    
    # Crear volcán de ejemplo
    print("\n[2] Creando volcán de ejemplo (Monte Etna)...")
    session = get_session()
    
    volcan = session.query(Volcan).filter_by(nombre="Monte Etna").first()
    if not volcan:
        volcan = Volcan(
            nombre="Monte Etna",
            latitud=37.751,
            longitud=14.993,
            pais="Italia",
            altitud_m=3329
        )
        session.add(volcan)
        session.commit()
        print(f"    Volcán creado con ID: {volcan.id}")
    else:
        print(f"    Volcán existente con ID: {volcan.id}")
    
    # Demostrar cálculo de flujo
    print("\n[3] Demostración de cálculo de flujo...")
    calculador = CalculadorFlujoSO2()
    
    # Valores típicos para una erupción moderada
    integral_so2 = 0.5  # mol/m
    velocidad_viento = 10  # m/s
    
    resultado = calculador.calcular_flujo(
        integral_so2_mol_m=integral_so2,
        velocidad_viento_ms=velocidad_viento
    )
    
    print(f"\n    Parámetros de entrada:")
    print(f"      - Integral SO2: {integral_so2} mol/m")
    print(f"      - Velocidad viento: {velocidad_viento} m/s")
    print(f"\n    Resultados:")
    print(f"      - Flujo SO2: {resultado['flujo_kg_s']:.2f} kg/s")
    print(f"      - Flujo SO2: {resultado['flujo_ton_dia']:.1f} ton/día")
    print(f"      - Incertidumbre: ±{resultado['incertidumbre_pct']:.1f}%")
    
    calculador.cerrar()
    session.close()
    
    # Información del sistema
    print("\n[4] Servicios disponibles:")
    print("    - API REST: http://localhost:8000")
    print("    - Documentación API: http://localhost:8000/docs")
    print("    - Frontend: http://localhost (con Docker)")
    print("    - Jupyter: http://localhost:8888 (con Docker)")
    
    print("\n" + "=" * 60)
    print("DEMOSTRACIÓN COMPLETADA")
    print("=" * 60)
    
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Sistema de Monitoreo de SO2 Volcánico",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        'comando',
        choices=['init', 'api', 'worker-calc', 'worker-etl', 'demo'],
        help='Comando a ejecutar'
    )
    
    args = parser.parse_args()
    
    comandos = {
        'init': cmd_init,
        'api': cmd_api,
        'worker-calc': cmd_worker_calc,
        'worker-etl': cmd_worker_etl,
        'demo': cmd_demo
    }
    
    return comandos[args.comando]()


if __name__ == "__main__":
    sys.exit(main())
