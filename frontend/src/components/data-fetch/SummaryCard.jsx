// import { useState } from "react";
// import { fetchPreview } from "../../api/fetchApi";
// import PreviewTable from "./PreviewTable";

// function SummaryCard({ file }) {
//   const [showPreview, setShowPreview] = useState(false);
//   const [previewData, setPreviewData] = useState(null);
//   const [previewStatus, setPreviewStatus] = useState("idle"); // idle | loading | error
//   const [previewError, setPreviewError] = useState("");

//   const handleTogglePreview = async () => {
//     const next = !showPreview;
//     setShowPreview(next);

//     if (next && !previewData) {
//       setPreviewStatus("loading");
//       setPreviewError("");
//       try {
//         const data = await fetchPreview(file.name);
//         setPreviewData(data);
//         setPreviewStatus("idle");
//       } catch (err) {
//         setPreviewError(err.message || "Couldn't load preview.");
//         setPreviewStatus("error");
//       }
//     }
//   };

//   return (
//     <div className="bg-white border border-gray-200 rounded-lg p-5">
//       <div className="flex items-center justify-between mb-4">
//         <h4 className="text-sm font-semibold text-gray-900">{file.name}.xlsx</h4>
//         <span className="text-xs text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full">
//           Ready
//         </span>
//       </div>

//       <dl className="grid grid-cols-2 gap-4 text-sm">
//         <div>
//           <dt className="text-gray-500">Source files</dt>
//           <dd className="text-gray-900 font-medium text-lg">{file.source_files}</dd>
//         </div>
//         <div>
//           <dt className="text-gray-500">Rows</dt>
//           <dd className="text-gray-900 font-medium text-lg">
//             {file.row_count.toLocaleString()}
//           </dd>
//         </div>
//       </dl>

//       <p className="text-xs text-gray-400 mt-4 truncate" title={file.saved_path}>
//         Saved to {file.saved_path}
//       </p>

//       <button
//         onClick={handleTogglePreview}
//         className="text-xs font-medium text-slate-900 hover:underline mt-4"
//       >
//         {showPreview ? "Hide preview" : "Preview file"}
//       </button>

//       {showPreview && previewStatus === "loading" && (
//         <p className="text-xs text-gray-500 mt-3">Loading preview…</p>
//       )}

//       {showPreview && previewStatus === "error" && (
//         <p className="text-xs text-red-600 mt-3">{previewError}</p>
//       )}

//       {showPreview && previewData && previewStatus === "idle" && (
//         <PreviewTable preview={previewData} />
//       )}
//     </div>
//   );
// }

// export default SummaryCard;

import { useState } from "react";
import { fetchPreview, downloadCombinedFile } from "../../api/fetchApi";
import PreviewTable from "./PreviewTable";

function SummaryCard({ file }) {
  const [showPreview, setShowPreview] = useState(false);
  const [previewData, setPreviewData] = useState(null);
  const [previewStatus, setPreviewStatus] = useState("idle"); // idle | loading | error
  const [previewError, setPreviewError] = useState("");
  const [downloadStatus, setDownloadStatus] = useState("idle"); // idle | loading | error
  const [downloadError, setDownloadError] = useState("");

  const handleDownload = async () => {
    setDownloadStatus("loading");
    setDownloadError("");
    try {
      await downloadCombinedFile(file.name);
      setDownloadStatus("idle");
    } catch (err) {
      setDownloadError(err.message || "Couldn't download file.");
      setDownloadStatus("error");
    }
  };

  const handleTogglePreview = async () => {
    const next = !showPreview;
    setShowPreview(next);

    if (next && !previewData) {
      setPreviewStatus("loading");
      setPreviewError("");
      try {
        const data = await fetchPreview(file.name);
        setPreviewData(data);
        setPreviewStatus("idle");
      } catch (err) {
        setPreviewError(err.message || "Couldn't load preview.");
        setPreviewStatus("error");
      }
    }
  };

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-5">
      <div className="flex items-center justify-between mb-4">
        <h4 className="text-sm font-semibold text-gray-900">{file.name}</h4>
        <span className="text-xs text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full">
          Ready
        </span>
      </div>

      <dl className="grid grid-cols-2 gap-4 text-sm">
        <div>
          <dt className="text-gray-500">Source files</dt>
          <dd className="text-gray-900 font-medium text-lg">{file.source_files}</dd>
        </div>
        <div>
          <dt className="text-gray-500">Rows</dt>
          <dd className="text-gray-900 font-medium text-lg">
            {file.row_count.toLocaleString()}
          </dd>
        </div>
      </dl>

      <p className="text-xs text-gray-400 mt-4 truncate" title={file.saved_path}>
        Saved to {file.saved_path}
      </p>

      <div className="flex items-center gap-4 mt-4">
        <button
          onClick={handleTogglePreview}
          className="text-xs font-medium text-slate-900 hover:underline"
        >
          {showPreview ? "Hide preview" : "Preview file"}
        </button>

        <button
          onClick={handleDownload}
          disabled={downloadStatus === "loading"}
          className="text-xs font-medium text-slate-900 hover:underline disabled:opacity-50"
        >
          {downloadStatus === "loading" ? "Preparing .xlsx…" : "Download .xlsx"}
        </button>
      </div>

      {downloadStatus === "error" && (
        <p className="text-xs text-red-600 mt-2">{downloadError}</p>
      )}

      {showPreview && previewStatus === "loading" && (
        <p className="text-xs text-gray-500 mt-3">Loading preview…</p>
      )}

      {showPreview && previewStatus === "error" && (
        <p className="text-xs text-red-600 mt-3">{previewError}</p>
      )}

      {showPreview && previewData && previewStatus === "idle" && (
        <PreviewTable preview={previewData} />
      )}
    </div>
  );
}

export default SummaryCard;