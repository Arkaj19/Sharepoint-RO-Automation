import logo from "../../assets/gyansys-logo.png";
function ConnectionStatus({ isConnected, connectionChecking }) {
  if (connectionChecking) {
    return (
      <div className="flex items-center gap-2 text-sm text-white/70">
        <span className="h-2 w-2 rounded-full bg-white/40 animate-pulse" />
        Checking connection…
      </div>
    );
    }
  return (
    <div className="flex items-center gap-2 text-sm">
      <span
        className={`h-2 w-2 rounded-full ${
          isConnected ? "bg-emerald-400" : "bg-red-400"
        }`}
      />
      <span className={isConnected ? "text-white/90" : "text-red-300"}>
        {isConnected ? "Connected" : "Disconnected"}
      </span>
    </div>
  );
}
function Header({
    isConnected,
    connectionChecking
}) {

    return (
        <header className="bg-black text-white shadow-md">
            <div className="max-w-[1600px] mx-auto px-6 py-4">
                <div className="flex items-center justify-between">
                    {/* Left */}
                    <div className="flex items-center gap-4">
                        <div>
                            <img 
                                src={logo} 
                                alt="Logo" 
                                className="h-10 w-auto"  // adjust height/width as needed
                            />
                        </div>
                        <div className="h-8 w-px bg-white/20" />
                        <div>
                            <h1 className="text-lg font-semibold">
                                Sharepoint Automation Tool
                            </h1>
                        </div>
                    </div>
 
                    <div className="flex items-center gap-5">
                        <ConnectionStatus
                            isConnected={isConnected}
                            connectionChecking={connectionChecking}
                        />
                    </div>
                </div>
            </div>
        </header>
    );
}
 
export default Header;