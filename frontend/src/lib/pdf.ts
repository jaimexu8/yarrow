import * as pdfjsLib from 'pdfjs-dist';

// The worker ships with the package, so the viewer does not depend on a CDN
// being reachable. Import this module only on the client (e.g. with a
// dynamic import), since pdf.js needs browser APIs.
pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.js',
  import.meta.url
).toString();

export default pdfjsLib;
