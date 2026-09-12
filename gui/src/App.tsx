import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import { getHealth } from './api/client';
import { SignalStatus } from './components/SignalStatus';
import { ThemeToggle } from './components/ThemeToggle';
import { LiveTab } from './tabs/LiveTab';
import { ModelTab } from './tabs/ModelTab';
import { ScanTab } from './tabs/ScanTab';

const TABS = ['live', 'scan', 'model'] as const;
type Tab = (typeof TABS)[number];

function tabFromHash(): Tab {
  const hash = window.location.hash.replace('#', '');
  return (TABS as readonly string[]).includes(hash) ? (hash as Tab) : 'live';
}

export function App() {
  const [tab, setTab] = useState<Tab>(tabFromHash);

  useEffect(() => {
    const sync = () => setTab(tabFromHash());
    window.addEventListener('hashchange', sync);
    return () => window.removeEventListener('hashchange', sync);
  }, []);

  function open(next: Tab) {
    window.location.hash = next;
    setTab(next);
  }

  return (
    <div className="shell">
      <nav className="rail">
        <div className="rail__brand">
          micro<span>guard</span>
        </div>
        <div className="rail__tabs">
          {TABS.map((name) => (
            <button
              key={name}
              type="button"
              className="rail__tab"
              aria-current={tab === name ? 'page' : undefined}
              onClick={() => open(name)}
            >
              {name}
            </button>
          ))}
        </div>
        <ThemeToggle />
        <HealthStatus />
      </nav>
      <main className="main">
        {tab === 'live' && <LiveTab />}
        {tab === 'scan' && <ScanTab />}
        {tab === 'model' && <ModelTab />}
      </main>
    </div>
  );
}

/**
 * Three states degrade every decision silently: no model means heuristics
 * only, no Redis means the live tab is blind, and a signal refresher nobody
 * started means every threat-intel signal is absent while looking exactly
 * like a clean actor. All three belong somewhere always visible.
 */
function HealthStatus() {
  const { data } = useQuery({ queryKey: ['health'], queryFn: getHealth });

  if (!data) return <div className="rail__status" />;

  return (
    <div className="rail__status">
      <span style={{ color: data.model_loaded ? 'var(--text-muted)' : 'var(--yellow)' }}>
        model {data.model_loaded ? 'loaded' : 'NOT LOADED'}
      </span>
      <span style={{ color: data.redis_connected ? 'var(--text-muted)' : 'var(--yellow)' }}>
        redis {data.redis_connected ? 'connected' : 'not connected'}
      </span>
      <SignalStatus health={data.signals} />
      <span>v{data.version}</span>
    </div>
  );
}
