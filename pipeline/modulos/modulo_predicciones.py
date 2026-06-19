from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from pmdarima import auto_arima
from statsmodels.tsa.api import ExponentialSmoothing

from utilidades.configuracion import cargar_config, cargar_env_desde_raiz, ConfigProyecto
from bd.conexion import crear_conexion_mysql



## Configuración

@dataclass(frozen=True)
class ConfiguracionPredicciones:
    # Identificadores
    modelo: str = "comparador_autoarima_holtwinters"
    version_modulo: str = "comparador_v1"

    # Split para métricas + horizonte
    horizonte_meses: int = 6
    test_meses: int = 6

    # Estacionalidad
    minimo_meses_total: int = 24
    usar_estacionalidad_si_procede: bool = True
    periodos_estacionales: int = 12

    # Umbral para entrenar series (evita series sin señal)
    minimo_meses_sin_cero: int = 12

    # Ultimos meses con varianza casi 0
    ventana_determinista: int = 12
    tolerancia_varianza: float = 1e-6
    tolerancia_valor_minimo: float = 1e-9

    # Guardar métricas
    guardar_metricas_excel: bool = True


CONFIG = ConfiguracionPredicciones()


## Métricas

def error_absoluto_medio(verdaderos: np.ndarray, predicciones: np.ndarray) -> float:
    return float(np.mean(np.abs(verdaderos - predicciones)))

def raiz_error_cuadratico_medio(verdaderos: np.ndarray, predicciones: np.ndarray) -> float:
    return float(np.sqrt(np.mean((verdaderos - predicciones) ** 2)))

def error_porcentual_absoluto_medio_seguro(verdaderos: np.ndarray, predicciones: np.ndarray) -> float:
    """Calculamos MAPE solo sobre verdaderos != 0"""
    mascara = verdaderos != 0
    if not np.any(mascara):
        return float("nan")
    return float(np.mean(np.abs((verdaderos[mascara] - predicciones[mascara]) / verdaderos[mascara])) * 100.0)

def formatear_mae(valor: float | None) -> str:
    if valor is None or pd.isna(valor):
        return "N/A"
    return f"{valor:.2f}"


## Helpers BD
def obtener_conexion(configuracion: ConfigProyecto | None = None):
    if configuracion is None:
        cargar_env_desde_raiz()
        configuracion = cargar_config()
    return crear_conexion_mysql(configuracion)

def leer_sql(conexion, consulta: str) -> pd.DataFrame:
    return pd.read_sql(consulta, conexion)

def asegurar_tabla_predicciones(conexion):
    """Crea la tabla si no existe"""
    sql = """
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
    """
    cursor = conexion.cursor()
    cursor.execute(sql)
    conexion.commit()
    cursor.close()

def insertar_predicciones(conexion, predicciones: pd.DataFrame):
    """ Inserta predicciones en la BD. Si ya existe una predicción para el mismo modelo, mes, categoría y etiqueta, se actualiza"""
    if predicciones.empty:
        return
    sql = """
    INSERT INTO predicciones (
        modelo,
        version_modulo,
        mes,
        categoria_nivel_1,
        etiqueta,
        gasto_predicho,
        ahorro_predicho,
        intervalo_inferior,
        intervalo_superior,
        fecha_creacion
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        gasto_predicho = VALUES(gasto_predicho),
        ahorro_predicho = VALUES(ahorro_predicho),
        intervalo_inferior = VALUES(intervalo_inferior),
        intervalo_superior = VALUES(intervalo_superior),
        version_modulo = VALUES(version_modulo),
        fecha_creacion = VALUES(fecha_creacion)
    """
    filas = []
    for _, registro in predicciones.iterrows():
        mes = registro["mes"]
        if isinstance(mes, pd.Timestamp):
            mes = mes.date()

        filas.append((
            registro["modelo"],
            registro["version_modulo"],
            mes,
            registro["categoria_nivel_1"],
            registro["etiqueta"],
            float(registro["gasto_predicho"]),
            None if pd.isna(registro.get("ahorro_predicho")) else float(registro.get("ahorro_predicho")),
            None if pd.isna(registro.get("intervalo_inferior")) else float(registro.get("intervalo_inferior")),
            None if pd.isna(registro.get("intervalo_superior")) else float(registro.get("intervalo_superior")),
            registro["fecha_creacion"].to_pydatetime() if isinstance(registro["fecha_creacion"], pd.Timestamp) else registro["fecha_creacion"],
        ))

    cursor = conexion.cursor()
    cursor.executemany(sql, filas)
    conexion.commit()
    cursor.close()



