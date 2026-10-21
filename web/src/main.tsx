import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { I18nProvider } from './i18n/I18nProvider';

async function start() {
  // Demo builds answer /api/* locally; production builds drop this branch.
  if (import.meta.env.VITE_ASG_DEMO === '1') await import('./demo/install');
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <I18nProvider>
      <React.StrictMode>
        <App />
      </React.StrictMode>
    </I18nProvider>,
  );
}
void start();
