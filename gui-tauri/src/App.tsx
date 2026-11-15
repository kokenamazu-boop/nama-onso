import { useEffect, useState } from "react";
import DbTab from "./tabs/DbTab";
import WaveTab from "./tabs/WaveTab";
import type { Theme, WaveOpenRequest } from "./lib/types";

const THEME_KEY = "nama.onso.theme";
type Tab = "wave" | "db" | "curate";

function initialTheme(): Theme {
  return localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light";
}

export default function App() {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [tab, setTab] = useState<Tab>("wave");
  const [waveRequest, setWaveRequest] = useState<WaveOpenRequest | null>(null);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  const openInWave = (request: WaveOpenRequest) => {
    setWaveRequest({ ...request, token: Date.now() });
    setTab("wave");
  };

  return (
    <div className="shell">
      <header className="shell-head">
        <div className="shell-brand">
          <span className="shell-mark">音</span>
          <span className="shell-title">
            なま音素<small>GUI 試作</small>
          </span>
        </div>
        <nav className="shell-tabs" aria-label="画面">
          <button
            className={`shell-tab${tab === "wave" ? " active" : ""}`}
            onClick={() => setTab("wave")}
            aria-current={tab === "wave" ? "page" : undefined}
          >
            波形
          </button>
          <button
            className={`shell-tab${tab === "db" ? " active" : ""}`}
            onClick={() => setTab("db")}
            aria-current={tab === "db" ? "page" : undefined}
          >
            音素DB
          </button>
          <button
            className={`shell-tab${tab === "curate" ? " active" : ""}`}
            onClick={() => setTab("curate")}
            aria-current={tab === "curate" ? "page" : undefined}
          >
            音素の選抜
          </button>
        </nav>
        <div className="theme-switch" role="group" aria-label="テーマ">
          <button
            className={theme === "light" ? "active" : ""}
            onClick={() => setTheme("light")}
            aria-pressed={theme === "light"}
          >
            ライト
          </button>
          <button
            className={theme === "dark" ? "active" : ""}
            onClick={() => setTheme("dark")}
            aria-pressed={theme === "dark"}
          >
            ダーク
          </button>
        </div>
      </header>

      <main className="shell-body">
        <div className="tab-host" hidden={tab !== "wave"}>
          <WaveTab isActive={tab === "wave"} request={waveRequest} theme={theme} />
        </div>
        <div className="tab-host" hidden={tab !== "db"}>
          <DbTab isActive={tab === "db"} onOpenWave={openInWave} />
        </div>
        <div className="tab-host" hidden={tab !== "curate"}>
          <section className="placeholder-pane">
            <h1>音素の選抜</h1>
            <p>選抜窓の中身は段階3で移植します。ここはタブの置き場だけ確保しています。</p>
          </section>
        </div>
      </main>
    </div>
  );
}
