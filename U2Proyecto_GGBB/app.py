import os
# Esto le dice a Python exactamente dónde buscar las librerías de WeasyPrint
os.add_dll_directory(r"C:\msys64\ucrt64\bin")

import atexit
from flask import Flask
from src.auth import auth_bp
from src.routes import routes_bp
from src.business_logic import check_loan_mora
from apscheduler.schedulers.background import BackgroundScheduler
import requests

app = Flask(__name__)
app.secret_key = 'super_secreto_sociedad_financiera' 

# --- REGISTRO DE BLUEPRINTS ---
app.register_blueprint(auth_bp)
app.register_blueprint(routes_bp)

# --- CONFIGURACIÓN DEL PROGRAMADOR DE TAREAS (SCHEDULER) ---
scheduler = BackgroundScheduler()

# Tarea Programada: Ejecutar check_loan_mora() una vez al día a medianoche
scheduler.add_job(func=check_loan_mora, trigger='cron', hour=0, minute=0, id='mora_diaria')

# Tarea de Prueba (Opcional): Ejecutar check_loan_mora() cada 60 segundos
scheduler.add_job(func=check_loan_mora, trigger='interval', seconds=60, id='mora_prueba')

scheduler.start()

# Asegurar que el programador se apague cuando la aplicación lo haga
atexit.register(lambda: scheduler.shutdown())

if __name__ == '__main__':
    app.run(debug=True, port=5000)