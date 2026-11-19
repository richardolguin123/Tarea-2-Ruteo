# Ejecuta las 4 exploraciones de conectividad (sql/01, 02, 03, 05) y muestra solo las lineas de resultado.
$salida = foreach ($f in "01_explorar_conectividad", "02_explorar_tolerancias_amplias", "03_explorar_nodado", "05_explorar_combinaciones_capas") {
  docker exec ruteo_tarea2_db psql -U postgres -d resiliencia -f "/sql/$f.sql" 2>&1 | ForEach-Object { "$_" }
}
$salida | Where-Object { $_ -match "tolerancia=|capas=|snap=" } |
  ForEach-Object { ($_ -replace "^psql:[^ ]+ NOTICE:\s+", "").Trim() }
