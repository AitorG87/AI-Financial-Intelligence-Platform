USE contabilidad_familiar;

DROP PROCEDURE IF EXISTS sp_imputar_eventos_en_fecha;
DROP EVENT IF EXISTS ev_imputar_eventos_diario;

DELIMITER $$

CREATE PROCEDURE sp_imputar_eventos_en_fecha(IN fecha_objetivo DATE)
BEGIN
  DECLARE mes_objetivo DATE;
  DECLARE dia_objetivo INT;

  SET mes_objetivo = DATE_FORMAT(fecha_objetivo, '%Y-%m-01');
  SET dia_objetivo = DAY(fecha_objetivo);

  /*
    1) Eventos fecha_unica: ocurren exactamente en fecha_unica
    - Se insertan con UNIQUE(id_evento, fecha) para idempotencia
  */
  INSERT INTO eventos_ocurrencias (
    id_evento, fecha, mes, importe, tipo, nombre, categoria_nivel_1, etiqueta
  )
  SELECT
    e.id_evento,
    e.fecha_unica AS fecha,
    DATE_FORMAT(e.fecha_unica, '%Y-%m-01') AS mes,
    e.importe,
    e.tipo,
    e.nombre,
    e.categoria_nivel_1,
    e.etiqueta
  FROM eventos e
  WHERE e.frecuencia = 'UNICA'
    AND e.fecha_unica = fecha_objetivo
    AND e.fecha_inicio <= fecha_objetivo
    AND (e.fecha_fin IS NULL OR e.fecha_fin >= fecha_objetivo)
  ON DUPLICATE KEY UPDATE
    importe = VALUES(importe),
    tipo = VALUES(tipo),
    nombre = VALUES(nombre),
    categoria_nivel_1 = VALUES(categoria_nivel_1),
    etiqueta = VALUES(etiqueta);

  /*
    2) Eventos recurrentes: ocurren en el "día efectivo" del mes
       día_efectivo = LEAST(dia_mes, último día del mes)
       y además deben cumplir la periodicidad (MENSUAL/SEMESTRAL/ANUAL) anclada en fecha_inicio
  */
  INSERT INTO eventos_ocurrencias (
    id_evento, fecha, mes, importe, tipo, nombre, categoria_nivel_1, etiqueta
  )
  SELECT
    e.id_evento,
    fecha_objetivo AS fecha,
    mes_objetivo AS mes,
    e.importe,
    e.tipo,
    e.nombre,
    e.categoria_nivel_1,
    e.etiqueta
  FROM eventos e
  WHERE e.frecuencia IN ('MENSUAL','SEMESTRAL','ANUAL')
    AND e.fecha_inicio <= fecha_objetivo
    AND (e.fecha_fin IS NULL OR e.fecha_fin >= fecha_objetivo)
    AND e.dia_mes IS NOT NULL
    AND dia_objetivo = LEAST(e.dia_mes, DAY(LAST_DAY(fecha_objetivo)))
    AND (
      e.frecuencia = 'MENSUAL'
      OR (e.frecuencia = 'SEMESTRAL'
          AND MOD(
            PERIOD_DIFF(DATE_FORMAT(mes_objetivo,'%Y%m'), DATE_FORMAT(DATE_FORMAT(e.fecha_inicio,'%Y-%m-01'),'%Y%m')),
            6
          ) = 0
      )
      OR (e.frecuencia = 'ANUAL'
          AND MOD(
            PERIOD_DIFF(DATE_FORMAT(mes_objetivo,'%Y%m'), DATE_FORMAT(DATE_FORMAT(e.fecha_inicio,'%Y-%m-01'),'%Y%m')),
            12
          ) = 0
      )
    )
  ON DUPLICATE KEY UPDATE
    importe = VALUES(importe),
    tipo = VALUES(tipo),
    nombre = VALUES(nombre),
    categoria_nivel_1 = VALUES(categoria_nivel_1),
    etiqueta = VALUES(etiqueta);

END$$


CREATE EVENT ev_imputar_eventos_diario
ON SCHEDULE EVERY 1 DAY
STARTS (TIMESTAMP(CURDATE(), '02:00:00'))
DO
BEGIN
  -- Genera ocurrencias del día de hoy
  CALL sp_imputar_eventos_en_fecha(CURDATE());

  -- Genera las de ayer por si MySQL no estaba activo
  CALL sp_imputar_eventos_en_fecha(DATE_SUB(CURDATE(), INTERVAL 1 DAY));
END$$

DELIMITER ;