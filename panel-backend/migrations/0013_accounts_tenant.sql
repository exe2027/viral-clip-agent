-- Migración 0013 (NUEVA): tenant_id en la tabla accounts (cuentas sociales).
--
-- La migración 0012 añadió tenant_id a todas las tablas con datos de
-- usuario EXCEPTO accounts (olvido): sin esta columna, GET
-- /api/social-accounts no podría filtrar por tenant y un tenant vería
-- las cuentas sociales del otro. Las filas migradas heredan
-- tenant_id='sebastian' por defecto, igual que el resto de tablas.
-- Sin esta migración aplicada, el endpoint de cuentas sociales
-- devuelve lista vacía (el Worker filtra por tenant_id y la columna
-- no existe: la consulta fallaría y se responde con error 500, nunca
-- con datos de otro tenant).

ALTER TABLE accounts ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'sebastian';
CREATE INDEX accounts_tenant ON accounts (tenant_id);
