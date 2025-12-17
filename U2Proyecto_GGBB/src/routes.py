from flask import Blueprint, render_template, redirect, url_for, session, request, flash, send_file
import requests
from datetime import date
from .db import get_db_connection
from .auth import login_required
from .business_logic import check_loan_mora, disburse_loan_logic # Importa la lógica
from dateutil.relativedelta import relativedelta
from .auth import get_db_connection
from io import BytesIO # Para manejar el archivo PDF en memoria
from weasyprint import HTML # <-- ¡Añadir WeasyPrint!

routes_bp = Blueprint('routes', __name__, url_prefix='/routes')

#RUTA MAESTRA DEL DASHBOARD
@routes_bp.route('/dashboard', defaults={'section': 'resumen'}, methods=['GET'])
@routes_bp.route('/dashboard/<section>', methods=['GET'])
@login_required
def dashboard(section):
    
    #CANDADO DE SEGURIDAD
    if 'user_id' not in session:
        return redirect(url_for('index'))
    
    
    loans_to_disburse = []
    loans_in_mora = []
    
    user_id = session['user_id']
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    # 1. Recuperar datos frescos del usuario
    cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    usuario = cursor.fetchone()
    
    # 2. Recuperar sus préstamos y clasificar (Lógica de las pestañas)
    cursor.execute("SELECT * FROM loans WHERE user_id = %s ORDER BY id DESC", (user_id,))
    prestamos_data = cursor.fetchall()
    
    c_activos = []
    c_pendientes = []
    c_rechazados = []
    c_finalizados = []

    for p in prestamos_data:
        cursor.execute("SELECT * FROM amortization_schedule WHERE loan_id = %s", (p['id'],))
        p['cronograma'] = cursor.fetchall()
        
        status = p['status']
        
        if status == 'RECHAZADO':
            c_rechazados.append(p)
        elif status in ['ACTIVO', 'EN_MORA']:
            c_activos.append(p)
        elif status in ['BORRADOR', 'EN_REVISION', 'EVALUACION', 'POR_DESEMBOLSAR']:
            c_pendientes.append(p)
        elif status in ['PAGADO', 'INCOBRABLE']:
            c_finalizados.append(p)
            
    # 3. Traer a todos los que pueden ser padrinos (Socios + Staff)
    cursor.execute("SELECT id, full_name FROM users WHERE role IN ('SOCIO', 'SECRETARIO', 'DIRECTOR', 'TESORERO')")
    socios = cursor.fetchall()

    # 4. Historial de Transacciones (Billetera)
    cursor.execute("SELECT * FROM transactions WHERE user_id = %s ORDER BY id DESC LIMIT 10", (user_id,))
    transacciones = cursor.fetchall()

    # 5. Lógica del Panel Admin (Si el usuario es Staff)
    admin_loans = [] # Inicialización segura
    loans_to_disburse = [] # Inicialización segura para Tesorero
    loans_in_mora = [] # Inicialización segura para Tesorero
    page_title = "Panel de Gestión" 
    user_role = usuario['role']

    if user_role == 'SECRETARIO':
        page_title = "Escritorio de Secretaría"
        # 💡 CAMBIO AQUÍ: Ahora busca créditos en BORRADOR O EN_REVISION
        cursor.execute("""
            SELECT loans.id, loans.amount, loans.status, loans.interest_rate, loans.loan_type, users.full_name, users.role 
            FROM loans 
            JOIN users ON loans.user_id = users.id 
            WHERE loans.status IN ('BORRADOR', 'EN_REVISION') 
            ORDER BY loans.id ASC
        """)
        admin_loans = cursor.fetchall()

    elif user_role == 'DIRECTOR':
        page_title = "Despacho del Director"
        # Director solo ve EN_REVISION
        cursor.execute("""
            SELECT loans.id, loans.amount, loans.status, loans.interest_rate, loans.loan_type, users.full_name, users.role 
            FROM loans 
            JOIN users ON loans.user_id = users.id 
            WHERE loans.status = 'EN_REVISION' 
            ORDER BY loans.id ASC
        """)
        admin_loans = cursor.fetchall()

    elif user_role == 'TESORERO':
        page_title = "Caja de Tesorería"
        
        # 5a. Préstamos listos para DESEMBOLSAR (Se quedan como admin_loans principal)
        cursor.execute("""
            SELECT loans.id, loans.amount, loans.status, users.full_name, users.role 
            FROM loans JOIN users ON loans.user_id = users.id 
            WHERE loans.status = 'POR_DESEMBOLSAR' 
            ORDER BY loans.id ASC
        """)
        admin_loans = cursor.fetchall()
        
        # 5b. Préstamos en MORA
        cursor.execute("""
            SELECT loans.id, loans.amount, loans.status, users.full_name, users.role, 
                   COUNT(a.id) AS cuotas_vencidas
            FROM loans JOIN users ON loans.user_id = users.id 
            JOIN amortization_schedule a ON loans.id = a.loan_id
            WHERE loans.status = 'EN_MORA' AND a.payment_status = 'PENDIENTE' 
            GROUP BY loans.id
            ORDER BY a.due_date ASC
        """)
        loans_in_mora = cursor.fetchall()
        
        cursor.execute("""
            SELECT loans.id, loans.amount, loans.status, users.full_name, users.dni, loans.interest_rate, loans.duration_months
            FROM loans JOIN users ON loans.user_id = users.id 
            WHERE loans.status IN ('ACTIVO', 'EN_MORA')
            ORDER BY loans.start_date DESC
        """)
        active_loans_all = cursor.fetchall() # <-- NUEVA LISTA

        loans_to_disburse = admin_loans
    
    conn.close()
    
    return render_template('dashboard.html', 
                           usuario=usuario, 
                           socios=socios, 
                           transacciones=transacciones,
                           admin_loans=admin_loans,
                           page_title=page_title,
                           section=section,
                           c_activos=c_activos,
                           c_pendientes=c_pendientes,
                           c_rechazados=c_rechazados, 
                           c_finalizados=c_finalizados,
                           active_loans_all=active_loans_all if user_role == 'TESORERO' else [],
                           loans_to_disburse=loans_to_disburse if user_role == 'TESORERO' else [],
                           loans_in_mora=loans_in_mora if user_role == 'TESORERO' else [])

