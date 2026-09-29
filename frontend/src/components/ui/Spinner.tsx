export function Spinner() {
  return (
    <div className="flex justify-center py-8" role="status" aria-label="Loading">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-gray-300 border-t-gray-700" />
    </div>
  );
}
