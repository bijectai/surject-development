SURJECT — Design & Hopes
=========================

What it is:
A one-command, lightweight agent monitor that catches when an agent does
something its user didn't intend. No dashboard. No login. No subscription.
pip install, run, forget. It only speaks when something is wrong.

Why it exists:
Agents are probabilistic. They occasionally do things no one asked for —
call a tool with the wrong parameter, delete a file, access something they
shouldn't, exceed a rate limit. Most of these go unnoticed because there's
nothing watching. Surject watches. It attaches to the agent's tool calls,
runs them against a simple user-defined policy, and flags anything outside
the bounds.

The thesis:
People don't know they need guardrails until an agent does something dumb
in front of them. Surject is how they discover the problem — by
experiencing it with their own agent, on their own machine, for free. The
moment Surject catches something, the user converts from "I don't have
this problem" to "I never want this problem again."

Who it's for:
- Researchers running agents on sensitive data (lab experiments, clinical
  data, proprietary code)
- Devs experimenting with agent workflows who want a safety net
- Anyone who has used an agent and wondered "did it do what I asked?"

The experience:
1. pip install biject-surject
2. surject init          — creates a default policy file
3. surject watch         — attaches to the agent's runtime
4. Agent runs normally. Surject is silent.
5. Agent calls a tool with params outside policy → Surject surfaces the
   call, the params, the violated rule, and the verdict.
6. User sees it. User understands it. User has a story to tell.

That's the whole loop. No accounts, no telemetry, no friction.

The policy model:
Start with simple parameter bounds, expected tool names, and rate limits.
YAML file in the project root. Editable. Commented. Extensible.
The philosophy: policies should be as easy to write as the agent itself
is to prompt. If writing a policy is harder than writing the agent,
nobody will do it.

The architecture (high-level):
- A lightweight proxy layer that intercepts agent tool calls
- A policy checker (initially Python, eventually the Lean kernel as an
  upgrade path)
- A local reporter that formats and surfaces violations
- No network calls. No data leaves the machine unless the user shares a
  report.

The upgrade path:
Surject ships with a Python reference checker by default — zero
infrastructure, instant use. The full biject stack (Lean kernel, signed
ledger, audit chain) is available as an opt-in upgrade for users who need
cryptographic evidence of enforcement. The upgrade doesn't change the
workflow — same policies, same interface, stronger guarantees.

Growth model:
Surject grows through stories, not ads. Every blocked tool call produces
a one-page report: what the agent tried to do, what policy blocked it,
the timestamp, the kernel verdict. The user can share this as a link or a
snippet. It requires zero context — anyone who uses agents understands
"my agent tried to X and got caught."

These reports are the marketing. A researcher posts on HN: "my agent
tried to delete our experiment results. Surject caught it." A dev on
Reddit: "didn't think I needed guardrails until Surject caught my agent
calling the production API with test data." Each post is a seed. Each
seed grows into someone else's "I should try this."

Channels:
- Academic labs connected through existing relationships (Dr. Shah's
  network, EON connections, CS departments)
- Open-source agent framework communities (Autogen, LangGraph, CrewAI)
  — one-page integration guide, surfaced through framework docs
- Technical communities (HN, r/MachineLearning, r/LocalLLaMA) — story
  posts, not launch posts
- The pipeline itself — every user who shares a report generates the
  next user

Success metrics:
- Active installs (surject init run)
- Reports generated (blocks caught)
- Reports shared (blocks that became stories)
- Upgrade conversions (Python checker → Lean kernel)
- Warm inbound from someone who saw a shared report

What success looks like:
A grad student at a bio lab installs Surject out of curiosity. On day
three, Surject catches their analysis agent trying to write over a raw
dataset. The student shares the report with their lab. The lab adopts
it. The PI mentions it at a conference. Someone from pharma compliance
hears and reaches out.

That's the funnel. Not a billboard. A chain of one-to-one stories,
each one earned by catching something real.

The hope:
Surject proves that the biject stack works not because we say so, but
because people use it and find value in it. It generates the data,
stories, and relationships that make everything else — pilots,
investment, adoption — a natural next step rather than a cold ask.