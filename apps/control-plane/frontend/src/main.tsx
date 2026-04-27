import React from 'react';
import ReactDOM from 'react-dom/client';
import { RouterProvider, createRouter } from '@tanstack/react-router';
import { EventType, type AuthenticationResult } from '@azure/msal-browser';

import { msalInstance } from './lib/msal';
import { routeTree } from './routeTree.gen';

const router = createRouter({ routeTree });

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}

async function bootstrap() {
  await msalInstance.initialize();

  // Process any auth code returned from a redirect-flow sign-in. Must finish
  // before route guards run, so they can see the freshly populated account
  // cache via getAllAccounts().
  await msalInstance.handleRedirectPromise();

  const accounts = msalInstance.getAllAccounts();
  if (accounts.length > 0 && !msalInstance.getActiveAccount()) {
    msalInstance.setActiveAccount(accounts[0] ?? null);
  }

  msalInstance.addEventCallback((event) => {
    if (
      event.eventType === EventType.LOGIN_SUCCESS &&
      event.payload &&
      'account' in event.payload
    ) {
      msalInstance.setActiveAccount(
        (event.payload as AuthenticationResult).account,
      );
    }
  });

  const rootElement = document.getElementById('root');
  if (rootElement) {
    ReactDOM.createRoot(rootElement).render(
      <React.StrictMode>
        <RouterProvider router={router} />
      </React.StrictMode>,
    );
  }
}

bootstrap();
