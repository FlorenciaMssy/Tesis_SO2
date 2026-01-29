#!/usr/bin/env python3
"""
Script para inicializar la base de datos y cargar volcanes predefinidos
"""
import sys
sys.path.insert(0, '.')

from database import init_database, get_session, Volcan
from config.settings import VOLCANES_PREDEFINIDOS


def main():
    print("=" * 60)
    print("INICIALIZACIÓN DEL SISTEMA DE MONITOREO SO2")
    print("=" * 60)
    
    # Inicializar base de datos
    print("\n[1/3] Creando tablas en la base de datos...")
    try:
        init_database()
        print("    ✓ Tablas creadas correctamente")
    except Exception as e:
        print(f"    ✗ Error: {e}")
        return 1
    
    # Cargar volcanes predefinidos
    print("\n[2/3] Cargando volcanes predefinidos...")
    session = get_session()
    
    volcanes_agregados = 0
    for clave, datos in VOLCANES_PREDEFINIDOS.items():
        # Verificar si ya existe
        existente = session.query(Volcan).filter_by(nombre=datos['nombre']).first()
        
        if existente:
            print(f"    - {datos['nombre']}: ya existe")
            continue
        
        volcan = Volcan(
            nombre=datos['nombre'],
            latitud=datos['lat'],
            longitud=datos['lon'],
            pais=datos.get('pais')
        )
        session.add(volcan)
        volcanes_agregados += 1
        print(f"    + {datos['nombre']}: agregado")
    
    session.commit()
    session.close()
    print(f"    ✓ {volcanes_agregados} volcanes agregados")
    
    # Verificar conexiones
    print("\n[3/3] Verificando sistema...")
    
    # Verificar base de datos
    try:
        session = get_session()
        count = session.query(Volcan).count()
        session.close()
        print(f"    ✓ Base de datos: {count} volcanes registrados")
    except Exception as e:
        print(f"    ✗ Base de datos: {e}")
    
    # Verificar RabbitMQ (opcional)
    try:
        from message_bus import MessageBus
        mb = MessageBus()
        mb.cerrar()
        print("    ✓ RabbitMQ: conectado")
    except Exception as e:
        print(f"    ! RabbitMQ: {e} (opcional)")
    
    print("\n" + "=" * 60)
    print("INICIALIZACIÓN COMPLETADA")
    print("=" * 60)
    print("\nPuede iniciar el sistema con:")
    print("  docker-compose up -d")
    print("\nO para desarrollo local:")
    print("  python -m api.main")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
