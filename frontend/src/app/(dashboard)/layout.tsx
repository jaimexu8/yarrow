import { RequireAuth } from '@/components/auth/RequireAuth';

/** Every page under (dashboard) requires a signed-in user. */
export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <RequireAuth>{children}</RequireAuth>;
}
