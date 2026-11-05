export type ViewerShortcutAction =
  | 'nextPage'
  | 'previousPage'
  | 'nextLowConfidence'
  | 'previousLowConfidence'
  | 'toggleBoundingBoxes'
  | 'toggleThumbnails'
  | 'showHelp';

export type ViewerShortcut = {
  action: ViewerShortcutAction;
  keys: readonly string[];
  label: string;
  description: string;
};

export const VIEWER_SHORTCUTS: readonly ViewerShortcut[] = [
  {
    action: 'nextPage',
    keys: ['ArrowRight', 'j'],
    label: '→ or J',
    description: 'Next page',
  },
  {
    action: 'previousPage',
    keys: ['ArrowLeft', 'k'],
    label: '← or K',
    description: 'Previous page',
  },
  {
    action: 'nextLowConfidence',
    keys: ['n'],
    label: 'N',
    description: 'Next low-confidence region',
  },
  {
    action: 'previousLowConfidence',
    keys: ['N'],
    label: 'Shift+N',
    description: 'Previous low-confidence region',
  },
  {
    action: 'toggleBoundingBoxes',
    keys: ['b'],
    label: 'B',
    description: 'Show or hide bounding boxes',
  },
  {
    action: 'toggleThumbnails',
    keys: ['t'],
    label: 'T',
    description: 'Show or hide page thumbnails',
  },
  {
    action: 'showHelp',
    keys: ['?'],
    label: '?',
    description: 'Show keyboard shortcuts',
  },
];

const ACTIONS_BY_KEY: ReadonlyMap<string, ViewerShortcutAction> = (() => {
  const actions = new Map<string, ViewerShortcutAction>();
  for (const shortcut of VIEWER_SHORTCUTS) {
    for (const key of shortcut.keys) actions.set(key, shortcut.action);
  }
  for (const shortcut of VIEWER_SHORTCUTS) {
    for (const key of shortcut.keys) {
      if (!/^[a-z]$/.test(key)) continue;
      const capsLock = key.toUpperCase();
      if (!actions.has(capsLock)) actions.set(capsLock, shortcut.action);
    }
  }
  return actions;
})();

const MODAL_DIALOG_SELECTOR = '[role="dialog"][aria-modal="true"]';
const EDITABLE_SELECTOR =
  '[contenteditable=""], [contenteditable="true"], [contenteditable="plaintext-only"]';

export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return true;
  return target.isContentEditable || target.closest(EDITABLE_SELECTOR) !== null;
}

export function isModalDialogOpen(): boolean {
  return window.document.querySelector(MODAL_DIALOG_SELECTOR) !== null;
}

export function hasCommandModifier(event: KeyboardEvent): boolean {
  return event.ctrlKey || event.metaKey || event.altKey;
}

export function shortcutFor(event: KeyboardEvent): ViewerShortcutAction | null {
  if (hasCommandModifier(event)) return null;
  if (event.isComposing || event.defaultPrevented) return null;
  if (isTypingTarget(event.target)) return null;
  if (isModalDialogOpen()) return null;
  return ACTIONS_BY_KEY.get(event.key) ?? null;
}