#ENDPOINTS DE ACCIÓN

@routes_bp.route('/web_request_loan', methods=['POST'])
@login_required
def web_request_loan():
    user_id = request.form['user_id']
    amount = float(request.form['amount'])
    duration = int(request.form['duration'])
    loan_type = request.form['type']
    guarantor_id = request.form.get('guarantor_id') 
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Averiguar Rol para la Tasa
    cursor.execute("SELECT role FROM users WHERE id = %s", (user_id,))
    user_row = cursor.fetchone()
    user_role = user_row[0]
    
    roles_socios = ['SOCIO', 'SECRETARIO', 'DIRECTOR', 'TESORERO']
    interest_rate = 5.00 if user_role in roles_socios else 15.00
    
    # 2. Insertar el Préstamo
    sql_loan = """INSERT INTO loans 
                  (user_id, amount, interest_rate, duration_months, loan_type, status, request_date) 
                  VALUES (%s, %s, %s, %s, %s, 'BORRADOR', CURDATE())"""
    cursor.execute(sql_loan, (user_id, amount, interest_rate, duration, loan_type))
    
    loan_id = cursor.lastrowid 
    
    # 3. Insertar al Padrino (SOLO SI SE ENVIÓ UNO)
    if guarantor_id:
        sql_guarantor = "INSERT INTO loan_guarantors (loan_id, socio_id, type) VALUES (%s, %s, 'PADRINO_PRINCIPAL')"
        cursor.execute(sql_guarantor, (loan_id, guarantor_id))

    conn.commit()
    conn.close()
    
    # Redirigir a la sección de Créditos para que vea su solicitud
    return redirect(url_for('routes.dashboard', section='creditos'))


@routes_bp.route('/admin_action', methods=['POST'])
@login_required
def admin_action():
    loan_id = request.form['loan_id']
    action = request.form['action']
    
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        if action == 'revisar':
            cursor.execute("UPDATE loans SET status='EN_REVISION' WHERE id=%s", (loan_id,))
        elif action == 'aprobar':
            cursor.execute("UPDATE loans SET status='POR_DESEMBOLSAR' WHERE id=%s", (loan_id,))
        elif action == 'rechazar':
            cursor.execute("UPDATE loans SET status='RECHAZADO' WHERE id=%s", (loan_id,))
        elif action == 'desembolsar':
            success = disburse_loan_logic(loan_id)
            if success:
                 flash(f"🎉 Préstamo #{loan_id} desembolsado y activado.", 'success')
            else:
                 flash(f"Error al desembolsar el préstamo #{loan_id}.", 'error')

        conn.commit()
    except Exception as e:
        print(f"Error en admin_action: {e}")
        conn.rollback()
    finally:
        conn.close()

    # Redirige a la sección de Administración para refrescar la tabla
    return redirect(url_for('routes.dashboard', section='admin'))


