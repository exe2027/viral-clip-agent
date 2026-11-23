// Scopes OAuth del panel (fuente única, 2026-11-23, v5.4.0).
//
// Los scopes viven AQUÍ y solo aquí: index.ts los usa para las URLs
// de autorización (Google/Meta) y native-engagement.ts los usa para
// decidir si la vía nativa puede comentar o solo registrar
// sin_permiso. Si se añade un permiso, se añade aquí y en los dos
// sitios a la vez; nunca se copia la lista a mano en otro archivo.
//
// Historial: hasta v5.3.0 GOOGLE_SCOPES era solo youtube.upload +
// youtube.readonly y META_SCOPES no incluía permisos de comentar.
// Por eso los tokens concedidos hasta ahora casi seguro NO traen los
// permisos de engagement: la sonda (probeNativeScopes) lo comprueba
// contra el token real y el panel pide re-autorizar en Cuentas.
// Ampliar la lista NO invalida los tokens actuales: los scopes son
// por concesión OAuth y solo afectan a la PRÓXIMA autorización.

export const GOOGLE_SCOPES =
  "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly https://www.googleapis.com/auth/youtube.force-ssl";

export const META_SCOPES =
  "public_profile,pages_show_list,pages_read_engagement,instagram_basic,instagram_content_publish,pages_manage_posts,business_management,instagram_manage_insights,read_insights,pages_read_user_content,instagram_manage_comments,pages_manage_engagement,instagram_manage_engagement";

// Permisos que exige la vía nativa de engagement (v2):
//   YouTube: responder / likear comentarios y comentar vídeos exige
//     youtube.force-ssl (readonly solo alcanza para LEER hilos).
//   Instagram: responder y likear comentarios exige
//     instagram_manage_comments; dar like a un post de tercero exige
//     instagram_manage_engagement (se pide en META_SCOPES para que
//     la re-autorización pueda concederlo; sin pedirlo, el like
//     afuera de IG sería sin_permiso para siempre).
//   Facebook: responder, likear comentarios y posts, y comentar en
//     posts de terceros exige pages_manage_engagement.
export const YOUTUBE_COMMENT_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl";
export const INSTAGRAM_REPLY_SCOPE = "instagram_manage_comments";
export const INSTAGRAM_LIKE_SCOPE = "instagram_manage_engagement";
export const FACEBOOK_ENGAGE_SCOPE = "pages_manage_engagement";

/** Permisos de comentar por plataforma (vía nativa). TikTok no tiene
 *  vía nativa: sigue solo por Zernio. */
export const NATIVE_REQUIRED_SCOPES: Record<string, string[]> = {
  youtube: [YOUTUBE_COMMENT_SCOPE],
  instagram: [INSTAGRAM_REPLY_SCOPE, INSTAGRAM_LIKE_SCOPE],
  facebook: [FACEBOOK_ENGAGE_SCOPE],
};

/** Permiso mínimo para responder a comentarios en esa plataforma
 *  (la acción principal adentro). Sin él, la plataforma queda en
 *  sin_permiso y NO se llama a ningún endpoint de comentar. */
export const NATIVE_REPLY_SCOPE: Record<string, string> = {
  youtube: YOUTUBE_COMMENT_SCOPE,
  instagram: INSTAGRAM_REPLY_SCOPE,
  facebook: FACEBOOK_ENGAGE_SCOPE,
};
