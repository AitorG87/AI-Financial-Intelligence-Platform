from __future__ import annotations
import mysql.connector
from mysql.connector.connection import MySQLConnection
from utilidades.configuracion import ConfigProyecto


def crear_conexion_mysql(cfg: ConfigProyecto) -> MySQLConnection:
    """Crea una conexión MySQL a partir de la configuración"""

    faltan = []
    if not cfg.mysql.host:
        faltan.append("MYSQL_HOST")
    if not cfg.mysql.port:
        faltan.append("MYSQL_PORT")
    if not cfg.mysql.user:
        faltan.append("MYSQL_USER")
    if not cfg.mysql.password:
        faltan.append("MYSQL_PASSWORD")
    if not cfg.mysql.database:
        faltan.append("MYSQL_DATABASE")

    if faltan:
        raise ValueError(f"Faltan variables de entorno para MySQL: {', '.join(faltan)}")

    conn = mysql.connector.connect(
        host=cfg.mysql.host,
        port=cfg.mysql.port,
        user=cfg.mysql.user,
        password=cfg.mysql.password,
        database=cfg.mysql.database,
        autocommit=False,
    )
    return conn