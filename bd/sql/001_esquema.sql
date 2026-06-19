CREATE DATABASE IF NOT EXISTS contabilidad_familiar;

USE contabilidad_familiar;


/* Control de versiones del esquema */

CREATE TABLE IF NOT EXISTS version_esquema (
  id TINYINT UNSIGNED NOT NULL,
  version INT NOT NULL,
  fecha_aplicacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id)
);

INSERT INTO version_esquema (id, version)
VALUES (1, 2)
ON DUPLICATE KEY UPDATE
  version = GREATEST(version, VALUES(version)),
  fecha_aplicacion = CURRENT_TIMESTAMP;


/* AUDITORÍA / PIPELINE */

CREATE TABLE IF NOT EXISTS documentos (
  id_documento BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

  nombre_original VARCHAR(512) NOT NULL,
  mime_type VARCHAR(128) NOT NULL,

  formato_original ENUM('JPG','PNG','PDF') NOT NULL,
  extension_original VARCHAR(10) NOT NULL,

  tamano_bytes BIGINT UNSIGNED NOT NULL,

  ruta_original_temporal VARCHAR(1024) NULL,
  ruta_jpg VARCHAR(255) NULL,

  fecha_ingesta DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

  estado ENUM('INGESTADO','FALLO_INGESTA') NOT NULL,
  avisos_json JSON NOT NULL,

  error_mensaje VARCHAR(1024) NULL,

  PRIMARY KEY (id_documento),

  INDEX idx_documentos_fecha_ingesta (fecha_ingesta),
  INDEX idx_documentos_estado (estado)
);

CREATE TABLE IF NOT EXISTS extracciones_qwen (
  id_extraccion      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  id_documento       BIGINT UNSIGNED NOT NULL,

  modelo             VARCHAR(128) NOT NULL,
  version_prompt     VARCHAR(64)  NOT NULL,
  texto_prompt       TEXT         NOT NULL,

  texto_bruto        LONGTEXT     NOT NULL,
  json_extraido      JSON         NULL,

  ok                 TINYINT      NOT NULL,

  errores_json       JSON         NOT NULL,
  avisos_json        JSON         NOT NULL,
  duracion_ms        INT          NOT NULL,
  fecha_extraccion   DATETIME     NOT NULL,

  ruta_jpg_usada     TEXT         NOT NULL,

  PRIMARY KEY (id_extraccion),

  CONSTRAINT fk_extracciones_documento
    FOREIGN KEY (id_documento)
    REFERENCES documentos(id_documento)
    ON DELETE CASCADE,

  INDEX idx_extracciones_doc (id_documento),
  INDEX idx_extracciones_ok (ok),
  INDEX idx_extracciones_fecha (fecha_extraccion),
  INDEX idx_extracciones_modelo_prompt (modelo, version_prompt)
);


CREATE TABLE IF NOT EXISTS normalizaciones (
  id_documento BIGINT UNSIGNED NOT NULL,

  ok_normalizacion TINYINT(1) NOT NULL,
  documento_normalizado_json JSON NULL,
  avisos_json JSON NOT NULL,

  error_mensaje TEXT NULL,
  version_modulo VARCHAR(64) NULL,

  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  fecha_modificacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (id_documento),

  CONSTRAINT fk_normalizaciones_documento
    FOREIGN KEY (id_documento) REFERENCES documentos(id_documento)
    ON DELETE CASCADE,

  INDEX idx_normalizaciones_ok (ok_normalizacion),
  INDEX idx_normalizaciones_modif (fecha_modificacion)
);

CREATE TABLE IF NOT EXISTS normalizaciones_hist (
  id_normalizacion BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  id_documento BIGINT UNSIGNED NOT NULL,

  ok_normalizacion TINYINT(1) NOT NULL,
  documento_normalizado_json JSON NULL,
  avisos_json JSON NOT NULL,

  error_mensaje TEXT NULL,
  version_modulo VARCHAR(64) NULL,

  origen ENUM('W4','USUARIO') NOT NULL DEFAULT 'W4',
  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (id_normalizacion),

  CONSTRAINT fk_normalizaciones_hist_documento
    FOREIGN KEY (id_documento) REFERENCES documentos(id_documento)
    ON DELETE CASCADE,

  INDEX idx_normalizaciones_hist_doc_fecha (id_documento, fecha_creacion),
  INDEX idx_normalizaciones_hist_ok (ok_normalizacion)
);