@routes_bp.route('/pay_installment', methods=['POST'])
@login_required
def pay_installment():
    schedule_id = request.form['schedule_id']
    loan_id = request.form['loan_id']
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # 1. Obtener datos de la cuota y su fecha de vencimiento
        cursor.execute("SELECT amount_interest, total_amount, payment_status, due_date FROM amortization_schedule WHERE id=%s", (schedule_id,))
        cuota = cursor.fetchone()
        
        if not cuota: 
            flash("Error: No se encontró la cuota de pago.", 'error')
            conn.close()
            return redirect(url_for('routes.dashboard', section='admin'))
        
        monto_interes = float(cuota[0])
        monto_total = float(cuota[1])
        estado_actual = cuota[2]
        due_date = cuota[3]

        if estado_actual == 'PAGADO': 
            flash("Advertencia: Esta cuota ya fue registrada anteriormente.", 'warning')
            conn.close()
            return redirect(url_for('routes.loan_details', loan_id=loan_id))

        today = date.today()
        if today < due_date:
            flash(f"Error: La cuota #{schedule_id} vence el {due_date}. No se permite el pago anticipado.", 'error')
            conn.close()
            return redirect(url_for('routes.loan_details', loan_id=loan_id))

        # 2. Marcar cuota como PAGADA
        cursor.execute("UPDATE amortization_schedule SET payment_status='PAGADO', paid_date=CURDATE() WHERE id=%s", (schedule_id,))
        
        # 3. Registrar Transacción de Ingreso
        cursor.execute("SELECT user_id FROM loans WHERE id=%s", (loan_id,))
        deudor_id = cursor.fetchone()[0]
        
        sql_ingreso = "INSERT INTO transactions (user_id, loan_id, type, amount, description) VALUES (%s, %s, 'PAGO_CUOTA', %s, 'Ingreso por cobro de cuota')"
        cursor.execute(sql_ingreso, (deudor_id, loan_id, monto_total))
        transaction_id = cursor.lastrowid # 🚨 CAPTURAMOS EL ID DE TRANSACCIÓN 🚨
        
        # 4. Motor de Comisiones (Padrino)
        cursor.execute("SELECT socio_id FROM loan_guarantors WHERE loan_id=%s AND type='PADRINO_PRINCIPAL'", (loan_id,))
        padrino = cursor.fetchone()
        
        if padrino:
            socio_id = padrino[0]
            comision = monto_interes * 0.07 
            if comision > 0:
                cursor.execute("UPDATE users SET wallet_balance = wallet_balance + %s WHERE id=%s", (comision, socio_id))
                sql_comision = "INSERT INTO transactions (user_id, loan_id, type, amount, description) VALUES (%s, %s, 'PAGO_COMISION', %s, 'Comisión del 7%%')"
                cursor.execute(sql_comision, (socio_id, loan_id, comision))

        # 5. VERIFICACIÓN DE CIERRE DE CRÉDITO
        cursor.execute("SELECT COUNT(*) FROM amortization_schedule WHERE loan_id = %s AND payment_status != 'PAGADO'", (loan_id,))
        pendientes = cursor.fetchone()[0]

        if pendientes == 0:
            cursor.execute("UPDATE loans SET status = 'PAGADO' WHERE id = %s", (loan_id,))
            print(f"🎉 Préstamo #{loan_id} finalizado correctamente.")

        conn.commit()
        download_url = url_for('routes.download_receipt', transaction_id=transaction_id)
        flash(f"✅ Pago de ${monto_total} registrado exitosamente. <a href='{download_url}' target='_blank' class='alert-link fw-bold'>Click para Descargar Recibo #{transaction_id}</a>", 'success')
        
    except Exception as e:
        print(f"Error en pay_installment: {e}")
        conn.rollback()
        flash(f"Error Crítico: No se pudo registrar el pago. {str(e)}", 'error')
        
    finally:
        cursor.close()
        conn.close()
        
    # Siempre redirigimos al detalle para que el Tesorero pueda seguir cobrando el resto
    return redirect(url_for('routes.loan_details', loan_id=loan_id))


