#!/usr/bin/env python
"""Descarga y consolida observaciones horarias de PM2.5 de SENAMHI en Lima.

El módulo consulta el endpoint que alimenta las gráficas del portal, extrae de
su respuesta los arrays JavaScript de fechas y valores, incorpora los metadatos
configurados en :data:`ESTACIONES` y escribe un único CSV ordenado. El intervalo
predeterminado, inclusivo en ambos extremos, es 2025-01-01 a 2026-06-30.

Las fechas y horas se conservan en la codificación entera del conjunto de
trabajo: ``FECHA=YYYYMMDD`` y ``HORA=HH*10000``. La zona horaria no queda
registrada en el CSV. Tampoco se guarda en el archivo la
procedencia ni la fecha de actualización de las coordenadas, altitudes y
divisiones administrativas definidas manualmente en este módulo.

Efectos laterales: realiza solicitudes HTTP, espera entre consultas, muestra
progreso por la salida estándar y crea o sobrescribe el CSV indicado por el
usuario. Los bloques que agotan sus reintentos se omiten con una advertencia;
si ninguna estación produce datos, el proceso termina con código 1.
"""

import argparse
import math
import re
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import requests


ESTACIONES = {
    "PARIACHI": {
        "codigo": "112280",
        "lon": -76.83829,
        "lat": -12.00231,
        "alt": None,
        "prov": "LIMA",
        "dist": "ATE",
    },
    "CAMPO_DE_MARTE": {
        "codigo": "112194",
        "lon": -77.0432,
        "lat": -12.0705,
        "alt": 117.0,
        "prov": "LIMA",
        "dist": "JESUS_MARIA",
    },
    "CARABAYLLO": {
        "codigo": "113260",
        "lon": -77.0336,
        "lat": -11.9022,
        "alt": 179.0,
        "prov": "LIMA",
        "dist": "CARABAYLLO",
    },
    "SANTA_ANITA": {
        "codigo": "00112271",
        "lon": -76.9714,
        "lat": -12.0430,
        "alt": 253.0,
        "prov": "LIMA",
        "dist": "SANTA_ANITA",
    },
    "SAN_BORJA": {
        "codigo": "00112270",
        "lon": -77.0077,
        "lat": -12.1086,
        "alt": 128.0,
        "prov": "LIMA",
        "dist": "SAN_BORJA",
    },
    "SAN_JUAN_DE_LURIGANCHO": {
        "codigo": "113258",
        "lon": -76.99925,
        "lat": -11.98080,
        "alt": 240.0,
        "prov": "LIMA",
        "dist": "SAN_JUAN_DE_LURIGANCHO",
    },
    "SAN_MARTIN_DE_PORRES": {
        "codigo": "00112272",
        "lon": -77.0845,
        "lat": -12.0089,
        "alt": 56.0,
        "prov": "LIMA",
        "dist": "SAN_MARTIN_DE_PORRES",
    },
    "VILLA_MARIA_DEL_TRIUNFO": {
        "codigo": "112233",
        "lon": -76.9200,
        "lat": -12.1664,
        "alt": 272.0,
        "prov": "LIMA",
        "dist": "VILLA_MARIA_DEL_TRIUNFO",
    },
    "CERES": {
        "codigo": "112275",
        "lon": -76.92706,
        "lat": -12.02869,
        "alt": None,
        "prov": "LIMA",
        "dist": "ATE",
    },
    "PUENTE_PIEDRA": {
        "codigo": "111287",
        "lon": -77.07413,
        "lat": -11.86325,
        "alt": None,
        "prov": "LIMA",
        "dist": "PUENTE_PIEDRA",
    },
}

URL_BASE = "https://www.senamhi.gob.pe/site/sea/www/site/sea/graficas/dato_hora.php"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

DEFAULT_INICIO = datetime(2025, 1, 1)
DEFAULT_FIN = datetime(2026, 6, 30)
DEFAULT_SALIDA = "pm25_lima_2025_2026.csv"
HISTORY_DIR = Path(__file__).resolve().parent / "history"
DIAS_LOTE = 10
KEY_COLUMNS = ["ESTACION", "FECHA", "HORA"]
OUTPUT_COLUMNS = [
    "ESTACION",
    "FECHA",
    "HORA",
    "LONGITUD",
    "LATITUD",
    "ALTITUD",
    "PM2_5",
    "PROVINCIA",
    "DISTRITO",
]
NUMERIC_SENTINELS = {-999.0, -9999.0, 9999.0, 99999.0}


