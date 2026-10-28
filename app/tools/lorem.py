"""Lorem Ipsum generator (placeholder text; randomness need not be cryptographic)."""
import random

from ..registry import Action, Field, Result, Tool, clamp_int, register

WORDS = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore et dolore "
    "magna aliqua enim ad minim veniam quis nostrud exercitation ullamco laboris nisi aliquip ex ea commodo "
    "consequat duis aute irure in reprehenderit voluptate velit esse cillum eu fugiat nulla pariatur excepteur "
    "sint occaecat cupidatat non proident sunt culpa qui officia deserunt mollit anim id est laborum "
    "curabitur pretium tincidunt lacus nunc gravida purus vitae mauris sapien vestibulum faucibus integer "
    "feugiat scelerisque varius morbi leo urna molestie at elementum eu facilisis sem porta vulputate"
).split()
CLASSIC_START = "Lorem ipsum dolor sit amet, consectetur adipiscing elit"
LIMITS = {"paragraphs": 50, "sentences": 200, "words": 2000}


def _sentence(rng: random.Random, classic: bool = False) -> str:
    n = rng.randint(8, 16)
    if classic:
        words = CLASSIC_START.lower().replace(",", "").split()
        words += [rng.choice(WORDS) for _ in range(max(0, n - len(words)))]
    else:
        words = [rng.choice(WORDS) for _ in range(n)]
    if len(words) > 6 and not classic:
        words[rng.randint(3, len(words) - 3)] += ","
    text = " ".join(words)
    if classic:
        text = text.replace("ipsum dolor sit amet", "ipsum dolor sit amet,", 1)
    return text[0].upper() + text[1:] + "."


def generate(kind: str, count: int, classic: bool, rng: random.Random | None = None) -> str:
    rng = rng or random.Random()  # nosec B311 - placeholder text, not security-sensitive
    if kind == "words":
        words = list(WORDS[:count]) if classic and count <= len(WORDS) else [rng.choice(WORDS) for _ in range(count)]
        if classic and count > len(WORDS):
            words[: len(WORDS)] = WORDS
        return " ".join(words)
    if kind == "sentences":
        return " ".join(_sentence(rng, classic and i == 0) for i in range(count))
    paras = []
    for p in range(count):
        sents = [_sentence(rng, classic and p == 0 and i == 0) for i in range(rng.randint(4, 7))]
        paras.append(" ".join(sents))
    return "\n\n".join(paras)


def handler(action, form, files):
    kind = form.get("kind", "paragraphs")
    if kind not in LIMITS:
        kind = "paragraphs"
    count = clamp_int(form.get("count", "3"), 1, LIMITS[kind], "Amount")
    return Result(generate(kind, count, form.get("classic") == "on"))


register(Tool(
    slug="lorem-ipsum", title="Lorem Ipsum",
    description="Generate placeholder text as paragraphs, sentences or words.",
    fields=(
        Field("kind", "Unit", "select", (("paragraphs", "Paragraphs"), ("sentences", "Sentences"), ("words", "Words")), "paragraphs"),
        Field("count", "Amount", "number", default="3", lo=1, hi=2000),
        Field("classic", "Start with “Lorem ipsum dolor sit amet…”", "checkbox", default="on"),
    ),
    actions=(Action("generate", "Generate", True),),
    handler=handler, group="Generators",
))