# Serie mensual

def construir_serie_mensual(dataframe: pd.DataFrame, columna_mes: str = "mes", columna_valor: str = "gasto") -> pd.Series:
    datos = dataframe.copy()
    datos[columna_mes] = pd.to_datetime(datos[columna_mes])
    serie = datos.groupby(columna_mes)[columna_valor].sum().sort_index()

    indice = pd.date_range(start=serie.index.min(), end=serie.index.max(), freq="MS")
    serie = serie.reindex(indice).fillna(0.0)
    serie.name = "valor"
    return serie

def serie_entrenable(serie: pd.Series, minimo_sin_cero: int) -> bool:
    return int((serie > 0).sum()) >= minimo_sin_cero

def serie_determinista(
    serie: pd.Series,
    ventana: int,
    tolerancia_var: float,
    tolerancia_valor_min: float,
) -> bool:
    if serie.empty:
        return False
    ventana_ajustada = min(ventana, len(serie))
    ultimos = serie.iloc[-ventana_ajustada:].astype(float)
    if float(ultimos.max()) <= tolerancia_valor_min:
        return False
    return float(ultimos.var()) < tolerancia_var



# Comparador de modelos

def entrenar_autoarima(serie_entrenamiento: np.ndarray, usar_estacional: bool, periodos: int):
    """Entrena modelo AutoARIMA"""
    return auto_arima(
        serie_entrenamiento,
        seasonal=usar_estacional,
        m=periodos,
        stepwise=True,
        suppress_warnings=True,
        error_action="ignore",
        trace=False,
        maxiter=50,
        max_p=3, max_q=3,
        max_P=1, max_Q=1,
    )

def entrenar_holt_winters(serie_entrenamiento: np.ndarray, usar_estacional: bool, periodos: int):
    """Entrena modelo Holt-Winters
    - Si no hay ceros ni negativos: multiplicativo
    - Si hay ceros o negativos: aditivo
    """
    if usar_estacional:
        # Verificar si hay valores cero o negativos
        hay_ceros_o_negativos = np.any(serie_entrenamiento <= 0)
        
        if not hay_ceros_o_negativos:
            # Intentar multiplicativo
            try:
                return ExponentialSmoothing(
                    serie_entrenamiento,
                    seasonal_periods=periodos,
                    trend='multiplicative',
                    seasonal='multiplicative',
                    initialization_method='estimated'
                ).fit()
            except:
                # Si falla multiplicativo, usar aditivo
                pass
        
        # Aditivo
        try:
            return ExponentialSmoothing(
                serie_entrenamiento,
                seasonal_periods=periodos,
                trend='additive',
                seasonal='additive',
                initialization_method='estimated'
            ).fit()
        except:
            # Si falla aditivo con estacionalidad, probar sin estacionalidad
            from statsmodels.tsa.api import Holt
            return Holt(serie_entrenamiento, initialization_method='estimated').fit()
    else:
        # Holt sin estacionalidad
        from statsmodels.tsa.api import Holt
        return Holt(serie_entrenamiento, initialization_method='estimated').fit()

