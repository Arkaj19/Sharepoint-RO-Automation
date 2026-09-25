function FetchActionCard({ onFetch, isLoading }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-6">
      <div className="flex items-center justify-between gap-6 flex-wrap">
        <div>
          <h3 className="text-sm font-medium text-gray-900">
            Databricks Files folder
          </h3>
          <p className="text-xs text-gray-500 mt-1 max-w-md">
            Pulls every S_MARC#FreeText and S_MBEW#FreeText file from
            SharePoint and merges each family into one combined file.
          </p>
        </div>

        <button
          onClick={onFetch}
          disabled={isLoading}
          className="bg-slate-900 hover:bg-slate-800 disabled:bg-slate-400 disabled:cursor-not-allowed text-white text-sm font-medium px-5 py-2.5 rounded-md transition-colors whitespace-nowrap"
        >
          {isLoading ? "Fetching…" : "Fetch from SharePoint"}
        </button>
      </div>
    </div>
  );
}

export default FetchActionCard;
