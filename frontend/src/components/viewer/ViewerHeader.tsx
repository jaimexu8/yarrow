'use client';

import Link from 'next/link';
import { useState, type ReactNode } from 'react';
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Eye,
  EyeOff,
  Keyboard,
  PanelLeftClose,
  PanelLeftOpen,
  Share2,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';
import { StatusBadge } from '@/components/ui/Badge';
import { cn } from '@/lib/cn';
import { DocumentDetails, ModelInfo } from './DocumentDetails';
import { FindInDocument } from './FindInDocument';
import { RegionFilterDropdown } from './RegionFilter';
import { ShareModal } from './ShareModal';
import { THUMBNAIL_SIDEBAR_ID } from './ThumbnailSidebar';
import { MIN_ZOOM, MAX_ZOOM, useViewer } from './ViewerContext';

const ICON_BUTTON =
  'rounded-md p-1.5 text-slate-600 hover:bg-slate-100 hover:text-slate-900 disabled:cursor-not-allowed disabled:text-slate-300 disabled:hover:bg-transparent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900';

function PageIndicator() {
  const { currentPage, pageCount, scrollToPage } = useViewer();
  if (!pageCount) return null;
  return (
    <div className="flex items-center gap-1 text-sm text-slate-600">
      <button
        type="button"
        className={ICON_BUTTON}
        disabled={currentPage <= 1}
        onClick={() => scrollToPage(currentPage - 1)}
        title="Previous page"
        aria-keyshortcuts="ArrowLeft K"
      >
        <ChevronLeft aria-hidden="true" className="size-4" />
        <span className="sr-only">Previous page</span>
      </button>
      <span
        aria-live="polite"
        className="min-w-[5.5rem] text-center tabular-nums"
      >
        Page {Math.min(currentPage, pageCount)} of {pageCount}
      </span>
      <button
        type="button"
        className={ICON_BUTTON}
        disabled={currentPage >= pageCount}
        onClick={() => scrollToPage(currentPage + 1)}
        title="Next page"
        aria-keyshortcuts="ArrowRight J"
      >
        <ChevronRight aria-hidden="true" className="size-4" />
        <span className="sr-only">Next page</span>
      </button>
    </div>
  );
}

function ThumbnailsToggle() {
  const { thumbnailsOpen, toggleThumbnails } = useViewer();
  const label = thumbnailsOpen
    ? 'Hide page thumbnails'
    : 'Show page thumbnails';
  return (
    <button
      type="button"
      className={ICON_BUTTON}
      onClick={toggleThumbnails}
      title={label}
      aria-expanded={thumbnailsOpen}
      aria-controls={THUMBNAIL_SIDEBAR_ID}
      aria-keyshortcuts="T"
    >
      {thumbnailsOpen ? (
        <PanelLeftClose aria-hidden="true" className="size-4" />
      ) : (
        <PanelLeftOpen aria-hidden="true" className="size-4" />
      )}
      <span className="sr-only">Page thumbnails</span>
    </button>
  );
}

function ZoomControls() {
  const { zoom, zoomIn, zoomOut, resetZoom } = useViewer();
  const percentage = Math.round(zoom * 100);

  return (
    <div
      className="flex items-center gap-0.5 text-sm text-slate-600"
      role="group"
      aria-label="Zoom controls"
    >
      <button
        type="button"
        className={ICON_BUTTON}
        disabled={zoom <= MIN_ZOOM}
        onClick={zoomOut}
        title="Zoom out"
        aria-label="Zoom out"
      >
        <ZoomOut aria-hidden="true" className="size-4" />
      </button>
      <button
        type="button"
        className="min-w-[3.5rem] rounded px-1.5 py-1 text-center text-xs font-medium tabular-nums text-slate-700 hover:bg-slate-100 hover:text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900"
        onClick={resetZoom}
        title="Reset zoom (100%)"
        aria-label={`Reset zoom, currently ${percentage}%`}
      >
        {percentage}%
      </button>
      <button
        type="button"
        className={ICON_BUTTON}
        disabled={zoom >= MAX_ZOOM}
        onClick={zoomIn}
        title="Zoom in"
        aria-label="Zoom in"
      >
        <ZoomIn aria-hidden="true" className="size-4" />
      </button>
    </div>
  );
}

function BoundingBoxToggle() {
  const { showBoundingBoxes, setShowBoundingBoxes } = useViewer();
  return (
    <button
      type="button"
      className={ICON_BUTTON}
      onClick={() => setShowBoundingBoxes(!showBoundingBoxes)}
      title={showBoundingBoxes ? 'Hide bounding boxes' : 'Show bounding boxes'}
      aria-pressed={showBoundingBoxes}
      aria-keyshortcuts="B"
    >
      {showBoundingBoxes ? (
        <Eye aria-hidden="true" className="size-4" />
      ) : (
        <EyeOff aria-hidden="true" className="size-4" />
      )}
      <span className="sr-only">
        {showBoundingBoxes ? 'Hide bounding boxes' : 'Show bounding boxes'}
      </span>
    </button>
  );
}

function ShortcutsHelpButton() {
  const { setShortcutsHelpOpen } = useViewer();
  return (
    <button
      type="button"
      className={ICON_BUTTON}
      onClick={() => setShortcutsHelpOpen(true)}
      title="Keyboard shortcuts (?)"
      aria-label="Keyboard shortcuts"
      aria-keyshortcuts="?"
    >
      <Keyboard aria-hidden="true" className="size-4" />
    </button>
  );
}

/**
 * The viewer's top bar
 */
export function ViewerHeader({ actions }: { actions?: ReactNode }) {
  const { document } = useViewer();
  const [shareOpen, setShareOpen] = useState(false);

  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-slate-200 bg-white px-4 py-3 sm:px-6">
      <Link href="/dashboard" className={cn(ICON_BUTTON, '-ml-1.5')}>
        <ArrowLeft aria-hidden="true" className="size-4" />
        <span className="sr-only">Back to documents</span>
      </Link>
      <ThumbnailsToggle />
      <div className="flex min-w-[10rem] flex-1 items-center gap-3">
        <h1
          title={document.filename}
          className="truncate text-base font-semibold text-slate-900"
        >
          {document.filename}
        </h1>
        <StatusBadge status={document.status} className="shrink-0" />
        <ModelInfo className="max-w-[18rem]" />
      </div>
      <DocumentDetails />
      <FindInDocument />
      <RegionFilterDropdown />
      <PageIndicator />
      <ZoomControls />
      <div className="flex items-center gap-1 border-l border-slate-200 pl-4">
        <BoundingBoxToggle />
        <ShortcutsHelpButton />
        <button
          type="button"
          className={ICON_BUTTON}
          onClick={() => setShareOpen(true)}
          title="Share document"
        >
          <Share2 aria-hidden="true" className="size-4" />
          <span className="sr-only">Share document</span>
        </button>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>

      <ShareModal
        documentId={document.id}
        documentTitle={document.filename}
        open={shareOpen}
        onClose={() => setShareOpen(false)}
      />
    </header>
  );
}
