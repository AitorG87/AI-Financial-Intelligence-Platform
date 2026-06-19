from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Protocol

from mysql.connector.connection import MySQLConnection


class RepositorioExtraccionesQwen(Protocol):
    def insertar_extraccion(
        self,
        *,
        id_documento: int,
        modelo: str,
        version_prompt: str,
        texto_prompt: str,
        ruta_jpg_usada: str,
        texto_bruto: str,
        json_extraido: Dict[str, Any] | None,
        ok: bool,
        errores: list[str],
        avisos: list[str],
        duracion_ms: int,
        fecha_extraccion: datetime,
    ) -> None:
        ...


@dataclass(frozen=True)
class InsercionExtraccionQwen:
    id_documento: int
    modelo: str
    version_prompt: str
    texto_prompt: str
    ruta_jpg_usada: str
    texto_bruto: str
    json_extraido: Dict[str, Any] | None
    ok: bool
    errores: list[str]
    avisos: list[str]
    duracion_ms: int
    fecha_extraccion: datetime


class RepositorioExtraccionesQwenMySQL:
    def __init__(self, conexion: MySQLConnection):
        self.conexion = conexion

    def json_dumps(self, valor: Any) -> str:
        """Serializa para columna JSON"""
        if valor is None:
            return json.dumps([], ensure_ascii=False)

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

    def insertar_extraccion(
        self,
        *,
        id_documento: int,
        modelo: str,
        version_prompt: str,
        texto_prompt: str,
        ruta_jpg_usada: str,
        texto_bruto: str,
        json_extraido: Dict[str, Any] | None,
        ok: bool,
        errores: list[str],
        avisos: list[str],
        duracion_ms: int,
        fecha_extraccion: datetime,
    ) -> None:
        sql = """
        INSERT INTO extracciones_qwen (
          id_documento,
          modelo,
          version_prompt,
          texto_prompt,
          texto_bruto,
          json_extraido,
          ok,
          errores_json,
          avisos_json,
          duracion_ms,
          fecha_extraccion,
          ruta_jpg_usada
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        cur = self.conexion.cursor()
        try:
            json_extraido_db = None
            if json_extraido is not None:
                json_extraido_db = self.json_dumps(json_extraido)

            cur.execute(
                sql,
                (
                    int(id_documento),
                    str(modelo),
                    str(version_prompt),
                    str(texto_prompt),
                    str(texto_bruto or ""),
                    json_extraido_db,
                    1 if ok else 0,
                    self.json_dumps(errores or []),
                    self.json_dumps(avisos or []),
                    int(duracion_ms),
                    fecha_extraccion,
                    str(ruta_jpg_usada or ""),
                ),
            )
            self.conexion.commit()
        finally:
            cur.close()