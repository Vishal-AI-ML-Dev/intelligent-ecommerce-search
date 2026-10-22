import { SearchPage } from '../domains/search/SearchPage';

export function App() {
  return (
    <>
      <header className="app-header">
        <p className="app-name">Product Search</p>
      </header>
      <main className="app-main">
        <SearchPage />
      </main>
    </>
  );
}
