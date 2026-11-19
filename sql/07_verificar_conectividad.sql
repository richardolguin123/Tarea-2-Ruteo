-- Verificacion independiente de la conectividad de la red construida
\echo '--- Componentes con nodos clave, CON puente inferido'
SELECT c.component, count(*) AS nodos_clave, string_agg(k.nombre, ', ' ORDER BY k.id) AS ciudades
FROM red.nodos_clave k
JOIN pgr_connectedComponents('SELECT id, source, target, longitud_m AS cost FROM red.aristas') c ON c.node = k.vertice_id
GROUP BY c.component ORDER BY 2 DESC;

\echo '--- Componentes con nodos clave, SIN puentes (solo infraestructura detectada)'
SELECT c.component, count(*) AS nodos_clave, string_agg(k.nombre, ', ' ORDER BY k.id) AS ciudades
FROM red.nodos_clave k
JOIN pgr_connectedComponents('SELECT id, source, target, longitud_m AS cost FROM red.aristas WHERE tipo = ''detectado''') c ON c.node = k.vertice_id
GROUP BY c.component ORDER BY 2 DESC;

\echo '--- Camino minimo Arica -> Punta Arenas (pgr_dijkstra, CON puente)'
SELECT count(*) AS aristas_en_la_ruta, round((sum(cost)/1000)::numeric) AS km_ruta,
       count(*) FILTER (WHERE a.tipo = 'inferido') AS puentes_usados,
       string_agg(DISTINCT a.capa, ', ') AS capas
FROM pgr_dijkstra('SELECT id, source, target, longitud_m AS cost FROM red.aristas',
     (SELECT vertice_id FROM red.nodos_clave WHERE nombre='Arica'),
     (SELECT vertice_id FROM red.nodos_clave WHERE nombre='Punta Arenas'), false) d
JOIN red.aristas a ON a.id = d.edge;