def _safe_measurement(value):
    """Normaliza ausencias, no finitos y sentinelas antes de fusionar capturas."""
    if value is None:
        return None
    text = str(value).strip()
    if text.upper() in {"", "NULL", "NAN", "NA", "N/A", "N/D", "S/D", "-"}:
        return None
    try:
        numeric = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or numeric in NUMERIC_SENTINELS:
        return None
    return numeric


def parse_senamhi_html(html):
    """Extrae las parejas fecha-hora/PM2.5 incrustadas en JavaScript.

    El contrato observado del endpoint no es JSON: la respuesta incluye un
    array ``categories: [...]`` y varios arrays ``data: [...]`` usados por la
    gráfica. Las expresiones regulares delimitan cada array hasta el siguiente
    ``]``; por ello dependen de que los elementos no contengan corchetes y de
    que SENAMHI mantenga esa sintaxis. Se usa el último ``data`` porque, según
    el contrato observado de estas respuestas, corresponde a la serie PM2.5,
    mientras los arrays anteriores pertenecen a otras series de la gráfica.

    Args:
        html: Texto completo devuelto por el endpoint de gráficas.

    Returns:
        Registros con ``FECHA`` como ``YYYYMMDD``, ``HORA`` como
        ``HH*10000`` y ``PM2_5`` como ``float`` o ``None``. Devuelve una
        colección vacía si faltan los arrays, hay menos de dos series o sus
        longitudes no coinciden. Las categorías con formato inesperado se
        omiten individualmente.

    Notes:
        Un valor no convertible se conserva como dato faltante. El texto de
        fecha no contiene información de zona horaria y aquí no se le asigna
        ninguna.
    """
    # Captura el interior del primer array de categorías sin interpretar JS.
    cats_match = re.search(r"categories: \[([^\]]+)\]", html)
    # Captura todos los arrays data para poder seleccionar la serie observada.
    all_data = re.findall(r"data: \[([^\]]*)\]", html)

    if not cats_match or len(all_data) < 2:
        raise ValueError("La respuesta no contiene el contrato de series PM2.5")

    cats_list = [
        c.strip().strip("'") for c in cats_match.group(1).split(",") if c.strip()
    ]
    data_list = [d.strip() for d in all_data[-1].split(",") if d.strip()]

    if len(cats_list) != len(data_list):
        raise ValueError("Categorías y valores PM2.5 tienen distinta longitud")

    rows = []
    for cat, val in zip(cats_list, data_list):
        m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})(\d{2}):(\d{2}):(?:00)?", cat)
        if not m:
            continue
        dd, mm, yyyy, hh, minute = m.groups()
        if minute != "00":
            continue
        try:
            # La regex comprueba la forma; datetime comprueba que la fecha y la
            # hora existan realmente antes de codificarlas como enteros.
            timestamp = datetime(int(yyyy), int(mm), int(dd), int(hh))
        except ValueError:
            continue
        pm25 = _safe_measurement(val)
        rows.append(
            {
                "FECHA": timestamp.year * 10000 + timestamp.month * 100 + timestamp.day,
                "HORA": timestamp.hour * 10000,
                "PM2_5": pm25,
            }
        )
    if not rows and cats_list:
        raise ValueError("La respuesta PM2.5 no contiene timestamps horarios válidos")
    return rows


def fetch_block(codigo, f1, f2, max_retries=3):
    """Solicita y parsea un intervalo inclusivo para una estación.

    Args:
        codigo: Código de estación esperado por el parámetro ``estacion``.
        f1: Primera fecha incluida en la consulta.
        f2: Última fecha incluida en la consulta.
        max_retries: Número total máximo de intentos; debe ser positivo para
            que se realice alguna solicitud.

    Returns:
        Registros producidos por :func:`parse_senamhi_html`, o una colección
        vacía cuando todos los intentos fallan o la respuesta no es parseable.

    Side Effects:
        Ejecuta solicitudes GET con timeout de 25 segundos. Ante cualquier
        excepción de red, HTTP o parseo reintenta con backoff exponencial de
        ``2**attempt`` segundos (1, 2, ...); al agotar intentos imprime una
        advertencia. La captura amplia es deliberada para que un bloque fallido
        no cancele la descarga de las demás fechas y estaciones.
    """
    params = {
        "estacion": codigo,
        "cont": "N_PM25",
        "f1": f1.strftime("%d/%m/%Y"),
        "f2": f2.strftime("%d/%m/%Y"),
    }
    hdrs = {
        **HEADERS,
        "Referer": f"https://www.senamhi.gob.pe/?p=calidad-del-aire-estacion&e={codigo}",
    }
    for attempt in range(max_retries):
        try:
            r = requests.get(URL_BASE, params=params, headers=hdrs, timeout=25)
            r.raise_for_status()
            return parse_senamhi_html(r.text)
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2**attempt)
            else:
                print(f"\n  [WARN] {f1:%d/%m/%Y}–{f2:%d/%m/%Y}: {e}")
    return []


