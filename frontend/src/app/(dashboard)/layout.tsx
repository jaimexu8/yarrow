import { RequireAuth } from '@/components/auth/RequireAuth';
import { AppShell } from '@/components/layout/AppShell';
import { BatchCompletionNotifier } from '@/components/notifications/BatchCompletionNotifier';

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <RequireAuth>
      <AppShell>{children}</AppShell>
      {/* On every signed-in page, so a finished upload is announced wherever
          the user is (US-60). */}
      <BatchCompletionNotifier />
    </RequireAuth>
  );
}
