from __future__ import annotations

import json
from pathlib import Path
from decimal import Decimal
from datetime import date, datetime
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol
from utilidades.configuracion import ConfigProyecto, cargar_config
from mysql.connector.connection import MySQLConnection


## INGESTA


@dataclass(frozen=True)
class DocumentoInsert:
    nombre_original: str
    mime_type: str
    formato_original: str
    extension_original: str
    tamano_bytes: int


class RepositorioDocumentosIngesta(Protocol):
    def crear_documento_inicial(self, doc: DocumentoInsert) -> int: ...
    def marcar_ingestado(
        self,
        id_documento: int,
        ruta_original_temporal: str,
        avisos: list[str],
    ) -> None: ...
    def marcar_fallo(
        self,
        id_documento: int,
        error_mensaje: str,
        avisos: list[str],
    ) -> None: ...


## PREPROCESADO


class RepositorioDocumentosPreprocesado(Protocol):
    def obtener_documento(self, id_documento: int) -> Dict[str, Any]:
        """Devuelve:
          - id_documento
          - formato_original
          - ruta_original_temporal
          - avisos_json
          - estado
          - error_mensaje
        """
        ...

    def actualizar_preprocesado_ok(
        self,
        id_documento: int,
        ruta_jpg: str,
        avisos_json: Any,
    ) -> None:
        """Actualiza:
          - ruta_jpg
          - avisos_json
        """
        ...

    def actualizar_preprocesado_error(
        self,
        id_documento: int,
        error_mensaje: str,
        avisos_json: Any,
    ) -> None:
        """Actualiza:
          - error_mensaje
          - avisos_json
        """
        ...


## EXTRACCIÓN


class RepositorioDocumentosExtraccion(Protocol):
    def obtener_ruta_jpg(self, id_documento: int) -> str:
        """Devuelve ruta_jpg para un documento preprocesado"""
        ...


## NORMALIZACIÓN


class RepositorioDocumentosNormalizacion(Protocol):
    def obtener_ultima_extraccion(self, id_documento: int) -> Dict[str, Any]:
        """Devuelve la última extracción de un documento
        Se espera:
          - id_documento
          - id_extraccion
          - json_extraido
          - texto_bruto
          - ok_extraccion
        """
        ...

    def guardar_normalizacion(
        self,
        id_documento: int,
        ok_normalizacion: bool,
        documento_normalizado_json: Any,
        avisos_json: Any,
        error_mensaje: Optional[str] = None,
        version_modulo: Optional[str] = None,
    ) -> None:
        """Persiste el resultado de normalización en:
          - normalizaciones_hist
          - normalizaciones (UPSERT por id_documento)
        - Si ok_normalizacion=False, documento_normalizado_json puede ser None
        """
        ...


## IMPLEMENTACIÓN


