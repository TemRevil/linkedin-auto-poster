"""
Humanizer module - removes AI writing patterns from text.
Based on: https://github.com/blader/humanizer
Implements the 29 AI-writing pattern detection and removal.
"""

import re
from typing import List, Tuple

# Openers to strip from the start of a line. Detection and removal both build
# their regex from this one list — they used to be written out separately, so
# "Certainly" and "Of course" were reported by detect_ai_patterns forever while
# humanize_text had no rule that removed them. Longest phrase first so the
# alternation consumes "That's a great point" rather than stopping at
# "That's a great" and leaving a dangling "point.".
SYCOPHANTIC_OPENERS = [
    "That's a great question",
    "That's a great point",
    "That's a great",
    "Great question",
    "Absolutely",
    "Certainly",
    "Of course",
]
_SYCO_ALT = "|".join(re.escape(o) for o in SYCOPHANTIC_OPENERS)
# The opener must close with punctuation to count. Without it, "Absolutely no
# regressions this week" reads as an ordinary sentence, not a preamble.
_SYCO_RE = re.compile(rf"^({_SYCO_ALT})\s*([.!?,]+)\s*",
                      re.MULTILINE | re.IGNORECASE)

# AI-isms to detect and fix
AI_PATTERNS: List[Tuple[str, str, str]] = [
    # (pattern_name, regex_or_keyword, fix_description)
    ("significance_inflation", r"\b(groundbreaking|revolutionary|game-changing|unprecedented|transformative|paradigm-shifting)\b", "Replace with specific factual claims"),
    ("vague_attribution", r"\b(experts say|many believe|it is widely known|studies show|research suggests)\b", "Name the specific source or remove"),
    ("promotional_language", r"\b(exciting|amazing|incredible|remarkable|stunning|impressive)\b", "Remove or replace with factual observation"),
    ("filler_transitions", r"\b(moreover|furthermore|additionally|in addition|it's worth noting|interestingly)\b", "Cut or replace with shorter connector"),
    ("em_dash_overuse", r"—", "Use period or comma instead"),
    ("colon_intro", r":\s*\n", "Rewrite as flowing sentence"),
    ("passive_voice", r"\b(is being|was being|has been|have been|will be|is considered|is regarded)\b", "Rewrite in active voice"),
    ("sycophantic_opener", _SYCO_RE.pattern, "Start with substance"),
    ("hedge_stacking", r"\b(it seems|perhaps|might|could potentially|it appears)\b", "Commit to the claim or remove"),
    ("list_dependency", r"^\s*[-•]\s", "Convert to flowing prose when possible"),
    ("bold_overuse", r"\*\*[^*]+\*\*", "Use bold sparingly, max 1-2 per post"),
    ("emoji_stuffing", r"[\U0001F600-\U0001F9FF\U00002600-\U000027BF]{2,}", "Max 1-2 emojis per post"),
    ("hashtag_spam", r"(#\w+\s*){6,}", "Limit to 3-5 relevant hashtags"),
    ("corporate_speak", r"\b(leverage|synergy|ecosystem|holistic|robust|scalable|streamline|optimize)\b", "Use plain language"),
    ("artificial_enthusiasm", r"[!]{2,}", "Max one exclamation per post"),
]

# Words/phrases that scream "AI wrote this"
DEAD_GIVEAWAYS = [
    "dive into", "delve into", "tapestry", "landscape", "in today's",
    "in the realm of", "it's important to note", "let's explore",
    "at the end of the day", "the bottom line is", "buckle up",
    "here's the thing", "let that sink in", "I'll be honest",
    "hot take", "unpopular opinion:", "thread 🧵",
]


def detect_ai_patterns(text: str) -> list[dict]:
    """Scan text for AI writing patterns. Returns list of findings."""
    findings = []
    for name, pattern, fix in AI_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
        if matches:
            findings.append({
                "pattern": name,
                "matches": matches[:3],  # Show up to 3 examples
                "fix": fix,
                "count": len(matches)
            })

    for phrase in DEAD_GIVEAWAYS:
        if phrase.lower() in text.lower():
            findings.append({
                "pattern": "dead_giveaway",
                "matches": [phrase],
                "fix": "Remove or rephrase entirely",
                "count": 1
            })

    return findings


def _reduce_em_dashes(text: str) -> str:
    """Keep the first em-dash, replace each later one with ". " so a tight dash
    like "context—aware" becomes "context. aware" rather than the run-together
    "context.aware". No-op when there are 0 or 1 em-dashes. Any double space
    created around an already-spaced em-dash is normalized by humanize_text's
    later `re.sub(r"  +", " ", ...)`. (audit-7 3.A)"""
    if text.count("—") <= 1:
        return text
    first_found = False
    out = []
    for ch in text:
        if ch == "—":
            if not first_found:
                out.append(ch)
                first_found = True
            else:
                out.append(". ")
        else:
            out.append(ch)
    return "".join(out)


def humanize_text(text: str) -> str:
    """Apply humanization rules to clean AI-isms from text."""
    result = text

    # Replace significance inflation with milder alternatives
    replacements = {
        r"\bgroundbreaking\b": "notable",
        r"\brevolutionary\b": "new",
        r"\bgame-changing\b": "significant",
        r"\bunprecedented\b": "unusual",
        r"\btransformative\b": "major",
        r"\bparadigm-shifting\b": "important",
    }
    for pattern, replacement in replacements.items():
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    # Reduce em dashes
    result = _reduce_em_dashes(result)

    # Remove double+ exclamation marks
    result = re.sub(r"!{2,}", "!", result)

    # Remove emoji clusters (keep singles)
    result = re.sub(r"([\U0001F600-\U0001F9FF\U00002600-\U000027BF])\s*[\U0001F600-\U0001F9FF\U00002600-\U000027BF]+", r"\1", result)

    # Remove filler transitions at start of sentences
    filler_starts = ["Moreover, ", "Furthermore, ", "Additionally, ", "In addition, ", "It's worth noting that ", "Interestingly, "]
    for filler in filler_starts:
        result = result.replace(filler, "")

    # Remove sycophantic openers, plus the punctuation and space that follow.
    result = _SYCO_RE.sub("", result)

    # Clean up double spaces and empty lines
    result = re.sub(r"  +", " ", result)
    result = re.sub(r"\n{3,}", "\n\n", result)

    return result.strip()


def get_humanization_report(text: str) -> str:
    """Generate a report on AI patterns found and what was fixed."""
    findings = detect_ai_patterns(text)
    if not findings:
        return "Clean - no obvious AI patterns detected."

    report = f"Found {len(findings)} AI pattern(s):\n"
    for f in findings:
        report += f"  - {f['pattern']}: {f['count']}x (e.g. '{f['matches'][0]}')\n"
        report += f"    Fix: {f['fix']}\n"
    return report


if __name__ == "__main__":
    # Test
    sample = """
    Groundbreaking AI breakthrough! 🚀🔥💡

    Moreover, this revolutionary technology is transformative in today's landscape.
    It's worth noting that experts say this is unprecedented — a game-changing
    paradigm-shifting development — that will leverage synergies across the ecosystem.

    Let's dive into what this means for you!! Here's the thing — buckle up!!

    #AI #MachineLearning #DeepLearning #Tech #Innovation #Future #Startup #Growth
    """

    print("=== ORIGINAL ===")
    print(sample)
    print("\n=== REPORT ===")
    print(get_humanization_report(sample))
    print("\n=== HUMANIZED ===")
    print(humanize_text(sample))
