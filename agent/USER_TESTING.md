# User testing the front desk (brief 5.5)

Show the front desk to **3-5 non-technical people** on a video call (10 minutes each). Their words,
written down, are our user-testing evidence. Change the wording where they stumble, then note what
changed.

## Script

1. Share the screen on the plan page (or run `python -m agent.intake` and `python -m agent.demo`).
2. Say only: *"This is a tool for island communities. Type a sentence about a place you know, or
   pretend you live on a small island that runs on diesel."* Don't explain anything else.
3. Let them answer the follow-up questions themselves.
4. Show the result and the plain-language summary. Then show `agent/out/checker_demo.html`.
5. Ask, and write down their exact words:
   - **"What do you think this product does?"**
   - **"What confused you?"**
   - "Was any question it asked you odd or hard to answer?"
   - "Do you believe the numbers? Why or why not?" (after the checker demo)
   - "Who would you show this to?"

Don't help, defend or correct while they're using it. Silence is data.

## Record

| # | Date | Who (role, not name) | Island / sentence they typed | "What does it do?" (their words) | "What confused you?" (their words) | What we changed |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | | | | | | |
| 2 | | | | | | |
| 3 | | | | | | |
| 4 | | | | | | |
| 5 | | | | | | |

## Pacific language: what worked, what didn't (record honestly)

- **Intake, rules mode (no API key):** English only. The Bislama test sentence (eval case 13) finds the
  island but not "60 haos" or "wan klinik"; the follow-up question gets the household count. Result:
  a correct plan, but the clinic is missed until the person ticks the box.
- **Intake, AI mode:** run `python -m agent.evaluate` with a key and record case 13 here.
- **Explanation in a Pacific language:** `explain(result, language="Samoan")` asks the model to write
  in that language with every number in digits, and the checker still checks every digit. It can't
  check numbers spelled out in Samoan words, and nobody on the team has verified the language. Show
  it to a speaker before using it on camera, and write down what they said.
- **The Bislama test sentence was written by an AI assistant and has not been checked by a speaker.**