def download_station(nombre, meta, fecha_inicio, fecha_fin):
    """Descarga una estación en bloques inclusivos y construye su DataFrame.

    Cada consulta cubre como máximo :data:`DIAS_LOTE` días contando ambos
    extremos: con un tamaño de 10, el final es ``inicio + 9 días`` y el bloque
    siguiente comienza al día posterior. Esto evita solapamientos y huecos sin
    asumir que el endpoint acepte intervalos extensos.

    Args:
        nombre: Etiqueta que se escribirá en la columna ``ESTACION``.
        meta: Mapeo con ``codigo``, ``lon``, ``lat``, ``alt``, ``prov`` y
            ``dist``. Esos metadatos se replican en cada observación; su
            procedencia y vigencia no se registran en el CSV.
        fecha_inicio: Primera fecha solicitada, incluida.
        fecha_fin: Última fecha solicitada, incluida.

    Returns:
        Un :class:`polars.DataFrame` eager con las columnas de salida en orden,
        o ``None`` si ningún bloque produjo filas.

    Side Effects:
        Muestra el progreso, realiza las solicitudes delegadas a
        :func:`fetch_block` y espera 0.5 segundos después de cada bloque,
        incluido el último, para moderar la frecuencia de acceso.
    """
    total_dias = (fecha_fin - fecha_inicio).days + 1
    total_bloques = (total_dias + DIAS_LOTE - 1) // DIAS_LOTE

    rows = []
    actual = fecha_inicio
    bloque = 0
    while actual <= fecha_fin:
        bloque += 1
        fin = min(actual + timedelta(days=DIAS_LOTE - 1), fecha_fin)
        print(
            f"  bloque {bloque:02d}/{total_bloques:02d}"
            f"  ({actual:%d/%m/%Y} – {fin:%d/%m/%Y})",
            end="\r",
            flush=True,
        )
        block = fetch_block(meta["codigo"], actual, fin)
        rows.extend(block)
        actual = fin + timedelta(days=1)
        time.sleep(0.5)

    print(" " * 65, end="\r")

    if not rows:
        return None

    # pl.DataFrame materializa aquí un DataFrame eager: las operaciones se
    # ejecutan inmediatamente, no se construye un plan LazyFrame.
    df = pl.DataFrame(rows).with_columns(
        [
            # pl.lit crea una expresión constante por fila; alias fija el
            # nombre de su columna resultante. with_columns evalúa todas estas
            # expresiones y agrega/reemplaza columnas sin eliminar las previas.
            pl.lit(nombre).alias("ESTACION"),
            pl.lit(meta["lon"]).alias("LONGITUD"),
            pl.lit(meta["lat"]).alias("LATITUD"),
            pl.lit(meta["alt"], dtype=pl.Float64).alias("ALTITUD"),
            pl.lit(meta["prov"]).alias("PROVINCIA"),
            pl.lit(meta["dist"]).alias("DISTRITO"),
        ]
    )
    # select proyecta y ordena las columnas; al recibir nombres conserva sus
    # valores sin transformarlos.
    return df.select(OUTPUT_COLUMNS)


def cargar_historicos(paths):
    """Carga snapshots brutos compatibles sin recortar su cobertura temporal."""
    historicos = []
    vistos = set()

    for path in paths:
        path = Path(path)
        resolved = path.resolve()
        if resolved in vistos:
            continue
        vistos.add(resolved)
        if not path.exists():
            raise FileNotFoundError(f"Snapshot histórico no encontrado: {path}")

        df = pl.read_csv(path, null_values=[""])
        if df.columns != OUTPUT_COLUMNS:
            raise ValueError(
                f"Esquema incompatible en {path}: se esperaba {OUTPUT_COLUMNS}"
            )
        duplicate_count = df.select(pl.struct(KEY_COLUMNS).is_duplicated().sum()).item()
        if duplicate_count:
            raise ValueError(f"{path} contiene {duplicate_count} claves duplicadas")
        historicos.append(df)

    return historicos