CREATE TABLE IF NOT EXISTS validaciones_contables (
  id_validacion BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  id_normalizacion BIGINT UNSIGNED NOT NULL,

  ok_validacion TINYINT(1) NOT NULL,

  errores_json JSON NOT NULL,
  avisos_json JSON NOT NULL,
  tolerancias_json JSON NOT NULL,

  error_mensaje TEXT NULL,
  version_modulo VARCHAR(64) NULL,

  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (id_validacion),

  CONSTRAINT fk_validaciones_norm_hist
    FOREIGN KEY (id_normalizacion) REFERENCES normalizaciones_hist(id_normalizacion)
    ON DELETE CASCADE,

  INDEX idx_validaciones_norm (id_normalizacion),
  INDEX idx_validaciones_ok (ok_validacion)
);

CREATE TABLE IF NOT EXISTS clasificaciones_hist (
  id_clasificacion BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  id_normalizacion BIGINT UNSIGNED NOT NULL,

  ok_clasificacion TINYINT(1) NOT NULL,

  categoria_nivel_1 VARCHAR(80) NOT NULL,
  etiqueta VARCHAR(80) NOT NULL,
  confianza DECIMAL(3,2) NULL,

  avisos_json JSON NOT NULL,
  error_mensaje TEXT NULL,
  version_modulo VARCHAR(64) NULL,

  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (id_clasificacion),

  CONSTRAINT fk_clasificaciones_norm_hist
    FOREIGN KEY (id_normalizacion) REFERENCES normalizaciones_hist(id_normalizacion)
    ON DELETE CASCADE,

  INDEX idx_clasificaciones_norm (id_normalizacion),
  INDEX idx_clasificaciones_ok (ok_clasificacion),
  INDEX idx_clasificaciones_cat (categoria_nivel_1, etiqueta)
);

CREATE TABLE IF NOT EXISTS clasificaciones (
  id_documento BIGINT UNSIGNED NOT NULL,

  categoria_nivel_1 VARCHAR(80) NOT NULL,
  etiqueta VARCHAR(80) NOT NULL,
  confianza DECIMAL(3,2) NULL,

  id_clasificacion_fuente BIGINT UNSIGNED NOT NULL,

  fecha_modificacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (id_documento),

  CONSTRAINT fk_clasificaciones_documento
    FOREIGN KEY (id_documento) REFERENCES documentos(id_documento)
    ON DELETE CASCADE,

  CONSTRAINT fk_clasificaciones_fuente
    FOREIGN KEY (id_clasificacion_fuente) REFERENCES clasificaciones_hist(id_clasificacion)
    ON DELETE RESTRICT,

  INDEX idx_clasificaciones_actual_cat (categoria_nivel_1, etiqueta)
);

CREATE TABLE IF NOT EXISTS auditoria_cambios (
  id_cambio BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  id_documento BIGINT UNSIGNED NOT NULL,

  fecha_cambio DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  origen ENUM('STREAMLIT','SISTEMA') NOT NULL DEFAULT 'STREAMLIT',
  usuario VARCHAR(64) NOT NULL DEFAULT 'usuario',

  campo VARCHAR(64) NOT NULL,
  valor_anterior_json JSON NULL,
  valor_nuevo_json JSON NULL,
  comentario VARCHAR(255) NULL,

  PRIMARY KEY (id_cambio),

  CONSTRAINT fk_auditoria_documento
    FOREIGN KEY (id_documento) REFERENCES documentos(id_documento)
    ON DELETE CASCADE,

  INDEX idx_auditoria_doc_fecha (id_documento, fecha_cambio)
);


/* CONSUMO BI */

CREATE TABLE IF NOT EXISTS empresas (
  id_empresa BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  nombre_normalizado VARCHAR(160) NOT NULL,
  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id_empresa),
  UNIQUE KEY uq_empresas_nombre (nombre_normalizado)
);

