/**
 * The wallet island, as a React tree inside a server-rendered page.
 *
 * The pathiel dashboard is Jinja templates plus vendored vanilla JS, and
 * stays that way. This mounts into one `<div>` in the masthead and owns
 * exactly one thing: accounts — connecting a wallet, authorizing a trading
 * key, or adding an API-key / watch-only account. Every other panel on every
 * page is untouched vanilla, and talks to this island through the small
 * `window.PathielWallet` bridge in index.tsx and the `pathiel:account-
 * changed` DOM event (lib/api.ts, lib/bridge.ts).
 *
 * There is no sign-in step and no RainbowKitAuthenticationProvider here
 * anymore. contracts/accounts/models.py's own docstring says why: connecting
 * a Hyperliquid account IS the identity, so there is nothing separate to
 * authenticate — services/auth now derives the session from the active
 * account instead of its own SIWE exchange. (The previous version of this
 * file drove that SIWE flow; see git history / services/auth/siwe.py for
 * what it replaced.)
 *
 * Why RainbowKit's picker rather than a direct `window.ethereum` call: with
 * two wallet extensions installed, `window.ethereum` is whichever one won a
 * race at page load — no way to choose, and the failure text told people who
 * already had a wallet to "install MetaMask or Rabby". RainbowKit resolves
 * wallets over EIP-6963, so each one appears by name and the user picks.
 */

import { RainbowKitProvider, darkTheme } from '@rainbow-me/rainbowkit';
import { WagmiProvider, createConfig, http } from 'wagmi';
import { injected } from '@wagmi/core';
import { arbitrum, arbitrumSepolia, hyperEvm } from 'viem/chains';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';

import { WalletRoot } from './components/WalletRoot';

/**
 * Wallet discovery is wagmi's, not RainbowKit's registry — see
 * services/wallet_ui/README.md for the 4.3 MB mistake this avoids
 * (`connectorsForWallets` with named wallets from
 * `@rainbow-me/rainbowkit/wallets` drags in @metamask/sdk and the whole
 * @reown/@walletconnect relay stack). wagmi's own EIP-6963 discovery builds
 * one connector per announced wallet and RainbowKit renders whatever the
 * config has, which is the same picker for 1.3 MB less. No WalletConnect, so
 * no relay, no project ID, no third party between the user and their signer.
 *
 * Three chains, for two different reasons. `hyperEvm` is the chain this
 * product actually trades on. `arbitrum` / `arbitrumSepolia` are NOT traded
 * on here — they are what Hyperliquid's own ApproveAgent EIP-712 domain
 * signs against (contracts/accounts/fixtures/agent_prepare.json:
 * domain.chainId 42161), independent of which chain the perps run on. Both
 * have to be in wagmi's `chains` list or `useSwitchChain` has nothing to
 * switch the wallet to when the stepper asks for one of them.
 */
const chains = [hyperEvm, arbitrum, arbitrumSepolia] as const;

export function App() {
  // One config/client per mount, not per render. `ssr` is off: this island
  // only ever renders in a browser, and turning it on would suppress the
  // EIP-6963 discovery that is the entire reason wagmi owns the wallet list.
  const [{ wagmiConfig, queryClient }] = useState(() => ({
    wagmiConfig: createConfig({
      chains,
      // The generic fallback, for a browser with exactly one wallet that
      // predates EIP-6963. Announced wallets are added by wagmi on top.
      connectors: [injected()],
      transports: {
        [hyperEvm.id]: http(),
        [arbitrum.id]: http(),
        [arbitrumSepolia.id]: http(),
      },
    }),
    queryClient: new QueryClient({
      defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
    }),
  }));

  return (
    <WagmiProvider config={wagmiConfig}>
      <QueryClientProvider client={queryClient}>
        <RainbowKitProvider
          theme={darkTheme({
            accentColor: '#4fd1b8',      // --accent, so the modal is ours
            accentColorForeground: '#06100e',
            borderRadius: 'small',       // --radius is 3px; RainbowKit's
                                         // default pill looks foreign here
            overlayBlur: 'small',
          })}
          appInfo={{ appName: 'pathiel' }}
        >
          <WalletRoot />
        </RainbowKitProvider>
      </QueryClientProvider>
    </WagmiProvider>
  );
}
