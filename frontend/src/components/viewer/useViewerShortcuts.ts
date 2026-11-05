'use client';

import { useEffect, useEffectEvent, useState } from 'react';
import {
  isLowConfidence,
  type DocumentTree,
  type RegionNode,
} from '@/lib/viewer';
import {
  hasCommandModifier,
  isTypingTarget,
  shortcutFor,
} from '@/lib/viewerShortcuts';
import { useViewer } from './ViewerContext';

type Direction = 1 | -1;

function lowConfidenceRegions(tree: DocumentTree | null): RegionNode[] {
  if (!tree) return [];
  return tree.pages
    .flatMap((page) => page.regions)
    .filter(isLowConfidence)
    .sort(
      (a, b) =>
        a.page_number - b.page_number || a.reading_order - b.reading_order
    );
}

function compareToAnchor(
  region: RegionNode,
  anchor: { page: number; order: number }
): number {
  return (
    region.page_number - anchor.page || region.reading_order - anchor.order
  );
}

function findRegion(tree: DocumentTree | null, regionId: string | null) {
  if (!tree || !regionId) return null;
  for (const page of tree.pages) {
    const region = page.regions.find((item) => item.id === regionId);
    if (region) return region;
  }
  return null;
}

function nextRegionIndex(
  regions: RegionNode[],
  anchor: { page: number; order: number },
  direction: Direction
): number {
  if (direction === 1) {
    const index = regions.findIndex(
      (region) => compareToAnchor(region, anchor) > 0
    );
    return index === -1 ? 0 : index;
  }
  const index = regions.findLastIndex(
    (region) => compareToAnchor(region, anchor) < 0
  );
  return index === -1 ? regions.length - 1 : index;
}

function scrollRegionIntoView(regionId: string) {
  for (const element of Array.from(
    window.document.querySelectorAll<HTMLElement>(
      `[data-region-id="${CSS.escape(regionId)}"]`
    )
  )) {
    if (element.offsetParent) element.scrollIntoView({ block: 'center' });
  }
}

export function useViewerShortcuts(): string {
  const {
    tree,
    pageCount,
    currentPage,
    scrollToPage,
    activeRegionId,
    setActiveRegionId,
    showBoundingBoxes,
    setShowBoundingBoxes,
    thumbnailsOpen,
    toggleThumbnails,
    shortcutsHelpOpen,
    setShortcutsHelpOpen,
    setShowLowConfidence,
  } = useViewer();
  const [announcement, setAnnouncement] = useState('');

  function goToPage(delta: Direction) {
    if (!pageCount) return;
    const page = Math.min(Math.max(currentPage + delta, 1), pageCount);
    if (page !== currentPage) scrollToPage(page);
  }

  function goToLowConfidence(direction: Direction) {
    const regions = lowConfidenceRegions(tree);
    if (regions.length === 0) {
      setAnnouncement('No low-confidence regions in this document');
      return;
    }
    const active = findRegion(tree, activeRegionId);
    const anchor = active
      ? { page: active.page_number, order: active.reading_order }
      : { page: currentPage, order: -Infinity };
    const index = nextRegionIndex(regions, anchor, direction);
    const region = regions[index];
    setShowLowConfidence(true);
    setActiveRegionId(region.id);
    scrollToPage(region.page_number);
    scrollRegionIntoView(region.id);
    setAnnouncement(
      `Low-confidence region ${index + 1} of ${regions.length}, page ${region.page_number}`
    );
  }

  const onKeyDown = useEffectEvent((event: KeyboardEvent) => {
    if (shortcutsHelpOpen) {
      if (
        event.key === '?' &&
        !hasCommandModifier(event) &&
        !event.isComposing &&
        !isTypingTarget(event.target)
      ) {
        event.preventDefault();
        setShortcutsHelpOpen(false);
      }
      return;
    }

    const action = shortcutFor(event);
    if (!action) return;
    event.preventDefault();

    switch (action) {
      case 'nextPage':
        goToPage(1);
        break;
      case 'previousPage':
        goToPage(-1);
        break;
      case 'nextLowConfidence':
        goToLowConfidence(1);
        break;
      case 'previousLowConfidence':
        goToLowConfidence(-1);
        break;
      case 'toggleBoundingBoxes':
        setShowBoundingBoxes(!showBoundingBoxes);
        setAnnouncement(
          showBoundingBoxes ? 'Bounding boxes hidden' : 'Bounding boxes shown'
        );
        break;
      case 'toggleThumbnails':
        toggleThumbnails();
        setAnnouncement(
          thumbnailsOpen ? 'Page thumbnails hidden' : 'Page thumbnails shown'
        );
        break;
      case 'showHelp':
        setShortcutsHelpOpen(true);
        break;
    }
  });

  useEffect(() => {
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  return announcement;
}
