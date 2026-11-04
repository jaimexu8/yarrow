'use client';

import { useSyncExternalStore } from 'react';
import {
  getStorageUsage,
  STORAGE_CHANGED_EVENT,
  type StorageUsage,
} from './storage';

export type StorageUsageState = {
  usage: StorageUsage | null;
  failed: boolean;
};

const INITIAL_STATE: StorageUsageState = { usage: null, failed: false };

const listeners = new Set<() => void>();
let state = INITIAL_STATE;
let generation = 0;
let inFlight = false;

function publish(next: StorageUsageState): void {
  state = next;
  listeners.forEach((notify) => notify());
}

function refresh(): void {
  const request = ++generation;
  inFlight = true;
  getStorageUsage().then(
    (usage) => {
      if (request !== generation) return;
      inFlight = false;
      publish({ usage, failed: false });
    },
    () => {
      if (request !== generation) return;
      inFlight = false;
      publish({ ...state, failed: true });
    }
  );
}

function subscribe(notify: () => void): () => void {
  listeners.add(notify);
  if (listeners.size === 1) {
    window.addEventListener(STORAGE_CHANGED_EVENT, refresh);
    window.addEventListener('focus', refresh);
  }
  if (!inFlight) refresh();
  return () => {
    listeners.delete(notify);
    if (listeners.size > 0) return;
    window.removeEventListener(STORAGE_CHANGED_EVENT, refresh);
    window.removeEventListener('focus', refresh);
    generation += 1;
    inFlight = false;
    state = INITIAL_STATE;
  };
}

function getSnapshot(): StorageUsageState {
  return state;
}

function getServerSnapshot(): StorageUsageState {
  return INITIAL_STATE;
}

export function useStorageUsage(): StorageUsageState {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
