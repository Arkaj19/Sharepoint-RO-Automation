import { NavLink } from "react-router-dom";

const TABS = [
  { to: "/data-fetch", label: "Data Fetch" },
  { to: "/migration", label: "Validation" },
  { to: "/validation", label: "LTMC Fill" },
];

function Navbar() {
  return (
    <nav className="bg-white border-b border-gray-200">
      <div className="max-w-[1600px] mx-auto px-6 flex gap-1">
        {TABS.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            className={({ isActive }) =>
              `px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                isActive
                  ? "border-black text-black"
                  : "border-transparent text-gray-500 hover:text-black"
              }`
            }
          >
            {tab.label}
          </NavLink>
        ))}
      </div>
    </nav>
  );
}

export default Navbar;