def comparar_y_predecir(serie_completa: pd.Series) -> dict:
    """Compara AutoARIMA vs Holt-Winters. Se queda con el mejor según MAE en test"""
    valores = serie_completa.values.astype(float)

    if len(valores) < (CONFIG.test_meses + 6):
        raise ValueError(f"Serie demasiado corta: {len(valores)} meses.")

    # Split train/test
    entrenamiento = valores[:-CONFIG.test_meses]
    prueba = valores[-CONFIG.test_meses:]

    # Caso determinista
    if serie_determinista(
        serie_completa,
        ventana=CONFIG.ventana_determinista,
        tolerancia_var=CONFIG.tolerancia_varianza,
        tolerancia_valor_min=CONFIG.tolerancia_valor_minimo,
    ):
        predicciones_prueba = np.full(CONFIG.test_meses, float(serie_completa.iloc[-1]), dtype=float)
        metricas = {
            "modelo_seleccionado": "DETERMINISTA",
            "mae": error_absoluto_medio(prueba, predicciones_prueba),
            "rmse": raiz_error_cuadratico_medio(prueba, predicciones_prueba),
            "mape": error_porcentual_absoluto_medio_seguro(prueba, predicciones_prueba),
            "estacional": False,
            "periodos_estacionales": 1,
            "orden_modelo": "ultimo_valor",
            "mae_autoarima": None,
            "mae_holtwinters": None,
        }
        predicciones_futuras = np.full(CONFIG.horizonte_meses, float(serie_completa.iloc[-1]), dtype=float)
        return {"metricas": metricas, "predicciones_futuras": predicciones_futuras}

    # Configuración estacional
    usar_estacional = False
    periodos = 1
    if CONFIG.usar_estacionalidad_si_procede and len(valores) >= CONFIG.minimo_meses_total:
        usar_estacional = True
        periodos = CONFIG.periodos_estacionales

    # AutoARIMA
    modelo_autoarima_full = None
    predicciones_autoarima = None
    predicciones_futuras_autoarima = None
    mae_autoarima = float("inf")
    try:
        modelo_autoarima = entrenar_autoarima(entrenamiento, usar_estacional, periodos)
        predicciones_autoarima = modelo_autoarima.predict(n_periods=CONFIG.test_meses)
        mae_autoarima = error_absoluto_medio(prueba, predicciones_autoarima)
    except Exception as e:
        print(f"    AutoARIMA falló: {str(e)[:50]}")

    if predicciones_autoarima is not None:
        try:
            modelo_autoarima_full = entrenar_autoarima(valores, usar_estacional, periodos)
            predicciones_futuras_autoarima = modelo_autoarima_full.predict(n_periods=CONFIG.horizonte_meses)
        except Exception as e:
            print(f"    AutoARIMA (full) falló: {str(e)[:50]}")

    # Holt-Winters
    modelo_holtwinters_full = None
    predicciones_holtwinters = None
    predicciones_futuras_holtwinters = None
    mae_holtwinters = float("inf")
    try:
        modelo_holtwinters = entrenar_holt_winters(entrenamiento, usar_estacional, periodos)
        predicciones_holtwinters = modelo_holtwinters.forecast(CONFIG.test_meses)
        mae_holtwinters = error_absoluto_medio(prueba, predicciones_holtwinters)
    except Exception as e:
        print(f"    Holt-Winters falló: {str(e)[:50]}")

    if predicciones_holtwinters is not None:
        try:
            modelo_holtwinters_full = entrenar_holt_winters(valores, usar_estacional, periodos)
            predicciones_futuras_holtwinters = modelo_holtwinters_full.forecast(CONFIG.horizonte_meses)
        except Exception as e:
            print(f"    Holt-Winters (full) falló: {str(e)[:50]}")

    # MEJOR MODELO
    candidatos = []
    if predicciones_autoarima is not None and predicciones_futuras_autoarima is not None:
        orden = getattr(modelo_autoarima_full, "order", None) if modelo_autoarima_full else None
        orden_estacional = getattr(modelo_autoarima_full, "seasonal_order", None) if modelo_autoarima_full else None
        candidatos.append({
            "modelo": "AUTOARIMA",
            "mae": mae_autoarima,
            "pred_prueba": predicciones_autoarima,
            "pred_futuras": predicciones_futuras_autoarima,
            "parametros": f"order={orden}, seasonal_order={orden_estacional}",
        })

    if predicciones_holtwinters is not None and predicciones_futuras_holtwinters is not None:
        if usar_estacional and modelo_holtwinters_full is not None:
            tendencia = getattr(modelo_holtwinters_full, "trend", None)
            estacional = getattr(modelo_holtwinters_full, "seasonal", None)
            if estacional is None:
                estacional = getattr(modelo_holtwinters_full, "season", None)
            parametros_holt = f"periodos={periodos}, tendencia={tendencia}, estacional={estacional}"
        else:
            parametros_holt = "tendencia_lineal"
        candidatos.append({
            "modelo": "HOLT_WINTERS",
            "mae": mae_holtwinters,
            "pred_prueba": predicciones_holtwinters,
            "pred_futuras": predicciones_futuras_holtwinters,
            "parametros": parametros_holt,
        })

    if not candidatos:
        raise RuntimeError("No se pudo entrenar ninguno de los modelos para generar predicciones")

    mejor = min(candidatos, key=lambda item: item["mae"])
    modelo_seleccionado = mejor["modelo"]
    predicciones_prueba = mejor["pred_prueba"]
    predicciones_futuras = mejor["pred_futuras"]
    parametros = mejor["parametros"]

    metricas = {
        "modelo_seleccionado": modelo_seleccionado,
        "mae": error_absoluto_medio(prueba, predicciones_prueba),
        "rmse": raiz_error_cuadratico_medio(prueba, predicciones_prueba),
        "mape": error_porcentual_absoluto_medio_seguro(prueba, predicciones_prueba),
        "estacional": usar_estacional,
        "periodos_estacionales": periodos,
        "orden_modelo": parametros,
        "mae_autoarima": mae_autoarima if mae_autoarima != float('inf') else None,
        "mae_holtwinters": mae_holtwinters if mae_holtwinters != float('inf') else None,
    }

    return {"metricas": metricas, "predicciones_futuras": predicciones_futuras}



