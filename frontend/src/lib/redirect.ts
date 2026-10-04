export const DEFAULT_AFTER_LOGIN = '/dashboard';

/**
 * Pages for signing in or recovering an account. They work without a
 * session, an expired session must never redirect away from them, and they
 * are never a destination after signing in.
 */
const PUBLIC_AUTH_PATHS = [
  '/login',
  '/register',
  '/forgot-password',
  '/reset-password',
];

export function isPublicAuthPath(path: string): boolean {
  return PUBLIC_AUTH_PATHS.some(
    (base) =>
      path === base ||
      path.startsWith(`${base}/`) ||
      path.startsWith(`${base}?`)
  );
}

// Any fixed origin works: it only has to be one the destination can be
// compared against. Using a constant keeps this safe to call during SSR.
const BASE = 'http://yarrow.invalid';

/**
 * The page to send the user to after signing in, reduced to a same-origin
 * path. The value is parsed the way the browser will parse it, because string
 * checks alone miss tricks like "/\evil.example", which browsers normalize to
 * "//evil.example" and treat as another site.
 */
export function safeNext(next: string | null | undefined): string {
  if (!next || !next.startsWith('/')) return DEFAULT_AFTER_LOGIN;

  let url: URL;
  try {
    url = new URL(next, BASE);
  } catch {
    return DEFAULT_AFTER_LOGIN;
  }
  if (url.origin !== BASE) return DEFAULT_AFTER_LOGIN;

  // Normalizing can itself produce a protocol-relative path: "/a/..//evil"
  // becomes "//evil". So the *result* must also be a plain same-origin path.
  const path = url.pathname;
  if (path.startsWith('//') || path.includes('\\')) return DEFAULT_AFTER_LOGIN;
  if (isPublicAuthPath(path)) return DEFAULT_AFTER_LOGIN;
  const result = `${path}${url.search}${url.hash}`;
  // Final check: the exact string we hand to the router resolves here.
  return new URL(result, BASE).origin === BASE ? result : DEFAULT_AFTER_LOGIN;
}
