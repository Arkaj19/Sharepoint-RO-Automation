// import { useState } from "react";
// import { fetchFromSharePoint } from "../api/fetchApi";
// import FetchActionCard from "../components/data-fetch/FetchActionCard";
// import StatusPanel from "../components/data-fetch/StatusPanel";
// import SummaryCard from "../components/data-fetch/SummaryCard";

// function DataFetchPage() {
//   const [status, setStatus] = useState("idle"); // idle | loading | success | error
//   const [result, setResult] = useState(null);
//   const [error, setError] = useState("");

//   const handleFetch = async () => {
//     setStatus("loading");
//     setError("");
//     try {
//       const data = await fetchFromSharePoint();
//       setResult(data);
//       setStatus("success");
//     } catch (err) {
//       setError(err.message || "Something went wrong while fetching.");
//       setStatus("error");
//     }
//   };

//   return (
//     <div className="max-w-[1600px] mx-auto px-6 py-8">
//       <div className="mb-6">
//         <h2 className="text-2xl font-semibold text-gray-900">Data Fetch</h2>
//         <p className="text-sm text-gray-500 mt-1">
//           Fetch, combine, and review source extracts pulled from SharePoint.
//         </p>
//       </div>

//       <div className="mb-6">
//         <FetchActionCard onFetch={handleFetch} isLoading={status === "loading"} />
//       </div>

//       {status !== "success" && <StatusPanel status={status} error={error} />}

//       {status === "success" && result && (
//         <div>
//           <p className="text-sm text-gray-600 mb-4">
//             {result.total_files_fetched} files fetched and combined into{" "}
//             {result.combined_files.length} files.
//           </p>
//           <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
//             {result.combined_files.map((file) => (
//               <SummaryCard key={file.name} file={file} />
//             ))}
//           </div>
//         </div>
//       )}
//     </div>
//   );
// }

// export default DataFetchPage;

import { useState } from "react";
import { fetchS4Files, fetchEccFiles } from "../api/fetchApi";
import FetchActionCard from "../components/data-fetch/FetchActionCard";
import StatusPanel from "../components/data-fetch/StatusPanel";
import SummaryCard from "../components/data-fetch/SummaryCard";

function DataFetchPage() {
  const [s4Status, setS4Status] = useState("idle");
  const [s4Result, setS4Result] = useState(null);
  const [s4Error, setS4Error] = useState("");

  const [eccStatus, setEccStatus] = useState("idle");
  const [eccResult, setEccResult] = useState(null);
  const [eccError, setEccError] = useState("");

  const handleFetchS4 = async () => {
    setS4Status("loading");
    try {
      const data = await fetchS4Files();
      setS4Result(data);
      setS4Status("success");
    } catch (err) {
      setS4Error(err.message || "Failed to fetch S4 files.");
      setS4Status("error");
    }
  };

  const handleFetchECC = async () => {
    setEccStatus("loading");
    try {
      const data = await fetchEccFiles();
      setEccResult(data);
      setEccStatus("success");
    } catch (err) {
      setEccError(err.message || "Failed to fetch ECC files.");
      setEccStatus("error");
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

      {/* ECC SECTION */}
      <div className="mb-8">
        <h3 className="text-lg font-medium text-gray-800 mb-4">ECC (DAP) Files</h3>
        <FetchActionCard 
          onFetch={handleFetchECC} 
          isLoading={eccStatus === "loading"} 
          title="Fetch ECC Files"
          description="Pulls MARC_DAP and MBEW_DAP Excel files from SharePoint root."
        />
        {eccStatus !== "success" && <StatusPanel status={eccStatus} error={eccError} />}
        {eccStatus === "success" && eccResult && (
          <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
            {eccResult.combined_files.map((file) => (
              <SummaryCard key={file.name} file={file} />
            ))}
          </div>
        )}
      </div>

      {/* S4 SECTION */}
      <div>
        <h3 className="text-lg font-medium text-gray-800 mb-4">S4 (Databricks) Files</h3>
        <FetchActionCard 
          onFetch={handleFetchS4} 
          isLoading={s4Status === "loading"} 
          title="Fetch S4 Files"
          description="Pulls S_MARC#FreeText and S_MBEW#FreeText CSV files from Databricks folder."
        />
        {s4Status !== "success" && <StatusPanel status={s4Status} error={s4Error} />}
        {s4Status === "success" && s4Result && (
          <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
            {s4Result.combined_files.map((file) => (
              <SummaryCard key={file.name} file={file} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default DataFetchPage;