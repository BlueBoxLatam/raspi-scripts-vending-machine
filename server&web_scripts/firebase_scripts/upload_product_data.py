import firebase_admin
from firebase_admin import credentials, firestore
import csv
import os

# Configuración de Firebase
cred_path = 'serviceAccountKey.json' # Asegúrate que esté en la misma carpeta
if not os.path.exists(cred_path):
    print(f"❌ Error: No se encuentra {cred_path}")
    exit()

cred = credentials.Certificate(cred_path)
firebase_admin.initialize_app(cred)
db = firestore.client()

def upload_products(csv_file):
    # Usamos 'utf-8-sig' para ignorar el BOM de Windows
    with open(csv_file, mode='r', encoding='utf-8-sig') as file:
        # Detectar automáticamente si es coma o punto y coma
        content = file.read(1024)
        file.seek(0)
        dialect = csv.Sniffer().sniff(content, delimiters=',;')
        
        reader = csv.DictReader(file, dialect=dialect)
        
        # Limpiar nombres de columnas (quitar espacios)
        reader.fieldnames = [name.strip().lower() for name in reader.fieldnames]
        
        for row in reader:
            # Ahora buscamos 'sku' en minúsculas y sin espacios
            sku = row.get('sku')
            
            if not sku:
                print(f"⚠️ Saltando fila: No se encontró la columna 'sku'. Columnas detectadas: {reader.fieldnames}")
                continue

            product_data = {
                u'sku': sku,
                u'name': row.get('name', 'Sin nombre'),
                u'category': row.get('category', 'General'),
                u'price': float(row.get('price', 0)),
                u'description': row.get('description', ''),
                u'tags': [t.strip() for t in row.get('tags', '').split(',')] if row.get('tags') else [],
                u'status': row.get('status', 'Available'),
                u'stockAlertThreshold': int(row.get('stockAlertThreshold', 5)),
                u'productImageUrl': u'', 
                u'nutritionalInfo': {}
            }
            
            db.collection(u'products').document(sku).set(product_data)
            print(f"✔️ Sincronizado: {sku} - {product_data['name']}")

if __name__ == "__main__":
    target_file = 'product_data.csv'
    if os.path.exists(target_file):
        print(f"🚀 Iniciando carga robusta desde {target_file}...")
        try:
            upload_products(target_file)
            print("✅ Proceso terminado con éxito.")
        except Exception as e:
            print(f"❌ Error durante la carga: {e}")
    else:
        print(f"❌ No se encontró el archivo: {target_file}")