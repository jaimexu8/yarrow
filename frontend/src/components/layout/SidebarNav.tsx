'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  LayoutDashboard,
  ListChecks,
  Search,
  Settings,
  Shield,
  Upload,
  type LucideIcon,
} from 'lucide-react';
import { useAuth } from '@/context/AuthContext';
import { cn } from '@/lib/cn';

type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Other path prefixes that belong to this section, e.g. a document opened
   * from the library keeps Dashboard highlighted. */
  alsoActiveOn?: string[];
  adminOnly?: boolean;
};

const NAV_ITEMS: NavItem[] = [
  {
    href: '/dashboard',
    label: 'Dashboard',
    icon: LayoutDashboard,
    alsoActiveOn: ['/documents'],
  },
  { href: '/upload', label: 'Upload', icon: Upload },
  { href: '/jobs', label: 'Jobs', icon: ListChecks },
  { href: '/search', label: 'Search', icon: Search },
  { href: '/settings', label: 'Settings', icon: Settings },
  { href: '/admin', label: 'Admin', icon: Shield, adminOnly: true },
];

function isUnder(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

function isActive(pathname: string, item: NavItem): boolean {
  return [item.href, ...(item.alsoActiveOn ?? [])].some((prefix) =>
    isUnder(pathname, prefix)
  );
}

export function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { user } = useAuth();
  const items = NAV_ITEMS.filter((item) => !item.adminOnly || user?.is_admin);

  return (
    <nav aria-label="Main">
      <ul className="space-y-0.5">
        {items.map((item) => {
          const active = isActive(pathname, item);
          const Icon = item.icon;
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                onClick={onNavigate}
                aria-current={active ? 'page' : undefined}
                className={cn(
                  'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900',
                  active
                    ? 'bg-white text-slate-900 shadow-sm ring-1 ring-slate-200'
                    : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                )}
              >
                <Icon
                  aria-hidden="true"
                  className={cn(
                    'size-4 shrink-0',
                    active ? 'text-slate-900' : 'text-slate-400'
                  )}
                />
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
