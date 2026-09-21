# Callback evidence recipe

Start with the requested client and customer, the relevant time range, and the business question. Resolve the client entity, retrieve matching concluded call legs, then read the relevant transcript and optionally its recap. A customer name may differ between Dialpad and the CRM; ground the match in independent signals rather than fuzzy-name confidence.

Separate: what the customer asked; what the agent actually said/promised; what current operational records establish; and what remains unknown. Include record IDs and call date. Use verified source URLs when available; never fabricate a Dialpad UI deep link.

If comparing connected customer or operational records, retrieve those through their existing authorized tools. Dialpad by itself cannot prove job status, pricing, scheduling, fulfillment, CRM entry, or that an earlier scope matched the current scope. Retain timestamps so current records do not rewrite historical conversations.

An effective brief contains: customer need, latest relevant exchange, current operational status from its source, commitments, uncertainties, and next action. A draft is separate from sending. A transfer announcement does not prove connection or resolution. Voicemail content does not prove the customer heard it. Short ASR errors are not grounds to invent names or commitments.