# Guardar métricas Excel

def guardar_metricas_excel(dataframe_metricas: pd.DataFrame, fecha_creacion: pd.Timestamp) -> Path:
    cargar_env_desde_raiz()
    configuracion = cargar_config()
    directorio_salida = configuracion.rutas.outputs / "predicciones"
    directorio_salida.mkdir(parents=True, exist_ok=True)

    timestamp = fecha_creacion.strftime("%Y%m%d_%H%M%S")
    ruta = directorio_salida / f"metricas_comparador_{timestamp}.xlsx"
    dataframe_metricas.to_excel(ruta, index=False)
    return ruta



# Helpers ersistencia

def construir_indice_futuro(ultimo_mes_observado: pd.Timestamp, mes_actual: pd.Timestamp) -> pd.DatetimeIndex:
    """Crea índice futuro a partir del mes siguiente al último observado,
    pero nunca antes del mes actual"""
    inicio = max(ultimo_mes_observado + pd.offsets.MonthBegin(1), mes_actual)
    return pd.date_range(start=inicio, periods=CONFIG.horizonte_meses, freq="MS")

def agregar_predicciones_futuras(
    filas_prediccion: list[dict],
    indice_futuro: pd.DatetimeIndex,
    valores_futuros: np.ndarray,
    categoria: str,
    etiqueta: str,
    modelo_ganador: str,
    fecha_creacion: pd.Timestamp,
):
    for mes, valor in zip(indice_futuro, valores_futuros):
        filas_prediccion.append({
            "modelo": modelo_ganador,
            "version_modulo": CONFIG.version_modulo,
            "mes": mes,
            "categoria_nivel_1": categoria,
            "etiqueta": etiqueta,
            "gasto_predicho": float(valor),
            "ahorro_predicho": np.nan,
            "intervalo_inferior": np.nan,
            "intervalo_superior": np.nan,
            "fecha_creacion": fecha_creacion,
        })



# Pipeline

