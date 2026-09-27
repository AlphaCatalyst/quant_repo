import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import Overview from "./pages/Overview";
import Campaign from "./pages/Campaign";
import { Factor, Factors } from "./pages/Factors";
import { Data, Ledger } from "./pages/Ledger";

function useHashPath(): string {
  const [path, setPath] = useState(() => window.location.hash.slice(1) || "/");
  useEffect(() => {
    const onChange = () => {
      setPath(window.location.hash.slice(1) || "/");
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return path;
}

const NAV: [string, string][] = [
  ["/", "总览"],
  ["/factors", "因子"],
  ["/ledger", "Ledger"],
  ["/data", "数据"],
];

function App() {
  const path = useHashPath();
  const parts = path.split("/").filter(Boolean);
  let page;
  if (parts[0] === "campaign" && parts[1]) page = <Campaign id={parts[1]} key={parts[1]} />;
  else if (parts[0] === "factor" && parts[1]) page = <Factor id={parts[1]} key={parts[1]} />;
  else if (parts[0] === "factors") page = <Factors />;
  else if (parts[0] === "ledger") page = <Ledger />;
  else if (parts[0] === "data") page = <Data />;
  else page = <Overview />;
  const active = "/" + (parts[0] === "campaign" ? "" : parts[0] === "factor" ? "factors" : parts[0] ?? "");
  return (
    <>
      <nav className="nav">
        <a className="brand" href="#/">AlphaSieve</a>
        {NAV.map(([p, label]) => (
          <a key={p} href={`#${p}`} className={active === p ? "active" : ""}>{label}</a>
        ))}
        <span className="nav-note">只读视图 · 审批与指令请用 CLI</span>
      </nav>
      <main>{page}</main>
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
