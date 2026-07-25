"""Markdown extension that shields LaTeX math from Markdown processing.

`$…$`, `$$…$$`, `\\(…\\)` and `\\[…\\]` spans are stashed before Markdown runs and
restored verbatim afterwards, so Markdown never turns `x_1` / `a*b` inside a formula
into emphasis. KaTeX then renders the delimiters client-side (see quizzes/_katex.html).
"""

import re

from markdown import Extension
from markdown.postprocessors import Postprocessor
from markdown.preprocessors import Preprocessor

# Display delimiters first, then inline, so $$…$$ is not split by the $…$ rule.
_PATTERNS = [
    re.compile(r"\$\$.+?\$\$", re.DOTALL),
    re.compile(r"\\\[.+?\\\]", re.DOTALL),
    re.compile(r"\\\(.+?\\\)", re.DOTALL),
    re.compile(r"(?<!\\)\$(?!\s)(?:\\.|[^$\\])+?\$", re.DOTALL),
]

_PLACEHOLDER = "MATHPROTECTEDSPAN{}ENDMATH"


class _MathStash(Preprocessor):
    def run(self, lines):
        text = "\n".join(lines)
        store = []
        self.md._math_store = store

        def stash(match):
            store.append(match.group(0))
            return _PLACEHOLDER.format(len(store) - 1)

        for pattern in _PATTERNS:
            text = pattern.sub(stash, text)
        return text.split("\n")


class _MathRestore(Postprocessor):
    def run(self, text):
        for i, raw in enumerate(getattr(self.md, "_math_store", [])):
            text = text.replace(_PLACEHOLDER.format(i), raw)
        return text


class MathProtectExtension(Extension):
    def extendMarkdown(self, md):
        md.preprocessors.register(_MathStash(md), "math_stash", 100)  # run first
        md.postprocessors.register(_MathRestore(md), "math_restore", 0)  # run last


def makeExtension(**kwargs):
    return MathProtectExtension(**kwargs)
