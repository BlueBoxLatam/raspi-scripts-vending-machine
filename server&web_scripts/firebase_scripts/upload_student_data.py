import firebase_admin
from firebase_admin import credentials, firestore
import pandas as pd
import os

# ==========================================
# CONFIGURACIÓN
# ==========================================
# Obtener la ruta absoluta del directorio donde está este script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

CSV_FILE = os.path.join(SCRIPT_DIR, 'student_data.csv')
COLLECTION_NAME = 'students'  # Nombre de la colección en Firestore
CERT_FILE = os.path.join(SCRIPT_DIR, 'serviceAccountKey.json')

# 1. Inicialización de Firebase
if not os.path.exists(CERT_FILE):
    print(f"Error: No se encuentra el archivo {CERT_FILE}")
else:
    cred = credentials.Certificate(CERT_FILE)
    firebase_admin.initialize_app(cred)
    db = firestore.client()

    def upload_data():
        # Leer el archivo CSV
        try:
            df = pd.read_csv(CSV_FILE)
            # Limpieza básica: quitar espacios en blanco de los nombres de columnas
            df.columns = df.columns.str.strip()
            
            print(f"🚀 Iniciando carga de {len(df)} alumnos a Blue Box DB...")

            for index, row in df.iterrows():
                # Convertimos la fila a un diccionario de Python
                data = row.to_dict()
                
                # Extraemos el UID para usarlo como ID del documento
                # Usamos .get para evitar errores si la columna no existe
                doc_id = str(data.get('UID_NFC')).strip()
                
                if not doc_id or doc_id == 'nan':
                    print(f"⚠️ Fila {index} saltada: UID no válido.")
                    continue

                # 2. Carga con Fusión (Merge)
                # .set(data, merge=True) cumple con tu requisito:
                # - Crea el documento si no existe.
                # - Añade campos nuevos si aparecen en el CSV.
                # - Actualiza los existentes sin borrar el resto.
                db.collection(COLLECTION_NAME).document(doc_id).set(data, merge=True)
                
                print(f"✅ Procesado: {doc_id} | {data.get('name')}")

            print("\n✨ ¡Carga completada con éxito!")

        except Exception as e:
            print(f"❌ Error crítico: {e}")

    if __name__ == "__main__":
        upload_data()