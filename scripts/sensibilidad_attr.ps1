# Sensibilidad del ATTR post-sismo al modelo de falla (factor K de ALA y tipo de fuente), red completa.
# Usa una tabla temporal red.prob_falla_sens (la tabla principal red.prob_falla no se toca).
$py = ".\.venv\Scripts\python.exe"
"{0,-26} {1,10} {2,12} {3,10} {4}" -f "caso", "p_media", "claves/16", "P(todos)", "ATTR (IC95%)"
$casos = @(
  @{n = "circular K=1.00 (base)"; f = "circular"; k = 1.0 },
  @{n = "circular K=0.50";        f = "circular"; k = 0.50 },
  @{n = "circular K=0.25";        f = "circular"; k = 0.25 },
  @{n = "circular K=0.10";        f = "circular"; k = 0.10 },
  @{n = "epicentro K=1.00";       f = "epicentro"; k = 1.0 }
)
foreach ($c in $casos) {
  $m = & $py scripts\modelo_falla.py --fuente $c.f --k $c.k --tabla prob_falla_sens
  $a = & $py scripts\calcular_attr.py --escenario sismo --n 2000 --semilla 42 --tabla prob_falla_sens
  $pm = ($m | Select-String "p_falla media").ToString().Split(":")[1].Trim()
  $cl = ($a | Select-String "mayor componente").ToString().Split(":")[1].Trim().Split(" ")[0]
  $pt = ($a | Select-String "P\(todos").ToString().Split(":")[1].Trim()
  $at = ($a | Select-String "ATTR \(post").ToString().Split("=")[1].Trim()
  "{0,-26} {1,10} {2,12} {3,10} {4}" -f $c.n, $pm, $cl, $pt, $at
}
# la tabla de sensibilidad se elimina y se restauran las zonas del modelo base (circular, K=1)
docker exec ruteo_tarea2_db psql -U postgres -d resiliencia -q -c "DROP TABLE IF EXISTS red.prob_falla_sens;"
& $py scripts\modelo_falla.py --fuente circular --k 1.0 --tabla prob_falla_sens | Out-Null
docker exec ruteo_tarea2_db psql -U postgres -d resiliencia -q -c "DROP TABLE IF EXISTS red.prob_falla_sens;"
