# Sample assignments

Two files to try the agent on before pointing it at real homework.

| File | Try it with | What it exercises |
| ---- | ----------- | ----------------- |
| `pset.txt` | `hw do examples/pset.txt -o examples/answers.md` | Six questions across calculus, kinematics and probability — differentiation, a problem the power rule doesn't solve, an integral, a three-significant-figures answer, an exact fraction, and a theorem to state and apply. |
| `worksheet.docx` | `hw fill examples/worksheet.docx -o examples/mine.docx` | Every placement the Word editor supports, and the cases that are easy to get wrong. |

## What `worksheet.docx` is built to stress

- **A1** — a plain question: the answer goes in a new paragraph underneath.
- **A2** — two blanks in one sentence, and the `________` is deliberately stored as
  several separate runs, the way Word actually writes one. Filling both takes two
  edits anchored on the question.
- **A3** — an `Answer:` label to replace rather than a blank.
- **Part B** — numbered questions. An answer here must come out as body text, or it
  becomes the next numbered question in the assignment.
- **Part C** — an empty table column to fill in cells.
- **Part D** — an indented extended-response question, so the answer should line up
  under it.
- The header line `Name: ______________________` exists to be a trap: anchoring on
  `________` alone is ambiguous, and the agent is told to anchor on the question
  instead.

## Putting them back

`hw fill` never destroys the original — the first edit copies it to
`worksheet.original.docx`. To reset the folder completely:

```bash
git checkout examples/ && rm -f examples/answers.md examples/mine.docx examples/*.original.docx
```