CREATE TABLE IF NOT EXISTS tickets (
  id_documento BIGINT UNSIGNED NOT NULL,

  id_empresa BIGINT UNSIGNED NULL,

  fecha DATE NOT NULL,
  total DECIMAL(6,2) NOT NULL,
  descuento_total DECIMAL(6,2) NULL,
  iva_incluido_en_precios TINYINT(1) NULL,

  id_normalizacion_fuente BIGINT UNSIGNED NOT NULL,
  id_clasificacion_fuente BIGINT UNSIGNED NULL,

  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  fecha_modificacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (id_documento),

  CONSTRAINT fk_tickets_documento
    FOREIGN KEY (id_documento) REFERENCES documentos(id_documento)
    ON DELETE CASCADE,

  CONSTRAINT fk_tickets_empresa
    FOREIGN KEY (id_empresa) REFERENCES empresas(id_empresa)
    ON DELETE SET NULL,

  CONSTRAINT fk_tickets_normalizacion_fuente
    FOREIGN KEY (id_normalizacion_fuente) REFERENCES normalizaciones_hist(id_normalizacion)
    ON DELETE RESTRICT,

  CONSTRAINT fk_tickets_clasificacion_fuente
    FOREIGN KEY (id_clasificacion_fuente) REFERENCES clasificaciones_hist(id_clasificacion)
    ON DELETE SET NULL,

  INDEX idx_tickets_fecha (fecha),
  INDEX idx_tickets_empresa_fecha (id_empresa, fecha)
);

CREATE TABLE IF NOT EXISTS items (
  id_item BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

  id_documento BIGINT UNSIGNED NOT NULL,
  numero_linea SMALLINT UNSIGNED NOT NULL,

  concepto VARCHAR(255) NOT NULL,
  unidades DECIMAL(5,3) NULL,
  importe_total DECIMAL(6,2) NOT NULL,

  descuento_porcentaje DECIMAL(3,2) NULL,
  descuento_importe DECIMAL(6,2) NULL,

  codigo_impuesto VARCHAR(16) NULL,

  PRIMARY KEY (id_item),

  CONSTRAINT fk_items_ticket
    FOREIGN KEY (id_documento) REFERENCES tickets(id_documento)
    ON DELETE CASCADE,

  UNIQUE KEY uq_items_doc_linea (id_documento, numero_linea),
  INDEX idx_items_doc (id_documento),
  INDEX idx_items_codigo (codigo_impuesto)
);

CREATE TABLE IF NOT EXISTS impuestos (
  id_impuesto BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

  id_documento BIGINT UNSIGNED NOT NULL,

  tipo_impuesto VARCHAR(32) NOT NULL,
  base_imponible DECIMAL(6,2) NULL,
  importe DECIMAL(6,2) NOT NULL,

  PRIMARY KEY (id_impuesto),

  CONSTRAINT fk_impuestos_ticket
    FOREIGN KEY (id_documento) REFERENCES tickets(id_documento)
    ON DELETE CASCADE,

  INDEX idx_impuestos_doc (id_documento),
  INDEX idx_impuestos_tipo (tipo_impuesto)
);


/* EVENTOS + OCURRENCIAS */


CREATE TABLE IF NOT EXISTS eventos (
  id_evento BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

  tipo ENUM('INGRESO','GASTO','SALDO_INICIAL','AJUSTE') NOT NULL,
  nombre VARCHAR(120) NOT NULL,

  categoria_nivel_1 VARCHAR(80) NULL,
  etiqueta VARCHAR(80) NULL,

  importe DECIMAL(10,2) NOT NULL,

  fecha_inicio DATE NOT NULL,
  fecha_fin DATE NULL,

  frecuencia ENUM('UNICA','MENSUAL','SEMESTRAL','ANUAL') NOT NULL,

  -- Recurrentes: día del mes (1-31). En UNICA se ignora.
  dia_mes TINYINT UNSIGNED NULL,

  -- Puntuales: fecha exacta. En recurrentes se ignora.
  fecha_unica DATE NULL,

  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  fecha_modificacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (id_evento),

  INDEX idx_eventos_tipo (tipo),
  INDEX idx_eventos_frecuencia (frecuencia),
  INDEX idx_eventos_fechas (fecha_inicio, fecha_fin),
  INDEX idx_eventos_cat (categoria_nivel_1, etiqueta),
  INDEX idx_eventos_fecha_unica (fecha_unica)
);

