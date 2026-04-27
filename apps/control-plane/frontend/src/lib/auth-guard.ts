import { redirect } from '@tanstack/react-router';
import { msalInstance } from './msal';

export function requireAuth({ location }: { location: { href: string } }) {
  if (msalInstance.getAllAccounts().length === 0) {
    throw redirect({
      to: '/signin',
      search: { redirect: location.href },
    });
  }
}
