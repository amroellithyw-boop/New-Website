# AI providers: what runs where, and why it is cheap

ForgeOS treats models the way it treats ledgers: as replaceable components
behind one contract. This document is the operating guide for that layer.

## The honest premise

No single model is the product. The product is the deterministic engine, the
evidence contract, the review hierarchy and the accumulated client knowledge.
Models do three things inside that: interpret evidence, challenge conclusions,
and draft communications. The gateway's job is to buy exactly the amount of
intelligence each of those needs, from whichever vendor is cheapest that day,
without ever letting a model add up money.

## Two adapters cover the whole market

| Adapter | Reaches | How |
| --- | --- | --- |
| Anthropic | Claude Haiku 4.5, Sonnet 5, Opus 5, Fable 5.1 | Official SDK, native structured outputs, prompt caching, refusal fallbacks |
| OpenAI-compatible | OpenAI, xAI Grok, Google Gemini, Groq, OpenRouter, Together, Fireworks, Mistral, DeepSeek, Ollama, vLLM, LM Studio | One adapter; every one of these speaks the same chat-completions wire format |

Open-weight models arrive through the second adapter. Nous Research's Hermes,
Meta's Llama, Qwen, DeepSeek and OpenAI's gpt-oss all run either hosted (Groq,
OpenRouter, Together) or locally (Ollama, vLLM) with no code change. A tool that
exposes an OpenAI-compatible endpoint, which is nearly all of them now, plugs in
as a base URL.

Enable a provider by setting its key. Nothing else changes.

| Provider | Environment variable |
| --- | --- |
| Anthropic | `ANTHROPIC_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| xAI (Grok) | `XAI_API_KEY` |
| Google Gemini | `GEMINI_API_KEY` |
| Groq | `GROQ_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY` |
| Together | `TOGETHER_API_KEY` |
| Fireworks | `FIREWORKS_API_KEY` |
| Mistral | `MISTRAL_API_KEY` |
| DeepSeek | `DEEPSEEK_API_KEY` |
| Ollama (local, free) | `OLLAMA_HOST`, e.g. `http://localhost:11434` |
| vLLM (local, free) | `VLLM_BASE_URL` |

Run `forge providers` to see what is enabled and how routing will use it.

## How routing decides

Each risk tier has a minimum model quality from one to five. For every call the
gateway takes the cheapest available model at or above that bar which has the
capabilities the call needs.

| Tier | Typical work | Minimum quality | What usually runs |
| --- | --- | --- | --- |
| R1 | Routine coding, extraction, batch classification | 2 | A free local model, or Haiku, Grok 4 Fast, gpt-4o-mini |
| R2 | Manager review, variance narratives | 3 | Sonnet 5, Gemini Flash, Hermes, gpt-oss |
| R3 | Controller and specialist review | 4 | Opus 5, GPT-5, Grok 4, DeepSeek R1 |
| R4 | Adversary, VP, CFO synthesis | 4 | Same, on different families |

Three rules sit on top of that:

**A local model wins when it clears the bar.** With Ollama running, receipt
extraction, vendor classification and routine first passes cost nothing and no
client data leaves the machine. Turn this off with `prefer_local=False` when
throughput matters more than privacy.

**Adversaries and executives always get a strong model.** Whatever the tier.
The job of the adversary is to break a conclusion, and that is the hardest
reasoning in the system.

**Frontier models are off by default.** Quality-five models such as Fable 5.1
cost roughly twice Opus and are enabled explicitly with `allow_frontier=True`
for the rare R4 synthesis where it changes the answer.

## Independence is what makes multiple providers worth having

Two instances of one model fail the same way, and a correlated failure looks
exactly like agreement. For R3 and above, every reviewer in a chain is routed to
a family not yet used in that chain. With Claude, GPT and Grok configured, a
preparer, a controller and an adversary each run on a different vendor. With one
family configured, the system records `independence_unavailable` on the work
item rather than implying an independence it does not have.

This is the single biggest reason to hold two keys. The second key does not
need to be expensive; a cheap independent reviewer that catches a correlated
error is worth more than a dear one from the same family.

## Cascade: pay for judgement only when it is needed

With `cascade=True` a call runs the cheapest capable model first. If the
structured result reports confidence below the floor or lists missing evidence,
the same call is escalated one quality level. Most routine items never reach the
expensive model, and the escalation is recorded on the call so the cost of a
cascade that saved nothing is visible.

The document pipeline's accounting pass uses this by default.

## Fallback: a provider outage is not an incident

A retryable provider error moves the call to the next model in the chain, across
vendors. A refusal moves it to another family. Only when every candidate fails
does the gateway raise, and it raises with the reasons.

## Budget: no silent overspend

Every gateway carries a budget of cost and call ceilings. Breaching either raises
`BudgetExceeded`. Set one per run or per work item; the demonstration commands
run unbounded because they use the offline stub.

## What it costs

Illustrative, on the seeded demonstration company, one month, review hierarchy
on for material items only:

| Configuration | Model calls | Cost |
| --- | --- | --- |
| Offline stub (CI, tests, demos) | 21 | $0 |
| Claude only, Haiku for R1, Opus for R3 and R4 | 21 | around $0.40 |
| Claude plus Grok plus Ollama, independence on | 21 | around $0.25 |

The cheapest configuration is also the most rigorous one, because independence
came free with the second vendor and extraction moved to a local model. That is
the design working as intended.

## Prices are data

`forge/agents/catalogue.py` holds every model with its quality, capabilities and
list price. Prices drift monthly. Override them from configuration in a
deployment; the values in the file exist so that cost telemetry is exercised
rather than left as a placeholder.

## Two names from the request that need a note

**Hermes Agent** refers to Nous Research's Hermes models, which are open-weight.
They are in the catalogue via OpenRouter and Ollama and route like any other
model.

**OpenClaw** is not a provider or model I can identify. If it exposes an
OpenAI-compatible endpoint, add a row to the catalogue with its base URL and it
will route immediately. If it is something else, describe what it does and I
will assess whether it belongs in the stack.
