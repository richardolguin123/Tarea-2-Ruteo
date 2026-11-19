-- Nodos clave de la red (supuestos de diseno, ver informe, seccion 2):
--   receptor   : capital regional (concentra usuarios/demanda de informacion)
--   generador  : sitio de alta generacion de contenido/datos (centros de datos, Santiago)
--   intercambio: punto de intercambio de trafico o aterrizaje de cables internacionales
-- Coordenadas aproximadas (WGS84) del centro urbano de cada ciudad.
CREATE SCHEMA IF NOT EXISTS red;
DROP TABLE IF EXISTS red.nodos_clave CASCADE;
CREATE TABLE red.nodos_clave (
  id serial PRIMARY KEY,
  nombre text UNIQUE NOT NULL,
  region text,
  rol text[] NOT NULL,
  geom geometry(Point,4326) NOT NULL
);
INSERT INTO red.nodos_clave(nombre, region, rol, geom) VALUES
 ('Arica',        'Arica y Parinacota',   '{receptor,intercambio}',           ST_SetSRID(ST_MakePoint(-70.3126,-18.4783),4326)),
 ('Iquique',      'Tarapaca',             '{receptor}',                       ST_SetSRID(ST_MakePoint(-70.1357,-20.2307),4326)),
 ('Antofagasta',  'Antofagasta',          '{receptor}',                       ST_SetSRID(ST_MakePoint(-70.3975,-23.6509),4326)),
 ('Copiapo',      'Atacama',              '{receptor}',                       ST_SetSRID(ST_MakePoint(-70.3323,-27.3668),4326)),
 ('La Serena',    'Coquimbo',             '{receptor}',                       ST_SetSRID(ST_MakePoint(-71.2519,-29.9027),4326)),
 ('Valparaiso',   'Valparaiso',           '{receptor,intercambio}',           ST_SetSRID(ST_MakePoint(-71.6127,-33.0472),4326)),
 ('Santiago',     'Metropolitana',        '{generador,intercambio,receptor}', ST_SetSRID(ST_MakePoint(-70.6693,-33.4489),4326)),
 ('Rancagua',     'OHiggins',             '{receptor}',                       ST_SetSRID(ST_MakePoint(-70.7444,-34.1708),4326)),
 ('Talca',        'Maule',                '{receptor}',                       ST_SetSRID(ST_MakePoint(-71.6554,-35.4264),4326)),
 ('Chillan',      'Nuble',                '{receptor}',                       ST_SetSRID(ST_MakePoint(-72.1034,-36.6063),4326)),
 ('Concepcion',   'Biobio',               '{receptor}',                       ST_SetSRID(ST_MakePoint(-73.0444,-36.8201),4326)),
 ('Temuco',       'Araucania',            '{receptor}',                       ST_SetSRID(ST_MakePoint(-72.5904,-38.7359),4326)),
 ('Valdivia',     'Los Rios',             '{receptor}',                       ST_SetSRID(ST_MakePoint(-73.2459,-39.8142),4326)),
 ('Puerto Montt', 'Los Lagos',            '{receptor}',                       ST_SetSRID(ST_MakePoint(-72.9424,-41.4693),4326)),
 ('Coyhaique',    'Aysen',                '{receptor}',                       ST_SetSRID(ST_MakePoint(-72.0685,-45.5712),4326)),
 ('Punta Arenas', 'Magallanes',           '{receptor,intercambio}',           ST_SetSRID(ST_MakePoint(-70.9171,-53.1638),4326));
SELECT id, nombre, rol FROM red.nodos_clave ORDER BY id;
