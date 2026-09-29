function ValidationStatusPanel({ status, error }) {
  if (status === "error") {
    return (
      <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg px-4 py-3">
        Validation failed to run: {error}
      </div>
    );
  }

  if (status === "loading") {
    return (
      <div className="bg-white border border-gray-200 rounded-lg px-6 py-10 text-center text-sm text-gray-500">
        Loading files and running validation rules…
      </div>
    );
  }

  // idle
  return (
    <div className="bg-white border border-gray-200 rounded-lg px-6 py-10 text-center text-sm text-gray-500">
      No validation run yet. Choose a sheet and click "Run Validation".
    </div>
  );
}

export default ValidationStatusPanel;
