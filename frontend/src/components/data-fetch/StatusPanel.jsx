function StatusPanel({ status, error }) {
  if (status === "error") {
    return (
      <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg px-4 py-3">
        Fetch failed: {error}
      </div>
    );
  }

  if (status === "loading") {
    return (
      <div className="bg-white border border-gray-200 rounded-lg px-6 py-10 text-center text-sm text-gray-500">
        Fetching files and combining them…
      </div>
    );
  }

  // idle
  return (
    <div className="bg-white border border-gray-200 rounded-lg px-6 py-10 text-center text-sm text-gray-500">
      No data fetched yet. Click "Fetch from SharePoint" to pull the latest files.
    </div>
  );
}

export default StatusPanel;
