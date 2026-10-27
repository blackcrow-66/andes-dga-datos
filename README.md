# Datos DGA para Andes Weather

Copia automática de estaciones de la Dirección General de Aguas (DGASAT,
https://snia.mop.gob.cl/dgasat/) en la precordillera de Santiago, para la alerta de quebradas de
Andes Weather.

| Serie | Estación DGA | Qué mide |
|---|---|---|
| `antupiren_lluvia` | Antupirén 05730013-2 | Lluvia acumulada (mm) |
| `quebrada_macul_lluvia` | Quebrada de Macul 05730039-6 | Lluvia acumulada (mm) |
| `quebrada_ramon_nivel` | Q. de Ramón en recinto EMOS 05730008-6 | Nivel de agua (m) |
| `quebrada_ramon_caudal` | Q. de Ramón en recinto EMOS 05730008-6 | Caudal (m³/s) |

## Archivos

- `data/latest.json`: últimas 48 h. Para la lluvia: mm por hora (la hora marca el **fin** de la hora,
  igual que Open-Meteo), lluvia de la última hora y de las últimas 24 h.
- `data/raw/<serie>/<AAAA-MM>.csv`: todas las mediciones tal como las entrega la DGA
  (`hora_local` en hora de Chile, `valor`). La lluvia es un contador acumulado: la lluvia de un
  período es la diferencia entre dos lecturas.

## Cómo funciona

GitHub Actions ejecuta `collector/dga_collect.py` cada 30 minutos
(`.github/workflows/collect.yml`) y guarda los cambios con un commit. La DGA tarda entre 2 y 5 minutos
en responder. Si la ejecución falla varias veces seguidas, GitHub envía un correo al dueño del
repositorio.

Para probar en el computador:

```bash
python collector/dga_collect.py data
```

Datos de la DGA, Ministerio de Obras Públicas de Chile. Se publican sin modificar, salvo el cálculo de
lluvia por hora.