def fusionar_observaciones(df_nuevo, historicos):
    """Fusiona snapshots por clave; una observación nueva no nula prevalece."""
    merged = {}

    def ingest(df):
        for row in df.iter_rows(named=True):
            row = {**row, "PM2_5": _safe_measurement(row["PM2_5"])}
            key = tuple(row[column] for column in KEY_COLUMNS)
            if key in merged:
                current_value = merged[key]["PM2_5"]
                incoming_value = row["PM2_5"]
                if current_value is None and incoming_value is not None:
                    merged[key]["PM2_5"] = incoming_value
                continue
            merged[key] = row

    if df_nuevo is not None:
        ingest(df_nuevo)

    for historical in historicos:
        ingest(historical)

    if not merged:
        return None

    for row in merged.values():
        meta = ESTACIONES.get(row["ESTACION"])
        if meta is None:
            continue
        row.update(
            {
                "LONGITUD": meta["lon"],
                "LATITUD": meta["lat"],
                "ALTITUD": meta["alt"],
                "PROVINCIA": meta["prov"],
                "DISTRITO": meta["dist"],
            }
        )

    return (
        pl.DataFrame(list(merged.values())).select(OUTPUT_COLUMNS).sort(KEY_COLUMNS)
    )


def respaldar_csv_existente(path):
    """Copia el CSV que será reemplazado dentro del historial local fechado."""
    path = Path(path)
    if not path.exists():
        return None

    run_id = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    backup_dir = HISTORY_DIR / run_id
    backup_path = backup_dir / path.name
    suffix = 1
    while backup_path.exists():
        backup_dir = HISTORY_DIR / f"{run_id}_{suffix}"
        backup_path = backup_dir / path.name
        suffix += 1

    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_path)
    return backup_path


