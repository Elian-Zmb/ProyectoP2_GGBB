import mysql.connector

# CONFIGURACIÓN DE LA BASE DE DATOS
def get_db_connection():
    connection = mysql.connector.connect(
        host='localhost',
        user='app_user',
        password='Tu_Password_Segura_123',
        database='sociedad_financiera'
    )
    return connection