from flask import Blueprint, render_template, redirect, url_for, session, request, flash
from functools import wraps
from .db import get_db_connection

auth_bp = Blueprint('auth', __name__)

#DECORADOR DE SEGURIDAD
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.index'))
        return f(*args, **kwargs)
    return decorated_function

#RUTAS DE AUTENTICACIÓN
@auth_bp.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('routes.dashboard', section='resumen'))
    return render_template('login.html')

@auth_bp.route('/login', methods=['POST'])
def login():
    dni_ingresado = request.form['user_id']
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE dni = %s", (dni_ingresado,))
    usuario = cursor.fetchone()
    conn.close()
    
    if usuario:
        session['user_id'] = usuario['id']
        session['role'] = usuario['role']
        session['name'] = usuario['full_name']
        return redirect(url_for('routes.dashboard', section='resumen'))
    else:
        flash("Cédula/DNI no encontrado. Intente de nuevo.", 'error')
        return redirect(url_for('auth.index'))

@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.index'))