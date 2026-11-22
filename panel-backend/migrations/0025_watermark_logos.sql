-- Migración 0025: logos de marca de agua por tenant (2026-11-18).
--
-- El usuario pidió la marca de agua en el panel: subir sus logos
-- (PNG/JPG) desde Ajustes, elegir el activo y que el render lo
-- queme semitransparente. Los logos viven en el bucket R2 que ya
-- existe (clips-avc) con su propio prefijo, y su fila en D1.
--
-- AISLAMIENTO: toda consulta filtra por tenant_id; la key R2 lleva
-- el prefijo watermarks/<tenant>/ con un componente aleatorio de
-- 64 bits, igual que los MP4 de clips (ver worker/src/r2.ts): dos
-- tenants nunca comparten key aunque el nombre coincida. El logo
-- activo se guarda en settings (watermark_logo_id) y NUNCA se copia
-- con la configuración base: ese id apunta a filas de otro tenant.
CREATE TABLE watermark_logos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id TEXT NOT NULL,
  r2_key TEXT NOT NULL UNIQUE,
  filename TEXT NOT NULL,
  content_type TEXT NOT NULL,
  size INTEGER NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX idx_watermark_logos_tenant ON watermark_logos(tenant_id);
