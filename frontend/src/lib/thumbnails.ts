import api from './api';

const objectUrls = new Map<string, string>();
const pending = new Map<string, Promise<string>>();
let generation = 0;

function cacheKey(
  documentId: string,
  pageNumber: number,
  version?: string
): string {
  return `${documentId}:${pageNumber}:${version ?? ''}`;
}

export async function getPageThumbnail(
  documentId: string,
  pageNumber: number,
  version?: string
): Promise<Blob> {
  const res = await api.get<Blob>(
    `/api/v1/documents/${documentId}/pages/${pageNumber}/thumbnail${
      version ? `?v=${encodeURIComponent(version)}` : ''
    }`,
    { responseType: 'blob' }
  );
  return res.data;
}

export function cachedPageThumbnail(
  documentId: string,
  pageNumber: number,
  version?: string
): string | null {
  return objectUrls.get(cacheKey(documentId, pageNumber, version)) ?? null;
}

export function loadPageThumbnail(
  documentId: string,
  pageNumber: number,
  version?: string
): Promise<string> {
  const key = cacheKey(documentId, pageNumber, version);
  const cached = objectUrls.get(key);
  if (cached) return Promise.resolve(cached);
  const inFlight = pending.get(key);
  if (inFlight) return inFlight;

  const started = generation;
  const request = getPageThumbnail(documentId, pageNumber, version)
    .then((blob) => {
      const url = URL.createObjectURL(blob);
      if (started !== generation) {
        URL.revokeObjectURL(url);
        throw new Error('The thumbnail cache was cleared');
      }
      objectUrls.set(key, url);
      return url;
    })
    .finally(() => {
      if (pending.get(key) === request) pending.delete(key);
    });
  pending.set(key, request);
  return request;
}

export function revokePageThumbnails(): void {
  generation += 1;
  for (const url of objectUrls.values()) URL.revokeObjectURL(url);
  objectUrls.clear();
  pending.clear();
}