def ejecutar_predicciones_temporales(generar_salidas: bool = True):
    conexion = obtener_conexion()
    try:
        asegurar_tabla_predicciones(conexion)

        fecha_creacion = pd.Timestamp(datetime.now())

        # Persistir solo desde el mes actual (YYYY-MM-01)
        hoy = pd.Timestamp("today").normalize()
        mes_actual = hoy.to_period("M").to_timestamp(how="start")

        # Extracción única
        consulta_agregada = """
        SELECT
            mes,
            categoria_nivel_1,
            etiqueta,
            -SUM(importe) AS gasto
        FROM contabilidad
        WHERE importe < 0
          AND mes IS NOT NULL
          AND categoria_nivel_1 IS NOT NULL AND categoria_nivel_1 <> ''
          AND etiqueta IS NOT NULL AND etiqueta <> ''
        GROUP BY mes, categoria_nivel_1, etiqueta
        ORDER BY mes;
        """
        dataframe = leer_sql(conexion, consulta_agregada)
        if dataframe.empty:
            raise RuntimeError("No hay datos agregables de gasto (importe<0) en la vista contabilidad")

        dataframe["mes"] = pd.to_datetime(dataframe["mes"])

        filas_prediccion: list[dict] = []
        filas_metricas: list[dict] = []

        print("\n" + "="*90)
        print(" PREDICCIONES CON COMPARADOR AUTOARIMA VS HOLT-WINTERS ".center(90, "="))
        print("="*90)

      
        # A) TOTAL
    
        print("\nPROCESANDO TOTAL...")
        dataframe_total = dataframe.groupby("mes", as_index=False)["gasto"].sum()
        serie_total = construir_serie_mensual(dataframe_total, columna_mes="mes", columna_valor="gasto")

        if serie_entrenable(serie_total, CONFIG.minimo_meses_sin_cero):
            resultado_total = comparar_y_predecir(serie_total)
            
            filas_metricas.append({
                "nivel": "total",
                "categoria_nivel_1": "total",
                "etiqueta": "total",
                **resultado_total["metricas"],
            })
            
            indice_futuro = construir_indice_futuro(serie_total.index.max(), mes_actual)
            agregar_predicciones_futuras(
                filas_prediccion, 
                indice_futuro, 
                resultado_total["predicciones_futuras"], 
                "total", 
                "total", 
                resultado_total["metricas"]["modelo_seleccionado"],
                fecha_creacion
            )
            
            print(f"Total procesado | Mejor modelo: {resultado_total['metricas']['modelo_seleccionado']} | MAE: {resultado_total['metricas']['mae']:.2f}")
            print(f"MAE AutoARIMA: {formatear_mae(resultado_total['metricas']['mae_autoarima'])} | MAE Holt-Winters: {formatear_mae(resultado_total['metricas']['mae_holtwinters'])}")
        else:
            filas_metricas.append({
                "nivel": "total",
                "categoria_nivel_1": "total",
                "etiqueta": "total",
                "modelo_seleccionado": "NO_ENTRENABLE",
                "mae": np.nan,
                "rmse": np.nan,
                "mape": np.nan,
                "estacional": False,
                "periodos_estacionales": 1,
                "orden_modelo": None,
                "mae_autoarima": None,
                "mae_holtwinters": None,
            })
            print("Total no entrenable (insuficientes meses con gasto > 0)")

 
        # B) CATEGORÍAS
   
        print("\nPROCESANDO CATEGORÍAS...")
        dataframe_categorias = dataframe.groupby(["mes", "categoria_nivel_1"], as_index=False)["gasto"].sum()

        categorias_entrenadas = 0
        categorias_saltadas = 0

        for categoria, subconjunto in dataframe_categorias.groupby("categoria_nivel_1", sort=True):
            serie_categoria = construir_serie_mensual(
                subconjunto[["mes", "gasto"]], 
                columna_mes="mes", 
                columna_valor="gasto"
            )

            if not serie_entrenable(serie_categoria, CONFIG.minimo_meses_sin_cero):
                categorias_saltadas += 1
                filas_metricas.append({
                    "nivel": "categoria",
                    "categoria_nivel_1": categoria,
                    "etiqueta": "total",
                    "modelo_seleccionado": "NO_ENTRENABLE",
                    "mae": np.nan,
                    "rmse": np.nan,
                    "mape": np.nan,
                    "estacional": False,
                    "periodos_estacionales": 1,
                    "orden_modelo": None,
                    "mae_autoarima": None,
                    "mae_holtwinters": None,
                })
                continue

            try:
                resultado_categoria = comparar_y_predecir(serie_categoria)
                categorias_entrenadas += 1
                
                filas_metricas.append({
                    "nivel": "categoria",
                    "categoria_nivel_1": categoria,
                    "etiqueta": "total",
                    **resultado_categoria["metricas"],
                })

                indice_futuro = construir_indice_futuro(serie_categoria.index.max(), mes_actual)
                agregar_predicciones_futuras(
                    filas_prediccion, 
                    indice_futuro, 
                    resultado_categoria["predicciones_futuras"], 
                    categoria, 
                    "total", 
                    resultado_categoria["metricas"]["modelo_seleccionado"],
                    fecha_creacion
                )
                
                print(f"{categoria:<35} | Mejor: {resultado_categoria['metricas']['modelo_seleccionado']:<14} | MAE: {resultado_categoria['metricas']['mae']:8.2f}")
                
            except Exception as error:
                categorias_saltadas += 1
                filas_metricas.append({
                    "nivel": "categoria",
                    "categoria_nivel_1": categoria,
                    "etiqueta": "total",
                    "modelo_seleccionado": "ERROR",
                    "mae": np.nan,
                    "rmse": np.nan,
                    "mape": np.nan,
                    "estacional": False,
                    "periodos_estacionales": 1,
                    "orden_modelo": str(error)[:50],
                    "mae_autoarima": None,
                    "mae_holtwinters": None,
                })
                print(f"{categoria:<35} | Error: {str(error)[:50]}")

  
        # C) ETIQUETAS
 
        print("\nPROCESANDO ETIQUETAS...")
        etiquetas_entrenadas = 0
        etiquetas_saltadas = 0

        for (categoria, etiqueta), subconjunto in dataframe.groupby(["categoria_nivel_1", "etiqueta"], sort=True):
            
            # Limitar output
            mostrar_en_consola = etiquetas_entrenadas + etiquetas_saltadas < 15

            serie_etiqueta = construir_serie_mensual(
                subconjunto[["mes", "gasto"]], 
                columna_mes="mes", 
                columna_valor="gasto"
            )

            if not serie_entrenable(serie_etiqueta, CONFIG.minimo_meses_sin_cero):
                etiquetas_saltadas += 1
                continue

            try:
                resultado_etiqueta = comparar_y_predecir(serie_etiqueta)
                etiquetas_entrenadas += 1
                
                filas_metricas.append({
                    "nivel": "etiqueta",
                    "categoria_nivel_1": categoria,
                    "etiqueta": etiqueta,
                    **resultado_etiqueta["metricas"],
                })

                indice_futuro = construir_indice_futuro(serie_etiqueta.index.max(), mes_actual)
                agregar_predicciones_futuras(
                    filas_prediccion, 
                    indice_futuro, 
                    resultado_etiqueta["predicciones_futuras"], 
                    categoria, 
                    etiqueta, 
                    resultado_etiqueta["metricas"]["modelo_seleccionado"],
                    fecha_creacion
                )
                
                if mostrar_en_consola:
                    print(f"{categoria}/{etiqueta:<30} | Mejor: {resultado_etiqueta['metricas']['modelo_seleccionado']}")
                    
            except Exception as error:
                etiquetas_saltadas += 1
                if mostrar_en_consola:
                    print(f"{categoria}/{etiqueta:<30} | Error: {str(error)[:50]}")
                continue


        # Persistencia en BD
 
        if filas_prediccion:
            dataframe_predicciones = pd.DataFrame(filas_prediccion)
            dataframe_predicciones["mes"] = pd.to_datetime(dataframe_predicciones["mes"])
            dataframe_predicciones = dataframe_predicciones[dataframe_predicciones["mes"] >= mes_actual].copy()

            if not dataframe_predicciones.empty:
                insertar_predicciones(conexion, dataframe_predicciones)
                print(f"\n{len(dataframe_predicciones)} predicciones futuras guardadas en base de datos")


        # Salidas y resumen

        if filas_metricas:
            dataframe_metricas = pd.DataFrame(filas_metricas).sort_values(["nivel", "categoria_nivel_1", "etiqueta"])
        else:
            dataframe_metricas = pd.DataFrame(columns=["nivel", "categoria_nivel_1", "etiqueta"])
        
        print("\n" + "="*90)
        print(" RESUMEN DEL COMPARADOR ".center(90, "="))
        print("="*90)
        print(f"\nCategorías entrenadas: {categorias_entrenadas:3d} | saltadas: {categorias_saltadas:3d}")
        print(f"Etiquetas entrenadas:  {etiquetas_entrenadas:3d} | saltadas: {etiquetas_saltadas:3d}")
        print(f"Filas de predicción FUTURAS generadas: {len(filas_prediccion):4d}")

        print("\nMODELO GANADOR POR NIVEL:")
        if not dataframe_metricas.empty:
            for nivel in ['total', 'categoria', 'etiqueta']:
                datos_nivel = dataframe_metricas[dataframe_metricas['nivel'] == nivel]
                if not datos_nivel.empty:
                    conteo = datos_nivel['modelo_seleccionado'].value_counts()
                    if 'DETERMINISTA' in conteo:
                        print(f"\n  {nivel.upper()}:")
                        for modelo, cantidad in conteo.items():
                            porcentaje = (cantidad / len(datos_nivel)) * 100
                            print(f"    • {modelo:<18}: {cantidad:3d} series ({porcentaje:.1f}%)")

        if generar_salidas and CONFIG.guardar_metricas_excel and not dataframe_metricas.empty:
            ruta = guardar_metricas_excel(dataframe_metricas, fecha_creacion)
            print(f"\n📁 Métricas guardadas en: {ruta}")

        print("\n" + "="*90)
        print(" PROCESO COMPLETADO ".center(90, "="))
        print("="*90)
        
    finally:
        conexion.close()


if __name__ == "__main__":
    ejecutar_predicciones_temporales()