import { useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";

import Header from "./components/layout/Header";
import Navbar from "./components/layout/NavBar";
import Footer from "./components/layout/Footer";
import { apiGet } from "./api/client";

import DataPage from "./pages/DataPage";
import ChangesPage from "./pages/ChangesPage";
import ProposalsPage from "./pages/ProposalsPage";
import MappingPage from "./pages/MappingPage";
import RuleBookPage from "./pages/RuleBookPage";
import OutputPage from "./pages/OutputPage";
import ValidationPage from "./pages/ValidationPage";

function App() {
  const [isConnected, setIsConnected] = useState(false);
  const [connectionChecking, setConnectionChecking] = useState(true);

  useEffect(() => {
    apiGet("/api/health")
      .then(() => setIsConnected(true))
      .catch(() => setIsConnected(false))
      .finally(() => setConnectionChecking(false));
  }, []);

  return (
    <BrowserRouter>
      <div className="min-h-screen flex flex-col">
        <Header isConnected={isConnected} connectionChecking={connectionChecking} />
        <Navbar />

        <main className="flex-1 bg-gray-50">
          <Routes>
            <Route path="/" element={<Navigate to="/data" replace />} />
            <Route path="/data-fetch" element={<Navigate to="/data" replace />} />
            <Route path="/data" element={<DataPage />} />
            <Route path="/changes" element={<ChangesPage />} />
            <Route path="/proposals" element={<ProposalsPage />} />
            <Route path="/mapping" element={<MappingPage />} />
            <Route path="/rulebook" element={<RuleBookPage />} />
            <Route path="/output" element={<OutputPage />} />
            <Route path="/validation" element={<ValidationPage />} />
          </Routes>
        </main>

        <Footer />
      </div>
    </BrowserRouter>
  );
}

export default App;
