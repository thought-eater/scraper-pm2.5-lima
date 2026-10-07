# Datos horarios de PM2.5 — SENAMHI Lima

Datos de adquisición y código de extracción de PM2.5 para Lima Metropolitana,
enero de 2025–junio de 2026. Este repositorio aporta la entrada de PM2.5 al
**resultado R1.1 de la tesis: dos datasets curados y georreferenciados de PM2.5
y meteorología**.

## Examinar los datos

- [CSV de adquisición](pm25_lima_2025_2026.csv).
- [Fuente, estaciones, diccionario y procedimiento](docs/adquisicion.md).
- [Datos curados y verificación de R1.1](https://github.com/thought-eater/senamhi-pre-procesamiento).
- [Extractor meteorológico complementario](https://github.com/thought-eater/senamhi-var-metereologicas-lima).

El CSV contiene valores extraídos y metadatos geográficos, sin imputaciones.
Hay diez estaciones configuradas; la captura publicada representa nueve, de las
cuales siete aportan observaciones válidas de PM2.5. Una celda vacía indica
ausencia de información.

## Reproducir la adquisición

Requiere Python ≥ 3.10, [uv](https://docs.astral.sh/uv/) y acceso a internet.
Desde la raíz del repositorio:

```bash
uv sync --frozen
uv run --frozen python scraper_pm2.5.py --salida capturas/pm25.csv \
  --fusionar-desde pm25_lima_2025_2026.csv
```

El ejemplo conserva el archivo publicado y escribe una nueva captura consolidada.
Consultar otra vez SENAMHI puede producir datos diferentes; para examinar los
resultados de la tesis se utiliza el CSV publicado.

## Pruebas de software

```bash
uv run --frozen python -m unittest discover -s tests/software -v
```

Las [pruebas offline](tests/README.md) comprueban el parser, la fusión y la
conservación de archivos. La validación del resultado académico se realiza sobre
los datasets reales en el repositorio de preprocesamiento.
