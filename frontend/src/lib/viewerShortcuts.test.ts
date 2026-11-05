import { afterEach, describe, expect, it } from 'vitest';
import {
  VIEWER_SHORTCUTS,
  isTypingTarget,
  shortcutFor,
  type ViewerShortcutAction,
} from './viewerShortcuts';

afterEach(() => {
  document.body.innerHTML = '';
});

function keydown(
  key: string,
  init: KeyboardEventInit = {},
  target: EventTarget = document.body
): KeyboardEvent {
  const event = new KeyboardEvent('keydown', {
    key,
    bubbles: true,
    cancelable: true,
    ...init,
  });
  let seen: KeyboardEvent | null = null;
  const listener = (received: Event) => {
    seen = received as KeyboardEvent;
  };
  window.addEventListener('keydown', listener);
  target.dispatchEvent(event);
  window.removeEventListener('keydown', listener);
  return seen ?? event;
}

describe('shortcutFor', () => {
  it.each<[string, ViewerShortcutAction]>([
    ['ArrowRight', 'nextPage'],
    ['j', 'nextPage'],
    ['J', 'nextPage'],
    ['ArrowLeft', 'previousPage'],
    ['k', 'previousPage'],
    ['K', 'previousPage'],
    ['n', 'nextLowConfidence'],
    ['N', 'previousLowConfidence'],
    ['b', 'toggleBoundingBoxes'],
    ['B', 'toggleBoundingBoxes'],
    ['t', 'toggleThumbnails'],
    ['T', 'toggleThumbnails'],
    ['?', 'showHelp'],
  ])('maps %s to %s', (key, action) => {
    expect(shortcutFor(keydown(key))).toBe(action);
  });

  it('maps every key in the shortcut table to its action', () => {
    for (const shortcut of VIEWER_SHORTCUTS) {
      for (const key of shortcut.keys) {
        expect(shortcutFor(keydown(key))).toBe(shortcut.action);
      }
    }
  });

  it('ignores keys that are not shortcuts', () => {
    for (const key of ['a', 'ArrowUp', 'Enter', ' ', 'Escape', '/']) {
      expect(shortcutFor(keydown(key))).toBeNull();
    }
  });

  it('allows Shift, which N and ? need', () => {
    expect(shortcutFor(keydown('N', { shiftKey: true }))).toBe(
      'previousLowConfidence'
    );
    expect(shortcutFor(keydown('?', { shiftKey: true }))).toBe('showHelp');
  });

  it.each(['ctrlKey', 'metaKey', 'altKey'] as const)(
    'leaves the event alone with %s held',
    (modifier) => {
      expect(shortcutFor(keydown('j', { [modifier]: true }))).toBeNull();
      expect(
        shortcutFor(keydown('ArrowRight', { [modifier]: true }))
      ).toBeNull();
    }
  );

  it('leaves the event alone while composing text', () => {
    expect(shortcutFor(keydown('n', { isComposing: true }))).toBeNull();
  });

  it('leaves an event another handler already took alone', () => {
    const divider = document.createElement('div');
    divider.tabIndex = 0;
    divider.addEventListener('keydown', (event) => event.preventDefault());
    document.body.appendChild(divider);

    expect(shortcutFor(keydown('ArrowRight', {}, divider))).toBeNull();
  });

  it.each(['input', 'textarea', 'select'])(
    'leaves the event alone when typing in a %s',
    (tag) => {
      const field = document.createElement(tag);
      document.body.appendChild(field);

      expect(shortcutFor(keydown('j', {}, field))).toBeNull();
      expect(shortcutFor(keydown('?', {}, field))).toBeNull();
    }
  );

  it('leaves the event alone while a modal dialog is open', () => {
    const dialog = document.createElement('div');
    dialog.setAttribute('role', 'dialog');
    document.body.appendChild(dialog);
    expect(shortcutFor(keydown('b'))).toBe('toggleBoundingBoxes');

    dialog.setAttribute('aria-modal', 'true');
    expect(shortcutFor(keydown('b'))).toBeNull();
  });
});

describe('isTypingTarget', () => {
  it('recognises form fields and editable content', () => {
    const input = document.createElement('input');
    const textarea = document.createElement('textarea');
    const select = document.createElement('select');
    const editable = document.createElement('div');
    editable.setAttribute('contenteditable', 'true');
    const inside = document.createElement('span');
    editable.appendChild(inside);

    for (const element of [input, textarea, select, editable, inside]) {
      expect(isTypingTarget(element)).toBe(true);
    }
  });

  it('does not treat other elements as typing targets', () => {
    const readOnly = document.createElement('div');
    readOnly.setAttribute('contenteditable', 'false');

    expect(isTypingTarget(document.createElement('button'))).toBe(false);
    expect(isTypingTarget(document.body)).toBe(false);
    expect(isTypingTarget(readOnly)).toBe(false);
    expect(isTypingTarget(window)).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
  });
});
