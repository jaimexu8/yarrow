import { describe, expect, it } from 'vitest';
import { diffIsNeutral, wordDiffParts } from './textDiff';

describe('wordDiffParts', () => {
  it('marks the corrected words as additions', () => {
    const parts = wordDiffParts('the quatrly summary', 'the quarterly summary');
    expect(parts).toEqual([
      { value: 'the ', added: false, removed: false },
      { value: 'quatrly', added: false, removed: true },
      { value: 'quarterly', added: true, removed: false },
      { value: ' summary', added: false, removed: false },
    ]);
  });

  it('marks the replaced words as deletions', () => {
    const parts = wordDiffParts('the quarterly summary', 'the quatrly summary');
    expect(parts).toEqual([
      { value: 'the ', added: false, removed: false },
      { value: 'quarterly', added: false, removed: true },
      { value: 'quatrly', added: true, removed: false },
      { value: ' summary', added: false, removed: false },
    ]);
  });

  it('is empty for identical text', () => {
    expect(wordDiffParts('unchanged', 'unchanged')).toEqual([
      { value: 'unchanged', added: false, removed: false },
    ]);
  });

  it('treats a case change as a visible change', () => {
    const parts = wordDiffParts('The Summary', 'the summary');
    expect(diffIsNeutral(parts)).toBe(false);
  });

  it('handles a wholesale replacement', () => {
    const parts = wordDiffParts('garbled', 'readable');
    expect(parts).toEqual([
      { value: 'garbled', added: false, removed: true },
      { value: 'readable', added: true, removed: false },
    ]);
  });
});
