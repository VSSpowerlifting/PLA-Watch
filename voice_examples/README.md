# voice_examples/

This directory holds writing samples used as rhythm and tone references for The PLA Watch.

## How to use these examples

Voice examples are **rhythm references only**. They show how a paragraph builds from concrete detail to interpretation, how sentences vary in length, and how claims stay close to evidence.

They are **not phrase banks**. Do not:
- Quote sentences from them
- Reuse metaphors
- Copy sentence structures closely
- Imitate their most dramatic moments

The PLA Watch should sound like itself — not like a reprint.

## How the generator uses this directory

The weekly generator (`scripts/generate_pla_watch.py`) does **not** load voice examples into the Claude prompt. This constraint is enforced at the code level. The style guide (`style_guide.md` at the repo root) is the sole editorial authority passed to the model.

Voice examples are for human reference: to calibrate what "good rhythm" looks like when reviewing generated drafts, or to orient a new editor joining the project.

## Adding examples

Drop plain `.txt` files here. Name them descriptively: `foreign-policy-brief-example.txt`, `discipline-paragraph-example.txt`, etc. No special format required.

Do not add copyrighted text without permission. Paraphrased or original writing is preferred.
