# migrate_data.py
import json
from datetime import datetime
from database import SessionLocal, SiniestroClasificado

def migrar_datos():
    """Migra datos del JSON a la base de datos"""
    
    # Leer archivo JSON
    try:
        with open('clasificacion_siniestros.json', 'r', encoding='utf-8') as f:
            datos = json.load(f)
    except FileNotFoundError:
        print("❌ Archivo 'clasificacion_siniestros.json' no encontrado")
        return
    except json.JSONDecodeError:
        print("❌ Error al leer JSON")
        return
    
    # Conectar a BD
    db = SessionLocal()
    
    try:
        print("📖 Leyendo archivo JSON...")
        
        # Contar registros
        total_registros = len(datos.get('siniestros', []))
        print(f"📊 Encontrados {total_registros} registros en JSON")
        
        registros_insertados = 0
        registros_duplicados = 0
        registros_error = 0
        
        # Migrar cada siniestro
        for item in datos.get('siniestros', []):
            try:
                # Verificar si ya existe
                existente = db.query(SiniestroClasificado).filter_by(
                    mail_id=item['mail_id']
                ).first()
                
                if existente:
                    registros_duplicados += 1
                    continue
                
                # Convertir fecha
                try:
                    fecha_mail = datetime.strptime(item['fecha'], '%Y-%m-%d %H:%M:%S')
                except:
                    fecha_mail = datetime.now()
                
                # Crear registro
                siniestro = SiniestroClasificado(
                    mail_id=item['mail_id'],
                    numero_siniestro=item.get('numero_siniestro'),
                    remitente=item['remitente'],
                    asunto=item['asunto'],
                    fecha_mail=fecha_mail
                )
                
                db.add(siniestro)
                registros_insertados += 1
                
                # Log
                numero = item.get('numero_siniestro', 'SIN NÚMERO')
                print(f"✅ OK: {numero}")
                
            except Exception as e:
                registros_error += 1
                print(f"❌ ERROR: {item['mail_id']} - {str(e)}")
        
        # Commit
        db.commit()
        
        # Resumen
        print("\n" + "="*60)
        print(f"✅ MIGRACIÓN COMPLETADA")
        print(f"   ✓ Insertados: {registros_insertados}")
        print(f"   ⚠ Duplicados: {registros_duplicados}")
        print(f"   ✗ Errores: {registros_error}")
        print("="*60)
        
    except Exception as e:
        db.rollback()
        print(f"\n❌ ERROR FATAL: {str(e)}")
    finally:
        db.close()

if __name__ == '__main__':
    migrar_datos()
