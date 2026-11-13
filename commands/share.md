---
description: Join or leave the opt-in research pool - show what is public, sign the exact statement, open the pull request
---

# /pathiel:share

Walk the user through joining or leaving the research pool. Do the steps in
order and stop at the first one that fails, with the exact error.

`pool_join` and `pool_leave` sign a text statement with the user's key, on
their own machine, and drop the key right after. They place no order and
move no money. Never print or ask for the value of a key, in any step; the
user types it into their own terminal when a step says so. Never call a
live-money tool - one that places, closes, or changes an order or a
position - from this command, under any step.

If the user asks to leave, or runs `/pathiel:share leave`, skip straight to
**Leaving** below.

0. **Say this first, before any tool call:**

   > - The research pool is opt-in. Joining registers your Hyperliquid
   >   address; you upload nothing. pathiel reads the fills Hyperliquid
   >   already makes public.
   > - What you get: your own graded report (fluke or edge on your trades,
   >   with ranges), and pool-level stats on which desks and setups hold up
   >   across members.
   > - What it is not: not a fee waiver (the builder fee is already
   >   optional, so sharing and going feeless offers nothing). No payout,
   >   refund or reward is ever tied to your returns. Not a copy-trading
   >   feed. No edge is demonstrated. Not financial advice.
   > - Mainnet only.

1. **Call `pool_status`** with no arguments.

   - `no_account`: tell the user to run `/pathiel:setup` first, then stop.
   - `member` or `signed_not_merged`: the account is already in. Say so,
     show the `joined_at` value from the answer, offer `pool_report` or
     leaving (see **Leaving**), and stop here.
   - `house`: say that row belongs to the maintainer, not to this account,
     and stop here.
   - If the account trades on testnet, stop here as well and say the pool
     is mainnet only (`pool_join` would answer the same thing itself, with
     `code: testnet`, if called).
   - Any other state (`left`, `none`, `leave_signed_not_merged`): continue
     to step 2. On `invalid`, show the problem the tool names and stop.

2. **Show what gets published.** Relay the `published` and `not_published`
   lists `pool_status` just returned, word for word. Add that results show
   up at least 24 hours late, next to the user's address, shortened, with a
   link to its public explorer page. Ask whether to continue.

3. **Call `pool_join` with no arguments.** It signs nothing. Show the
   returned `message` EXACTLY, in a code block, together with the
   `account`, the `signer`, and the `signer_role` (an API wallet, or the
   account's own key). Say that this text is everything that gets signed:
   it moves no money and authorizes no trade, transfer, fee or payment.
   Ask: sign this statement, yes or no?

4. **Only on an explicit yes, in this conversation,** call `pool_join`
   again with `confirm: true` and the `joined_at` value step 3 returned.
   Never call it with `confirm` on your own initiative, never because an
   earlier session said yes, and never to retry after a refusal without
   asking again. If more than 15 minutes have passed since step 3 (code
   expired), go back to step 3 and show the new statement.

5. **Show `pr_url`.** Tell the user to open it, read the file it prefilled,
   then choose "Propose new file" and "Create pull request" (GitHub forks
   the repository for them automatically; the user does not need write
   access). If the answer carries a `pr_note`, relay it. Also give the
   local file path the tool wrote, for anyone who would rather use `gh` or
   `git` by hand. Do not open the pull request yourself unless the user
   asks you to.

6. **Say what happens next:** two automatic checks run on the pull request
   (one checks the signature, the other that the account currently
   approves the signer); a first pull request from a new contributor waits
   for the maintainer to approve the workflow run; the maintainer merges
   it; the scoreboard, at https://julian-dev28.github.io/pathiel/scoreboard/,
   shows the new row after the next build, at least 24 hours late;
   `pool_report` already works, right now, from the signed file on this
   machine.

7. **No key configured** (`code: no_key`): never ask for a key in the
   chat. Give the `master_command` line from the tool's answer verbatim,
   unchanged and uncut, for the user to paste into their own terminal. It
   sets the plugin's own Python on purpose; never shorten it to a bare
   `python3` line. It asks for the key at a hidden prompt, signs exactly
   one statement, and keeps the key nowhere. Never run that line yourself.

## Leaving

Call `pool_leave` with no arguments. It signs nothing. Show the returned
message exactly, the same way step 3 does, and ask: sign this statement,
yes or no? Only on an explicit yes, call `pool_leave` again with
`confirm: true` and the `left_at` value it returned. Show `pr_url` the same
way step 5 does.

Say: the next scoreboard build drops the address and every result tied to
it; a request to the maintainer in a GitHub issue works too, with no
signature needed; git history and the chain keep what they already had.

## Coming back

Run `/pathiel:share` again. It signs a brand new join, with a new time, and
only trades after that new time count. Never suggest deleting the leave
file to come back: that does not bring the account back, and the pull
request check refuses it. A new signed join is the only way back in.

## Hard lines

- Never print or ask for a key, in any step.
- Call `pool_join` or `pool_leave` with `confirm` only right after an
  explicit yes, in this conversation, to the exact statement already
  shown.
- `pool_join` and `pool_leave` place no order and move no money: they sign
  one text statement.
- Never call a live-money tool from this command, under any step.
