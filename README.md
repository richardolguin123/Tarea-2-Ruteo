# Resiliencia de la infraestructura de Internet de Chile frente a un terremoto M8.8

Tarea 2 (Ruteo), trabajo del estudiante. Se modela la infraestructura de telecomunicaciones de Chile (extracto del proyecto Yafún,
NIC Chile / SUBTEL) como una red con topología (`pgr_createTopology`). Se calculan las métricas de resiliencia del paper
del curso (Boettcher et al., IEEE Latin America Transactions 16(9), 2018) en operación y después del
terremoto de Maule (M8.8, 27-02-2010, catálogo USGS). La principal es el **ATTR**, la fiabilidad
promedio de dos terminales: la fracción de pares de los 16 nodos clave que siguen conectados. Todo se
muestra en un mapa Leaflet.

El procedimiento, los supuestos, los resultados y sus evidencias están en el informe de la tarea.

## Resultados (Monte Carlo n = 2.000, semilla 42)
| Métrica (paper) | Red completa, antes | Red completa, después del M8.8 | Solo fibra, antes | Solo fibra, después |
|---|---|---|---|---|
| ATTR, ec. 8 | **1,0000** | **0,1007** | 0,0083 | 0,0000 |
| Componentes conexas N, ec. 4 | 19 | 246,8 | 124 | 280,1 |
| Centralidad por distancia, ec. 12 | 0,0060 | 0,0011 | 0,0003 | 0,0000 |
| Grado medio | 2,074 | 1,940 | 1,913 | 1,758 |
| Coef. de agrupamiento medio | 0,0043 | 0,0039 | 0,0052 | 0,0047 |

La red completa son las 4 capas más 1 puente inferido de 2,6 km. La probabilidad de que los 16 nodos
queden todos conectados después del sismo es 0.

## Requisitos
- Python 3.12, Docker (Docker Compose)

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt     # Windows
```

## Uso
```bash
python main.py
```
Un solo comando corre todo el flujo: si la base no responde la levanta con `docker compose up -d`
(PostGIS 3.4 + pgRouting 3.6, puerto 5433), carga la BD, calcula el ATTR antes y después del sismo con
las demás métricas, y abre el mapa Leaflet en http://127.0.0.1:5055.

Atajos opcionales:
```bash
python main.py cargar    # shapefiles -> topología -> sismo (GeoJSON USGS) -> zona circular -> main.sql
python main.py metricas  # ATTR y demás métricas del paper, antes y después del sismo (ambas redes)
python main.py mapa      # solo el mapa
python main.py todo      # cargar + metricas, sin abrir el mapa
```

`main.sql` es autocontenido y también se puede cargar directo en una base vacía:
```bash
psql -v ON_ERROR_STOP=1 -f main.sql
```

## Contenido
| Ruta | Descripción |
|---|---|
| `main.py` | Levanta la base si hace falta, carga la BD, calcula el ATTR (antes/después) y sirve el mapa Leaflet |
| `main.sql` | Base autocontenida: red, nodos clave, sismo, zonas y probabilidad de falla de cada enlace (`red.prob_falla` y `red.aristas.p_falla`) |
| `static/index.html` | Mapa Leaflet: enlaces, nodos clave, epicentro, zona circular, perímetro de daño, métricas, selector de red y simulación |
| `scripts/cargar_shapefiles.py` | Carga las 4 capas a PostGIS |
| `scripts/cargar_sismo.py` | Carga el GeoJSON del sismo |
| `scripts/modelo_falla.py` | Zona de ruptura circular, modelo probabilístico de falla y generación de `main.sql` |
| `scripts/calcular_attr.py` | ATTR por pares (ec. 8), en operación y con Monte Carlo post-sismo |
| `scripts/metricas_resiliencia.py` | Las cuatro métricas del paper; distancias con `pgr_dijkstraCost` |
| `sql/` | Nodos clave, construcción de la topología y consultas de verificación |
| `datos_crudos/` | Datos originales (shapefiles de la respuesta de SUBTEL y GeoJSON de USGS) |

## Uso de cada librería
La topología (`pgr_createTopology`) y las rutas/componentes (`pgr_dijkstra`, `pgr_connectedComponents`)
se calculan dentro de la base de datos con pgRouting. Python se usa para cargar datos, calcular la
confiabilidad y mostrar el mapa:

| Librería | Uso |
|---|---|
| `psycopg` | Conexión a PostgreSQL/PostGIS y ejecución del SQL |
| `pyshp` | Lectura de los shapefiles para cargarlos a la BD |
| `NumPy` | Simulación Monte Carlo y cálculos del modelo de falla |
| `SciPy` | Componentes conexas, grado y agrupamiento de cada simulación Monte Carlo (ATTR, N y demás métricas) |
| `Flask` | API y página del mapa Leaflet |
| `Leaflet` | Dibujo del mapa en el navegador |
| `matplotlib`, `shapely` | Figuras estáticas de la red para el informe |

## Pruebas
```bash
python tests/validar.py --externo    # 53 pruebas: datos, topología, nodos, métricas, modelo, main.sql y web
```

## Modelo de falla
El sismo se modela como una **zona de ruptura circular** centrada en el epicentro, con el área de
Strasser et al. (2010): R = √(A/π) = 159,3 km. Las fallas regionales circulares corresponden a los
"cortes circulares" de Neumayer y Modiano (2011), ref. [5] del paper.

La cadena del modelo es: distancia al círculo → PGA (Youngs et al. 1997) → MMI/PGV (Wald et al. 1999)
→ tasa de reparaciones (ALA 2001, K = 1) → probabilidad de falla de Poisson por enlace.

El perímetro de daño (MMI ≥ VI) tiene un radio de 265,9 km. Detalles y supuestos en el informe
(sección 6).

## Métricas de resiliencia (paper del curso)
1. **ATTR**, fiabilidad promedio de dos terminales (ec. 8).
2. **Número de componentes conexas** N (ec. 4).
3. **Centralidad por distancia** (ec. 12): promedio de 1/d entre pares de nodos clave, con las rutas
   calculadas por pgRouting.
4. **Grado medio y coeficiente de agrupamiento medio** (Fig. 2).

Variables de entorno para la conexión: `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`
(por defecto `127.0.0.1:5433`, base `resiliencia`, usuario `postgres`). Puerto del mapa: `PUERTO`.

## Uso de inteligencia artificial
El código de apoyo (scripts de Python, SQL y la página del mapa) se escribió con ayuda de un asistente de
inteligencia artificial. Qué se pidió en cada programa y cómo se validó está en la sección 10 del informe.
