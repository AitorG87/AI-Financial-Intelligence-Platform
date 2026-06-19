from __future__ import annotations

import sys
from pathlib import Path
from typing import List

import mysql.connector
from mysql.connector.connection import MySQLConnection

# Resolver ruta base antes de cualquier import del proyecto
ruta_archivo = Path(__file__).resolve()
ruta_raiz = ruta_archivo.parents[1]
if str(ruta_raiz) not in sys.path:
    sys.path.insert(0, str(ruta_raiz))

from utilidades.configuracion import cargar_config, cargar_env_desde_raiz
from bd.conexion import crear_conexion_mysql



## SQL runner con soporte de DELIMITER


def partir_sentencias_sql(sql: str) -> List[str]:
    """Divide un script SQL en sentencias soportando cambios de DELIMITER

    - Interpreta líneas 'DELIMITER xx' como instrucción del cliente
    - Soporta delimitadores multi-caracter (p.ej. $$)
    - Respeta comillas simples/dobles para no cortar dentro de strings
    """
    stmts: List[str] = []
    buf: List[str] = []
    delim = ";"

    in_squote = False
    in_dquote = False
    escape = False

    i = 0
    n = len(sql)

    # Pre-normalizar saltos de línea para el parser (sin tocar el contenido)
    while i < n:
        # Detectar 'DELIMITER ' al inicio de línea (ignorando espacios)
        # Miramos desde el comienzo de la línea actual
        line_start = sql.rfind("\n", 0, i) + 1
        # Si estamos al principio lógico de línea y no estamos dentro de comillas, check delimiter
        if i == line_start and not in_squote and not in_dquote:
            # Tomar la línea completa
            line_end = sql.find("\n", i)
            if line_end == -1:
                line_end = n
            line = sql[i:line_end].strip()
            if line.upper().startswith("DELIMITER "):
                # Flush buffer previo (si hubiera algo no vacío)
                pending = "".join(buf).strip()
                if pending:
                    stmts.append(pending)
                    buf = []
                delim = line.split(None, 1)[1].strip()
                # Saltar la línea DELIMITER completa
                i = line_end + 1
                continue

        ch = sql[i]

        if escape:
            buf.append(ch)
            escape = False
            i += 1
            continue

        if ch == "\\":
            buf.append(ch)
            escape = True
            i += 1
            continue

        if ch == "'" and not in_dquote:
            in_squote = not in_squote
            buf.append(ch)
            i += 1
            continue

        if ch == '"' and not in_squote:
            in_dquote = not in_dquote
            buf.append(ch)
            i += 1
            continue

        # Si no estamos dentro de comillas, comprobar delimitador
        if not in_squote and not in_dquote:
            if delim and sql.startswith(delim, i):
                stmt = "".join(buf).strip()
                if stmt:
                    stmts.append(stmt)
                buf = []
                i += len(delim)
                continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)

    return stmts


def ejecutar_sql(conn: MySQLConnection, ruta_sql: Path) -> None:
    contenido = ruta_sql.read_text(encoding="utf-8")

    # Partir en sentencias
    sentencias = partir_sentencias_sql(contenido)

    cursor = conn.cursor()
    try:
        for s in sentencias:
            s_clean = s.strip()
            if not s_clean:
                continue
            cursor.execute(s_clean)
            if cursor.with_rows:
                cursor.fetchall()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


## Bootstrap BD

def asegurar_base_datos(cfg) -> None:
    """Crea la BD si no existe"""
    conn = mysql.connector.connect(
        host=cfg.mysql.host,
        port=cfg.mysql.port,
        user=cfg.mysql.user,
        password=cfg.mysql.password,
        autocommit=False,
    )
    cursor = conn.cursor()
    try:
        nombre_bd = cfg.mysql.database
        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS `{nombre_bd}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_spanish_ci;"
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def main() -> None:
    # Cargar configuración centralizada
    cargar_env_desde_raiz()
    cfg = cargar_config()

    ruta_sql = cfg.rutas.base / "bd" / "sql"
    ruta_esquema = ruta_sql / "001_esquema.sql"
    ruta_rutinas_eventos = ruta_sql / "002_rutinas_y_eventos.sql"

    if not ruta_esquema.exists():
        raise FileNotFoundError(f"No existe el archivo SQL de esquema: {ruta_esquema}")
    if not ruta_rutinas_eventos.exists():
        raise FileNotFoundError(f"No existe el archivo SQL de rutinas/eventos: {ruta_rutinas_eventos}")

    # 1) Asegurar BD
    asegurar_base_datos(cfg)

    # 2) Conectar con BD seleccionada y aplicar scripts
    conn = crear_conexion_mysql(cfg)
    try:
        ejecutar_sql(conn, ruta_esquema)
        ejecutar_sql(conn, ruta_rutinas_eventos)
    finally:
        conn.close()

    print("Bootstrap completado: base de datos, tablas, rutinas y eventos aplicados correctamente")


if __name__ == "__main__":
    main()