/* Migraciones idempotentes para instalaciones previas sin fecha_unica */

SET @col_fecha_unica := (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'eventos'
    AND COLUMN_NAME = 'fecha_unica'
);
SET @sql_fecha_unica := IF(
  @col_fecha_unica = 0,
  'ALTER TABLE eventos ADD COLUMN fecha_unica DATE NULL',
  'SELECT 1'
);
PREPARE stmt FROM @sql_fecha_unica;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @idx_fecha_unica := (
  SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
  WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'eventos'
    AND INDEX_NAME = 'idx_eventos_fecha_unica'
);
SET @sql_idx_fecha_unica := IF(
  @idx_fecha_unica = 0,
  'CREATE INDEX idx_eventos_fecha_unica ON eventos (fecha_unica)',
  'SELECT 1'
);
PREPARE stmt FROM @sql_idx_fecha_unica;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;


CREATE TABLE IF NOT EXISTS eventos_ocurrencias (
  id_ocurrencia BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  id_evento BIGINT UNSIGNED NOT NULL,

  fecha DATE NOT NULL,

  -- Primer día del mes (YYYY-MM-01)
  mes DATE NOT NULL,

  importe DECIMAL(10,2) NOT NULL,

  -- Congelado para histórico/BI
  tipo ENUM('INGRESO','GASTO','SALDO_INICIAL','AJUSTE') NOT NULL,
  nombre VARCHAR(120) NOT NULL,
  categoria_nivel_1 VARCHAR(80) NULL,
  etiqueta VARCHAR(80) NULL,

  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (id_ocurrencia),

  CONSTRAINT fk_ocurrencias_evento
    FOREIGN KEY (id_evento) REFERENCES eventos(id_evento)
    ON DELETE CASCADE,

  UNIQUE KEY uq_evento_fecha (id_evento, fecha),
  INDEX idx_ocurrencias_mes (mes),
  INDEX idx_ocurrencias_fecha (fecha),
  INDEX idx_ocurrencias_cat (categoria_nivel_1, etiqueta)
);


/* PREDICCIONES */

CREATE TABLE IF NOT EXISTS predicciones (
  id_prediccion BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

  modelo VARCHAR(64) NOT NULL,
  version_modulo VARCHAR(64) NULL,

  mes DATE NOT NULL,

  categoria_nivel_1 VARCHAR(80) NOT NULL,
  etiqueta VARCHAR(80) NOT NULL,

  gasto_predicho DECIMAL(10,2) NOT NULL,
  ahorro_predicho DECIMAL(10,2) NULL,

  intervalo_inferior DECIMAL(10,2) NULL,
  intervalo_superior DECIMAL(10,2) NULL,

  fecha_creacion DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

  PRIMARY KEY (id_prediccion),

  UNIQUE KEY uq_predicciones (modelo, mes, categoria_nivel_1, etiqueta),
  INDEX idx_predicciones_mes (mes),
  INDEX idx_predicciones_cat (categoria_nivel_1, etiqueta)
);


/* VISTA DE CONSUMO UNIFICADO */
-- Tickets + Eventos


CREATE OR REPLACE VIEW contabilidad AS

SELECT
  'ticket' AS origen,
  t.id_documento AS id_origen,
  t.fecha AS fecha,
  DATE_FORMAT(t.fecha, '%Y-%m-01') AS mes,
  c.categoria_nivel_1 AS categoria_nivel_1,
  c.etiqueta AS etiqueta,
  -ABS(t.total) AS importe,
  e.nombre_normalizado AS nombre
FROM tickets t
INNER JOIN clasificaciones c
  ON c.id_documento = t.id_documento
LEFT JOIN empresas e
  ON e.id_empresa = t.id_empresa

UNION ALL

SELECT
  'evento' AS origen,
  eo.id_ocurrencia AS id_origen,
  eo.fecha AS fecha,
  DATE_FORMAT(eo.fecha, '%Y-%m-01') AS mes,
  eo.categoria_nivel_1 AS categoria_nivel_1,
  eo.etiqueta AS etiqueta,
  eo.importe AS importe,
  eo.nombre AS nombre
FROM eventos_ocurrencias eo;