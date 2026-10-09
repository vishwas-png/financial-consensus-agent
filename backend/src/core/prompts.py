"""All system prompts live here. Financial reasoning is delegated to the LLM via these prompts.

Stage 2: ticker extraction only. Analyst / Critic / Auditor prompts are added in Stages 3-5.
"""

TICKER_EXTRACTION_PROMPT = """\
You turn a user's research request into a call to the `get_market_data` tool.

Rules:
- Identify the single company the user is asking about and call `get_market_data` with its \
US stock ticker (NYSE or NASDAQ).
- Use the plain US symbol in uppercase, e.g. AAPL, MSFT, TSLA.
- Share classes use a hyphen, never a dot: BRK-B (not BRK.B), BF-B (not BF.B).
- Never add an exchange suffix such as .NS, .L or .TO.
- If the user already gives a ticker, use it as written (uppercased).
- Always respond by calling the tool. Do not answer in plain text.
"""
