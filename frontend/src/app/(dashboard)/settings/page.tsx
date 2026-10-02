
'use client';

import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { PasswordInput } from "@/components/ui/PasswordInput";
import { useAuth } from "@/context/AuthContext";
import { deleteAccount } from "@/lib/auth";
import { toApiError } from "@/lib/errors";
import { useState } from "react";

export default function SettingsPage() {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const { clearSession } = useAuth();

  async function handleDeleteAccount() {
    setIsDeleting(true);

    try {
      await deleteAccount(password);
      clearSession();
      window.location.replace("/login");
      return;
    } catch (error) {
      const apiError = toApiError(error);
      console.error("Failed to delete account:", apiError.message);
      setError(`Failed to delete account: ${apiError.message}`);
    } finally {
      setIsDeleting(false);
    }
  }

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
        Settings
      </h1>

      <Modal
        open={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        title="Confirm Account Deletion"
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => setIsModalOpen(false)}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDeleteAccount}
              loading={isDeleting}
            >
              Delete Account
            </Button>
          </>
        }
      >
        <p className="text-sm text-slate-600">
          Enter your password to confirm account deletion.
        </p>

        <div className="mt-4">
          <PasswordInput
            id="current-password"
            label="Password"
            autoComplete="current-password"
            enterKeyHint="done"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            invalidMessage="Enter your password."
          />
        </div>

        {error && (
          <p role="alert" className="mt-3 text-sm text-red-600">
            {error}
          </p>
        )}
      </Modal>

      <div className="mt-10">
        <h2 className="text-lg font-semibold text-slate-900">
          Danger zone
        </h2>
        <section className="mt-4 rounded-lg border border-red-200 bg-white p-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h3 className="font-medium text-slate-900">
                Delete account
              </h3>
              <p className="mt-1 text-sm text-slate-600">
                Permanently delete your account and its associated data.
                This action cannot be undone.
              </p>
            </div>

            <div className="shrink-0">
              <Button
                variant="destructive"
                onClick={() => {
                  setError(null);
                  setPassword("");
                  setIsModalOpen(true);
                }}
              >
                Delete Account
              </Button>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}