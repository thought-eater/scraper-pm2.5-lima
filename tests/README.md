# Pruebas de software

Desde la raíz del repositorio, con las dependencias instaladas:

```bash
uv run --frozen python -m unittest discover -s tests/software -v
```

`software/test_adquisicion.py` comprueba el parser con HTML sintético, la
normalización de ausencias, la fusión de capturas y los respaldos en directorios
temporales. Incluye pruebas unitarias y de integración con archivos; no consulta
SENAMHI ni modifica el CSV publicado.

La evidencia sobre los datasets reales se encuentra en la
[validación de R1.1](https://github.com/thought-eater/senamhi-pre-procesamiento/tree/HEAD/validacion/r1_1).