class RepositorioDocumentosMySQL:
    """Implementación MySQL unificada para:
      - RepositorioDocumentosIngesta
      - RepositorioDocumentosPreprocesado
      - RepositorioDocumentosExtraccion
      - RepositorioDocumentosNormalizacion

    Solución:
      - La BD puede guardar rutas relativas
      - Este repositorio devuelve rutas absolutas
      - Sólo normaliza
    """

    def __init__(self, conn: MySQLConnection, cfg: ConfigProyecto | None = None):
        self.conn = conn
        self.cfg = cfg or cargar_config()
        self._ruta_base: Path = self.cfg.rutas.base


    # Helpers de rutas (A)

    def abs_path(self, ruta: Any) -> Optional[str]:
        """Convierte ruta a absoluta"""
        if ruta is None:
            return None
        s = str(ruta).strip()
        if not s:
            return None

        p = Path(s)
        if p.is_absolute():
            return str(p)

        return str((self._ruta_base / p).resolve())

    def normalizar_rutas_en_fila_documento(self, fila: Dict[str, Any]) -> Dict[str, Any]:
        """Normaliza campos de ruta"""
        if "ruta_jpg" in fila:
            fila["ruta_jpg"] = self.abs_path(fila.get("ruta_jpg"))
        if "ruta_original_temporal" in fila:
            fila["ruta_original_temporal"] = self.abs_path(fila.get("ruta_original_temporal"))
        return fila


    # Helpers JSON

    def json_dumps(self, valor: Any) -> str:
        """Serializa para columna JSON"""
        if valor is None:
            return json.dumps({}, ensure_ascii=False)

        if isinstance(valor, str):
            try:
                json.loads(valor)
                return valor
            except Exception:
                return json.dumps({"valor": valor}, ensure_ascii=False)

        try:
            return json.dumps(valor, ensure_ascii=False)
        except Exception:
            return json.dumps({"valor": str(valor)}, ensure_ascii=False)

    def json_loads_seguro(self, valor: Any) -> Any:
        """Intenta parsear JSON si es string; si no, devuelve el valor tal cual"""
        if valor is None:
            return None
        if isinstance(valor, (dict, list)):
            return valor
        if isinstance(valor, str):
            s = valor.strip()
            if not s:
                return None
            try:
                return json.loads(s)
            except Exception:
                return valor
        return valor


    # INGESTA

    def crear_documento_inicial(self, doc) -> int:
        sql = """
        INSERT INTO documentos (
          nombre_original, mime_type, formato_original, extension_original, tamano_bytes,
          ruta_original_temporal, ruta_jpg, estado, avisos_json, error_mensaje
        )
        VALUES (%s, %s, %s, %s, %s, NULL, NULL, 'FALLO_INGESTA', %s, NULL)
        """
        cur = self.conn.cursor()
        try:
            cur.execute(
                sql,
                (
                    doc.nombre_original,
                    doc.mime_type,
                    doc.formato_original,
                    doc.extension_original,
                    doc.tamano_bytes,
                    json.dumps([], ensure_ascii=False),
                ),
            )
            self.conn.commit()
            return int(cur.lastrowid)
        finally:
            cur.close()

    def marcar_ingestado(
        self,
        id_documento: int,
        ruta_original_temporal: str,
        avisos: list[str],
    ) -> None:
        sql = """
        UPDATE documentos
        SET estado='INGESTADO',
            ruta_original_temporal=%s,
            avisos_json=%s,
            error_mensaje=NULL
        WHERE id_documento=%s
        """
        cur = self.conn.cursor()
        try:
            cur.execute(
                sql,
                (ruta_original_temporal, json.dumps(avisos, ensure_ascii=False), id_documento),
            )
            self.conn.commit()
        finally:
            cur.close()

    def marcar_fallo(
        self,
        id_documento: int,
        error_mensaje: str,
        avisos: list[str],
    ) -> None:
        sql = """
        UPDATE documentos
        SET estado='FALLO_INGESTA',
            avisos_json=%s,
            error_mensaje=%s
        WHERE id_documento=%s
        """
        cur = self.conn.cursor()
        try:
            cur.execute(
                sql,
                (json.dumps(avisos, ensure_ascii=False), error_mensaje, id_documento),
            )
            self.conn.commit()
        finally:
            cur.close()


    # PREPROCESADO

    def obtener_documento(self, id_documento: int) -> Dict[str, Any]:
        sql = """
        SELECT
            id_documento,
            nombre_original,
            mime_type,
            formato_original,
            extension_original,
            tamano_bytes,
            ruta_original_temporal,
            ruta_jpg,
            fecha_ingesta,
            estado,
            avisos_json,
            error_mensaje
        FROM documentos
        WHERE id_documento=%s
        """
        cur = self.conn.cursor(dictionary=True)
        try:
            cur.execute(sql, (id_documento,))
            fila = cur.fetchone()
            if not fila:
                raise ValueError(f"No existe documento con id_documento={id_documento}")

            # Solución: devolver rutas absolutas
            fila = self.normalizar_rutas_en_fila_documento(fila)

            return fila
        finally:
            cur.close()

    def actualizar_preprocesado_ok(
        self,
        id_documento: int,
        ruta_jpg: str,
        avisos_json: Any,
    ) -> None:
        sql = """
        UPDATE documentos
        SET
            ruta_jpg=%s,
            avisos_json=%s,
            error_mensaje=NULL
        WHERE id_documento=%s
        """
        cur = self.conn.cursor()
        try:
            cur.execute(
                sql,
                (ruta_jpg, self.json_dumps(avisos_json), id_documento),
            )
            self.conn.commit()
        finally:
            cur.close()

    def actualizar_preprocesado_error(
        self,
        id_documento: int,
        error_mensaje: str,
        avisos_json: Any,
    ) -> None:
        sql = """
        UPDATE documentos
        SET
            error_mensaje=%s,
            avisos_json=%s
        WHERE id_documento=%s
        """
        cur = self.conn.cursor()
        try:
            cur.execute(
                sql,
                (error_mensaje, self.json_dumps(avisos_json), id_documento),
            )
            self.conn.commit()
        finally:
            cur.close()


    # EXTRACCIÓN

    def obtener_ruta_jpg(self, id_documento: int) -> str:
        sql = """
        SELECT ruta_jpg, estado
        FROM documentos
        WHERE id_documento=%s
        """
        cur = self.conn.cursor()
        try:
            cur.execute(sql, (id_documento,))
            row = cur.fetchone()
            if not row:
                raise ValueError(f"No existe documento con id_documento={id_documento}")

            ruta_jpg, estado = row

            if estado != "INGESTADO":
                raise ValueError(
                    f"Documento id_documento={id_documento} estado={estado} no apto para extracción"
                )

            if ruta_jpg is None or str(ruta_jpg).strip() == "":
                raise ValueError(
                    f"Documento id_documento={id_documento} no tiene ruta_jpg "
                    f"(preprocesado no realizado o fallido)"
                )

            # Solución: devolver rutas absolutas
            ruta_abs = self.abs_path(ruta_jpg)
            if not ruta_abs:
                raise ValueError(
                    f"Documento id_documento={id_documento} ruta_jpg inválida tras normalización"
                )

            return ruta_abs
        finally:
            cur.close()


    # NORMALIZACIÓN

    def obtener_ultima_extraccion(self, id_documento: int) -> Dict[str, Any]:
        """Lee la última extracción para normalizxación"""
        sql = """
        SELECT
            id_extraccion,
            id_documento,
            json_extraido,
            texto_bruto,
            ok AS ok_extraccion
        FROM extracciones_qwen
        WHERE id_documento=%s
        ORDER BY id_extraccion DESC
        LIMIT 1
        """
        cur = self.conn.cursor(dictionary=True)
        try:
            cur.execute(sql, (id_documento,))
            fila = cur.fetchone()
            if not fila:
                raise ValueError(f"No existe extracción para id_documento={id_documento}")

            fila["json_extraido"] = self.json_loads_seguro(fila.get("json_extraido"))
            fila["texto_bruto"] = fila.get("texto_bruto") if fila.get("texto_bruto") is not None else ""
            fila["ok_extraccion"] = bool(fila.get("ok_extraccion"))
            fila["id_extraccion"] = int(fila.get("id_extraccion"))

            return fila
        finally:
            cur.close()

    def guardar_normalizacion(
        self,
        id_documento: int,
        ok_normalizacion: bool,
        documento_normalizado_json: Any,
        avisos_json: Any,
        error_mensaje: Optional[str] = None,
        version_modulo: Optional[str] = None,
    ) -> None:
        """Persistencia de normalización:
          - Siempre INSERT en normalizaciones_hist (histórico completo)
          - En normalizaciones (estado actual) se guarda si ok_normalizacion = 1
        """
    
        doc_json = None if documento_normalizado_json is None else self.json_dumps_normalizacion(documento_normalizado_json)
        avisos_dump = self.json_dumps(avisos_json)
    
        sql_hist = """
        INSERT INTO normalizaciones_hist (
            id_documento,
            ok_normalizacion,
            documento_normalizado_json,
            avisos_json,
            error_mensaje,
            version_modulo,
            origen
        )
        VALUES (%s, %s, %s, %s, %s, %s, 'W4')
        """
    
        sql_upsert_actual = """
        INSERT INTO normalizaciones (
            id_documento,
            ok_normalizacion,
            documento_normalizado_json,
            avisos_json,
            error_mensaje,
            version_modulo
        )
        VALUES (%s, 1, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            ok_normalizacion=1,
            documento_normalizado_json=VALUES(documento_normalizado_json),
            avisos_json=VALUES(avisos_json),
            error_mensaje=VALUES(error_mensaje),
            version_modulo=VALUES(version_modulo)
        """
    
        sql_borrar_actual = """
        DELETE FROM normalizaciones
        WHERE id_documento=%s
        """
    
        cur = self.conn.cursor()
        try:
            cur.execute(
                sql_hist,
                (
                    int(id_documento),
                    1 if ok_normalizacion else 0,
                    doc_json,
                    avisos_dump,
                    error_mensaje,
                    version_modulo,
                ),
            )
    
            if ok_normalizacion:
                cur.execute(
                    sql_upsert_actual,
                    (
                        int(id_documento),
                        doc_json,
                        avisos_dump,
                        error_mensaje,
                        version_modulo,
                    ),
                )
            else:
                cur.execute(sql_borrar_actual, (int(id_documento),))
    
            self.conn.commit()
        finally:
            cur.close()


    def convertir_decimales_a_numeros_json(self, obj: Any) -> Any:
        if obj is None:
            return None
    
        if isinstance(obj, Decimal):
            return float(obj)
    
        if isinstance(obj, dict):
            return {k: self.convertir_decimales_a_numeros_json(v) for k, v in obj.items()}
    
        if isinstance(obj, list):
            return [self.convertir_decimales_a_numeros_json(v) for v in obj]
    
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
    
        return obj


    def json_dumps_normalizacion(self, valor: Any) -> str:
        """Serializa JSON para normalización. Convierte Decimal anúmero JSON"""
        if valor is None:
            return json.dumps({}, ensure_ascii=False)
    
        if isinstance(valor, str):
            try:
                json.loads(valor)
                return valor
            except Exception:
                return json.dumps({"valor": valor}, ensure_ascii=False)
    
        try:
            valor_convertido = self.convertir_decimales_a_numeros_json(valor)
            return json.dumps(valor_convertido, ensure_ascii=False)
        except Exception:
            return json.dumps({"valor": str(valor)}, ensure_ascii=False)