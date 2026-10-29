---
name: using-alcf-croissants-mini
description: Compact card-usage guide for small models. Read the dataset's
  documentation card and answer from it.
---

# Use the documentation card

You have documentation.txt: the card(s) describing the dataset(s) in the
question. The card is the authority. Rules:

1. Open and read documentation.txt FIRST, before doing anything else.
2. If the card covers the question, answer from the card and stop:
   column meanings, units, coded values, what a counter measures, which
   columns are broken/missing, and how tables join. Use the card's exact
   column names.
3. Look up what the question names (e.g. "memory", "ECC") by searching the
   card's field list. Never say something is absent before you have
   searched the card for it.
4. Trust the card over your priors about HPC systems. If the card calls a
   column broken, unused, or a constant sentinel, believe the card.
5. State a number ONLY if the card states it or you measured it. Never
   guess, round, or invent a number. If you cannot measure it, say so.
6. Join tables only on keys the card names. If it names none, answer that
   the join is undocumented.
7. Finish your answer by saying which facts came from the card and which
   you measured.
