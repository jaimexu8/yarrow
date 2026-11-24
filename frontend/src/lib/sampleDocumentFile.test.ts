import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, it } from 'vitest';
import { SAMPLE_DOCUMENT_URL } from './sampleDocument';

// The file the Try sample document button uploads (US-116). Its text is
// stored uncompressed so this can read it.
const file = readFileSync(
  path.join(__dirname, '../../public', SAMPLE_DOCUMENT_URL)
);
const text = file.toString('latin1');

it('the bundled sample is a small PDF at the URL the app fetches', () => {
  expect(text.startsWith('%PDF-')).toBe(true);
  expect(file.byteLength).toBeLessThan(100 * 1024);
});

it('the sample says it is fictional', () => {
  expect(text).toContain('Maple Hollow Garden Supply');
  expect(text).toContain('Everything in it is made up');
  expect(text).toContain('Fictional sample. Not a real invoice.');
});

it('the sample holds no contact details', () => {
  // No email addresses, web addresses or phone numbers.
  expect(text).not.toMatch(/[\w.+-]+@[\w-]+\.[a-z]{2,}/i);
  expect(text).not.toMatch(/https?:\/\/|www\./i);
  expect(text).not.toMatch(/\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b/);
});
