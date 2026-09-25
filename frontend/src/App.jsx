import { useState } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";

import Header from "./components/layout/Header";
import Navbar from "./components/layout/NavBar";
import Footer from "./components/layout/Footer";

import DataFetchPage from "./pages/DataFetchPage";
import MigrationPage from "./pages/MigrationPage";
import ValidationPage from "./pages/ValidationPage";

function App() {
  // Wire this up to a real health-check call against /api/health later.
  const [isConnected] = useState(true);
  const [connectionChecking] = useState(false);

  return (
    <BrowserRouter>
      <div className="min-h-screen flex flex-col">
        <Header isConnected={isConnected} connectionChecking={connectionChecking} />
        <Navbar />

        <main className="flex-1 bg-gray-50">
          <Routes>
            <Route path="/" element={<Navigate to="/data-fetch" replace />} />
            <Route path="/data-fetch" element={<DataFetchPage />} />
            <Route path="/migration" element={<MigrationPage />} />
            <Route path="/validation" element={<ValidationPage />} />
          </Routes>
        </main>

        <Footer />
      </div>
    </BrowserRouter>
  );
}

export default App;
