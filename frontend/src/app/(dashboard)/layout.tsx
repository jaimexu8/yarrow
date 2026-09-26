import { RequireAuth } from '@/components/auth/RequireAuth';
import Navbar from '@/components/layout/Navbar';

/** Every page under (dashboard) requires a signed-in user and has the nav bar. */
export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <RequireAuth>
      <Navbar />
      <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
    </RequireAuth>
  );
}
