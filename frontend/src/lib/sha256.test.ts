import { createHash, randomBytes } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import { Sha256, sha256Hex } from './sha256';

const text = (value: string) => new TextEncoder().encode(value);
const reference = (data: Uint8Array) =>
  createHash('sha256').update(data).digest('hex');

describe('sha256', () => {
  it('matches the FIPS 180-4 test vectors', () => {
    expect(sha256Hex(text(''))).toBe(
      'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    );
    expect(sha256Hex(text('abc'))).toBe(
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
    );
    expect(
      sha256Hex(
        text('abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq')
      )
    ).toBe('248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1');
  });

  it('matches node for every length around the block boundaries', () => {
    for (let length = 0; length <= 200; length += 1) {
      const data = randomBytes(length);
      expect(sha256Hex(data)).toBe(reference(data));
    }
  });

  it('gives the same digest however the input is split', () => {
    const data = randomBytes(300_001);
    const hasher = new Sha256().update(new Uint8Array(0));
    // Piece sizes that straddle the 64-byte blocks in different ways.
    const pieces = [1, 63, 64, 65, 1000, 4096, 100_000];
    let offset = 0;
    for (let i = 0; offset < data.length; i += 1) {
      const size = pieces[i % pieces.length];
      hasher.update(data.subarray(offset, offset + size));
      offset += size;
    }
    expect(hasher.hex()).toBe(reference(data));
  });
});
