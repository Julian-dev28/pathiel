/**
 * The connect button, as a React island inside a server-rendered page.
 *
 * The pathia dashboard is Jinja templates plus vendored vanilla JS, and stays
 * that way. This mounts into one `<div>` in the masthead and owns exactly one
 * thing: connecting a wallet and proving control of it. Every other panel on
 * every page is untouched vanilla, and talks to this island through the small
 * `window.PathiaWallet` bridge in index.tsx.
 *
 * Why RainbowKit rather than the `window.ethereum` call this replaces: with two
 * extensions installed, `window.ethereum` is whichever one won a race at page
 * load. There was no way to choose, and the failure text said "install MetaMask
 * or Rabby" to people who had a wallet installed already. RainbowKit resolves
 * wallets over EIP-6963, so each one appears by name and the user picks.
 */

import { useEffect, useState } from 'react';
import {
  ConnectButton,
  RainbowKitProvider,
  RainbowKitAuthenticationProvider,
  darkTheme,
  type AuthenticationStatus,
} from '@rainbow-me/rainbowkit';
import { WagmiProvider, createConfig, http } from 'wagmi';
import { injected } from '@wagmi/core';
import { hyperEvm } from 'viem/chains';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { buildAdapter, fetchMe, type User } from './auth';

/**
 * Wallet discovery is wagmi's, not RainbowKit's registry.
 *
 * The obvious way to build this list is `connectorsForWallets` with named
 * wallets from `@rainbow-me/rainbowkit/wallets`. Do not: that barrel re-exports
 * every wallet RainbowKit supports, and esbuild cannot tree-shake past the
 * dynamic icon imports inside it. Importing two names from it cost 4.3 MB,
 * because it drags in @metamask/sdk and the whole @reown/@walletconnect relay
 * stack whether or not any of it is reachable.
 *
 * wagmi discovers EIP-6963 wallets natively (`multiInjectedProviderDiscovery`,
 * on by default) and builds one connector per wallet that announces itself.
 * RainbowKit renders whatever connectors the config has, so the modal still
 * lists OKX, MetaMask, Rabby, Phantom — each by its own name and icon, supplied
 * by the wallet rather than by a bundled registry.
 *
 * What this gives up is phone wallets over a WalletConnect relay. That is a
 * deliberate loss: it costs a vendor, a project ID and a third party between
 * the user and their signer, on an operator console opened from a desktop.
 */

export function App() {
  const [status, setStatus] = useState<AuthenticationStatus>('loading');
  const [, setUser] = useState<User | null>(null);

  // One config per mount. `ssr` is off: this island only ever renders in a
  // browser, and setting it would suppress the EIP-6963 discovery that is the
  // entire reason for the change.
  const [{ wagmiConfig, queryClient }] = useState(() => {
    return {
      wagmiConfig: createConfig({
        chains: [hyperEvm],
        // The generic fallback, for a browser with exactly one wallet that
        // predates EIP-6963. Announced wallets are added by wagmi on top.
        connectors: [injected()],
        transports: { [hyperEvm.id]: http() },
      }),
      queryClient: new QueryClient({
        defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
      }),
    };
  });

  // The session is a cookie the server set, so the source of truth for "am I
  // signed in" is /auth/me, not anything wagmi knows.
  useEffect(() => {
    let live = true;
    fetchMe().then((me) => {
      if (!live) return;
      setUser(me);
      setStatus(me ? 'authenticated' : 'unauthenticated');
    });
    return () => {
      live = false;
    };
  }, []);

  const adapter = buildAdapter((me) => {
    setUser(me);
    setStatus('authenticated');
    // Panels that 401'd while signed out have to be refetched, and a reload is
    // both the honest and the cheapest way to do that in a multi-page app.
    window.location.reload();
  });

  const wrapped = {
    ...adapter,
    signOut: async () => {
      await adapter.signOut();
      setStatus('unauthenticated');
      setUser(null);
      window.location.reload();
    },
  };

  return (
    <WagmiProvider config={wagmiConfig}>
      <QueryClientProvider client={queryClient}>
        <RainbowKitAuthenticationProvider adapter={wrapped} status={status}>
          <RainbowKitProvider
            theme={darkTheme({
              accentColor: '#4fd1b8',      // --accent, so the modal is ours
              accentColorForeground: '#06100e',
              borderRadius: 'small',       // --radius is 3px; RainbowKit's
                                           // default pill looks foreign here
              overlayBlur: 'small',
            })}
            modalSize="compact"
            appInfo={{ appName: 'pathia' }}
          >
            <ConnectButton
              // The masthead is a single narrow row. A balance and a chain
              // pill would wrap it onto two lines on a laptop.
              showBalance={false}
              accountStatus="address"
              chainStatus="none"
              label="Connect wallet"
            />
          </RainbowKitProvider>
        </RainbowKitAuthenticationProvider>
      </QueryClientProvider>
    </WagmiProvider>
  );
}
