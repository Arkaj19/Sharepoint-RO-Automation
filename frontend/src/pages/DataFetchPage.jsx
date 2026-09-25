import { useState } from "react";
import { fetchFromSharePoint } from "../api/fetchApi";
import FetchActionCard from "../components/data-fetch/FetchActionCard";
import StatusPanel from "../components/data-fetch/StatusPanel";
import SummaryCard from "../components/data-fetch/SummaryCard";

function DataFetchPage() {
  const [status, setStatus] = useState("idle"); // idle | loading | success | error
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  const handleFetch = async () => {
    setStatus("loading");
    setError("");
    try {
      const data = await fetchFromSharePoint();
      setResult(data);
      setStatus("success");
    } catch (err) {
      setError(err.message || "Something went wrong while fetching.");
      setStatus("error");
    }
  };

  return (
    <div className="max-w-[1600px] mx-auto px-6 py-8">
      <div className="mb-6">
        <h2 className="text-2xl font-semibold text-gray-900">Data Fetch</h2>
        <p className="text-sm text-gray-500 mt-1">
          Fetch, combine, and review source extracts pulled from SharePoint.
        </p>
      </div>

      <div className="mb-6">
        <FetchActionCard onFetch={handleFetch} isLoading={status === "loading"} />
      </div>

      {status !== "success" && <StatusPanel status={status} error={error} />}

      {status === "success" && result && (
        <div>
          <p className="text-sm text-gray-600 mb-4">
            {result.total_files_fetched} files fetched and combined into{" "}
            {result.combined_files.length} files.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {result.combined_files.map((file) => (
              <SummaryCard key={file.name} file={file} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default DataFetchPage;
