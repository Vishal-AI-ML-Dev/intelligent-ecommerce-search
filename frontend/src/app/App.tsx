import { type KeyboardEvent, useRef, useState } from 'react';
import { AssistantPage } from '../domains/assistant/AssistantPage';
import { SearchPage } from '../domains/search/SearchPage';

const VIEWS = [
  { id: 'search', label: 'Search' },
  { id: 'assistant', label: 'Shopping assistant' },
] as const;

type View = (typeof VIEWS)[number]['id'];

export function App() {
  const [view, setView] = useState<View>('search');
  const tabs = useRef<Partial<Record<View, HTMLButtonElement | null>>>({});

  // Arrow keys move between the two tabs (WAI-ARIA tabs pattern, automatic activation).
  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') {
      return;
    }
    event.preventDefault();
    const index = VIEWS.findIndex((v) => v.id === view);
    const step = event.key === 'ArrowRight' ? 1 : VIEWS.length - 1;
    const next = VIEWS[(index + step) % VIEWS.length]?.id ?? 'search';
    setView(next);
    tabs.current[next]?.focus();
  }

  return (
    <>
      <header className="app-header">
        <p className="app-name">Product Search</p>
        <div role="tablist" aria-label="Sections" className="app-tabs" onKeyDown={onKeyDown}>
          {VIEWS.map((v) => (
            <button
              key={v.id}
              ref={(element) => {
                tabs.current[v.id] = element;
              }}
              type="button"
              role="tab"
              id={`tab-${v.id}`}
              aria-selected={view === v.id}
              aria-controls={`panel-${v.id}`}
              tabIndex={view === v.id ? 0 : -1}
              onClick={() => setView(v.id)}
            >
              {v.label}
            </button>
          ))}
        </div>
      </header>
      <main className="app-main">
        {/* Both panels stay mounted so switching tabs keeps each page's state. */}
        <div role="tabpanel" id="panel-search" aria-labelledby="tab-search" hidden={view !== 'search'}>
          <SearchPage />
        </div>
        <div
          role="tabpanel"
          id="panel-assistant"
          aria-labelledby="tab-assistant"
          hidden={view !== 'assistant'}
        >
          <AssistantPage />
        </div>
      </main>
    </>
  );
}
