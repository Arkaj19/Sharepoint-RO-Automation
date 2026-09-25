function PreviewTable({ preview }) {
  const { columns, rows, total_rows: totalRows, preview_row_count: previewCount } = preview;

  if (rows.length === 0) {
    return (
      <p className="text-xs text-gray-500 mt-3">No rows to preview.</p>
    );
  }

  return (
    <div className="mt-4">
      <p className="text-xs text-gray-500 mb-2">
        Showing {previewCount} of {totalRows.toLocaleString()} rows
      </p>
      <div className="border border-gray-200 rounded-md overflow-auto max-h-64">
        <table className="min-w-full text-xs">
          <thead className="bg-gray-50 sticky top-0">
            <tr>
              {columns.map((col) => (
                <th
                  key={col}
                  className="text-left font-medium text-gray-600 px-3 py-2 whitespace-nowrap border-b border-gray-200"
                >
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="odd:bg-white even:bg-gray-50">
                {columns.map((col) => (
                  <td
                    key={col}
                    className="px-3 py-2 whitespace-nowrap text-gray-700 border-b border-gray-100"
                  >
                    {row[col] === null || row[col] === undefined || row[col] === ""
                      ? "—"
                      : String(row[col])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default PreviewTable;
