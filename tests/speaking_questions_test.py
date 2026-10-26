"""Splitting a Speaking question set (bot/utils/speaking_questions.py) -
offline, no OpenAI, Telegram or database:

    python tests/speaking_questions_test.py

The bug of 2026-10-26: six questions, the last "...young people or older
people? Why?", were asked as "Question 1/7" - the trailing "Why?" became a
question of its own.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.utils.speaking_questions import split_questions  # noqa: E402

fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


def main() -> int:
    six = ("Do you like travelling?\nWhere did you go on your last holiday?\nDo you prefer travelling alone or with "
           "friends?\nHow do you usually plan a trip?\nIs tourism important in your country?\nWho travels more, "
           "young people or older people? Why?")
    got = split_questions(six, "part1")
    check("six lines, the last ending '? Why?': six questions, not seven", len(got) == 6, got)
    check("...the 'Why?' stays with its question", got[-1].endswith("older people? Why?"), got[-1])
    check("spelling and punctuation are kept as written ('travelling')", got[0] == "Do you like travelling?", got[0])

    got = split_questions("Do you enjoy reading?\nWhy?\nWhat did you read last?", "part1")
    check("a 'Why?' on its own line joins the question above", got == ["Do you enjoy reading? Why?",
                                                                     "What did you read last?"], got)
    got = split_questions("Do you like your job?\nWhy or why not?", "part3")
    check("'Why or why not?' joins too", got == ["Do you like your job? Why or why not?"], got)
    got = split_questions("Should children learn to cook at school?\nExplain.", "part3")
    check("'Explain.' joins too", got == ["Should children learn to cook at school? Explain."], got)

    got = split_questions("Do you work or study? What do you like about it?", "part1")
    check("one line = one question (two '?' on one line stay together)", len(got) == 1, got)

    got = split_questions("1. Do you like music?\nWhat kind do you listen to most?\n2. Did you learn an instrument "
                          "as a child?\n3. Is music important in your culture?", "part1")
    check("numbered questions: the numbers split, an unnumbered line continues the one above", len(got) == 3
          and got[0] == "Do you like music? What kind do you listen to most?", got)
    got = split_questions("1. Where is your hometown? 2. What is it famous for? 3. Would you move away?", "part1")
    check("numbers typed on one line still split", got == ["Where is your hometown?", "What is it famous for?",
                                                            "Would you move away?"], got)
    got = split_questions("- Do you cook?\n- What do you cook most often?\n• Who taught you?", "part1")
    check("bullets split like numbers", len(got) == 3 and got[2] == "Who taught you?", got)
    got = split_questions("1) Describe your favourite room.\n2) Why do you like it?", "part1")
    check("a numbered item without '?' is still a question", got == ["Describe your favourite room.",
                                                                     "Why do you like it?"], got)

    got = split_questions("Let's talk about your hometown.\nWhere is your hometown?\nDo you like living there?",
                          "part1")
    check("a lead-in line goes with the question after it", got == ["Let's talk about your hometown.\nWhere is your "
                                                                    "hometown?", "Do you like living there?"], got)
    got = split_questions("Part 1 - Hometown\nAnswers here\n2 questions\nNEW\nWhere do you live?\nShow answer\n"
                          "Do you live in a house or a flat?", "part1")
    check("screenshot leftovers are dropped", len(got) == 2 and got[1] == "Do you live in a house or a flat?", got)
    got = split_questions("Are you an ambitious person?\nDo you think you are an ambitious person?\nWhy is it "
                          "important to be ambitious?", "part1")
    check("a repeated question is dropped, a 'why' question is not", len(got) == 2, got)
    check("no '?' anywhere: the whole text is one prompt", split_questions("Tell me about your family.", "part1")
          == ["Tell me about your family."])
    card = "Describe a place you visited.\nYou should say:\nwhere it is\nand explain why you liked it"
    check("Part 2: the cue card stays whole", split_questions(card, "part2") == [card])
    check("a long question that merely contains 'how' is not a follow-up",
          len(split_questions("Do you cook?\nHow often do you cook for your family?", "part1")) == 2)

    print("FAILURES:", fails or "none")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
