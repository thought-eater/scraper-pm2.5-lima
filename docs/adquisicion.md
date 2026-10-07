# Adquisición de PM2.5

## Fuente y alcance

Se consulta el [portal gráfico de SENAMHI](https://www.senamhi.gob.pe/site/sea/www/site/sea/graficas/dato_hora.php).
La respuesta contiene HTML y arreglos JavaScript; el extractor interpreta
`categories` como tiempo y el último arreglo `data` como PM2.5 según la estructura
observada del portal. El periodo predeterminado es 2025-01-01 a 2026-06-30,
ambos días incluidos.

| Estación configurada | Código SENAMHI | Distrito |
|---|---|---|
| PARIACHI | 112280 | Ate |
| CAMPO_DE_MARTE | 112194 | Jesús María |
| CARABAYLLO | 113260 | Carabayllo |
| SANTA_ANITA | 00112271 | Santa Anita |
| SAN_BORJA | 00112270 | San Borja |
| SAN_JUAN_DE_LURIGANCHO | 113258 | San Juan de Lurigancho |
| SAN_MARTIN_DE_PORRES | 00112272 | San Martín de Porres |
| VILLA_MARIA_DEL_TRIUNFO | 112233 | Villa María del Triunfo |
| CERES | 112275 | Ate |
| PUENTE_PIEDRA | 111287 | Puente Piedra |

Puente Piedra no está representada en el CSV publicado. Carabayllo y Ceres
están representadas, pero no aportan observaciones válidas en esta captura.
Las coordenadas y altitudes empleadas están en `ESTACIONES`, dentro del
[extractor](../scraper_pm2.5.py), y se incorporan a cada fila.

## Diccionario del CSV

Separador coma, codificación UTF-8, cabecera y campos vacíos para ausencias.
La clave es `(ESTACION, FECHA, HORA)`.

| Campo | Tipo / unidad | Significado |
|---|---|---|
| `ESTACION` | Texto | Nombre normalizado de estación |
| `FECHA` | Entero `YYYYMMDD` | Fecha reportada |
| `HORA` | Entero `HH*10000` | Hora; `140000` representa 14:00 |
| `LONGITUD`, `LATITUD` | Grados decimales | Coordenadas configuradas |
| `ALTITUD` | Metros o vacío | Altitud configurada |
| `PM2_5` | µg/m³ o vacío | Concentración de PM2.5 |
| `PROVINCIA`, `DISTRITO` | Texto | División administrativa configurada |

El CSV no incorpora zona horaria; el preprocesamiento interpreta las horas como
locales de Lima. Las unidades corresponden a la interpretación de la fuente;
no se dispone de metadatos completos de instrumentos, calibración o referencia
vertical. Las coordenadas no registran procedencia ni fecha de actualización.

## Procedimiento y opciones

El extractor consulta bloques de hasta diez días, con una pausa de 0,5 s y hasta
tres intentos por bloque. Normaliza valores vacíos, no finitos y sentinelas;
descarta fechas inválidas y consolida las observaciones por su clave horaria.

| Opción | Valor predeterminado / función |
|---|---|
| `--inicio`, `--fin` | `2025-01-01`, `2026-06-30`; formato `YYYY-MM-DD` |
| `--salida` | `pm25_lima_2025_2026.csv` |
| `--no-fusionar-existente` | Desactiva el respaldo desde el CSV de salida |
| `--fusionar-desde CSV` | Añade una captura bruta de respaldo; repetible |

Por defecto se fusiona con el CSV de salida existente. Un valor nuevo no nulo
prevalece; el histórico completa ausencias y conserva filas fuera del intervalo
solicitado. Solo se admiten capturas brutas como respaldo, nunca imputaciones.
Antes de reemplazar una salida existente se crea una copia en
`history/<fecha_hora>/`. El directorio se genera automáticamente cuando se necesita.

## Interpretación

El archivo bruto ya incluye parseo, normalización y consolidación; no es una
copia de todas las respuestas remotas. Un bloque sin filas puede deberse a
indisponibilidad o a un cambio del portal. Una nueva adquisición puede diferir
del archivo publicado. La limpieza, imputación y evidencia final se documentan
en [preprocesamiento](https://github.com/thought-eater/senamhi-pre-procesamiento).
