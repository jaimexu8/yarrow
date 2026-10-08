'use client';

import { Filter, X } from 'lucide-react';
import { cn } from '@/lib/cn';
import { useViewer } from './ViewerContext';

export const REGION_FILTERS = [
  { id: 'all', label: 'All Regions' },
  { id: 'header', label: 'Headers' },
  { id: 'paragraph', label: 'Paragraphs' },
  { id: 'table', label: 'Tables' },
  { id: 'figure', label: 'Figures' },
] as const;

export type RegionFilterId = (typeof REGION_FILTERS)[number]['id'];

/**
 * Filter pills shown above the extracted document pane.
 */
export function RegionFilterPills() {
  const { regionFilter, setRegionFilter } = useViewer();
  const current = regionFilter || 'all';

  return (
    <div
      role="toolbar"
      aria-label="Filter document regions"
      className="sticky top-0 z-20 flex flex-wrap items-center gap-1.5 border-b border-slate-200 bg-white/95 px-6 py-2.5 backdrop-blur-sm sm:px-10"
    >
      <div className="flex items-center gap-1.5 text-xs font-medium text-slate-500 mr-1.5">
        <Filter aria-hidden="true" className="size-3.5" />
        <span>Filter:</span>
      </div>
      {REGION_FILTERS.map((item) => {
        const isSelected = current === item.id;
        return (
          <button
            key={item.id}
            type="button"
            onClick={() => setRegionFilter(item.id === 'all' ? null : item.id)}
            className={cn(
              'rounded-full px-2.5 py-0.5 text-xs font-medium transition-colors',
              isSelected
                ? 'bg-indigo-600 text-white shadow-sm ring-1 ring-indigo-500'
                : 'bg-slate-100 text-slate-600 hover:bg-slate-200 hover:text-slate-900'
            )}
          >
            {item.label}
          </button>
        );
      })}
      {current !== 'all' && (
        <button
          type="button"
          onClick={() => setRegionFilter(null)}
          className="ml-1 inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-600"
          title="Clear filter"
        >
          <X aria-hidden="true" className="size-3" />
          <span>Clear</span>
        </button>
      )}
    </div>
  );
}

/**
 * Compact dropdown for the viewer header toolbar.
 */
export function RegionFilterDropdown() {
  const { regionFilter, setRegionFilter } = useViewer();
  const current = regionFilter || 'all';

  return (
    <div className="flex items-center gap-1.5">
      <label htmlFor="viewer-region-filter" className="sr-only">
        Filter document regions
      </label>
      <div className="relative inline-flex items-center">
        <select
          id="viewer-region-filter"
          value={current}
          onChange={(e) => {
            const val = e.target.value;
            setRegionFilter(val === 'all' ? null : val);
          }}
          className={cn(
            'h-8 rounded-md border text-xs font-medium pl-7 pr-8 transition-colors appearance-none cursor-pointer',
            current !== 'all'
              ? 'border-indigo-400 bg-indigo-50 text-indigo-900 font-semibold'
              : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'
          )}
        >
          {REGION_FILTERS.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </select>
        <Filter
          aria-hidden="true"
          className="pointer-events-none absolute left-2 size-3.5 text-slate-400"
        />
        <span
          aria-hidden="true"
          className="pointer-events-none absolute right-2 text-slate-400 text-[10px]"
        >
          ▼
        </span>
      </div>
    </div>
  );
}
