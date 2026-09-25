import React from "react";

function Footer() {
  return (
    <footer className="bg-black border-gray-200">
      <div className="max-w-[1600px] mx-auto px-6 py-3">

        <div className="flex items-center justify-between">

          <p className="text-xs text-gray-500">
            Gyansys Migration Tool
          </p>

          <div className="flex items-center gap-4 text-xs text-gray-400">
            <span>Version 1.0.0</span>
            <span>•</span>
            <span>© 2026</span>
          </div>

        </div>

      </div>
    </footer>
  );
}

export default Footer;