@routes_bp.route('/admin/loan/<int:loan_id>')
@login_required
def loan_details(loan_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    # 1. Datos del Préstamo + Usuario
    cursor.execute("""
        SELECT loans.*, users.full_name 
        FROM loans JOIN users ON loans.user_id = users.id 
        WHERE loans.id = %s
    """, (loan_id,))
    loan = cursor.fetchone()
    
    # 2. Datos del Padrino (si existe)
    cursor.execute("""
        SELECT users.full_name FROM loan_guarantors 
        JOIN users ON loan_guarantors.socio_id = users.id 
        WHERE loan_guarantors.loan_id = %s
    """, (loan_id,))
    guarantor = cursor.fetchone()
    
    # 3. Cronograma de Pagos
    cursor.execute("SELECT * FROM amortization_schedule WHERE loan_id = %s", (loan_id,))
    schedule = cursor.fetchall()
    
    conn.close()
    return render_template('loan_details.html', loan=loan, schedule=schedule, guarantor=guarantor)

@routes_bp.route('/admin/review/<int:loan_id>')
@login_required
def review_request(loan_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    # 1. Datos del Préstamo + Usuario (Incluyendo Scoring)
    cursor.execute("""
        SELECT loans.*, users.full_name, users.role, users.scoring_label 
        FROM loans 
        JOIN users ON loans.user_id = users.id 
        WHERE loans.id = %s
    """, (loan_id,))
    loan = cursor.fetchone()
    
    # 2. Buscar al Padrino
    cursor.execute("""
        SELECT users.full_name 
        FROM loan_guarantors 
        JOIN users ON loan_guarantors.socio_id = users.id 
        WHERE loan_guarantors.loan_id = %s
    """, (loan_id,))
    guarantor = cursor.fetchone()
    
    conn.close()
    
    return render_template('review_request.html', loan=loan, guarantor=guarantor)
   
@routes_bp.route('/download_receipt/<int:transaction_id>', methods=['GET'])
@login_required
def download_receipt(transaction_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # 1. Obtener la transacción (recibo)
    cursor.execute("SELECT * FROM transactions WHERE id = %s", (transaction_id,))
    transaction = cursor.fetchone()

    if not transaction:
        conn.close()
        flash("Error: Transacción no encontrada.", 'error')
        return redirect(url_for('routes.dashboard', section='admin'))

    loan_id = transaction['loan_id']

    # 2. Obtener el cliente y el préstamo (Datos del recibo)
    cursor.execute("""
        SELECT u.full_name, u.dni FROM users u JOIN loans l ON u.id = l.user_id WHERE l.id = %s
    """, (loan_id,))
    client_info = cursor.fetchone()

    cursor.execute("SELECT * FROM loans WHERE id = %s", (loan_id,))
    loan_info = cursor.fetchone()

    # 3. Obtener el ítem del cronograma pagado (Detalle financiero)
    # Buscamos el ítem del cronograma que fue pagado por esta transacción
    cursor.execute("""
        SELECT * FROM amortization_schedule WHERE loan_id = %s AND payment_status = 'PAGADO' 
        ORDER BY paid_date DESC LIMIT 1
    """, (loan_id,)) # Asumimos que la transacción corresponde al pago más reciente
    schedule_item = cursor.fetchone()
    
    conn.close()
    
    # Preparamos los datos para la plantilla
    receipt_data = {
        'transaction': transaction,
        'client': client_info,
        'loan': loan_info,
        'schedule_item': schedule_item,
        'mora_amount': 0.00 # Placeholder: Tendrías que calcular esto si se implementa lógica de mora
    }

    # Renderizar el HTML de la plantilla (WeasyPrint lo convierte a PDF)
    html_out = render_template('receipt_pdf.html', **receipt_data)
    
    # 4. Generar el PDF en memoria
    pdf = HTML(string=html_out).write_pdf()
    
    # Devolver el PDF como una descarga
    return send_file(
        BytesIO(pdf),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'RECIBO_PAGO_{transaction_id}_{client_info["full_name"].replace(" ", "_")}.pdf'
    )
    
    