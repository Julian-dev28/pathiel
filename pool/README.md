# pool

This folder is the member list for pathiel's opt-in research pool. A member
is one Hyperliquid address plus a signed statement, added by a pull request.
pathiel reads each address's PUBLIC fills (Hyperliquid's info endpoint, read
only) and grades them with the same rules as the project's own trade review.
No one uploads anything.

What a member gets: their own graded report (fluke or edge on their trades,
with ranges), and pool-level stats on which desks and setups hold up across
members. There is no fee waiver here and no payout. Nothing is ever paid or
waived on returns.

Files:

- `members/<address>.json`: a signed join statement. One file per member.
- `leaves/<address>.json`: a signed leave statement, for a member who left.
- `house.json`: the maintainer's own record, unsigned, counted from a fixed
  date set before the pool existed.

See [docs/PRIVACY.md](../docs/PRIVACY.md) for what is public, what is not,
and how long results stay held back. The scoreboard reads as
<https://julian-dev28.github.io/pathiel/scoreboard/>.

## How to join

Run `/pathiel:share` in Claude Code. It shows you the exact statement you
would sign, asks for your key only after you say yes, and writes one JSON
file plus a link that opens a pull request with that file already filled in.
Open the link, check the file is the only change, and open the pull request.

A statement names the network, the account, the signer, the time and the
project, so it can be checked only against the join or leave it was made
for. It authorizes nothing else: no trade, no transfer, no fee.

## How to leave

Run `/pathiel:share leave`, or ask in an issue if you no longer hold the key.
Leaving writes a second signed file under `leaves/`; it never edits or
deletes the join file. Once that pull request merges, the next scoreboard
build drops the address and all of its graded rows.

## How to come back

A new join, signed after the leave. That is the only way back in. Deleting
or editing a leave file instead, so that an old join looks like it is still
in force, fails the pull request check (`reinstated`): the address would be
back in the pool from its old join time, with no new consent from its
owner. The same check refuses a leave file swapped for an older one, and a
member file restored from git history. Git keeps every file this folder
ever held, so none of that is actually hidden, only unsigned.

## The two checks

Every pull request against this repository runs two checks:

1. **Tests**: the whole offline test suite, including every file under
   `pool/` checked against the rules above.
2. **Pool members**: a check that runs on every pull request, read only
   (`permissions: contents: read`), and reports at once when nothing under
   `pool/` changed. When a pull request does touch `members/` or `leaves/`,
   it checks the statement's signature and freshness, confirms an API
   wallet signer is approved on the account right now, and confirms the
   change is a real way in or a real way out, not a replay of something the
   repository already saw.

A membership pull request changes exactly one or two files, both under
`pool/members/` or `pool/leaves/`, both naming the same address, and
nothing else. A pull request that touches any other file alongside a
membership file fails the check.

## For the maintainer: merge checklist

- If the pull request is from a first-time contributor, GitHub holds its
  workflow run until you approve it. Read the file list and the diff first:
  it should be one or two files under `pool/members/` or `pool/leaves/`.
- Confirm both checks are green on the current `main`.
- If the "Pool members" run is more than 24 hours old, update the branch or
  re-run the check. It reads the clock and asks Hyperliquid about agent
  approval at the moment it runs, not at the moment you click merge.
- Never use an admin bypass on a membership pull request. If the check is
  red, the fix is to ask the member to sign again, not to force the merge.
- Merge.
- Build the scoreboard when convenient (`python -m services.pool build`),
  and match every `POOL-NEW` line in its output to a pull request you
  merged. A `POOL-NEW` line with no matching pull request means an address
  got in some other way, such as a direct push to `main`.
