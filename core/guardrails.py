"""Safety guardrails enforced in code, not just in the prompt.

1. Emergency detection runs on every caller utterance *before* the LLM. A match
   produces a fixed 911 response — the LLM is not asked, so it can't get it wrong.
2. Clinical questions are flagged so the LLM is reminded not to answer them.
3. Every LLM reply passes through `ReplyFilter`, which drops anything that looks
   like medical advice and substitutes a safe redirect.

Detection deliberately favours false positives: telling someone with no
emergency to call 911 is a minor annoyance; missing a real one is not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

EMERGENCY_MESSAGE = (
    "This sounds like it could be a medical emergency. Please hang up now and call 9-1-1. "
    "If you can't call yourself, stay on the line and I'll connect you to the clinic."
)
CRISIS_MESSAGE = (
    "I'm really sorry you're going through this. If you're in immediate danger, please hang up and call 9-1-1. "
    "You can also call or text 9-8-8, the Suicide Crisis Helpline, any time, day or night."
)
# FULL RECEPTIONIST version (offers booking and transfer). Restore it with the full flow in core/agent.py.
# SAFE_REDIRECT = (
#     "I'm not able to give medical advice. I can take a message for the clinical team, book you an appointment "
#     "with a doctor, or transfer you to a staff member. What would you prefer?"
# )
# Intake flow: the agent only takes details, and the front desk calls every caller back.
SAFE_REDIRECT = (
    "I'm not able to give medical advice, but I'll make sure someone from the clinic calls you back about it."
)

_I = re.IGNORECASE
_WHO = r"(?:my|his|her|their|the)"

EMERGENCY_PATTERNS: tuple[re.Pattern, ...] = tuple(re.compile(p, _I) for p in (
    # cardiac
    r"\bchest\s+(?:pain|pains|pressure|tightness|is\s+(?:tight|hurting))",
    r"\b(?:pain|pressure|tightness|squeezing)\s+in\s+" + _WHO + r"\s+chest",
    r"\bchest\s+hurts?\b",
    r"\bheart\s+attack",
    r"\bcardiac\s+arrest",
    # breathing
    r"\b(?:can'?t|cannot|can\s+not|could\s*n'?t|unable\s+to|struggling\s+to|hard\s+to)\s+(?:breathe|breath|catch\s+\w+\s+breath)",
    r"\b(?:trouble|difficulty|problems?)\s+breathing",
    r"\b(?:not|stopped|isn'?t|wasn'?t)\s+breathing",
    r"\bshort(?:ness)?\s+of\s+breath",
    r"\bturning\s+blue\b",
    r"\bchok(?:e|ing)\b",
    # consciousness / neuro
    r"\bunconscious\b", r"\bunresponsive\b", r"\bpassed\s+out\b", r"\bfainted\b", r"\bcollapsed\b",
    r"\b(?:won'?t|will\s+not|can'?t|isn'?t)\s+wak(?:e|ing)\s+(?:up|him|her|them)",
    r"\bstroke\b", r"\bface\s+(?:is\s+)?droop", r"\bslurr(?:ed|ing)\s+(?:speech|words|\w+\s+words)",
    r"\b(?:can'?t|cannot)\s+move\s+" + _WHO + r"\s+(?:arm|leg|side|face)",
    r"\bnumb(?:ness)?\s+(?:on|down)\s+one\s+side",
    r"\bseizure", r"\bconvuls", r"\bworst\s+headache",
    # bleeding / trauma
    r"\bbleeding\s+(?:really\s+|very\s+|so\s+)?(?:heavily|badly|a\s+lot|won'?t\s+stop|that\s+won'?t\s+stop|everywhere|profusely)",
    r"\b(?:lots|a\s+lot|so\s+much|too\s+much)\s+(?:of\s+)?blood\b",
    r"\b(?:won'?t|can'?t|cannot|couldn'?t)\s+(?:get\s+(?:it|the\s+bleeding)\s+to\s+)?stop\s+(?:the\s+)?bleeding",
    r"\bcoughing\s+(?:up\s+)?blood",
    r"\bvomiting\s+blood", r"\bhit\s+by\s+a\s+(?:car|truck|vehicle)", r"\bhead\s+injury",
    # allergy / poisoning
    r"\banaphyla", r"\bthroat\s+(?:is\s+)?(?:closing|swelling|swollen)", r"\b(?:tongue|lips?)\s+(?:is\s+|are\s+)?swell",
    r"\b(?:severe|bad|serious)\s+allergic\s+reaction", r"\bepi-?pen\b",
    r"\boverdos", r"\b(?:took|taken|swallowed|had)\s+(?:way\s+|far\s+|a\s+lot\s+|much\s+)?too\s+(?:many|much)\s+(?:of\s+(?:my|his|her|the)\s+)?(?:pills?|tablets?|meds|medications?|medicine|insulin|tylenol|advil|sleeping)",
    r"\b(?:took|taken|swallowed)\s+(?:a\s+(?:whole|full)\s+)?(?:bottle|handful|pack)\s+of\b", r"\btook\s+too\s+many\b", r"\bpoison", r"\b(?:swallowed|ate|drank)\s+(?:a\s+|an\s+|some\s+)?(?:bleach|poison|chemicals?|cleaner|(?:button\s+)?batter(?:y|ies)|magnets?)",
    # pregnancy
    r"\bwater\s+(?:just\s+)?broke", r"\bin\s+labou?r\b", r"\bbleeding\b.*\bpregnan|\bpregnan.*\bbleeding\b",
    # explicit
    r"\b(?:it'?s|this\s+is)\s+an\s+emergency\b", r"\bmedical\s+emergency\b", r"\bneed\s+an\s+ambulance\b",
    r"\bcall\s+(?:an\s+)?ambulance\b",
))

CRISIS_PATTERNS: tuple[re.Pattern, ...] = tuple(re.compile(p, _I) for p in (
    r"\bkill\s+(?:my|him|her)self\b", r"\bsuicid", r"\bend\s+(?:my|his|her)\s+life\b", r"\bwant\s+to\s+die\b",
    r"\bdon'?t\s+want\s+to\s+(?:live|be\s+alive)", r"\b(?:hurt|harm)(?:ing)?\s+myself\b", r"\bself[-\s]?harm",
    r"\bbetter\s+off\s+dead\b",
))

# Questions that ask for clinical judgement. The LLM must not answer these.
CLINICAL_QUESTION_PATTERNS: tuple[re.Pattern, ...] = tuple(re.compile(p, _I) for p in (
    r"\bshould\s+i\s+(?:take|stop|keep|be\s+(?:worried|concerned)|go\s+to|double|skip|increase|reduce)",
    r"\bwhat\s+(?:dose|dosage)", r"\bhow\s+(?:much|many)\b.*\b(?:take|give|mg|pills?|tablets?)",
    r"\bis\s+(?:it|this|that)\s+(?:normal|safe|serious|bad|dangerous|okay\s+to)",
    r"\bdo\s+(?:i|you\s+think\s+i)\s+(?:have|need)\b(?!.*\b(?:bring|book|appointment|card|form))",
    r"\bcould\s+(?:it|this)\s+be\b", r"\bwhat\s+(?:does|could)\b.*\bmean\b",
    r"\bside\s+effects?\b", r"\binteract(?:ion)?s?\b", r"\bdiagnos", r"\bwhat\s+(?:medicine|medication|antibiotic)",
    r"\bcan\s+i\s+(?:take|mix|drink)\b",
))

# Reply patterns that indicate the LLM is giving medical advice. Kept specific to
# medication, dosing, diagnosis and reassurance so ordinary receptionist lines
# ("you can use the elevator", "I'd suggest an extended appointment") pass.
_DRUGS = (r"(?:ibuprofen|advil|motrin|tylenol|acetaminophen|paracetamol|aspirin|naproxen|aleve|benadryl|"
          r"antihistamines?|antibiotics?|amoxicillin|painkillers?|pain\s+relievers?|cough\s+syrup|"
          r"decongestants?|melatonin|steroids?|prednisone|insulin|nitro(?:glycerin)?)")
_MED_NOUN = (r"(?:medications?|medicines?|meds|pills?|tablets?|doses?|dosage|prescriptions?|antibiotics?|"
             r"inhalers?|insulin|puffer)")
_CONDITION = (r"(?:an?\s+)?(?:infection|virus|viral|the\s+flu|flu|cold|covid|strep|pneumonia|bronchitis|allergy|"
              r"allergies|migraine|sprain|strain|fracture|uti|concussion|anxiety|depression|condition)")
ADVICE_PATTERNS: tuple[re.Pattern, ...] = tuple(re.compile(p, _I) for p in (
    r"\b\d+(?:\.\d+)?\s?(?:mg|milligrams?|mcg|micrograms?|ml|millilit(?:er|re)s?|cc|units?)\b",
    r"\b(?:take|try|use|give|have)\s+(?:some\s+|an?\s+|two\s+|one\s+)?" + _DRUGS + r"\b",
    r"\b(?:take|stop|skip|double|increase|reduce|lower|change|continue)\s+(?:taking\s+)?"
    r"(?:your|the|this|that|a|an|some|more|less)?\s*(?:\w+\s+)?" + _MED_NOUN + r"\b",
    r"\byou\s+(?:should|might\s+want\s+to|may\s+want\s+to|need\s+to|ought\s+to)\s+"
    r"(?:rest|ice|elevate|gargle|apply|avoid\s+(?:eating|drinking|exercise)|drink\s+(?:lots|plenty|more))\b",
    r"\b(?:i|i'd|i\s+would)\s+(?:recommend|suggest|advise)\s+(?:that\s+you\s+)?(?:you\s+)?"
    r"(?:take|taking|stop|stopping|use|using|try|trying|rest|resting|apply|applying|avoid|avoiding|drink|drinking)\b",
    r"\byou\s+(?:probably|likely|might|may|could)?\s*(?:have|have\s+got)\s+" + _CONDITION + r"\b",
    r"\b(?:sounds|seems|looks)\s+like\s+(?:you\s+(?:have|might\s+have|may\s+have|could\s+have)\s+)?"
    + _CONDITION + r"\b",
    r"\b(?:that'?s|that\s+is|it'?s|it\s+is|this\s+is|sounds)\s+(?:probably\s+|perfectly\s+|completely\s+)?"
    r"(?:normal|nothing\s+to\s+worry\s+about|not\s+serious|not\s+dangerous|harmless|a\s+common\s+side\s+effect)\b",
    r"\b(?:is|are|it'?s|should\s+be)\s+(?:safe|fine|okay|ok)\s+to\s+(?:take|mix|use|drink|combine)\b",
    r"\b(?:your|the)\s+diagnosis\s+is\b",
))


@dataclass(frozen=True)
class Detection:
    kind: Literal["emergency", "crisis"]
    matched: str

    @property
    def message(self) -> str:
        return CRISIS_MESSAGE if self.kind == "crisis" else EMERGENCY_MESSAGE


def detect_emergency(text: str, extra_phrases: list[str] | tuple[str, ...] = ()) -> Detection | None:
    """Check one caller utterance. Crisis (self-harm) is checked first: it needs a different message."""
    if not text:
        return None
    for p in CRISIS_PATTERNS:
        if m := p.search(text):
            return Detection("crisis", m.group(0))
    for p in EMERGENCY_PATTERNS:
        if m := p.search(text):
            return Detection("emergency", m.group(0))
    lowered = text.lower()
    for phrase in extra_phrases:
        if phrase and phrase.lower() in lowered:
            return Detection("emergency", phrase)
    return None


def is_clinical_question(text: str) -> bool:
    return any(p.search(text) for p in CLINICAL_QUESTION_PATTERNS)


def looks_like_advice(text: str) -> str | None:
    for p in ADVICE_PATTERNS:
        if m := p.search(text):
            return m.group(0)
    return None


# FULL RECEPTIONIST version. Restore it with the full flow in core/agent.py.
# CLINICAL_REMINDER = (
#     "[guardrail] The caller just asked a clinical question. Do not answer it or speculate. "
#     "Say you can't give medical advice, then offer to take a message for the clinical team, book an appointment, "
#     "or transfer to staff if it sounds urgent."
# )
CLINICAL_REMINDER = (
    "[guardrail] The caller just described a health question. Do not answer it, explain it or speculate. "
    "Say: \"I can't give medical advice, but someone from the clinic will call you back about it.\" Then ask "
    "the next thing you still need (their full name, or their phone number)."
)

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")


class ReplyFilter:
    """Sentence-buffered filter for a streamed LLM reply.

    Feed text chunks as they arrive; it emits whole sentences that pass. On the
    first sentence that looks like medical advice it emits SAFE_REDIRECT once and
    swallows the rest of the reply. Also strips Qwen <think> blocks.
    Buffering is one sentence, so it adds very little latency.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._in_think = False
        self.blocked: str | None = None  # the matched advice phrase, if any

    def feed(self, chunk: str) -> list[str]:
        if self.blocked is not None:
            return []
        self._buf += chunk
        self._strip_think()
        out: list[str] = []
        while not self._in_think:
            parts = _SENTENCE_END.split(self._buf, maxsplit=1)
            if len(parts) < 2:
                break
            sentence, self._buf = parts
            if emitted := self._check(sentence):
                out.append(emitted)
                if self.blocked is not None:
                    break
        return out

    def flush(self) -> list[str]:
        if self.blocked is not None:
            self._buf = ""
            return []
        self._strip_think()
        rest, self._buf = self._buf, ""
        if self._in_think:  # reply ended inside an unterminated think block: keep only what came before it
            rest = rest[: rest.lower().index("<think>")]
            self._in_think = False
        emitted = self._check(rest)
        return [emitted] if emitted else []

    def _strip_think(self) -> None:
        # Drop complete <think>…</think> blocks; while one is still open, hold everything.
        self._buf = _THINK.sub("", self._buf)
        self._in_think = "<think>" in self._buf.lower()

    def _check(self, sentence: str) -> str | None:
        sentence = sentence.strip()
        if not sentence:
            return None
        if hit := looks_like_advice(sentence):
            self.blocked = hit
            self._buf = ""
            return SAFE_REDIRECT
        return sentence


def filter_reply(text: str) -> str:
    """Non-streaming convenience: run a whole reply through ReplyFilter."""
    f = ReplyFilter()
    parts = f.feed(text) + f.flush()
    return " ".join(parts)
