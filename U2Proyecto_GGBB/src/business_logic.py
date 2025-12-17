# src/business_logic.py
from datetime import date
from dateutil.relativedelta import relativedelta
from flask import jsonify, flash
from .db import get_db_connection
import requests
import time # Añadir si es necesario para simular tareas o retrasos

# --- FUNCIÓN DE MANTENIMIENTO: DETECTAR MORA ---
def check_loan_mora():
    """Ejecuta el procedimiento almacenado en la BD para revisar y actualizar
    el estado de los préstamos en mora, asegurando SERIALIZABLE isolation.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 1. Establecer el nivel de aislamiento (DCL)
        cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        
        # 2. Llamada al Procedimiento Almacenado 'update_loan_mora'
        #    El SP maneja su propia transacción (DML y COMMIT)
        cursor.callproc('update_loan_mora')
        
        # Opcional: Si el SP no tiene COMMIT, deberías hacer conn.commit() aquí.
        # Dado que tu SP lo tiene, este commit asegura que la instrucción SET SESSION se complete.
        conn.commit() 
        print("Mantenimiento de mora ejecutado exitosamente (vía SP).")
    except Exception as e:
        print(f"Error al ejecutar Procedimiento Almacenado de mora: {e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()


def disburse_loan_logic(loan_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT * FROM loans WHERE id = %s", (loan_id,))
        loan = cursor.fetchone()
        
        if not loan or loan['status'] != 'POR_DESEMBOLSAR':
            return jsonify({"error": "El préstamo no está listo para desembolsar"}), 400

        monto = float(loan['amount'])
        tasa = float(loan['interest_rate'])
        plazo = int(loan['duration_months'])
        
        capital_mensual = monto / plazo
        interes_mensual = monto * (tasa / 100)
        cuota_fija = capital_mensual + interes_mensual
        
        fecha_inicio = date.today()
        
        #Tabla de Amortización
        for i in range(1, plazo + 1):
            fecha_pago = fecha_inicio + relativedelta(months=i)
            
            sql_schedule = """INSERT INTO amortization_schedule 
                              (loan_id, installment_number, due_date, amount_capital, amount_interest, total_amount) 
                              VALUES (%s, %s, %s, %s, %s, %s)"""
            
            cursor.execute(sql_schedule, (
                loan['id'], 
                i, 
                fecha_pago, 
                round(capital_mensual, 2), 
                round(interes_mensual, 2), 
                round(cuota_fija, 2)
            ))

        # 4. Actualizar estado y registrar salida de dinero
        cursor.execute("UPDATE loans SET status = 'ACTIVO', start_date = %s WHERE id = %s", (fecha_inicio, loan['id']))
        sql_trans = "INSERT INTO transactions (user_id, loan_id, type, amount, description) VALUES (%s, %s, 'DESEMBOLSO_CREDITO', %s, 'Entrega de capital')"
        cursor.execute(sql_trans, (loan['user_id'], loan['id'], monto))

        conn.commit()
        return True # Retorna True si fue exitoso

    except Exception as e:
        conn.rollback()
        print(f"Error al desembolsar: {e}")
        return False
    finally:
        cursor.close()
        conn.close()