def escribir_csv_atomico(df, path):
    """Escribe un CSV mediante reemplazo atómico para conservar el anterior."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    df.write_csv(temporary)
    temporary.replace(path)


def imprimir_resumen(df):
    """Imprime estadísticas y devuelve el DataFrame ordenado.

    Args:
        df: DataFrame no vacío con el esquema producido por la fusión.

    Returns:
        DataFrame eager ordenado por estación, fecha y hora.

    Side Effects:
        Imprime una tabla de cobertura y rangos. Las estadísticas de una
        estación sin valores válidos se muestran con un guion.
    """
    df_all = df.sort(["ESTACION", "FECHA", "HORA"])

    # group_by crea un grupo por estación y agg reduce cada grupo mediante las
    # expresiones indicadas. pl.len cuenta todas las filas, incluidos los nulos;
    # pl.col(...).count cuenta solo valores no nulos de esa columna.
    stats = (
        df_all.group_by("ESTACION")
        .agg(
            [
                pl.len().alias("n"),
                pl.col("PM2_5").count().alias("valid"),
                pl.col("PM2_5").mean().round(2).alias("media"),
                pl.col("PM2_5").min().alias("min"),
                pl.col("PM2_5").max().alias("max"),
            ]
        )
        # pl.col referencia columnas dentro de una expresión; alias nombra el
        # porcentaje calculado. El chaining pasa cada DataFrame al paso siguiente.
        .with_columns((pl.col("valid") / pl.col("n") * 100).round(1).alias("pct"))
        .sort("ESTACION")
    )

    print()
    print("─" * 70)
    print(
        f"  {'ESTACION':<27} {'REGISTROS':>9} {'% VÁLIDO':>9} {'MEDIA':>7} {'MIN':>6} {'MAX':>7}"
    )
    print("─" * 70)
    # iter_rows(named=True) cruza del modelo columnar de Polars a registros
    # nombrados, apropiados para el formateo fila a fila de esta salida humana.
    for row in stats.iter_rows(named=True):
        media = "—" if row["media"] is None else f"{row['media']:.2f}"
        minimo = "—" if row["min"] is None else f"{row['min']:.1f}"
        maximo = "—" if row["max"] is None else f"{row['max']:.1f}"
        print(
            f"  {row['ESTACION']:<27} {row['n']:>9,} {row['pct']:>8.1f}%"
            f" {media:>7} {minimo:>6} {maximo:>7}"
        )
    print("─" * 70)
    # n_unique cuenta valores distintos de la serie ESTACION, no filas.
    print(
        f"  Total: {len(df_all):,} registros | {df_all['ESTACION'].n_unique()} estaciones"
    )
    print(f"  Fechas: {df_all['FECHA'].min()} – {df_all['FECHA'].max()}")
    print()

    return df_all


def parse_fecha(s):
    """Convierte una fecha CLI estricta ``YYYY-MM-DD`` a ``datetime``.

    Args:
        s: Valor textual recibido por :mod:`argparse`.

    Returns:
        Fecha a medianoche como ``datetime`` ingenuo, sin zona horaria.

    Raises:
        argparse.ArgumentTypeError: Si el texto no representa una fecha válida
            con el formato exacto requerido.
    """
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"Fecha inválida: {s!r}  (usa YYYY-MM-DD)")


def build_parser():
    """Construye la interfaz de extracción y fusión."""
    parser = argparse.ArgumentParser(
        description="Descarga datos horarios de PM2.5 desde SENAMHI para Lima.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python scraper_pm2.5.py\n"
            "  python scraper_pm2.5.py --inicio 2025-06-01 --fin 2025-12-31\n"
            "  python scraper_pm2.5.py --salida mi_dataset.csv\n"
            "  python scraper_pm2.5.py --fusionar-desde snapshot_anterior.csv\n"
        ),
    )
    parser.add_argument(
        "--inicio",
        type=parse_fecha,
        default=DEFAULT_INICIO,
        metavar="YYYY-MM-DD",
        help=f"Fecha de inicio (default: {DEFAULT_INICIO:%Y-%m-%d})",
    )
    parser.add_argument(
        "--fin",
        type=parse_fecha,
        default=DEFAULT_FIN,
        metavar="YYYY-MM-DD",
        help=f"Fecha de fin (default: {DEFAULT_FIN:%Y-%m-%d})",
    )
    parser.add_argument(
        "--salida",
        default=DEFAULT_SALIDA,
        metavar="ARCHIVO",
        help=f"Nombre del CSV de salida (default: {DEFAULT_SALIDA})",
    )
    parser.add_argument(
        "--fusionar-existente",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Usa el CSV de salida existente como respaldo (default: activado)",
    )
    parser.add_argument(
        "--fusionar-desde",
        action="append",
        default=[],
        type=Path,
        metavar="CSV",
        help="Snapshot bruto adicional usado como respaldo; se puede repetir",
    )
    return parser


def main():
    """Ejecuta la interfaz CLI y escribe el CSV consolidado.

    Valida que el rango sea ascendente, procesa las estaciones de forma
    secuencial y conserva las estaciones completadas si el usuario interrumpe
    el bucle. Una interrupción dentro de una estación descarta los bloques aún
    no convertidos de esa estación, pues :func:`download_station` no alcanzó a
    devolver su DataFrame.

    Side Effects:
        Lee argumentos de proceso, accede a la red, espera, imprime progreso y
        reemplaza ``--salida`` atómicamente. Llama a :func:`sys.exit` con código
        1 si no se obtuvo ningún dato nuevo ni histórico.
    """
    parser = build_parser()
    args = parser.parse_args()

    if args.inicio > args.fin:
        parser.error("--inicio debe ser anterior a --fin")

    n_est = len(ESTACIONES)
    print()
    print("=" * 55)
    print("  Descarga de PM2.5 — SENAMHI Lima")
    print("=" * 55)
    print(f"  Rango     : {args.inicio:%Y-%m-%d} → {args.fin:%Y-%m-%d}")
    print(f"  Estaciones: {n_est}")
    print(f"  Salida    : {args.salida}")
    print("=" * 55)
    print()

    salida = Path(args.salida)
    historical_paths = list(args.fusionar_desde)
    if args.fusionar_existente and salida.exists():
        historical_paths.insert(0, salida)
    try:
        historicos = cargar_historicos(historical_paths)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))

    all_dfs = []
    try:
        for i, (nombre, meta) in enumerate(ESTACIONES.items(), 1):
            print(f"[{i}/{n_est}] {nombre}")
            df = download_station(nombre, meta, args.inicio, args.fin)
            if df is not None and len(df) > 0:
                all_dfs.append(df)
                valid = df["PM2_5"].is_not_null().sum()
                print(f"  ✓  {len(df):,} registros  |  {valid:,} con PM2.5 válido")
            else:
                print("  —  sin datos disponibles")
    except KeyboardInterrupt:
        print("\n\n[!] Interrumpido por el usuario — guardando datos parciales...")

    if not all_dfs and salida.exists():
        print("No se obtuvieron datos nuevos; se conserva el CSV existente.")
        return

    if not all_dfs and not historicos:
        print("No se encontraron datos para ninguna estación.")
        sys.exit(1)

    df_nuevo = pl.concat(all_dfs).sort(KEY_COLUMNS) if all_dfs else None
    df_final = fusionar_observaciones(df_nuevo, historicos)
    if df_final is None:
        print("No se encontraron datos nuevos ni históricos.")
        sys.exit(1)

    imprimir_resumen(df_final)
    backup_path = respaldar_csv_existente(salida)
    escribir_csv_atomico(df_final, salida)
    if backup_path is not None:
        print(f"  Respaldo: {backup_path}")
    print(f"  Guardado: {salida}")
    print()


if __name__ == "__main__":
    main()
